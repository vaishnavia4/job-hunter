"""Read LinkedIn / Jobright job-alert emails from Gmail and add matching jobs to the tracker.

No AI tokens: plain IMAP + HTTP + the plugin's own scoring (config/search.json, profile.json).

Setup (once): create a Google App Password (Google Account > Security > 2-Step Verification > App passwords)
and put it in profile/credentials.json:
  "gmail": {"email": "you@gmail.com", "app_password": "abcd efgh ijkl mnop"}
Then turn on LinkedIn job alerts (and optionally Jobright alerts) for your target roles.
"""
import email
import html
import imaplib
import re
import time
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from urllib.parse import unquote

import requests

from .search import Scorer, hard_skip_reason, reject_key, strip_html
from .workspace import Workspace, load_json, save_json, today

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
SENDERS = ["jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com", "jobs-listings@linkedin.com",
           "linkedin.com", "jobright.ai"]
LI_ID = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d{6,})")
JR_ANCHOR = re.compile(r'<a\b[^>]*href="https://jobright\.ai/jobs/info/([A-Za-z0-9]+)[^"]*"[^>]*>(.*?)</a>', re.S | re.I)
JR_SUBJECT = re.compile(r"^(?P<company>.+?) just posted an? (?P<pct>\d+)% match (?P<title>.+?) role\b", re.S)
MAX_LINKEDIN_FETCH = 40


def _subject(msg):
    try:
        return str(make_header(decode_header(msg.get("Subject", ""))))
    except Exception:
        return msg.get("Subject", "")


def _body(msg):
    parts = []
    for p in msg.walk() if msg.is_multipart() else [msg]:
        if p.get_content_type() in ("text/html", "text/plain"):
            try:
                parts.append(p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "ignore"))
            except Exception:
                pass
    return "\n".join(parts)


def _clean(t):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)).replace("​", "")).strip()


def _toks(t):
    return set(re.findall(r"[a-z0-9#+]+", t.lower()))


def parse_jobright(subject, body):
    """Jobright email -> [{company, title, link, pct}], one entry per distinct job.

    A Jobright email holds several job cards, each with its own /jobs/info/<id> link, so grabbing the
    first link in the email reports the wrong job. Cards are matched by link id and their anchor text.
    'X just posted a N% match T role' emails: only the card whose title matches the subject (its main job).
    Digest emails: every card (company = start of the card text)."""
    subject = _clean(subject)
    cards = {}  # id -> best anchor text (longest, ignoring APPLY NOW / empty)
    for jid, inner in JR_ANCHOR.findall(body):
        t = _clean(inner)
        if t and not t.upper().startswith("APPLY NOW") and len(t) > len(cards.get(jid, "")):
            cards[jid] = t
    link = lambda jid: f"https://jobright.ai/jobs/info/{jid}"
    m = JR_SUBJECT.match(subject)
    if m:
        want = _toks(m["title"])
        best, score = None, 0.0
        for jid, t in cards.items():
            tk = _toks(t)
            sc = len(want & tk) / max(1, len(want | tk))
            if sc > score:
                best, score = jid, sc
        if best is None and cards:
            best = next(iter(cards))  # first job card as fallback
        if best is None:
            return []
        return [{"company": m["company"].strip(), "title": m["title"].strip(), "link": link(best), "pct": int(m["pct"])}]
    out = []
    for jid, t in cards.items():  # digest: "Company  Industry . Stage  91%  Title ..."
        parts = re.split(r"\s{2,}|\s[·•]\s", t)
        pct = re.search(r"(\d+)%", t)
        title = re.sub(r"^.*?\d+%\s*", "", t)[:100]
        out.append({"company": parts[0][:60], "title": title, "link": link(jid), "pct": int(pct[1]) if pct else None})
    return out


def fetch_emails(creds, since):
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(creds["email"], creds["app_password"].replace(" ", ""))
    M.select('"[Gmail]/All Mail"', readonly=True)
    out, seen = [], set()
    date = since.strftime("%d-%b-%Y")
    for s in SENDERS:
        typ, data = M.search(None, f'(SINCE {date} FROM "{s}")')
        for num in (data[0].split() if data and data[0] else []):
            if num in seen:
                continue
            seen.add(num)
            typ, msg_data = M.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            out.append({"from": msg.get("From", ""), "subject": _subject(msg), "body": _body(msg)})
    M.logout()
    return out


