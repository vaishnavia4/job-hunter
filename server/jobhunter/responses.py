"""Read employer replies from Gmail and close the loop in the tracker.

  python -m jobhunter --workspace WS responses [--days 14] [--dry-run]

How it works (no AI tokens, plain IMAP + regex rules):
  1. Headers only (From / Subject / Date / Message-ID) for every message in "All Mail" since N days ago.
  2. A message is a candidate when it comes from an applicant-tracking system (myworkday.com, greenhouse,
     ashbyhq.com, lever.co, ...) or when a tracked company's name appears in the sender or subject.
     Recruiter spam, newsletters and job alerts never get their body downloaded.
  3. Body downloaded only for candidates, then:
       match    -> which tracker row (Workday tenant, requisition id, company name, title words)
       classify -> application_received | rejected | interview | offer | action_needed | other
  4. Tracker: status moves to rejected / interview / offer only for a confident class on an unambiguous
     match, and only forward (never applied -> new, never away from offer/rejected). Every matched email
     adds a dated note "email YYYY-MM-DD: <class> - <subject>". Everything is recorded in
     data/responses.json keyed by Message-ID so a message is never processed twice.

Uses the same Google App Password as the job-alert reader (profile/credentials.json "gmail").
Email bodies are only kept in memory for classification: nothing from a body is printed or stored.
"""
import email
import html
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from .workspace import Workspace, load_json, save_json

CLASSES = ["application_received", "rejected", "interview", "offer", "action_needed", "other"]

# Sender domains of applicant-tracking systems (and their mailers).
ATS_DOMAINS = {
    "myworkday.com": "workday", "workday.com": "workday", "greenhouse.io": "greenhouse",
    "greenhouse-mail.io": "greenhouse", "ashbyhq.com": "ashby", "lever.co": "lever",
    "smartrecruiters.com": "smartrecruiters", "icims.com": "icims", "successfactors.com": "successfactors",
    "successfactors.eu": "successfactors", "jobs2web.com": "successfactors", "ultipro.com": "ukg",
    "ukg.com": "ukg", "taleo.net": "taleo", "oraclecloud.com": "oracle", "amazon.jobs": "amazon",
    "jobvite.com": "jobvite", "workable.com": "workable", "workablemail.com": "workable",
    "bamboohr.com": "bamboohr", "paylocity.com": "paylocity", "paycomonline.com": "paycom",
    "dayforcehcm.com": "dayforce", "eightfold.ai": "eightfold", "phenompeople.com": "phenom",
    "hackajob.com": "hackajob", "hackajob.co": "hackajob", "rippling.com": "rippling",
    "applytojob.com": "jazzhr", "recruitee.com": "recruitee", "teamtailor.com": "teamtailor",
    "breezy.hr": "breezy", "hire.lever.co": "lever", "neogov.com": "neogov", "governmentjobs.com": "neogov",
}
ATS_VENDOR_NAMES = {"workday", "myworkday", "greenhouse", "ashby", "lever", "smartrecruiters", "icims",
                    "no-reply", "noreply", "no reply", "do not reply", "hiring team", "talent acquisition"}
# Alert / marketing senders that are never replies about an application.
IGNORE_SENDERS = re.compile(r"jobalerts-noreply@linkedin\.com|jobs-listings@linkedin\.com|jobright\.ai|"
                            r"-jobnotification@|@jobalerts\.|builtin\.com|seek\.com|medium\.com|substack\.com", re.I)
IGNORE_SUBJECTS = re.compile(r"new jobs? (posted|alerts?)|job alert|latest job opportunities|new job opportunities|"
                             r"job matches|talent community|your latest .* jobs|jobs? you may (like|be interested)", re.I)
# Name words too generic to identify a company on their own.
STOPWORDS = {"the", "inc", "llc", "corp", "corporation", "co", "company", "usa", "us", "ltd", "limited", "group",
             "enterprise", "enterprises", "holdings", "technologies", "technology", "services", "solutions",
             "systems", "global", "international", "america", "american", "united", "national", "health", "web",
             "cloud", "data", "software", "labs", "of", "and", "ma", "na"}

