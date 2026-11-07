"""Interview prep pack for one application: applications/<folder>/interview_prep.md

  python -m jobhunter --workspace WS prep <folder | job_id>

Deterministic templates, no LLM calls. Sources:
  job.json / JD.md        company + role snapshot, requirements (-> likely questions, questions to ask)
  master_resume.json      the ONLY source of stories: every Situation/Task/Action/Result line is quoted from a
                          master bullet (or its role context) word for word, so every metric stays exact
  changes.md              reworded resume lines she must be able to defend (rebuilt from tailored.json if absent)
  match_report.md, notes.md   true gaps, framed honestly with the closest thing she HAS done
"""
import re
from pathlib import Path

from .workspace import Workspace, load_json

STOP = set("""a an the and or of to in on for with by at as is are be from that this you your our we will who
have has having into across using use used within plus etc via more than other such their they them able
experience years year strong knowledge understanding ability skills skill working work including e g ie
service services solution solutions tool tools system systems team teams best practice practices environment
environments enterprise level levels all varying people high degree similar etc
""".split())
SECTION_RX = [  # (weight, heading regex)
    (3.0, r"required|requirements|qualifications|must[- ]have|what you('ll)? (bring|need)|you (have|bring)|about you|"
          r"who you are|skills|what we('re)? look(ing)? for|minimum|basic"),
    (1.5, r"preferred|nice[- ]to[- ]have|bonus|plus|desired|supporting knowledge"),
    (2.0, r"responsibilit|what you('ll)? (do|own|work on)|the role|in this role|day[- ]to[- ]day|duties|you will"),
    (0.0, r"benefits|perks|compensation|pay|salary|equal opportunity|eeo|about (us|the company)|why join|physical"),
]
NOISE_RX = re.compile(r"equal opportunity|benefit|401\(?k|\bpto\b|insurance|salary|pay range|compensation|accommodat|"
                      r"veteran|disabilit|e-verify|privacy|background check|drug|base pay|bonus|equity|holiday|"
                      r"https?://|apply now|recruit|visa|sponsor", re.I)
REQ_WORDS = re.compile(r"experience|certif|knowledge|proficien|familiar|ability|years|strong|understanding|hands-on|expert|"
                       r"design|build|own|lead|implement|operate|automat|manage|support", re.I)
BEHAVIOURAL = re.compile(r"\blead|mentor|coach|stakeholder|communicat|collaborat|cross-functional|ownership|"
                         r"prioriti|ambigu|influence|customer|business|translate|partner", re.I)
