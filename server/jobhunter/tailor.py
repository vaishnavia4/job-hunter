"""One-call screening + tailoring: `python -m jobhunter --workspace WS tailor <job_id|folder> [--model sonnet] [--level L]`.

Sends ONE request (master resume + posting + rules) to a model through the headless Claude Code CLI with no tools, no
project instructions and a fresh context, writes decision.json / tailored.json / cover_letter.md, runs the normal build
(truth check, 2-page check, level check) and, if the build flags something, sends at most two short repair requests.
Prints one compact JSON result including the measured cost of the calls.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import resume
from .workspace import Workspace, load_json, save_json, today

LEVEL_RULES = {
    "auto": "Standard tailoring: pick and reword the most relevant bullets with the posting's vocabulary; keep each bullet's facts.",
    "low": "LOW: change only headline, summary, skill order and AT MOST 4 bullets. Every other bullet must be copied word-for-word from the master.",
    "medium": "MEDIUM: reorder/reword UP TO 12 bullets with the posting's keywords; drop irrelevant skill categories; other bullets word-for-word.",
    "high": "HIGH: rewrite everything around the posting (every bullet reworded and ranked by relevance, summary mirrors the posting's top requirements). Still only true facts.",
}

SYSTEM = """You screen one job posting for one candidate and, if it passes, write a tailored resume and cover letter.
Reply with ONE JSON object and nothing else (no code fence, no commentary).

TRUTH RULE (overrides everything): use ONLY facts, numbers, tools, skills, employers, titles, dates and certifications
that appear in MASTER_RESUME. Never invent or inflate. Every number you write must appear in the master exactly. A
posting keyword that the master does not back is a GAP: list it in decision.gaps, never put it on the resume or letter.
Skills listed under SECONDARY may be used only if the posting asks for them.

SCREENING: decision.decision = "skip" (with decision.reason) when a SKIP_RULE applies; otherwise "apply". Be honest about
fit (0-100) and gaps. When skipping, set "tailored" and "cover_letter" to null.

RESUME ("tailored", same shape as the master: name, contact, headline, summary, skills{category:[...]}, experience[
{company,title,location,dates,bullets[]}], certifications, education):
- headline = ONE job title only: the posting's title, cleaned up (e.g. "Site Reliability Engineer - DevOps, Azure" becomes
  "Site Reliability Engineer"). No pipes, no skill lists, no team names, no requisition numbers.
- summary: 3-4 sentences mirroring the posting's title and top 3-4 requirements in its own wording, only where true.
- skills: reorder categories and items so the posting's keywords come first, use the posting's spelling, drop irrelevant categories.
- experience: same employers, titles, dates and order as the master, never changed. Choose the most relevant bullets
  (BULLET_COUNTS gives the target per employer). Keep every metric exact. Bold 1-2 key metrics per role with **...**.
- certifications and education copied from the master (earned ones only).
- Must fit 2 pages: stay within the bullet counts and keep bullets to 1-2 lines.

COVER LETTER ("cover_letter", markdown): 3-4 short paragraphs, about 250 words, specific to this company and role, one
or two concrete metrics, no cliches, NEVER mentions gaps or missing skills, signed with the candidate's full name.