def linkedin_job(job_id):
    """Public guest job page (no login): title, company, location, description, external apply URL."""
    r = requests.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}", headers=UA, timeout=20)
    if r.status_code != 200:
        return None
    t = r.text

    def g(rx):
        m = re.search(rx, t, re.S)
        return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""

    apply_url = ""
    m = re.search(r'<code id="applyUrl"[^>]*><!--"(.*?)"--></code>', t, re.S)
    if m:
        u = html.unescape(m.group(1))
        q = re.search(r"[?&]url=([^&]+)", u)
        apply_url = unquote(q.group(1)) if q else u
    desc = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', t, re.S)
    return {
        "title": g(r'class="top-card-layout__title[^"]*"[^>]*>(.*?)</h2>'),
        "company": g(r'class="topcard__org-name-link[^"]*"[^>]*>(.*?)</a>') or g(r'class="topcard__flavor"[^>]*>(.*?)</span>'),
        "location": g(r'class="topcard__flavor topcard__flavor--bullet"[^>]*>(.*?)</span>'),
        "description": strip_html(desc.group(1) if desc else ""),
        "apply_url": apply_url,
    }


def read_alerts(ws: Workspace, days=None, dry_run=False, log=print):
    creds = (load_json(ws.profile_dir / "credentials.json", {}) or {}).get("gmail") or {}
    if not creds.get("email") or not creds.get("app_password"):
        raise ValueError("Add gmail.email + gmail.app_password (a Google App Password) to profile/credentials.json "
                         "- see the README section 'Email job alerts'.")
    state_path = ws.data / "email_alerts_state.json"
    state = load_json(state_path, {}) or {}
    since = (datetime.now() - timedelta(days=days)) if days else \
        datetime.fromisoformat(state.get("last_run", (datetime.now() - timedelta(days=3)).isoformat()))
    scorer = Scorer(ws)
    mails = fetch_emails(creds, since)
    li_ids, jr = [], []
    for m in mails:
        li_ids += LI_ID.findall(m["body"])
        if "jobright" in m["from"].lower():
            jr.append({"subject": _clean(m["subject"]), "jobs": parse_jobright(m["subject"], m["body"])})
    li_ids = list(dict.fromkeys(li_ids))
    tracker = ws.load_tracker()
    known = {r["job_id"] for r in tracker}
    known_keys = {(r["company"].lower(), r["title"].lower()) for r in tracker}
    log(f"{len(mails)} alert emails since {since:%Y-%m-%d} | {len(li_ids)} LinkedIn job links | {len(jr)} Jobright emails")

    added, rejected = [], {}
    for jid in li_ids[:MAX_LINKEDIN_FETCH]:
        key = f"li:{jid}"
        if key in known:
            continue
        j = linkedin_job(jid)
        time.sleep(1.5)
        if not j or not j["title"]:
            continue
        job = {"job_id": key, "ats": "linkedin-alert", "company": j["company"], "title": j["title"],
               "location": j["location"], "url": f"https://www.linkedin.com/jobs/view/{jid}",
               "apply_url": j["apply_url"] or f"https://www.linkedin.com/jobs/view/{jid}",
               "posted": None, "description": j["description"], "salary_text": ""}
        if (job["company"].lower(), job["title"].lower()) in known_keys:
            continue
        res, why = scorer.score(job)
        if not res:
            k = reject_key(why)
            rejected[k] = rejected.get(k, 0) + 1
            continue
        added.append(res)
    added.sort(key=lambda j: -j["score"])
    seen_jr, jr_leads = set(), []
    for e in jr:
        for j in e["jobs"]:
            jid = j["link"].rsplit("/", 1)[1]
            if jid in seen_jr:
                continue
            seen_jr.add(jid)
            hs = hard_skip_reason(scorer.hard_rules, j["title"], "")
            if hs:
                k = reject_key("hard_skip: " + hs)
                rejected[k] = rejected.get(k, 0) + 1
                continue
            jr_leads.append(j)
    summary = {"emails": len(mails), "linkedin_links": len(li_ids), "jobright_emails": len(jr), "filtered_out": rejected,
               "jobright_leads": jr_leads[-40:],
               "matches": [{"score": j["score"], "company": j["company"], "title": j["title"], "location": j["location"],
                            "apply": "company site" if "linkedin.com" not in j["apply_url"] else "LinkedIn page (find the company site)"}
                           for j in added]}
    if dry_run:
        return summary
    for j in added:
        save_json(ws.jd / (j["job_id"].replace(":", "_") + ".json"), j)
        tracker.append({"job_id": j["job_id"], "date_found": today(), "company": j["company"], "title": j["title"],
                        "location": j["location"], "ats": "linkedin-alert", "url": j["url"], "apply_url": j["apply_url"],
                        "score": j["score"], "keyword_coverage": j.get("keyword_coverage", ""), "salary": j.get("salary", ""),
                        "flags": (j.get("flags", "") + "; from LinkedIn alert").strip("; "), "status": "new"})
    ws.save_tracker(tracker)
    ws.export_xlsx()
    save_json(state_path, {"last_run": datetime.now().isoformat(timespec="seconds"), "jobright_leads": jr_leads[-60:]})
    summary["added"] = len(added)
    return summary