# ---------------------------------------------------------------- classification rules
# Each rule: (class, weight, regex). Weight 2 = decisive on its own, 1 = supporting evidence.
RULES = [
    ("offer", 2, r"pleased to (extend|offer) you|(extend|extending) (you )?an? (formal |verbal )?offer|offer letter|"
                 r"your offer (of employment|package)|congratulations[^.]{0,60}\boffer\b"),
    ("rejected", 2, r"not (to )?(be )?moving forward|will not be moving forward|decided (not )?to (move|proceed|pursue)[^.]{0,40}"
                    r"(other|another|different) (candidate|applicant|direction)|pursue other candidates|"
                    r"(other|another) candidates? (whose|who)|regret to inform|no longer (under consideration|being considered)|"
                    r"(were|was|have|has) not been selected|(were|was) not selected|position has (been|now been) filled|we (have|'ve) filled|"
                    r"decided to move forward with (other|another)|unable to (move forward|offer you)|"
                    r"not (be )?progressing|will not be progressing|closed (your|the) application|not a match at this time"),
    ("rejected", 1, r"\bunfortunately\b|after careful (consideration|review)|keep your (resume|information) on file|"
                    r"encourage you to (apply|keep an eye)|wish you (the best|luck|success) in your (job )?search"),
    ("interview", 2, r"(like|love|want) to (schedule|set up|arrange|invite you)|invite you (to|for) an? [^.]{0,30}"
                     r"(interview|conversation|call|chat|screen)|schedule (an?|your) [^.]{0,25}(interview|call|chat|screen)|"
                     r"(phone|video|technical|recruiter|initial) (screen|interview)\b[^.]{0,40}(schedule|availability|invite)|"
                     r"(share|send|provide|select|pick|book) (your|a) (availability|time)|calendly\.com|goodtime\.io|"
                     r"(move|moving) (you )?forward (to|with) (the )?(next|an?) (round|step|stage|interview)|"
                     r"next (round|stage) of (the|our) (interview|hiring|process)|interview (invitation|request)|"
                     r"(complete|take) (the|an|our|a) [^.]{0,30}(assessment|coding (challenge|exercise)|take-home)|"
                     r"hackerrank|codility|codesignal|karat\.(io|com)"),
    ("interview", 1, r"\binterview\b|next steps?\b|\bschedul|\bassessment\b"),
    ("action_needed", 2, r"task awaits|action (required|requested|needed)|please complete|complete (the|your|this) "
                         r"[^.]{0,40}(form|survey|questionnaire|profile|application)|verify your|verification code|"
                         r"activate your|confirm your (email|account)|reset your password|password setup|"
                         r"finish (your|the) application|incomplete application"),
    ("application_received", 2, r"(thank(s| you)|thanks) (you )?for (applying|your application|submitting|your interest)|"
                                r"application (has been |was )?(received|submitted)|received your (job )?application|"
                                r"we('ve| have) (received|got) your|got it!? application received|application confirmation|"
                                r"keep track of your application|successfully (applied|submitted)"),
]
RULES = [(c, w, re.compile(rx, re.I)) for c, w, rx in RULES]


