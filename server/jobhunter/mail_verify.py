"""Find account-verification emails (activation links or one-time codes) in Gmail over IMAP.

Used by the apply flow when an applicant account has just been created (Workday sends
"Verify your candidate account" from workday@otp.workday.com with a /<site>/activate/<token> link that
expires after 24 hours; other systems send 6-digit codes). Needs gmail.email + gmail.app_password in
profile/credentials.json (the same Google App Password the job-alert reader uses).

  python -m jobhunter --workspace WS mail-verify --host statestreet.wd1 --minutes 15 --wait 180
"""
import email
import html
import imaplib
import re
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from .workspace import Workspace, load_json

SENDERS = ["otp.workday.com", "myworkday.com", "workday.com", "no-reply", "noreply", "donotreply", "do-not-reply"]
SUBJECT_RX = re.compile(r"verif|activat|confirm|one[- ]time|passcode|security code|sign[- ]in code|your code|reset|password|candidate account", re.I)
LINK_RX = re.compile(r"/activate/|/passwordreset|reset|verif|confirm|activat|validate", re.I)
CODE_RX = re.compile(r"(?:code|passcode|one[- ]time|verification)[^0-9]{0,60}\b(\d{4,8})\b|\b(\d{6})\b[^.]{0,40}(?:is your|code)", re.I)


def _creds(ws):
    c = (load_json(ws.profile_dir / "credentials.json", {}) or {}).get("gmail") or {}
    if not c.get("email") or not c.get("app_password"):
        raise RuntimeError("Add gmail.email + gmail.app_password (Google App Password) to profile/credentials.json")
    return c


def _text(msg):
    parts = []
    for p in msg.walk() if msg.is_multipart() else [msg]:
        if p.get_content_type() in ("text/html", "text/plain"):
            try:
                parts.append(p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "ignore"))
            except Exception:
                pass
    return html.unescape("\n".join(parts))


def parse(subject, body, host_hint=""):
    """-> {"type": "link"|"code", "value": ...} or None. Prefers links on the expected host."""
    links = [u.rstrip(").,") for u in dict.fromkeys(re.findall(r'https?://[^\s"<>\']+', body))]
    links = [u for u in links if LINK_RX.search(u) and not re.search(r"\.(png|jpe?g|gif)$|unsubscribe|privacy", u, re.I)]
    if host_hint:
        own = [u for u in links if host_hint.lower() in u.lower()]
        links = own or links
    if links:
        act = [u for u in links if "/activate/" in u]
        return {"type": "link", "value": (act or links)[0]}
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
    m = CODE_RX.search(plain)
    if m:
        return {"type": "code", "value": m.group(1) or m.group(2)}
    return None


def find_verification(ws: Workspace, since_minutes=15, host_hint="", sender_hint="", wait_seconds=0, poll=10, log=print):
    """Newest verification email received in the last `since_minutes` (polls up to `wait_seconds`).
    host_hint: e.g. 'statestreet.wd1' to pick that tenant's activation link. Searches Inbox, All Mail and Spam."""
    c = _creds(ws)
    after = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    deadline = time.time() + max(0, wait_seconds)
    senders = [sender_hint] if sender_hint else list(SENDERS)
    # tenants with their own mail domain (KLA: KLAHR@workday.kla.com, 2026-11-09) match none of the generic senders
    if host_hint and not sender_hint:
        senders.append(host_hint.lower().split(".")[0])
    while True:
        M = imaplib.IMAP4_SSL("imap.gmail.com")
        M.login(c["email"], c["app_password"].replace(" ", ""))
        best = None
        try:
            for box in ('"[Gmail]/All Mail"', '"[Gmail]/Spam"'):
                if M.select(box, readonly=True)[0] != "OK":
                    continue
                seen = set()
                for s in senders:
                    # IMAP SINCE is a DATE in the server's zone (Gmail: Pacific) - search a day earlier and let the
                    # exact `when < after` check below filter; a UTC date missed mail sent 16:00-24:00 Pacific (F5/KLA 2026-11-09)
                    typ, d = M.search(None, f'(SINCE {(after - timedelta(days=1)).strftime("%d-%b-%Y")} FROM "{s}")')
                    for n in (d[0].split() if d and d[0] else []):
                        if n in seen:
                            continue
                        seen.add(n)
                        # headers first (cheap); full body only for recent verification-like mail
                        hdr = email.message_from_bytes(M.fetch(n, "(BODY.PEEK[HEADER.FIELDS (DATE SUBJECT FROM)])")[1][0][1])
                        try:
                            when = parsedate_to_datetime(hdr.get("Date"))
                        except Exception:
                            continue
                        if when.tzinfo is None:
                            when = when.replace(tzinfo=timezone.utc)
                        subj = str(make_header(decode_header(hdr.get("Subject", ""))))
                        if when < after or not SUBJECT_RX.search(subj):
                            continue
                        msg = email.message_from_bytes(M.fetch(n, "(BODY.PEEK[])")[1][0][1])
                        body = _text(msg)
                        # with a host hint, only that tenant's mail counts (sender "<tenant>@otp.workday.com" or a link
                        # on its host) - never another company's activation link received a few minutes earlier
                        if host_hint:
                            tenant = host_hint.lower().split(".")[0]
                            if tenant not in (msg.get("From", "") + " " + " ".join(re.findall(r"https?://[^\s\"<>']+", body))).lower():
                                continue
                        hit = parse(subj, body, host_hint)
                        if hit and (best is None or when > best["received"]):
                            best = {**hit, "subject": subj, "from": msg.get("From", ""), "received": when, "folder": box.strip('"')}
        finally:
            M.logout()
        if best or time.time() >= deadline:
            if best:
                best["received"] = best["received"].isoformat()
            return best
        log(f"no verification email yet; checking again in {poll}s")
        time.sleep(poll)