TECH_FOLLOWUPS = [  # (regex over requirement + skill names, follow-up question)
    (r"terraform|bicep|\biac\b|infrastructure as code|arm templ|pulumi|cloudformation",
     "How do you structure IaC modules, state and promotion between environments, and how do you stop drift?"),
    (r"kubernetes|\baks\b|\beks\b|\bgke\b|helm|container",
     "How do you run Kubernetes upgrades safely, and how would you debug a pod stuck in CrashLoopBackOff?"),
    (r"network|vnet|vpc|firewall|\bdns\b|private endpoint|\bwaf\b|load balanc|hub-and-spoke",
     "Walk us through the traffic path from the internet to a private backend in your hub-and-spoke design."),
    (r"ci/?cd|pipeline|azure devops|github actions|jenkins|release|deploy",
     "Describe a pipeline you built end to end: stages, gates/approvals, secrets, and how you roll back."),
    (r"incident|on-?call|sre|reliab|observab|monitor|alert|sev",
     "Walk us through a production incident you led: detection, triage, communication and the follow-up."),
    (r"iam|identity|entra|okta|rbac|least privilege|access|zero trust|conditional access",
     "How do you enforce least privilege and run access reviews without slowing engineers down?"),
    (r"complian|iso ?27001|soc ?2|audit|grc|policy|controls|pci|fedramp",
     "How did you prepare for the audit: control ownership, evidence collection, and handling findings?"),
    (r"cost|finops|spend|right-?siz|budget",
     "How did you find the savings, and how did you keep costs from creeping back?"),
    (r"security|devsecops|vulnerab|secret|key vault|vault|snyk|defender",
     "How do you build security checks into delivery without blocking releases?"),
    (r"python|powershell|bash|script|automation|api",
     "Show us a piece of automation you wrote: what it replaced and how you made it safe to rerun."),
]
ADJACENT = [  # (gap regex, phrases she may honestly point to INSTEAD - kept only if found in master_resume.json)
    (r"okta|ping|sailpoint|saviynt|cyberark|auth0|iam", ["Microsoft Entra ID", "Conditional Access", "Identity lifecycle", "RBAC"]),
    (r"\bgo\b|golang|\bjava\b|\brust\b|c\+\+", ["Python", "PowerShell", ".NET", "C#", "Bash"]),
    (r"helm|argo|flux|kustomize", ["Kubernetes", "Docker", "Bicep"]),
    (r"capacity", ["right-sizing", "Azure Monitor", "Log Analytics"]),
    (r"aws|cloudformation|cdk", ["AWS", "EC2", "EKS", "Terraform"]),
    (r"gcp|google cloud", ["GCP", "GKE", "Cloud Run"]),
    (r"datadog|prometheus|grafana|splunk|new relic|observab", ["Azure Monitor", "Application Insights", "Log Analytics"]),
    (r"power ?bi|copilot studio|rpa|desktop flow|power automate", ["Power Platform", "Power Automate", "Dataverse"]),
    (r"soc ?2|pci|fedramp|nist|hipaa|grc", ["ISO 27001", "Azure Policy", "evidence collection"]),
    (r"siem|sentinel|soc\b|detection|edr|xdr", ["Microsoft Defender", "Defender XDR", "Incident management"]),
    (r"bgp|ospf|mpls|cisco|palo alto|juniper|arista", ["Azure Firewall", "VPN", "hub-and-spoke", "Routing"]),
    (r"\bml\b|machine learning|llm|\bai\b", ["Azure OpenAI", "MCP", "AI agents"]),
    (r"mortgage|lending|regulat", ["Mortgage-tech", "ISO 27001"]),
]
UNITS = (r"%|\+|x\b|[MK]\b(?:\s+[a-z/]+){0,3}|\s*(?:days?|hours?|h\b|min(?:utes)?\b|weeks?|engineers?|clients?|"
         r"subscriptions?|tenants?|(?:enterprise )?applications?|private endpoints?|private DNS zones?|products?|"
         r"controls?|teams?|users?|pipelines?|modules?|environments?|regions?|services?)\b")
METRIC_RX = re.compile(r"from (?:over |under |about )?(?:a|an|\d[\d.,-]*)\s+[a-z]+ to (?:under |about )?(?:a|an|\d[\d.,-]*\s+)?[a-z]+"
                       r"|(?<![A-Za-z0-9.])(?:over |under |nearly |around |about )?\d[\d,.]*(?:-\d[\d,.]*)?(?:" + UNITS + r")"
                       r"(?:\s+to\s+(?:under\s+)?(?:a\s+day|minutes|\d[\w-]*(?:\s+\w+)?))?", re.I)
SYN = {"secret": "key vault managed identity credential", "credential": "key vault managed identity secret",
       "monitor": "observability alert dashboard log analytics application insights", "alert": "monitor observability",
       "incident": "sev1 escalation on-call", "cost": "spend right-siz", "audit": "iso 27001 evidence control",
       "governance": "policy rbac azure policy", "iac": "bicep terraform infrastructure as code",
       "pipeline": "ci/cd azure devops", "identity": "entra rbac access conditional",
       "directory": "entra identity rbac", "communicat": "present advise customers ceo mentor",
       "collaborat": "partner customers mentor present", "stakeholder": "present advise customers ceo",
       "analytic": "troubleshoot escalation production issues", "troubleshoot": "escalation production issues",
       "problem": "troubleshoot escalation production issues", "mfa": "conditional access entra",
       "authentication": "conditional access mfa entra"}