def classify(subject, body=""):
    """-> (class, confident: bool, reasons: [str]). Rules first, conservative.

    Order of precedence: offer > rejected > interview > action_needed > application_received > other.
    Confirmation emails often say 'next steps: we will reach out to schedule an interview' - weak interview
    words never override a clear 'thank you for applying', so those stay application_received."""
    subj = subject or ""
    text = f"{subj}\n{body or ''}"
    strong = {c: False for c in CLASSES}
    weak = {c: 0 for c in CLASSES}
    in_subj = {c: False for c in CLASSES}
    reasons = {c: [] for c in CLASSES}
    for cls, w, rx in RULES:
        m = rx.search(text)
        if not m:
            continue
        reasons[cls].append(m.group(0)[:60])
        if w == 2:
            strong[cls] = True
        else:
            weak[cls] += 1
        if rx.search(subj):
            in_subj[cls] = True  # the subject is the sender's own summary of the email
    received = strong["application_received"]
    if strong["offer"]:
        return "offer", True, reasons["offer"]
    if strong["rejected"] or (weak["rejected"] >= 2 and not received) or (weak["rejected"] and in_subj["rejected"]):
        return "rejected", True, reasons["rejected"]
    if strong["interview"] and not weak["rejected"]:
        # "Thank you for applying ... we'd like to schedule a call" is an invite only when the subject says so too;
        # otherwise it is the usual confirmation boilerplate about what happens next.
        if not received or in_subj["interview"]:
            return "interview", True, reasons["interview"]
    if strong["action_needed"]:
        return "action_needed", True, reasons["action_needed"]
    if received:
        unsure = weak["rejected"] > 0
        return "application_received", not unsure, reasons["application_received"]
    if weak["rejected"] or weak["interview"] or strong["interview"]:
        cls = "rejected" if weak["rejected"] >= (weak["interview"] + strong["interview"]) else "interview"
        return "other", False, [f"weak {cls} signal: " + ", ".join(reasons[cls])]
    return "other", False, []


# ---------------------------------------------------------------- matching emails to tracker rows
def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()


def company_keys(company):
    """'Target Enterprise Inc' -> full 'target', squashed 'target', short 'target'.
    'Freddiemac' and 'Freddie Mac' both squash to 'freddiemac'."""
    words = [w for w in _norm(company).split() if w not in STOPWORDS] or _norm(company).split()
    full = " ".join(words)
    short = words[0] if words and len(words[0]) >= 4 else ""
    return {"full": full, "squash": full.replace(" ", ""), "short": short}


def workday_tenant(row):
    m = re.match(r"wd:([^:]+):", row.get("job_id", ""))
    if m:
        return m.group(1).lower()
    for u in (row.get("url", ""), row.get("apply_url", "")):
        m = re.search(r"https?://([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com", u or "", re.I)
        if m:
            return m.group(1).lower()
    return ""


def req_ids(row):
    """Requisition-like ids from the job_id tail: 'wd:tmobile:REQ367236-1' -> {'REQ367236-1', 'REQ367236'}."""
    tail = row.get("job_id", "").split(":")[-1]
    out = set()
    if re.search(r"\d{4,}", tail) and len(tail) <= 20:
        out.add(tail)
        out.add(re.sub(r"-\d{1,2}$", "", tail))
    return {x for x in out if len(x) >= 5}


def _title_words(t):
    return {w for w in _norm(t).split() if len(w) >= 3 and w not in {"senior", "the", "and", "for", "engineer"}}


def sender_info(from_header):
    name, addr = parseaddr(from_header or "")
    addr = addr.lower()
    local, _, domain = addr.partition("@")
    ats = ""
    for d, vendor in ATS_DOMAINS.items():
        if domain == d or domain.endswith("." + d):
            ats = vendor
            break
    return {"name": name, "addr": addr, "local": local, "domain": domain, "ats": ats}


def _has(hay, needle):
    return bool(needle) and re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", hay) is not None