def find_greenhouse_code(ws: Workspace, company="", since_minutes=10, wait_seconds=120, poll=8):
    """Newest Greenhouse "Security code for your application to <Company>" email -> the code string, or None.
    The code is returned to the caller only (never logged or printed)."""
    c = _creds(ws)
    after = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    deadline = time.time() + max(0, wait_seconds)
    key = re.sub(r"[^a-z0-9]+", " ", (company or "").lower()).split()[:1]
    while True:
        M = imaplib.IMAP4_SSL("imap.gmail.com")
        M.login(c["email"], c["app_password"].replace(" ", ""))
        best = None
        try:
            for box in ('"[Gmail]/All Mail"', '"[Gmail]/Spam"'):
                if M.select(box, readonly=True)[0] != "OK":
                    continue
                typ, d = M.search(None, f'(SINCE {(after - timedelta(days=1)).strftime("%d-%b-%Y")} FROM "greenhouse" SUBJECT "Security code")')
                for n in (d[0].split() if d and d[0] else [])[-8:]:
                    msg = email.message_from_bytes(M.fetch(n, "(BODY.PEEK[])")[1][0][1])
                    try:
                        when = parsedate_to_datetime(msg.get("Date"))
                    except Exception:
                        continue
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    subj = str(make_header(decode_header(msg.get("Subject", ""))))
                    if when < after or (key and key[0] not in subj.lower()):
                        continue
                    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _text(msg)))
                    m = re.search(r"security code field[^:]{0,60}:\s*([A-Za-z0-9]{6,12})\b", plain, re.I)
                    if m and (best is None or when > best[0]):
                        best = (when, m.group(1))
        finally:
            M.logout()
        if best or time.time() >= deadline:
            return best[1] if best else None
        time.sleep(poll)


def recent_confirmation(ws: Workspace, company, minutes=240):
    """Number of application-confirmation emails for this company arrived in the last `minutes`
    ("Thank you for applying to X" and similar). Used right before Submit so a form the user has just
    submitted by hand is never sent a second time."""
    key = re.sub("[^a-z0-9]+", " ", (company or "").lower()).split()[:1]
    if not key:
        return 0
    c = _creds(ws)
    after = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    hits = 0
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(c["email"], c["app_password"].replace(" ", ""))
    try:
        if M.select('"[Gmail]/All Mail"', readonly=True)[0] != "OK":
            return 0
        typ, d = M.search(None, f'(SINCE {(after - timedelta(days=1)).strftime("%d-%b-%Y")} SUBJECT "{key[0]}")')
        for n in (d[0].split() if d and d[0] else [])[-25:]:
            h = email.message_from_bytes(M.fetch(n, "(BODY.PEEK[HEADER.FIELDS (DATE SUBJECT)])")[1][0][1])
            subj = str(make_header(decode_header(h.get("Subject", "")))).lower()
            try:
                when = parsedate_to_datetime(h.get("Date"))
            except Exception:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when >= after and re.search("thank you for (applying|your application|your interest)|application (received|submitted)|we('ve| have) received your application", subj):
                hits += 1
    finally:
        M.logout()
    return hits
