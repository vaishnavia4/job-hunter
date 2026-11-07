"""Oracle Taleo Enterprise career sections (public job board, no login).

Token format:  "<host>/<section>[/<org>]"
  host     the Taleo host, e.g. "cu.taleo.net"
  section  the career section id from https://<host>/careersection/<section>/jobsearch.ftl
  org      optional: only this organization, given by its code (e.g. "C0001") or numeric id
Examples (University of Colorado):
  "cu.taleo.net/2"              every CU campus on the public section
  "cu.taleo.net/2/U0001"        CU Anschutz / CU Denver only
  "cu.taleo.net/2/C0001"        UCCS only
  "cu.taleo.net/2/S0001"        CU System Administration only
  "cu.taleo.net/2/B0001"        CU Boulder (most Boulder jobs are on jobs.colorado.edu instead)

How it works:
  1. GET  /careersection/<section>/jobsearch.ftl   -> portal number, section title, organization list
  2. POST /careersection/rest/jobboard/searchjobs?lang=en&portal=<portal>   (JSON, 25 jobs per page)
     once per organization so each job gets its campus/organization as `company`.
     The "tz" header is required (without it Taleo answers HTTP 500).
  3. taleo_detail(job): GET jobdetail.ftl?job=<job number>; the description is inside the hidden
     "initialHistory" field ("!|!"-separated, URL-encoded, rich-text values start with "!*!").

cfg keys: taleo_max_pages (default 30, per organization), taleo_delay (seconds between pages, default 1.0).
"""
import html
import json
import re
import threading
import time
import urllib.parse
from datetime import datetime, timezone

import requests

from .search import TIMEOUT, _annual, strip_html

BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
              "Accept-Language": "en-US,en;q=0.9"}
PAGE_SIZE = 25
_DETAIL_SLOTS = threading.Semaphore(3)  # search.py runs details 16-wide; keep Taleo to 3 at a time
_sleep = time.sleep  # replaced in tests


def _session():
    s = requests.Session()
    s.headers.update(BROWSER_UA)
    return s


def parse_token(token):
    parts = re.sub(r"^https?://", "", token.strip()).strip("/").split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(f"taleo token must be 'host/section[/org]', got {token!r}")
    return parts[0], parts[1], (parts[2] if len(parts) > 2 and parts[2] else None)


def parse_section_page(page):
    """jobsearch.ftl -> (portal, section title, [(org id, code, label)])."""
    portal = re.search(r"portal=(\d+)", page)
    title = re.search(r"<title>\s*([^<]*?)\s*</title>", page, re.I)
    orgs, seen = [], set()
    for oid, text in re.findall(r'<option value="(\d+)">([^<]*?\s--\s[^<]*)</option>', page):
        if oid in seen:
            continue
        seen.add(oid)
        code, label = (html.unescape(x).strip() for x in text.split("--", 1))
        orgs.append((oid, code, label))
    return (portal.group(1) if portal else None), (html.unescape(title.group(1)) if title else ""), orgs


def search_body(page_no, org_id=None):
    return {"multilineEnabled": False,
            "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
            "fieldData": {"fields": {"KEYWORD": "", "LOCATION": "", "ORGANIZATION": ""}, "valid": True},
            "filterSelectionParam": {"searchFilterSelections": []},
            "advancedSearchFiltersSelectionParam": {"searchFilterSelections": [
                {"id": "ORGANIZATION", "selectedValues": [org_id] if org_id else []}]},
            "pageNo": page_no}


def _posted_iso(text):
    m = re.search(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})", text or "")
    if not m:
        return None
    try:
        return datetime.strptime(" ".join(m.groups()), "%b %d %Y").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def _locations(cell):
    try:
        v = json.loads(cell)
        return [str(x) for x in v] if isinstance(v, list) else [str(v)]
    except (TypeError, ValueError):
        return [cell] if cell else []


def parse_requisition(r):
    """One row of searchjobs requisitionList -> (job number, title, location, posted ISO).
    Columns are configured per career section, so use linkedColumn / locationsColumns and look for a date."""
    cols = r.get("column") or []
    title = cols[r.get("linkedColumn", 0)] if cols else ""
    loc_idx = set(r.get("locationsColumns") or [])
    locs = [x for i in sorted(loc_idx) if i < len(cols) for x in _locations(cols[i])]
    posted = next((_posted_iso(c) for i, c in enumerate(cols) if i not in loc_idx and _posted_iso(c)), None)
    num = str(r.get("contestNo") or r.get("jobId") or "")
    return num, (title or "").strip(), " | ".join(dict.fromkeys(l.strip() for l in locs if l.strip())), posted


def _company(section_title, org_label):
    if not org_label:
        return section_title
    if not section_title or org_label.lower().startswith(section_title.lower()):
        return org_label
    return f"{section_title} - {org_label}"