def _stem(w):
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > 5 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _norm_tokens(s, expand=False):
    toks = {_stem(w) for w in re.findall(r"[a-z0-9#+./-]+", (s or "").lower()) if w not in STOP and len(w) > 2}
    if expand:
        for t in list(toks):
            for k, more in SYN.items():
                if t.startswith(k):
                    toks |= {_stem(x) for x in more.split()}
    return toks


def _vocab(ws):
    try:
        from .search import load_vocab
        return load_vocab(ws)
    except Exception:
        return {}


def resolve_folder(ws: Workspace, target):
    p = Path(target)
    for cand in (p, ws.root / target, ws.apps / target):
        if cand.is_dir() and (cand / "job.json").exists():
            return cand.resolve()
    row = next((r for r in ws.load_tracker() if r["job_id"] == target), None)
    if row and row.get("folder"):
        f = ws.root / row["folder"].replace("\\", "/")
        if f.is_dir():
            return f.resolve()
    raise FileNotFoundError(f"No application folder for '{target}' (give the folder name or a tracker job_id)")


# ---------------------------------------------------------------- JD parsing
def jd_lines(text):
    """JD text -> [(section_weight, line)] with headings tracked. Long blobs are split into sentences."""
    out, weight = [], 0.5
    for raw in re.split(r"\n|•|\s{3,}|(?<=[.;:])\s+(?=[A-Z])", text or ""):
        line = re.sub(r"\s+", " ", raw).strip(" -*·\t")
        if not line:
            continue
        if len(line) < 70 and not line.endswith("."):
            for w, rx in SECTION_RX:
                if re.search(rx, line, re.I):
                    weight = w
                    break
            continue
        if 30 <= len(line) <= 400:
            out.append((weight, line))
    return out


INTRO_RX = re.compile(r"\b(we|we're|we are|our|us)\b|looking for|join (us|our|the)|belong|meaningful work|whether you|culture|career|fortune \d+|mission", re.I)


def requirements(job, vocab, n_max=11):
    text = job.get("description") or ""
    comp = (job.get("company") or "").split()[0] if job.get("company") else ""
    cands = []
    for i, (w, line) in enumerate(jd_lines(text)):
        if w == 0.0 or NOISE_RX.search(line):
            continue
        skills = [k for k, (rx, _) in vocab.items() if rx.search(line)]
        if len(skills) < 2 and (INTRO_RX.search(line) or (comp and re.search(r"\b" + re.escape(comp) + r"\b", line, re.I))):
            continue  # company blurb / "we are looking for", not a requirement
        score = w + 1.5 * min(len(skills), 4) + (1.0 if REQ_WORDS.search(line) else 0)
        if not skills and not REQ_WORDS.search(line):
            continue
        cands.append((score, i, line, skills))
    chosen = []
    for score, i, line, skills in sorted(cands, key=lambda c: (-c[0], c[1])):
        toks = _norm_tokens(line)
        if any(len(toks & _norm_tokens(c[2])) / max(1, len(toks | _norm_tokens(c[2]))) > 0.5 for c in chosen):
            continue
        chosen.append((score, i, line, skills))
        if len(chosen) >= n_max:
            break
    return [{"text": line, "skills": skills} for _, i, line, skills in sorted(chosen, key=lambda c: c[1])]


def _short(req, n=110):
    s = re.sub(r"^((strong|deep|solid|proven|demonstrated)\s+)?(experience|knowledge|proficiency|familiarity|ability|"
               r"understanding)\b\s*(?:(with|of|in|to|using)\b)?\s*", "", req, flags=re.I)
    s = s.rstrip(".;: ")
    return (s[:n].rsplit(" ", 1)[0] + "...") if len(s) > n else s