def match_rows(rows, sender, subject, body=""):
    """Score every tracker row against one email. -> (best_rows, score, how).
    best_rows has >1 entry when the email cannot be pinned to one job (e.g. two Freddie Mac applications)."""
    subj_n = _norm(subject)
    name_n = _norm(sender["name"])
    if sender["ats"]:  # "MyWorkday T-Mobile" -> "t mobile"; a bare "Workday" display name says nothing
        name_n = " ".join(w for w in name_n.split() if w not in {"myworkday", "workday", "greenhouse", "ashby", "lever"})
    vendor_display = not name_n or name_n in ATS_VENDOR_NAMES
    body_n = _norm(body)[:20000]
    raw = f"{subject}\n{body}"
    scored = []
    for r in rows:
        k = company_keys(r.get("company", ""))
        s, how = 0, []
        ten = workday_tenant(r)
        if ten and sender["domain"] == "myworkday.com" and sender["local"].replace("-", "") == ten.replace("-", ""):
            s += 5; how.append(f"workday tenant {ten}")
        rid = [x for x in req_ids(r) if re.search(r"(?<![A-Za-z0-9])" + re.escape(x) + r"(?![A-Za-z0-9])", raw, re.I)]
        if rid:
            s += 6; how.append(f"req id {rid[0]}")
        if _has(subj_n, k["full"]) or (k["squash"] and k["squash"] in subj_n.replace(" ", "")):
            s += 4; how.append("company in subject")
        elif _has(subj_n, k["short"]):
            s += 3; how.append("company word in subject")
        if not vendor_display and (_has(name_n, k["full"]) or _has(name_n, k["short"])
                                   or (k["squash"] and k["squash"] in name_n.replace(" ", ""))):
            s += 3; how.append("company in sender name")
        elif k["squash"] and len(k["squash"]) >= 5 and k["squash"] in sender["domain"].replace("-", "") \
                and not sender["ats"]:
            s += 3; how.append("company in sender domain")
        if s == 0 and _has(body_n, k["full"]) and len(k["full"]) >= 5:
            s += 1; how.append("company in body")
        if s:
            tw = _title_words(r.get("title", ""))
            if tw:
                hit = len(tw & set((subj_n + " " + body_n[:6000]).split())) / len(tw)
                if hit >= 0.5:
                    s += 2; how.append(f"title words {hit:.0%}")
        if s:
            scored.append((s, r, how))
    if not scored:
        return [], 0, []
    scored.sort(key=lambda x: -x[0])
    top = scored[0][0]
    best = [x for x in scored if x[0] == top]
    # Prefer rows that were actually applied to over skipped/new ones with the same score.
    live = [x for x in best if x[1].get("status") not in ("new", "skipped")]
    best = live or best
    return [x[1] for x in best], top, best[0][2]


# ---------------------------------------------------------------- tracker updates
STATUS_FROM = {  # target status -> statuses it may replace (forward moves only)
    "interview": {"tailored", "ready", "blocked", "applied"},
    "rejected": {"tailored", "ready", "blocked", "applied", "interview"},
    "offer": {"tailored", "ready", "blocked", "applied", "interview"},
}
CLASS_STATUS = {"interview": "interview", "rejected": "rejected", "offer": "offer"}
MIN_MATCH = 4  # match score needed before touching a tracker row


def next_status(current, cls, confident):
    """Status a row should move to, or None. Monotonic: never backwards, offer/rejected are final."""
    target = CLASS_STATUS.get(cls)
    if not target or not confident:
        return None
    if current in STATUS_FROM[target]:
        return target
    return None


def safe_subject(s):
    """Subjects only, one line, one-time codes masked."""
    s = re.sub(r"\s+", " ", s or "").strip()
    if re.search(r"code|passcode|verif|otp|pin\b", s, re.I):
        s = re.sub(r"\b\d{4,8}\b", "######", s)
    return s[:140]


def _append_note(notes, line):
    notes = (notes or "").strip()
    if line in notes:
        return notes
    return f"{notes} | {line}" if notes else line