OUTPUT SHAPE:
{"decision": {"decision": "apply"|"skip", "reason": "", "fit": 0, "gaps": [""], "notes": "location / relocation / salary / questions to expect"},
 "tailored": {...} | null, "cover_letter": "..." | null, "changes": "3-5 short lines on what was changed and why"}"""


def _claude(prompt, system, model, timeout=600):
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not found on PATH")
    cwd = tempfile.mkdtemp(prefix="jh_tailor_")  # empty dir: no project instructions, no project settings
    # the rules travel in the message (stdin): long multi-line arguments do not survive every Windows launcher
    prompt = "RULES:\n" + system + "\n\n" + prompt
    cmd = [exe, "-p", "--model", model, "--system-prompt", "Follow the RULES in the message exactly. Reply with one JSON object only.",
           "--tools", "", "--strict-mcp-config",
           "--disable-slash-commands", "--no-session-persistence", "--setting-sources", "user", "--output-format", "json"]
    try:
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd)
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
    try:
        res = json.loads(p.stdout)
    except Exception:
        raise RuntimeError("claude -p gave no JSON: " + ((p.stderr or p.stdout or "")[-400:]))
    if isinstance(res, list):  # some versions print the event list
        res = next((x for x in reversed(res) if isinstance(x, dict) and x.get("type") == "result"), {})
    if res.get("is_error"):
        raise RuntimeError("model call failed: " + str(res.get("result"))[:400])
    u = res.get("usage") or {}
    return res.get("result") or "", {"cost_usd": res.get("total_cost_usd") or 0,
                                     "in": (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0),
                                     "out": u.get("output_tokens") or 0}


def _parse(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    return json.loads(t[i:j + 1])


def _skip_rules(ws):
    prof = ws.profile() or {}
    floor = ((prof.get("job_preferences") or {}).get("salary_floor") or (prof.get("job_preferences") or {}).get("min_salary") or "")
    rules = ["requires citizenship, any security clearance or public trust, or says no visa sponsorship (candidate needs H-1B transfer sponsorship)"
             if re.search(r"h-?1b|sponsor", json.dumps(prof.get("work_authorization") or prof), re.I) else "",
             "requires as a primary skill something the master does not have (e.g. a main programming language, deep ML research)",
             "is really a different function (pure software engineering, data engineering, sales/presales)",
             f"the posted salary maximum is below {floor}" if floor else "",
             "is far more senior than the candidate's experience"]
    extra = ((ws.cfg("search", {}) or {}).get("screen_rules") or [])
    return [r for r in rules + list(extra) if r]


def _bullet_counts(master):
    exp = master.get("experience") or []
    out = {}
    for i, e in enumerate(exp):
        n = len(e.get("bullets") or [])
        out[e.get("company", f"role {i}")] = "8-10" if i == 0 and n >= 10 else ("about 5" if i in (1, 2) and n >= 5 else f"about {min(n, 3)}")
    return out


PRESCREEN = """You decide whether one job posting is worth tailoring a resume for. You see a short candidate summary, not the
full resume. Reply with one JSON object only: {"decision": "apply"|"skip", "reason": "one sentence"}.
Answer "skip" ONLY when a SKIP_RULE clearly applies or the job is clearly a different function from the candidate's
(for example sales, people management, policy, hardware, pure software development in a language the candidate does not
list as a main skill). When in doubt, answer "apply": a second, careful screening follows."""


def _summary(master):
    """A few hundred tokens that describe the candidate well enough for a first screen."""
    exp = master.get("experience") or []
    skills = master.get("skills") or {}
    flat = json.dumps(skills, ensure_ascii=False)
    return "\n".join([
        "Headline: " + str(master.get("headline") or ""),
        "Summary: " + str(master.get("summary") or "")[:700],
        "Roles: " + "; ".join(f"{e.get('title')} at {e.get('company')}" for e in exp),
        "Skills: " + flat[:1500],
        "Certifications: " + ", ".join(map(str, master.get("certifications") or [])),
    ])


def prescreen(ws, row, jd, master, model="haiku"):
    """Cheap first screen (default Haiku). -> (decision, reason, usage). Errors count as 'apply' (the full screen decides)."""
    prompt = "\n\n".join([
        "SKIP_RULES:\n- " + "\n- ".join(_skip_rules(ws)),
        "CANDIDATE:\n" + _summary(master),
        f"JOB: {row['company']} - {row['title']} | location: {row.get('location', '')} | salary: {row.get('salary', '') or 'not listed'}",
        "POSTING:\n" + jd[:12000],
    ])
    try:
        text, u = _claude(prompt, PRESCREEN, model, timeout=180)
        d = _parse(text)
        return ("skip" if d.get("decision") == "skip" else "apply"), str(d.get("reason") or ""), u
    except Exception as e:
        return "apply", f"prescreen failed ({type(e).__name__})", {"cost_usd": 0, "in": 0, "out": 0}


def tailor(ws, target, model="sonnet", level=None, force=False, jd_text=None, prescreen_model="haiku"):
    ws = ws if isinstance(ws, Workspace) else Workspace(ws)
    rows = ws.load_tracker()
    key = str(target).replace("\\", "/").rstrip("/").split("/")[-1]
    row = next((r for r in rows if r["job_id"] == target), None) or \
        next((r for r in rows if r.get("folder") and r["folder"].replace("\\", "/").rstrip("/").split("/")[-1] == key), None)
    if not row:
        return {"status": "error", "detail": f"not in tracker: {target}"}
    if not row.get("folder"):
        from . import applications
        applications.prepare(ws, row["job_id"])
        row = next(r for r in ws.load_tracker() if r["job_id"] == row["job_id"])
    folder = ws.root / row["folder"].replace("\\", "/")
    if (folder / "tailored.json").exists() and not force:
        return {"status": "error", "job_id": row["job_id"], "detail": "already tailored (use --force to redo)"}
    jd = (folder / "JD.md").read_text(encoding="utf-8") if (folder / "JD.md").exists() else ""
    if len(jd) < 300 and jd_text and len(jd_text) >= 300:  # search could not fetch the description: use the live page text
        jd = "# " + row["company"] + " - " + row["title"] + "\n\n" + jd_text
        (folder / "JD.md").write_text(jd, encoding="utf-8")
    if len(jd) < 300:
        return {"status": "error", "job_id": row["job_id"], "detail": "JD.md missing or too short"}
    if level:
        resume.set_tailor_level(folder, resume.tailor_level(ws, folder, level))
    lvl = resume.tailor_level(ws, folder)
    master = {k: v for k, v in (ws.master() or {}).items() if not k.startswith("_")}
    skills_cfg = ws.cfg("skills", {}) or {}
    secondary = [k for k, v in (skills_cfg.get("vocab") or {}).items() if isinstance(v, dict) and v.get("have") == "secondary"]
    pre_cost = 0.0
    if prescreen_model and prescreen_model != model:
        d0, why0, u0 = prescreen(ws, row, jd, master, prescreen_model)
        pre_cost = u0.get("cost_usd") or 0
        if d0 == "skip":
            save_json(folder / "decision.json", {"decision": "skip", "reason": why0, "screened_by": prescreen_model})
            ws.set_fields(row["job_id"], status="skipped", notes=("screen: " + why0)[:300])
            return {"status": "skipped", "job_id": row["job_id"], "company": row["company"], "reason": why0[:300],
                    "model": prescreen_model, "cost_usd": round(pre_cost, 4)}
    prompt = "\n\n".join([
        "TAILORING_LEVEL: " + LEVEL_RULES[lvl],
        "SKIP_RULES:\n- " + "\n- ".join(_skip_rules(ws)),
        "BULLET_COUNTS: " + json.dumps(_bullet_counts(master)),
        "SECONDARY: " + json.dumps(secondary),
        f"JOB: {row['company']} - {row['title']} | location: {row.get('location', '')} | salary: {row.get('salary', '') or 'not listed'}",
        "POSTING:\n" + jd[:24000],
        "MASTER_RESUME:\n" + json.dumps(master, ensure_ascii=False),
    ])
    calls, cost = [], pre_cost
    try:
        text, u = _claude(prompt, SYSTEM, model)
        calls.append(u); cost += u["cost_usd"]
        out = _parse(text)
    except Exception as e:
        return {"status": "error", "job_id": row["job_id"], "detail": f"{type(e).__name__}: {str(e)[:400]}", "cost_usd": round(cost, 4)}
    dec = out.get("decision") or {}
    if dec.get("decision") == "skip" or not out.get("tailored"):
        save_json(folder / "decision.json", {**dec, "decision": "skip"})
        ws.set_fields(row["job_id"], status="skipped", notes=("screen: " + str(dec.get("reason") or "skip"))[:300])
        return {"status": "skipped", "job_id": row["job_id"], "company": row["company"], "reason": str(dec.get("reason"))[:300],
                "model": model, "cost_usd": round(cost, 4)}

    build = {}
    for attempt in range(3):
        save_json(folder / "tailored.json", out["tailored"])
        (folder / "cover_letter.md").write_text(out.get("cover_letter") or "", encoding="utf-8")
        build = resume.build(ws, folder)
        issues = [] if build.get("truth_check") == "PASS" else list(build.get("truth_check") or [])
        if build.get("level_check") not in (None, "ok"):
            issues.append(build["level_check"])
        if not issues or attempt == 2:
            break
        fix = ("Your previous answer failed these checks:\n- " + "\n- ".join(str(i)[:300] for i in issues) +
               "\n\nFix ONLY those problems (use the master's exact wording or remove the item; trim the least relevant bullets if it is too long)"
               " and reply with the full corrected JSON object in the same shape.\n\nPREVIOUS_ANSWER:\n" + json.dumps(out, ensure_ascii=False) +
               "\n\nMASTER_RESUME:\n" + json.dumps(master, ensure_ascii=False))
        try:
            text, u = _claude(fix, SYSTEM, model)
            calls.append(u); cost += u["cost_usd"]
            new = _parse(text)
            if new.get("tailored"):
                out = {**out, **new}
        except Exception as e:
            issues.append(f"repair call failed: {str(e)[:200]}")
            break
    ok = not issues
    save_json(folder / "decision.json", {"decision": "apply", "fit": dec.get("fit"), "coverage": build.get("coverage"),
                                         "gaps": dec.get("gaps") or [], "notes": dec.get("notes") or ""})
    notes = folder / "notes.md"
    if notes.exists():
        s = notes.read_text(encoding="utf-8")
        why = f"Fit {dec.get('fit')}%, keyword coverage {build.get('coverage')}. {dec.get('notes') or ''}\n\nTrue gaps (resume/interview only, never in cover letter):\n" + \
              "\n".join("- " + str(g) for g in (dec.get("gaps") or []))
        s = s.replace("(filled during screening/tailoring: fit %, gaps, location, salary)", why)
        s = s.replace("## Tailoring changes\n(filled during tailoring)",
                      f"## Tailoring changes\nLevel {lvl}; {build.get('bullets_reworded')} bullets reworded, {build.get('bullets_kept')} kept ({model}).\n{out.get('changes') or ''}")
        s = s.rstrip() + f"\n- {today()}: screened (apply) and tailored by `jobhunter tailor` ({model})\n"
        notes.write_text(s, encoding="utf-8")
    if ok:
        ws.set_fields(row["job_id"], status="tailored")
    return {"status": "tailored" if ok else "needs_review", "job_id": row["job_id"], "company": row["company"], "folder": str(folder.name),
            "fit": dec.get("fit"), "gaps": [str(g)[:120] for g in (dec.get("gaps") or [])][:8], "level": lvl,
            "bullets_reworded": build.get("bullets_reworded"), "bullets_kept": build.get("bullets_kept"), "pages": build.get("pages"),
            "coverage": build.get("coverage"), "truth_check": "PASS" if ok else issues, "model": model, "calls": len(calls),
            "tokens_in": sum(c["in"] for c in calls), "tokens_out": sum(c["out"] for c in calls), "cost_usd": round(cost, 4)}