def fetch_taleo(token, cfg):
    host, section, org_want = parse_token(token)
    base = f"https://{host}/careersection"
    short = host.split(".")[0]
    max_pages = int(cfg.get("taleo_max_pages", 30))
    delay = float(cfg.get("taleo_delay", 1.0))
    s = _session()
    r = s.get(f"{base}/{section}/jobsearch.ftl", params={"lang": "en"}, timeout=TIMEOUT)
    r.raise_for_status()
    portal, section_title, orgs = parse_section_page(r.text)
    if not portal:
        raise RuntimeError(f"taleo {token}: no portal id on jobsearch.ftl (section unavailable or login-only)")
    if org_want:
        orgs = [o for o in orgs if org_want in (o[0], o[1])]
        if not orgs:
            raise ValueError(f"taleo {token}: organization {org_want!r} not found on this section")
    queries = orgs or [(None, "", "")]
    api = f"{base}/rest/jobboard/searchjobs"
    seen = set()
    first = True
    for org_id, _code, label in queries:
        for page_no in range(1, max_pages + 1):
            if not first:
                _sleep(delay)
            first = False
            resp = s.post(api, params={"lang": "en", "portal": portal}, json=search_body(page_no, org_id),
                          headers={"tz": "GMT-07:00", "Accept": "application/json"}, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("requisitionList") or []
            for row in rows:
                num, title, loc, posted = parse_requisition(row)
                if not num or num in seen:
                    continue
                seen.add(num)
                url = f"{base}/{section}/jobdetail.ftl?job={num}&lang=en"
                yield {"job_id": f"tl:{short}:{num}", "ats": "taleo", "company": _company(section_title, label),
                       "title": title, "location": loc, "url": url, "apply_url": url, "posted": posted,
                       "description": "", "salary_text": "", "_detail": url}
            total = int((data.get("pagingData") or {}).get("totalCount") or 0)
            if not rows or page_no * PAGE_SIZE >= total:
                break


# ---------------------------------------------------------------- detail
_UNIT_WORDS = (("hour", "hour"), ("hr", "hour"), ("month", "month"), ("mo", "month"), ("week", "week"),
               ("annual", "year"), ("year", "year"), ("yr", "year"))
_RANGE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:-|–|to)\s*\$?\s?([\d,]+(?:\.\d+)?)(.{0,40})", re.I)


def salary_from_text(text):
    """First pay range in the description, annualized ('$79,000 - $97,000 per year').
    Lines mentioning salary/pay/compensation/hiring range (on the line or in the 3 lines above,
    e.g. a 'HIRING RANGE:' heading) win over other dollar ranges."""
    all_lines = [l for l in (text or "").splitlines() if l.strip()]
    kw = re.compile(r"salary|\bpay\b|compensation|hiring range|wage", re.I)
    cands = [(0 if any(kw.search(x) for x in all_lines[max(0, i - 3):i + 1]) else 1, i, l)
             for i, l in enumerate(all_lines) if "$" in l]
    for _rank, _i, line in sorted(cands):
        for m in _RANGE.finditer(line):
            lo, hi = (float(x.replace(",", "")) for x in m.group(1, 2))
            if not lo or hi < lo:
                continue
            tail = m.group(3).lower()
            unit = next((u for w, u in _UNIT_WORDS if re.search(rf"\b{w}\b", tail)), None)
            if unit is None:
                unit = "hour" if hi < 300 else "month" if hi < 20_000 else "year"
            return _annual(lo, hi, unit)
    return ""


def parse_detail(page):
    """jobdetail.ftl -> {'description', 'org'} from the hidden initialHistory field."""
    m = re.search(r'id="initialHistory"\s+value="([^"]*)"', page) or re.search(r'name="initialHistory"[^>]*value="([^"]*)"', page)
    if not m:
        return {}
    parts = [re.sub(r"\\(.)", lambda x: x.group(1), urllib.parse.unquote(p)) for p in html.unescape(m.group(1)).split("!|!")]
    rich = list(dict.fromkeys(p[3:] for p in parts if p.startswith("!*!") and p[3:].strip()))
    org = next((p.split("--", 1)[1].strip() for p in parts if re.fullmatch(r"[A-Z0-9]{2,10} -- .{2,120}", p.strip())), "")
    return {"description": "\n\n".join(strip_html(x) for x in rich).strip(), "org": org}


def taleo_detail(job):
    try:
        with _DETAIL_SLOTS:
            r = _session().get(job["_detail"], timeout=TIMEOUT)
            r.raise_for_status()
        d = parse_detail(r.text)
        if d.get("description"):
            job["description"] = d["description"]
            if not job.get("salary_text"):
                job["salary_text"] = salary_from_text(d["description"])
        if d.get("org") and d["org"].lower() not in (job.get("company") or "").lower():
            job["company"] = _company(job.get("company") or "", d["org"])
    except Exception:
        pass