def question_for(req, asked=None):
    asked = asked if asked is not None else set()
    text = req["text"] + " " + " ".join(req["skills"])
    if BEHAVIOURAL.search(req["text"]) and len(req["skills"]) <= 1:
        return "Behavioural", f"Tell us about a time that shows this: \"{_short(req['text'])}\". What did you do and what changed?"
    for rx, q in TECH_FOLLOWUPS:
        if re.search(rx, text, re.I) and q not in asked:
            asked.add(q)
            lead = f"Where have you used {', '.join(req['skills'][:3])} in production? " if req["skills"] else ""
            return "Technical", lead + q
    return "Technical", f"How have you handled this in your current role: \"{_short(req['text'])}\"?"


# ---------------------------------------------------------------- master resume stories
def master_bullets(master):
    out = []
    for ri, e in enumerate(master.get("experience", [])):
        for b in e.get("bullets", []):
            out.append({"company": e.get("company", ""), "title": e.get("title", ""), "dates": e.get("dates", ""),
                        "context": e.get("context", ""), "bullet": b, "role_index": ri})
    return out


def best_bullets(req, bullets, vocab, used, k=2):
    rt = _norm_tokens(req["text"], expand=True)
    rs = set(req["skills"])
    scored = []
    for i, b in enumerate(bullets):
        bs = {s for s, (rx, _) in vocab.items() if rx.search(b["bullet"])}
        tok = len(rt & _norm_tokens(b["bullet"])) / max(1, min(len(rt), 12))
        s = 1.5 * len(rs & bs) + 8 * tok + (0.3 if METRIC_RX.search(b["bullet"]) else 0) - (2.0 if i in used else 0)
        s += (1.0, 0.5, 0.2, 0.0)[min(b.get("role_index", 3), 3)]
        scored.append((s, i))
    scored.sort(key=lambda x: -x[0])
    return [i for s, i in scored[:k] if s > 0.6]


def metrics_in(text):
    out = []
    for m in METRIC_RX.finditer(text):
        t = m.group(0).strip(" ,.")
        if (re.search(r"\d", t) or t.lower().startswith("from ")) and not re.fullmatch(r"\d{4}", t) and t not in out:
            out.append(t)
    return out


def first_clause(text):
    depth = 0
    for i, ch in enumerate(text):
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0 and ch in ",;:" and i + 1 < len(text) and text[i + 1] == " ":
            return text[:i]
    return text


def first_sentence(text):
    for m in re.finditer(r"\.\s+(?=[A-Z0-9])", text or ""):
        prev = text[: m.start()].rsplit(" ", 1)[-1].lower()
        if prev not in ("incl", "e.g", "i.e", "etc", "vs", "approx", "inc", "co"):
            return text[: m.start() + 1]
    return text or ""


def star(b):
    """Split one master bullet into S/T/A/R using only its own words (and its role context)."""
    text = b["bullet"].strip()
    ctx = first_sentence(b["context"].strip()) if b["context"] else ""
    situation = f"{b['title']} at {b['company']} ({b['dates']})" + (f". {ctx}" if ctx else "")
    first = first_clause(text)
    m = re.match(r"^(.*?)\s+by\s+((?:[a-z]+ing)\b.*)$", text)
    if m:
        task, action, result_txt = m.group(1), m.group(2), m.group(1)
    else:
        task, action, result_txt = first, text, text
    mets = metrics_in(result_txt) or metrics_in(text)
    return {"situation": situation, "task": task, "action": action, "metrics": mets, "bullet": text}


def _bold_metrics(text, mets):
    for m in sorted(mets, key=len, reverse=True):
        if m in text and f"**{m}**" not in text:
            text = text.replace(m, f"**{m}**", 1)
    return text