def apply_results(ws: Workspace, results, dry_run=False):
    """results: classified + matched emails (oldest first). Returns the tracker changes made (or planned)."""
    rows = ws.load_tracker()
    by_id = {r["job_id"]: r for r in rows}
    changes = []
    for res in results:
        if len(res["job_ids"]) != 1 or res["match_score"] < MIN_MATCH:
            continue
        r = by_id.get(res["job_ids"][0])
        if not r:
            continue
        line = f"email {res['date']}: {res['class']} - {res['subject']}"
        ch = {"job_id": r["job_id"], "company": r["company"], "class": res["class"], "note": line,
              "status_from": r["status"], "status_to": None}
        new = next_status(r["status"], res["class"], res["confident"])
        if new:
            ch["status_to"] = new
            r["status"] = new
        r["notes"] = _append_note(r.get("notes"), line)
        res["status_change"] = f"{ch['status_from']} -> {new}" if new else ""
        changes.append(ch)
        if not dry_run and r.get("folder"):
            nf = ws.root / r["folder"].replace("\\", "/") / "notes.md"
            if nf.exists():
                txt = nf.read_text(encoding="utf-8")
                entry = f"- {res['date']}: email {res['class']}" + (f" (status -> {new})" if new else "") + f" - {res['subject']}"
                if entry not in txt:
                    nf.write_text(txt.rstrip("\n") + "\n" + entry + "\n", encoding="utf-8")
    if not dry_run and changes:
        ws.save_tracker(rows)
        ws.export_xlsx()
    return changes


# ---------------------------------------------------------------- Gmail
def _hdr(v):
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def _text(msg):
    parts = []
    for p in msg.walk() if msg.is_multipart() else [msg]:
        if p.get_content_type() in ("text/plain", "text/html"):
            try:
                parts.append(p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "ignore"))
            except Exception:
                pass
    t = "\n".join(parts)
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t))).replace("​", "").strip()


def is_candidate(sender, subject, rows_keys, own_addr=""):
    if own_addr and sender["addr"] == own_addr.lower():
        return False
    if IGNORE_SENDERS.search(sender["addr"]) or IGNORE_SUBJECTS.search(subject or ""):
        return False
    if sender["ats"]:
        return True
    hay = _norm(f"{sender['name']} {subject}")
    dom = sender["domain"].replace("-", "")
    for k in rows_keys:
        if _has(hay, k["full"]) or _has(hay, k["short"]) or (len(k["squash"]) >= 5 and k["squash"] in dom):
            return True
    return False


