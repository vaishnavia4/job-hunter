"""Answer bank: deterministic answers for application questions, learned from the user.

Resolution order for a question label:
  1. LEARNED answers in <workspace>/profile/answer_bank.json (source user_correction / user_filled) whose patterns match
  2. the canonical question bank (templates/question_bank.json, first matching entry wins)
       -> the workspace answer_bank entry with that id (source setup), else profile.json[profile_key]
  3. unknown -> ASK_USER  (then record_answer so it's never asked again)

Options matching is subgroup-aware: options_hint (most specific first) is tried before anything generic, and a
generic substring that hits several options (e.g. 'Asian' in 'East Asian'/'South Asian') is AMBIGUOUS, never a
silent first match.
"""
import re
from datetime import date

from .workspace import TEMPLATES, Workspace, load_json, save_json

STATUS_OK, ASK, TICK, ESSAY, AMBIG = "ok", "ASK_USER", "USER_MUST_TICK", "GENERATE_ESSAY", "AMBIGUOUS_OPTIONS"
STOP = set("a an the and or of to in for on at by with your you are is do does did have has will would be been being this that "
           "our we us any please select one which what how if as from it its not no yes can may currently ever".split())
NEG_RX = re.compile(r"\b(no|not|never|n't|haven't|don't|won't)\b")
DECLINE_RX = re.compile(r"decline|prefer not|do not wish|don't wish|not wish to|choose not|rather not")