# ---------------------------------------------------------------- changes / gaps
def reworded_lines(folder, ws):
    ch = folder / "changes.md"
    out = []
    if ch.exists():
        txt = ch.read_text(encoding="utf-8")
        for m in re.finditer(r"- REWORDED \((\d+)% similar\):\s*\n\s*- now: (.+?)\n\s*- from: (.+?)(?:\n|$)", txt):
            out.append({"similar": int(m.group(1)), "now": m.group(2).strip(), "from": m.group(3).strip()})
        return out, "changes.md"
    tailored = load_json(folder / "tailored.json")
    if not tailored:
        return [], "none"
    from .resume import change_report
    rep = change_report(tailored, ws.master())
    for r in rep["roles"]:
        for b in r["bullets"]:
            if b["status"] == "reworded":
                out.append({"similar": int(b["similarity"] * 100), "now": b["tailored"], "from": b["closest_master"]})
    return out, "tailored.json vs master_resume.json"


def gaps(folder):
    out = []
    mr = folder / "match_report.md"
    if mr.exists():
        m = re.search(r"\*\*True gaps[^*]*\*\*\s*(.+)", mr.read_text(encoding="utf-8"))
        if m and m.group(1).strip().lower() not in ("none", "-"):
            out += [g.strip() for g in m.group(1).split(",") if g.strip()]
    nf = folder / "notes.md"
    if nf.exists():
        m = re.search(r"^##\s*(True )?Gaps?\b.*?\n(.*?)(?=^##\s|\Z)", nf.read_text(encoding="utf-8"), re.M | re.S | re.I)
        if m:
            out += [re.sub(r"^\s*[-*]\s*", "", l).strip() for l in m.group(2).splitlines() if re.match(r"\s*[-*]\s+\S", l)]
    uniq = []
    for g in sorted(out, key=len, reverse=True):  # keep the more descriptive wording
        t = _norm_tokens(_gap_name(g))
        if any(len(t & _norm_tokens(_gap_name(u))) >= max(1, min(len(t), 2)) for u in uniq):
            continue
        uniq.append(g)
    return [g for g in out if g in uniq]


def covered_now(gap, master_text):
    """Parts of a recorded gap that master_resume.json now mentions (e.g. a note written before the resume was updated)."""
    parts = [p.strip() for p in re.split(r"[/,]| and ", _gap_name(gap)) if len(p.strip()) >= 3]
    hits = [p for p in parts if re.search(r"(?<![a-z])" + re.escape(p.lower()) + r"(?![a-z])", master_text.lower())]
    return hits if parts and len(hits) * 2 > len(parts) else []


def gap_framing(gap, master_text, bullets, vocab):
    now = covered_now(gap, master_text)
    if now:
        best = best_bullets({"text": " ".join(now), "skills": []}, bullets, vocab, set(), k=1)
        return (f"recorded as a gap earlier, but master_resume.json now covers {', '.join(now)}: talk about it as "
                f"real experience (story below), and re-check notes.md."), (bullets[best[0]] if best else None)
    lists = [[p for p in phrases if p.lower() in master_text.lower()] for rx, phrases in ADJACENT if re.search(rx, gap, re.I)]
    adj = []
    for i in range(4):  # one phrase from each matching area first, then the rest
        for lst in lists:
            if i < len(lst) and lst[i] not in adj:
                adj.append(lst[i])
    best = best_bullets({"text": " ".join([gap] + adj), "skills": []}, bullets, vocab, set(), k=1) if adj else []
    if adj:
        line = (f"\"I haven't done {_gap_name(gap)} hands-on in production. The closest experience I have is with "
                f"{', '.join(adj[:3])}; the same patterns carry over, and I'd ramp up on the specifics quickly.\"")
    else:
        line = (f"\"That's not something I've done hands-on yet ({_gap_name(gap)}). Here's how I'd get up to speed, "
                f"and the related work I have done...\" (be honest; do not stretch an adjacent skill into a claim)")
    return line, (bullets[best[0]] if best else None)