def fetch_candidates(creds, since, rows, log=print):
    """Header-first scan of All Mail; bodies only for candidates. -> list of raw dicts (bodies in memory only)."""
    keys = [company_keys(r.get("company", "")) for r in rows if r.get("status") not in ("new", "skipped")]
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(creds["email"], creds["app_password"].replace(" ", ""))
    out = []
    try:
        M.select('"[Gmail]/All Mail"', readonly=True)
        typ, d = M.search(None, f'(SINCE {since.strftime("%d-%b-%Y")})')
        nums = d[0].split() if d and d[0] else []
        headers = []
        for i in range(0, len(nums), 200):
            chunk = nums[i:i + 200]
            typ, data = M.fetch(b",".join(chunk), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
            for part in data:
                if isinstance(part, tuple):
                    num = re.match(rb"(\d+)", part[0]).group(1)
                    headers.append((num, email.message_from_bytes(part[1])))
        log(f"{len(headers)} emails since {since:%Y-%m-%d} (headers only)")
        for num, h in headers:
            sender = sender_info(_hdr(h.get("From")))
            subject = _hdr(h.get("Subject"))
            if not is_candidate(sender, subject, keys, creds.get("email", "")):
                continue
            try:
                when = parsedate_to_datetime(h.get("Date"))
            except Exception:
                when = datetime.now(timezone.utc)
            mid = (h.get("Message-ID") or "").strip() or f"num:{num.decode()}:{subject}"
            out.append({"num": num, "message_id": mid, "sender": sender, "subject": subject, "when": when})
        log(f"{len(out)} candidate emails (ATS sender or tracked company); downloading their bodies")
        for c in out:
            typ, data = M.fetch(c.pop("num"), "(BODY.PEEK[])")
            raw = next((p[1] for p in data if isinstance(p, tuple)), b"")
            c["body"] = _text(email.message_from_bytes(raw))[:30000]
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return out


def process(ws: Workspace, mails, dry_run=False, state=None):
    """Classify + match already-fetched mails (offline-testable). Returns the summary dict."""
    state = state if state is not None else (load_json(ws.data / "responses.json", {}) or {})
    seen = state.setdefault("messages", {})
    rows = ws.load_tracker()
    results, skipped_dupes = [], 0
    for m in sorted(mails, key=lambda x: x["when"]):
        if m["message_id"] in seen:
            skipped_dupes += 1
            continue
        cls, confident, why = classify(m["subject"], m.get("body", ""))
        best, score, how = match_rows(rows, m["sender"], m["subject"], m.get("body", ""))
        if not best and not m["sender"]["ats"]:
            continue  # company word in the subject but no tracker row: not about our applications
        when = m["when"]
        results.append({
            "message_id": m["message_id"], "date": when.strftime("%Y-%m-%d"), "from": m["sender"]["addr"],
            "ats": m["sender"]["ats"], "subject": safe_subject(m["subject"]), "class": cls, "confident": confident,
            "why": why[:3], "job_ids": [r["job_id"] for r in best], "companies": sorted({r["company"] for r in best}),
            "match_score": score, "match": how, "status_change": "",
        })
    changes = apply_results(ws, results, dry_run=dry_run)
    if not dry_run:
        for res in results:
            seen[res["message_id"]] = {k: v for k, v in res.items() if k != "message_id"}
        state["last_run"] = datetime.now().isoformat(timespec="seconds")
        save_json(ws.data / "responses.json", state)
    counts = {c: sum(1 for r in results if r["class"] == c) for c in CLASSES}
    return {"dry_run": dry_run, "processed": len(results), "already_seen": skipped_dupes, "by_class": counts,
            "status_changes": [c for c in changes if c["status_to"]], "notes_added": len(changes),
            "results": results, "digest": digest(results, changes, rows)}


def digest(results, changes, rows):
    lines = []
    counts = {c: sum(1 for r in results if r["class"] == c) for c in CLASSES}
    lines.append("Replies: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v) if results else "No new replies.")
    sc = [c for c in changes if c["status_to"]]
    for c in sc:
        lines.append(f"  STATUS {c['company']}: {c['status_from']} -> {c['status_to']}")
    order = ["offer", "interview", "action_needed", "rejected", "application_received", "other"]
    for cls in order:
        items = [r for r in results if r["class"] == cls]
        if not items:
            continue
        lines.append(f"{cls} ({len(items)}):")
        for r in items:
            who = ", ".join(r["companies"]) or f"(no tracker match, {r['ats'] or r['from'].split('@')[-1]})"
            amb = " [ambiguous: " + str(len(r["job_ids"])) + " jobs]" if len(r["job_ids"]) > 1 else ""
            if r["job_ids"] and r["match_score"] < MIN_MATCH:
                amb += " [weak match, tracker not touched]"
            lines.append(f"  {r['date']} {who}{amb}: {r['subject']}")
    by_id = {r["job_id"]: r for r in rows}
    hint = [r for r in results if r["class"] == "application_received" and len(r["job_ids"]) == 1
            and by_id.get(r["job_ids"][0], {}).get("status") in ("ready", "blocked", "tailored")]
    for r in hint:
        lines.append(f"  NOTE: confirmation received for {r['companies'][0]} but tracker says "
                     f"{by_id[r['job_ids'][0]]['status']} - mark it applied if she submitted it.")
    return "\n".join(lines)


def read_responses(ws: Workspace, days=14, dry_run=False, log=print):
    creds = (load_json(ws.profile_dir / "credentials.json", {}) or {}).get("gmail") or {}
    if not creds.get("email") or not creds.get("app_password"):
        raise ValueError("Add gmail.email + gmail.app_password (a Google App Password) to profile/credentials.json")
    since = datetime.now() - timedelta(days=days or 14)
    mails = fetch_candidates(creds, since, ws.load_tracker(), log=log)
    return process(ws, mails, dry_run=dry_run)