def norm(s):
    s = (s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s.rstrip("*").strip()


def _bank():
    return load_json(TEMPLATES / "question_bank.json", {"questions": []})


def _ws_bank_path(ws):
    return ws.profile_dir / "answer_bank.json"


def load_answers(ws):
    return load_json(_ws_bank_path(ws), {"answers": []})


def _dig(d, path):
    cur = d
    for part in (path or "").split("."):
        if not part:
            return None
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _match_any(patterns, text):
    for p in patterns or []:
        try:
            if re.search(p, text, re.I):
                return p
        except re.error:
            if p.lower() in text:
                return p
    return None


def match_question(text):
    """Return the first canonical question whose patterns match the normalized label (or None)."""
    t = norm(text)
    for q in _bank()["questions"]:
        if _match_any(q["patterns"], t):
            return q
    return None


# ---------------------------------------------------------------- options
def _num(s):
    m = re.findall(r"\$?\s*(\d[\d,]*)(\s*k)?", s or "", re.I)
    out = []
    for n, k in m:
        v = int(n.replace(",", ""))
        if k:
            v *= 1000
        out.append(v)
    return out


def choose_option(answer, options, hints=None, strategy=""):
    """Pick the option that matches `answer`. Returns (option|None, how)."""
    if not options:
        return None, "no-options"
    opts = [o for o in options if norm(o) not in ("", "select one", "select...", "--")]
    na = norm(str(answer))
    # 1. bracket dropdowns (salary)
    if strategy == "bracket":
        vals = _num(str(answer))
        if vals:
            v = vals[0]
            ranges = []
            for o in opts:
                ns = _num(o)
                if len(ns) >= 2:
                    ranges.append((ns[0], ns[1], o))
                elif len(ns) == 1 and re.search(r"greater|more than|over|\+", o, re.I):
                    ranges.append((ns[0], 10**9, o))
            for lo, hi, o in ranges:
                if lo == v:
                    return o, "bracket-start"
            for lo, hi, o in ranges:
                if lo <= v < hi:
                    return o, "bracket-contains"
    # 2. exact
    for o in opts:
        if norm(o) == na:
            return o, "exact"
    # 3. hints, most specific first
    for h in hints or []:
        hits = [o for o in opts if re.search(h, norm(o), re.I)]
        if len(hits) == 1:
            return hits[0], f"hint:{h}"
        if len(hits) > 1:
            # prefer one that also contains the stored answer text, else the shortest
            both = [o for o in hits if na and na in norm(o)]
            if len(both) == 1:
                return both[0], f"hint+answer:{h}"
            return sorted(hits, key=len)[0], f"hint-multi:{h}"
    # 4. yes / no polarity
    bank = _bank()
    if na in ("yes", "no"):
        rx = bank.get("yes_hints" if na == "yes" else "no_hints", [])
        cands = []
        for o in opts:
            no_ = norm(o)
            if DECLINE_RX.search(no_):
                continue
            if any(re.search(r, no_) for r in rx):
                if na == "yes" and NEG_RX.search(no_) and not no_.startswith("yes"):
                    continue
                cands.append(o)
        if len(cands) == 1:
            return cands[0], "polarity"
        if len(cands) > 1:
            starts = [o for o in cands if norm(o).startswith(na)]
            if len(starts) == 1:
                return starts[0], "polarity-start"
    # 5. answer as a whole word inside exactly one option (never a silent first partial match)
    if na:
        hits = [o for o in opts if re.search(r"(^|\W)" + re.escape(na) + r"($|\W)", norm(o))]
        if len(hits) == 1:
            return hits[0], "contains"
        if len(hits) > 1:
            return None, "ambiguous:" + " | ".join(hits)
        # option text inside the answer (answer is more detailed than the option)
        inside = [o for o in opts if len(norm(o)) > 3 and norm(o) in na]
        if len(inside) == 1:
            return inside[0], "option-in-answer"
    return None, "no-match"


# ---------------------------------------------------------------- lookup
def _skill_years(ws, text, prof):
    m = re.search(r"(with|in|using|of) ([a-z0-9 .#+/-]{2,40})", norm(text))
    if not m:
        return None
    skill = re.sub(r"\b(experience|do you have|years?)\b", "", m.group(2)).strip(" ?.")
    exp = prof.get("experience", {}) or {}
    table = dict(exp.get("skill_years", {}) or {})
    for k, v in exp.items():
        if k.startswith("years_") and v not in (None, ""):
            table.setdefault(k[6:], v)
    for k, v in table.items():
        if k.lower() and (k.lower() in skill or skill in k.lower()):
            return v
    return None


def lookup(ws, question_text, options=None):
    """Best answer for a question label. Returns a dict with status/answer/option/confidence/source."""
    ws = ws if isinstance(ws, Workspace) else Workspace(ws)
    t = norm(question_text)
    bank_ws = load_answers(ws)
    prof = ws.profile() or {}
    res = {"question": question_text, "question_id": None, "status": ASK, "answer": None, "option": None,
           "confidence": 0.0, "source": None, "note": ""}

    # 1. learned answers (user corrections / user-filled) win
    learned = [a for a in bank_ws.get("answers", []) if a.get("source") in ("user_correction", "user_filled") and a.get("patterns")]
    for a in reversed(learned):  # newest first
        if _match_any(a["patterns"], t):
            q = match_question(question_text) or {}
            res.update(question_id=a.get("id") or q.get("id"), answer=a["answer"], source=a["source"], confidence=0.98,
                       status=TICK if a["answer"] == TICK else STATUS_OK, note=a.get("note", ""))
            hints = a.get("options_hint") or q.get("options_hint")
            return _with_option(res, options, hints, q.get("option_strategy", ""))

    q = match_question(question_text)
    if not q:
        res["note"] = "Unknown question - ask the user, then record_answer()."
        return res
    res["question_id"] = q["id"]
    if q["type"] == "user_must_tick":
        # a user-given standing answer may exist (e.g. they said 'tick certify boxes for me')
        own = next((a for a in bank_ws.get("answers", []) if a.get("id") == q["id"] and a.get("answer") not in (None, "", TICK)), None)
        if own:
            res.update(answer=own["answer"], source=own.get("source", "setup"), confidence=0.9, status=STATUS_OK, note=own.get("note", ""))
            return _with_option(res, options, own.get("options_hint"), "")
        res.update(status=TICK, note=q.get("notes", "") or "Legal attestation - the user ticks this.")
        return res
    if q["type"] == "per_job_essay":
        res.update(status=ESSAY, note="Write a truthful, job-specific answer from master_resume.json; save it in notes.md.")
        return res

    # 2. workspace answer for this id (setup)
    own = next((a for a in bank_ws.get("answers", []) if a.get("id") == q["id"]), None)
    hints = (own or {}).get("options_hint") or q.get("options_hint")
    if own and own.get("answer") not in (None, ""):
        ans = own["answer"]
        res.update(answer=ans, source=own.get("source", "setup"), confidence=0.95, note=own.get("note", ""),
                   status=ASK if ans == ASK else (TICK if ans == TICK else STATUS_OK))
        return _with_option(res, options, hints, q.get("option_strategy", ""))
    # 3. profile fallback
    val = _skill_years(ws, question_text, prof) if q["id"] == "years_skill" else _dig(prof, q.get("profile_key"))
    if isinstance(val, (list, dict)):
        val = None
    if val not in (None, ""):
        res.update(answer=str(val), source="profile:" + q["profile_key"], confidence=0.75, status=STATUS_OK)
        return _with_option(res, options, hints, q.get("option_strategy", ""))
    res["note"] = f"No stored answer for '{q['id']}' - ask the user ({q['prompt']}), then record_answer()."
    return res


def _with_option(res, options, hints, strategy):
    if res["status"] != STATUS_OK or not options:
        return res
    opt, how = choose_option(res["answer"], options, hints, strategy)
    res["option_match"] = how
    if opt is None:
        res["status"] = AMBIG if how.startswith("ambiguous") else ASK
        res["confidence"] = 0.3
        res["note"] = (res.get("note") or "") + f" | could not map answer '{res['answer']}' to options ({how}) - ask the user, then record_answer() with options_hint."
    else:
        res["option"] = opt
        res["confidence"] = round(min(res["confidence"], 0.99 if how in ("exact", "bracket-start") else 0.9), 2)
    return res


# ---------------------------------------------------------------- record
def auto_pattern(question_text):
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9'+#.-]*", norm(question_text)) if w not in STOP and len(w) > 2]
    words = words[:6] or [re.escape(norm(question_text))]
    return "".join(rf"(?=.*\b{re.escape(w)})" for w in words)


def record(ws, question_text, answer, source="user_correction", note="", options_hint=None, qid=None):
    """Store/overwrite an answer. Learned entries (user_correction/user_filled) override everything for matching labels."""
    ws = ws if isinstance(ws, Workspace) else Workspace(ws)
    data = load_answers(ws)
    answers = data.setdefault("answers", [])
    q = match_question(question_text)
    qid = qid or (q or {}).get("id") or "custom_" + re.sub(r"[^a-z0-9]+", "_", norm(question_text))[:40].strip("_")
    t = norm(question_text)
    pat = auto_pattern(question_text)
    entry = None
    for a in answers:
        if a.get("source") == source and a.get("patterns") and _match_any(a["patterns"], t):
            entry = a
            break
    if entry is None and source == "setup":
        entry = next((a for a in answers if a.get("id") == qid and a.get("source") == "setup"), None)
    if entry is None:
        entry = {"id": qid, "patterns": [], "source": source}
        answers.append(entry)
    if source != "setup" and pat not in entry["patterns"]:
        entry["patterns"].append(pat)
    entry.update(answer=answer, date=date.today().isoformat(), note=note or entry.get("note", ""),
                 example=question_text[:200])
    if options_hint:
        entry["options_hint"] = options_hint
    save_json(_ws_bank_path(ws), data)
    return entry


# ---------------------------------------------------------------- verify
def verify_form(ws, fields):
    """fields: [{label, value, options?, required?, kind?('file'|...)}] read back from the page.
    Returns ok / mismatches / missing_required / unknown / user_must_tick / file_problems."""
    ws = ws if isinstance(ws, Workspace) else Workspace(ws)
    out = {"ok": [], "mismatches": [], "missing_required": [], "unknown": [], "user_must_tick": [], "essays": [], "file_problems": []}
    for f in fields or []:
        label, value = f.get("label", ""), (f.get("value") or "")
        nl, nv = norm(label), norm(str(value))
        # file uploads go in the right box
        if f.get("kind") == "file" or re.search(r"resume|\bcv\b|cover letter|attach", nl):
            if "cover letter" in nl and re.match(r"resume", nv):
                out["file_problems"].append({"label": label, "value": value, "problem": "Resume uploaded into the Cover Letter field"})
                continue
            if re.search(r"resume|\bcv\b", nl) and "cover" not in nl and re.match(r"cover", nv):
                out["file_problems"].append({"label": label, "value": value, "problem": "Cover letter uploaded into the Resume field"})
                continue
            if f.get("required") and not nv:
                out["missing_required"].append(label)
            else:
                out["ok"].append(label)
            continue
        if f.get("required") and not nv:
            out["missing_required"].append(label)
            continue
        r = lookup(ws, label, f.get("options"))
        if r["status"] == TICK:
            out["user_must_tick"].append(label)
            continue
        if r["status"] == ESSAY:
            out["essays"].append(label)
            continue
        if r["status"] in (ASK, AMBIG):
            if nv:
                out["unknown"].append({"label": label, "value": value, "note": r["note"]})
            continue
        expected = r.get("option") or r["answer"]
        ne = norm(str(expected))
        if not nv:
            out["mismatches"].append({"label": label, "expected": expected, "found": value, "problem": "empty"})
        elif nv == ne or (len(ne) > 3 and (ne in nv or nv in ne)) or (
                len(re.sub(r"\D", "", nv)) >= 7 and re.sub(r"\D", "", nv)[-10:] == re.sub(r"\D", "", ne)[-10:]):
            out["ok"].append(label)
        else:
            out["mismatches"].append({"label": label, "expected": expected, "found": value, "source": r["source"]})
    out["pass"] = not (out["mismatches"] or out["missing_required"] or out["file_problems"] or out["unknown"])
    return out