def _gap_name(g):
    g = re.sub(r"\s*\(.*?\)\s*", " ", g)
    g = re.split(r"\s+(?:not (?:explicit|claimed|on)|only via|only\b|hands-on not|depth\b)", g)[0]
    return g.strip().rstrip(".:;,").strip()


# ---------------------------------------------------------------- questions to ask
ASK = [
    (r"on-?call|incident|sre|reliab|sev", "What does on-call look like here: rotation size, paging volume, and how incidents are reviewed afterwards?"),
    (r"terraform|bicep|\biac\b|infrastructure as code", "How is infrastructure as code organised today (modules, environments, promotion), and what would you want changed in the first 90 days?"),
    (r"complian|iso|soc ?2|audit|grc|pci|fedramp|hipaa", "Which frameworks are you audited against, and how is evidence collected today: automated or manual?"),
    (r"network|vnet|vpc|firewall|zero trust", "Who owns the network and identity boundaries today, and where do platform and security teams hand off?"),
    (r"azure.*aws|aws.*azure|multi-?cloud|gcp", "How is the estate split across cloud providers, and is that mix expected to change?"),
    (r"ci/?cd|pipeline|devops|platform|developer", "How do platform and application teams share ownership of pipelines and production?"),
    (r"mortgage|lend|bank|financ|credit union|fintech", "Which regulatory requirements shape how you ship infrastructure changes?"),
]
ASK_ALWAYS = [
    "What would make the person in this role clearly successful at six months?",
    "What is the biggest reliability or security risk you would want this hire to take on first?",
    "How is the team structured, and who would I work with most closely day to day?",
]


def questions_to_ask(job_text):
    out = [q for rx, q in ASK if re.search(rx, job_text, re.I)][:3]
    for q in ASK_ALWAYS:
        if len(out) >= 5:
            break
        out.append(q)
    return out[:5]


# ---------------------------------------------------------------- main
def _snapshot(job, row, notes_txt):
    desc = job.get("description") or ""
    comp = job.get("company") or row.get("company", "")
    key = re.escape(comp.split()[0]) if comp else "$^"
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", desc)) if 40 < len(s.strip()) < 350]
    about = [s for s in sents if re.search(key, s, re.I) and not NOISE_RX.search(s)][:2]
    sal = job.get("salary") or job.get("salary_text") or ""
    if not sal:
        m = re.search(r"\*\*Salary listed:\*\*\s*(.+)", notes_txt)
        sal = m.group(1).strip() if m else ""
    return comp, about, sal


def interview_prep(ws: Workspace, target):
    folder = resolve_folder(ws, target)
    job = load_json(folder / "job.json", {}) or {}
    if not job.get("description") and (folder / "JD.md").exists():
        job["description"] = (folder / "JD.md").read_text(encoding="utf-8").split("---", 1)[-1]
    master = ws.master()
    rel = str(folder.relative_to(ws.root)) if str(folder).startswith(str(ws.root)) else str(folder)
    row = next((r for r in ws.load_tracker() if r.get("folder", "").replace("\\", "/") == rel.replace("\\", "/")
                or r["job_id"] == job.get("job_id")), {})
    notes_txt = (folder / "notes.md").read_text(encoding="utf-8") if (folder / "notes.md").exists() else ""
    vocab = _vocab(ws)
    bullets = master_bullets(master)
    master_text = str(master)
    comp, about, sal = _snapshot(job, row, notes_txt)
    title = job.get("title") or row.get("title", "")
    tailored = load_json(folder / "tailored.json", {}) or {}

    reqs = requirements(job, vocab)
    used, stories = set(), []
    for rq in reqs:
        idx = best_bullets(rq, bullets, vocab, used, k=2)
        if idx:
            used.add(idx[0])
        stories.append((rq, [bullets[i] for i in idx]))

    L = [f"# Interview prep - {comp} - {title}", "",
         "Every story below is quoted from master_resume.json. Numbers are exact: say them as written, never round up.", "",
         "## Snapshot", "",
         f"- **Company:** {comp}", f"- **Role:** {title}", f"- **Location:** {job.get('location') or row.get('location', '')}",
         f"- **Pay listed:** {sal or 'not listed'} (her floor: $140,000)",
         f"- **Applied:** {row.get('date_applied') or '?'} via {job.get('ats') or row.get('ats', '?')} | status {row.get('status', '?')}",
         f"- **Posting:** {job.get('url') or row.get('url', '')}",
         f"- **Resume headline sent:** {tailored.get('headline', '-')}"]
    if about:
        L += ["", "About them (from the posting):"] + [f"> {s}" for s in about]
    L += ["", "## Likely questions", ""]
    qs = [("Opener", f"Walk us through your background, and why {comp} and this {title} role?")]
    asked = set()
    qs += [question_for(rq, asked) for rq in reqs]
    for i, (kind, q) in enumerate(qs, 1):
        L.append(f"{i}. **{kind}:** {q}")
    L += ["", "## Requirement -> your best story (STAR, quoted from the master resume)", ""]
    for n, (rq, bs) in enumerate(stories, 1):
        L.append(f"### {n}. {_short(rq['text'], 140)}")
        if rq["skills"]:
            L.append(f"JD skills: {', '.join(rq['skills'])}")
        L.append("")
        if re.search(r"certif", rq["text"], re.I) and master.get("certifications"):
            L += ["Answer with the certifications in master_resume.json (say 'in progress' exactly as written):", ""]
            L += [f"- {c}" for c in master["certifications"]] + [""]
            continue
        if not bs:
            L += ["No matching bullet in the master resume. Treat this as a gap (see below); do not improvise a story.", ""]
            continue
        s = star(bs[0])
        L += [f"- **Situation:** {s['situation']}",
              f"- **Task:** {s['task']}",
              f"- **Action:** {_bold_metrics(s['action'], s['metrics'])}",
              "- **Result:** " + ("; ".join(f"**{m}**" for m in s["metrics"]) if s["metrics"]
                                  else "no number in the master resume for this one: describe the outcome in words, do not add numbers"),
              f"- **Resume line:** {s['bullet']}"]
        if len(bs) > 1:
            L.append(f"- **Back-up example:** {bs[1]['bullet']} ({bs[1]['company']})")
        L.append("")
    rw, src = reworded_lines(folder, ws)
    L += ["## Reworded resume lines to defend", "",
          f"These lines on the resume she sent differ from the master wording (source: {src}). For each, know the "
          f"original fact underneath it.", ""]
    if rw:
        for r in sorted(rw, key=lambda x: x["similar"]):
            L += [f"- **Sent:** {r['now']}", f"  - **Master ({r['similar']}% similar):** {r['from']}"]
    else:
        L.append("- none: every bullet she sent matches the master wording" if src != "none"
                 else "- no tailored.json or changes.md in this folder")
    L += ["", "## Honest gaps", ""]
    gp = gaps(folder)
    if not gp:
        L.append("- none recorded in match_report.md / notes.md")
    for g in gp:
        line, b = gap_framing(g, master_text, bullets, vocab)
        L.append(f"- **{g}**: {line}")
        if b:
            L.append(f"  - Related real work: {b['bullet']} ({b['company']})")
    L += ["", "## Questions to ask them", ""]
    L += [f"{i}. {q}" for i, q in enumerate(questions_to_ask(f"{title}\n{job.get('description', '')}"), 1)]
    L += ["", "## Facts to have ready", ""]
    facts = (master.get("facts") or {}).get("metrics") or []
    L += [f"- {f}" for f in facts[:14]]
    L.append("")
    md = "\n".join(L)
    out = folder / "interview_prep.md"
    out.write_text(md, encoding="utf-8")
    return {"file": str(out), "requirements": len(reqs), "questions": len(qs),
            "stories": sum(1 for _, b in stories if b), "reworded_lines": len(rw), "gaps": len(gp),
            "questions_to_ask": 5}
