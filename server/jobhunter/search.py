"""Job search across public ATS job boards, scored by the workspace's config.

Sources: Greenhouse, Lever, Ashby, SmartRecruiters, Workday (public CXS API), Amazon (amazon.jobs), Remotive.
Nothing here is person-specific: titles, locations, skills, companies and blockers all come from the workspace.
"""
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from .workspace import Workspace, load_json, save_json, today

UA = {"User-Agent": "Mozilla/5.0 (job-hunter personal job search)"}
TIMEOUT = 25

# Blockers added automatically from profile.work_authorization
SPONSORSHIP_BLOCKERS = [
    r"(unable|not able|cannot|can ?not|will not|won'?t|do(es)? not|are not able to) (to )?(provide |offer |support )?(visa |immigration |employment )?sponsor",
    r"(no|without) (visa |h-?1b |employment |immigration )?sponsorship",
    r"sponsorship (is )?not (available|provided|offered)",
    r"not (eligible|available) for (visa )?sponsorship",
    r"authorized to work .{0,40}without .{0,20}sponsorship",
]
CITIZENSHIP_BLOCKERS = [
    r"u\.?s\.? citizens? only", r"must be a u\.?s\.? citizen", r"u\.?s\.? citizenship (is )?required",
    r"requires? u\.?s\.? citizenship", r"citizenship (is )?(required|mandatory)",
    r"green card holders? (only|or citizens)", r"\b(gc|usc) (holders? )?only", r"permanent residen(t|cy) (is )?required",
    r"\bitar\b", r"public trust", r"\bclearance\b", r"\bts/sci\b",
    r"suitability (determination|investigation|background)", r"moderate background investigation",
]


# ---------------------------------------------------------------- helpers
def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "\n- ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n\s*\n+", "\n\n", html.unescape(s)).strip()


def get(url, **kw):
    r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r.json()


def iso_age_days(ts):
    if not ts:
        return None
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def parse_salary(text):
    nums = []
    for m in re.finditer(r"\$\s?(\d{2,3})(?:,(\d{3})|(\.\d)?\s?[kK])", text or ""):
        n = int(m.group(1)) * 1000 if not m.group(2) else int(m.group(1) + m.group(2))
        if 30_000 <= n <= 900_000:
            nums.append(n)
    return (min(nums), max(nums)) if nums else (None, None)


# ---------------------------------------------------------------- fetchers
def fetch_greenhouse(token, cfg):
    try:
        name = get(f"https://boards-api.greenhouse.io/v1/boards/{token}").get("name") or token
    except Exception:
        name = token
    data = get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"})
    for j in data.get("jobs", []):
        yield {"job_id": f"gh:{token}:{j['id']}", "ats": "greenhouse", "company": name, "title": j.get("title", ""),
               "location": (j.get("location") or {}).get("name", ""), "url": j.get("absolute_url"),
               "apply_url": j.get("absolute_url"), "posted": j.get("first_published") or j.get("updated_at"),
               "description": strip_html(j.get("content", "")), "salary_text": ""}


def fetch_lever(token, cfg):
    for j in get(f"https://api.lever.co/v0/postings/{token}", params={"mode": "json"}):
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location", "")]
        desc = "\n".join([j.get("descriptionPlain", "")] +
                         [f"{l.get('text', '')}\n{strip_html(l.get('content', ''))}" for l in j.get("lists", [])] +
                         [j.get("additionalPlain", "")])
        sr = j.get("salaryRange") or {}
        yield {"job_id": f"lv:{token}:{j['id']}", "ats": "lever", "company": token.title(), "title": j.get("text", ""),
               "location": " | ".join(filter(None, locs)) + (" | Remote" if j.get("workplaceType") == "remote" else ""),
               "url": j.get("hostedUrl"), "apply_url": j.get("applyUrl"), "posted": j.get("createdAt"),
               "description": desc, "salary_text": f"${sr.get('min')} - ${sr.get('max')}" if sr.get("max") else ""}


def fetch_ashby(token, cfg):
    data = get(f"https://api.ashbyhq.com/posting-api/job-board/{token}", params={"includeCompensation": "true"})
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations", []) or []]
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        yield {"job_id": f"ab:{token}:{j['id']}", "ats": "ashby", "company": token.title(), "title": j.get("title", ""),
               "location": " | ".join(filter(None, locs)) + (" | Remote" if j.get("isRemote") else ""),
               "url": j.get("jobUrl"), "apply_url": j.get("applyUrl") or j.get("jobUrl"), "posted": j.get("publishedAt"),
               "description": j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", "")), "salary_text": comp}


def fetch_smartrecruiters(token, cfg):
    data = get(f"https://api.smartrecruiters.com/v1/companies/{token}/postings", params={"limit": 100})
    for j in data.get("content", []):
        loc = j.get("location") or {}
        yield {"job_id": f"sr:{token}:{j['id']}", "ats": "smartrecruiters",
               "company": (j.get("company") or {}).get("name", token), "title": j.get("name", ""),
               "location": ", ".join(filter(None, [loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()]))
                           + (" | Remote" if loc.get("remote") else ""),
               "url": f"https://jobs.smartrecruiters.com/{token}/{j['id']}",
               "apply_url": f"https://jobs.smartrecruiters.com/{token}/{j['id']}",
               "posted": j.get("releasedDate"), "description": "", "salary_text": "", "_detail": j.get("ref")}


def smartrecruiters_detail(job):
    try:
        d = get(job["_detail"])
        secs = (d.get("jobAd") or {}).get("sections") or {}
        job["description"] = "\n\n".join(strip_html((secs.get(k) or {}).get("text", "")) for k in
                                         ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))
    except Exception:
        pass


def _wd_age(posted_on):
    s = (posted_on or "").lower()
    if "today" in s:
        return 0
    if "yesterday" in s:
        return 1
    m = re.search(r"(\d+)\+? days", s)
    return int(m.group(1)) if m else None


def fetch_workday(spec, cfg):
    """spec = 'tenant.wdN/SiteName' (from https://tenant.wdN.myworkdayjobs.com/SiteName)."""
    host, site = spec.split("/")
    tenant = host.split(".")[0]
    base = f"https://{host}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}"
    seen = {}
    for q in cfg.get("workday_queries") or ["analyst"]:
        r = requests.post(f"{api}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": q},
                          headers={**UA, "Accept": "application/json"}, timeout=TIMEOUT)
        r.raise_for_status()
        for p in r.json().get("jobPostings", []):
            seen.setdefault(p["externalPath"], p)
    for path, p in seen.items():
        yield {"job_id": f"wd:{tenant}:{path.rsplit('_', 1)[-1]}", "ats": "workday", "company": tenant.title(),
               "title": p.get("title", ""), "location": p.get("locationsText", ""),
               "url": f"{base}/{site}{path}", "apply_url": f"{base}/{site}{path}/apply",
               "posted": None, "age_override": _wd_age(p.get("postedOn")), "description": "", "salary_text": "",
               "_detail": f"{api}{path}"}


def workday_detail(job):
    try:
        d = get(job["_detail"])
        info = d.get("jobPostingInfo") or {}
        job["description"] = strip_html(info.get("jobDescription", ""))
        org = (d.get("hiringOrganization") or {}).get("name")
        if org:
            job["company"] = re.sub(r"^\d+\s+", "", org).replace(" USA", "").replace(", Inc.", "").strip()
        if info.get("location"):
            extra = " | ".join(info.get("additionalLocations") or [])
            job["location"] = info["location"] + (f" | {extra}" if extra else "")
        if info.get("remoteType"):
            job["location"] += f" | {info['remoteType']}"
    except Exception:
        pass


def amazon_queries(cfg):
    """Search terms for amazon.jobs, derived from the user's target titles (config/search.json)."""
    qs = list(cfg.get("amazon_queries") or cfg.get("workday_queries") or [])
    if not qs:  # last resort: words inside title_include_regex
        qs = [w.strip() for w in re.split(r"[|()]", cfg.get("title_include_regex") or "") if re.fullmatch(r"[A-Za-z][A-Za-z /-]{2,40}", w.strip())]
    return list(dict.fromkeys(q.strip() for q in qs if q.strip()))[:12]


def fetch_amazon(loc, cfg):
    """token = location query (e.g. 'remote', a state or a city). Uses the public amazon.jobs search.json API."""
    seen = {}
    for q in amazon_queries(cfg):
        try:
            data = get("https://www.amazon.jobs/en/search.json", params={
                "base_query": q, "loc_query": loc, "country": "USA", "result_limit": 100, "offset": 0})
        except Exception:
            continue
        for j in data.get("jobs", []):
            seen.setdefault(j.get("id_icims") or j["id"], j)
    for jid, j in seen.items():
        try:
            posted = datetime.strptime(j.get("posted_date", ""), "%B %d, %Y").replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            posted = None
        url = "https://www.amazon.jobs" + (j.get("job_path") or "")
        desc = "\n\n".join(strip_html(j.get(k, "")) for k in ("description", "basic_qualifications", "preferred_qualifications"))
        yield {"job_id": f"amz:{jid}", "ats": "amazon", "company": "Amazon", "title": j.get("title", ""),
               "location": j.get("normalized_location") or j.get("location", ""), "url": url, "apply_url": url,
               "posted": posted, "description": desc, "salary_text": ""}


def fetch_remotive(category, cfg):
    data = get("https://remotive.com/api/remote-jobs", params={"category": category})
    for j in data.get("jobs", []):
        yield {"job_id": f"rm:{j['id']}", "ats": "remotive (company site)", "company": j.get("company_name", ""),
               "title": j.get("title", ""), "location": "Remote | " + (j.get("candidate_required_location") or ""),
               "url": j.get("url"), "apply_url": j.get("url"), "posted": j.get("publication_date"),
               "description": strip_html(j.get("description", "")), "salary_text": j.get("salary", "")}


# ---------------------------------------------------------------- government: NEOGOV / GovernmentJobs.com
NEOGOV = "https://www.governmentjobs.com"
_PER = {"year": 1, "annually": 1, "annual": 1, "month": 12, "monthly": 12, "hour": 2080, "hourly": 2080,
        "biweekly": 26, "week": 52, "weekly": 52}


def _annual(lo, hi, unit):
    """Monthly/hourly government pay -> annual '$X - $Y' so the salary floor works."""
    k = _PER.get(str(unit or "year").lower().strip(), 1)
    try:
        lo, hi = float(lo) * k, float(hi) * k
    except (TypeError, ValueError):
        return ""
    return f"${lo:,.0f} - ${hi:,.0f} per year"


def fetch_neogov(agency, cfg):
    """token = GovernmentJobs.com agency slug (e.g. 'colorado' from governmentjobs.com/careers/colorado).
    Uses NEOGOV's public XML feed (/SearchEngine/JobsFeed?agency=...): every open posting in one request,
    with title, department, pay + interval and dates. Full descriptions come from neogov_detail (title matches only)."""
    r = requests.get(f"{NEOGOV}/SearchEngine/JobsFeed", params={"agency": agency}, headers=UA, timeout=60)
    r.raise_for_status()
    feed = r.content.decode("utf-8-sig", "replace")
    tag = lambda blk, name: html.unescape((re.search(rf"<(?:joblisting:)?{name}>(.*?)</(?:joblisting:)?{name}>", blk, re.S) or [None, ""])[1]).strip()
    for blk in re.findall(r"<item\b.*?</item>", feed, re.S):
        jid = tag(blk, "jobId")
        if not jid:
            continue
        url = f"{NEOGOV}/careers/{agency}/jobs/{jid}"
        loc = ", ".join(x for x in (tag(blk, "city") or tag(blk, "location"), tag(blk, "state")) if x)
        dept = tag(blk, "department") or tag(blk, "agency")
        posted = tag(blk, "pubDate")
        try:
            posted = datetime.strptime(posted, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            posted = None
        job = {"job_id": f"ng:{agency}:{jid}", "ats": "neogov", "company": dept or agency.title(),
               "title": tag(blk, "title"), "location": loc, "url": url, "apply_url": url, "posted": posted,
               "description": "\n\n".join(strip_html(tag(blk, k)) for k in
                                          ("description", "examplesofduties", "qualifications", "supplementalinformation")
                                          if tag(blk, k)),
               "salary_text": _annual(tag(blk, "minimumSalary"), tag(blk, "maximumSalary"), tag(blk, "salaryInterval"))
               if tag(blk, "maximumSalary") else ""}
        close = tag(blk, "advertiseToDateTimeUTC")[:16]
        try:
            if datetime.strptime(close, "%a, %d %b %Y") < datetime.utcnow():
                job["age_override"] = 10 ** 6  # closing date passed
        except ValueError:
            pass
        yield job


def neogov_detail(job):
    """Job page JSON-LD (schema.org JobPosting): full description, posting/closing dates, pay."""
    try:
        t = requests.get(job["_detail"], headers=UA, timeout=TIMEOUT).text
        for blk in re.findall(r'<script type="application/ld\+json">(.*?)</script>', t, re.S):
            try:
                d = json.loads(blk)
            except ValueError:
                continue
            if d.get("@type") != "JobPosting":
                continue
            job["description"] = strip_html(html.unescape(d.get("description", "")))
            job["posted"] = d.get("datePosted")
            org = (d.get("hiringOrganization") or {}).get("name")
            if org and org not in job["company"]:
                job["company"] = f"{org} - {job['company']}"
            v = ((d.get("baseSalary") or {}).get("value") or {})
            if v.get("maxValue"):
                job["salary_text"] = _annual(v.get("minValue"), v.get("maxValue"), v.get("unitText"))
            close = d.get("validThrough")
            if close and iso_age_days(close) is not None and iso_age_days(close) > 0:
                job["age_override"] = 10 ** 6  # closing date passed -> treated as expired
            break
    except Exception:
        pass


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby,
            "smartrecruiters": fetch_smartrecruiters, "workday": fetch_workday, "amazon": fetch_amazon,
            "remotive": fetch_remotive, "neogov": fetch_neogov}
DETAIL_FETCHERS = {"smartrecruiters": smartrecruiters_detail, "workday": workday_detail}  # neogov feed already has full text


# Oracle Taleo (e.g. University of Colorado: token "cu.taleo.net/2"). taleo.py imports helpers from this module,
# so it is imported lazily here to avoid a circular import.
def _fetch_taleo(token, cfg):
    from .taleo import fetch_taleo
    return fetch_taleo(token, cfg)


def _taleo_detail(job):
    from .taleo import taleo_detail
    return taleo_detail(job)


FETCHERS["taleo"] = _fetch_taleo
DETAIL_FETCHERS["taleo"] = _taleo_detail


# ---------------------------------------------------------------- vocabulary
def load_vocab(ws: Workspace):
    """config/skills.json -> {name: (compiled_regex, have)} ; have in {True, 'secondary', False}."""
    raw = (ws.cfg("skills", {}) or {}).get("vocab", {})
    out = {}
    for name, spec in raw.items():
        try:
            out[name] = (re.compile(spec["regex"], re.I), spec.get("have", False))
        except re.error:
            continue
    return out


def blockers_for(ws: Workspace, cfg):
    prof = ws.profile()
    wa = prof.get("work_authorization", {})
    pats = list(cfg.get("blocker_patterns", []))
    if str(wa.get("requires_sponsorship_now_or_future", "")).lower().startswith("y"):
        pats += SPONSORSHIP_BLOCKERS
    if str(wa.get("us_citizen", "")).lower().startswith("n") and str(wa.get("security_clearance", "none")).lower() in ("", "none", "no"):
        pats += CITIZENSHIP_BLOCKERS
    return [re.compile(p, re.I) for p in dict.fromkeys(pats)]


def hard_skip_rules(ws: Workspace, cfg):
    """Free pre-screen rules from config/search.json -> (title_rules, jd_rules), each [(regex, reason)].

    A rule may carry "when": needs_sponsorship | not_citizen | no_clearance, so it only applies when the
    profile's work authorization makes it a real blocker. Rules without "when" always apply."""
    wa = (ws.profile() or {}).get("work_authorization", {}) or {}
    flags = {
        "needs_sponsorship": str(wa.get("requires_sponsorship_now_or_future", "")).lower().startswith("y"),
        "not_citizen": str(wa.get("us_citizen", "")).lower().startswith("n"),
        "no_clearance": str(wa.get("security_clearance", "none")).lower() in ("", "none", "no"),
    }

    def build(key):
        out = []
        for e in cfg.get(key, []) or []:
            if e.get("when") and not flags.get(e["when"], False):
                continue
            try:
                out.append((re.compile(e["pattern"], re.I), e["reason"]))
            except re.error:
                continue
        return out
    return build("title_hard_skip"), build("hard_skip_patterns")


def hard_skip_reason(rules, title, desc):
    """Reason string if a title/JD rule matches, else None. Records the label and the matched text."""
    title_rules, jd_rules = rules
    for rx, reason in title_rules:
        m = rx.search(title or "")
        if m:
            return f"{reason} [title: '{m.group(0)}']"
    for rx, reason in jd_rules:
        m = rx.search(desc or "")
        if m:
            return f"{reason} ['{m.group(0)[:70]}']"
    return None


def reject_key(why):
    """Counter key for a rejection reason; hard skips keep their label so the counter shows why."""
    return why.split(" [")[0] if why.startswith("hard_skip") else why


def prescreen(ws: Workspace, dry_run=False, statuses=("new",)):
    """Re-run the free hard-skip rules over tracker rows (default status 'new') using cached jobs/jd/*.json
    descriptions; matches become 'skipped' with the reason in notes. No AI tokens."""
    rules = hard_skip_rules(ws, ws.cfg("search", {}))
    rows = ws.load_tracker()
    hits, by_reason = [], {}
    for r in rows:
        if r["status"] not in statuses:
            continue
        p = ws.jd / (r["job_id"].replace(":", "_") + ".json")
        desc = (load_json(p, {}) or {}).get("description", "") if p.exists() else ""
        why = hard_skip_reason(rules, r["title"], desc)
        if why:
            hits.append({"job_id": r["job_id"], "company": r["company"], "title": r["title"], "reason": why})
            by_reason[why.split(" [")[0]] = by_reason.get(why.split(" [")[0], 0) + 1
            if not dry_run:
                r["status"] = "skipped"
                r["notes"] = "; ".join(x for x in (r.get("notes", ""), f"prescreen: {why}") if x)[:400]
    if hits and not dry_run:
        ws.save_tracker(rows)
        ws.export_xlsx()
    return {"dry_run": dry_run, "skipped": len(hits), "by_reason": by_reason, "jobs": hits}


# ---------------------------------------------------------------- scoring
class Scorer:
    def __init__(self, ws: Workspace):
        self.ws = ws
        self.cfg = ws.cfg("search", {})
        c = self.cfg
        rx = lambda k, d="(?!x)x": re.compile(c.get(k) or d, re.I)
        self.include, self.exclude = rx("title_include_regex", "."), rx("title_exclude_regex")
        self.bonus, self.senior, self.penalty = rx("title_bonus_regex"), rx("seniority_penalty_regex"), rx("title_penalty_regex")
        self.penalty_points = int(c.get("title_penalty_points", 8))
        loc = c.get("location", {})
        self.country = re.compile(loc.get("country_regex") or ".", re.I)
        self.non_country = re.compile(loc.get("exclude_regex") or "(?!x)x", re.I)
        self.remote = re.compile(loc.get("remote_regex") or "remote|anywhere|distributed|work from home", re.I)
        self.country_mention = re.compile(loc.get("plain_remote_requires_regex") or ".", re.I)
        self.preferred = [(re.compile(p["regex"], re.I), p.get("bonus", 15), p.get("label", "preferred")) for p in loc.get("preferred", [])]
        self.remote_bonus, self.other_bonus = loc.get("remote_bonus", 15), loc.get("other_bonus", 5)
        self.accept_onsite = loc.get("accept_onsite_anywhere_in_country", True)
        self.kw_bonus = [(re.compile(k["regex"], re.I), k.get("title", 0), k.get("desc", 0)) for k in c.get("keyword_bonus", [])]
        self.blockers = blockers_for(ws, c)
        self.hard_rules = hard_skip_rules(ws, c)
        self.vocab = load_vocab(ws)
        prof = ws.profile()
        self.floor = (prof.get("job_preferences") or {}).get("salary_floor") or 0
        self.excluded = [e.lower() for e in (prof.get("job_preferences") or {}).get("excluded_companies", [])]
        wa = prof.get("work_authorization") or {}
        self.needs_sponsor = str(wa.get("requires_sponsorship_now_or_future", "")).lower().startswith("y")
        self.h1b_cfg = c.get("h1b", {}) or {}
        self.max_age = c.get("max_age_days", 14)

    def in_preferred_or_remote(self, location):
        loc_l = (location or "").lower()
        return any(r.search(loc_l) for r, _, _ in self.preferred) or bool(self.remote.search(loc_l))

    def title_ok(self, title):
        return bool(self.include.search(title)) and not self.exclude.search(title)

    def score(self, job):
        title, loc, desc = job["title"], job.get("location") or "", job.get("description") or ""
        reasons = []
        if any(e and e in job["company"].lower() for e in self.excluded):
            return None, "excluded company"
        hs = hard_skip_reason(self.hard_rules, title, desc)
        if hs:
            return None, f"hard_skip: {hs}"
        if not self.title_ok(title):
            return None, "title"
        loc_l = loc.lower()
        is_remote = bool(self.remote.search(loc_l))
        in_country = bool(self.country.search(loc_l))
        if in_country and self.non_country.search(loc_l):
            # 'Toronto, Ontario, CA' must not pass as California: every location part that looks domestic
            # must not also name a foreign place; keep 'San Francisco | Toronto' and 'United States | Canada'.
            parts = [p for p in re.split(r"\s*[|;/]\s*|\s{2,}", loc_l) if p.strip()]
            in_country = any(self.country.search(p) and not self.non_country.search(p) for p in parts)
        if not in_country:
            if self.non_country.search(loc_l) or not is_remote or not self.country_mention.search(desc):
                return None, "location"
        pref = next(((b, lab) for r, b, lab in self.preferred if r.search(loc_l)), None)
        if not pref and not is_remote and not self.accept_onsite:
            return None, "location (onsite outside preferred)"
        age = job["age_override"] if job.get("age_override") is not None else iso_age_days(job.get("posted"))
        if age is not None and age > self.max_age:
            return None, "old"
        for b in self.blockers:
            m = b.search(desc)
            if m:
                return None, "blocker"
        lo, hi = parse_salary(job.get("salary_text") or "")
        if hi is None:
            lo, hi = parse_salary(desc)
        if self.floor and hi is not None and hi < self.floor:
            return None, "salary below floor"

        score = 25
        if self.bonus.search(title):
            score += 12
        if self.senior.search(title):
            score -= 8
            reasons.append("senior-stretch title")
        if self.penalty.search(title):
            score -= self.penalty_points
        for r, tb, db in self.kw_bonus:
            if r.search(title):
                score += tb
            elif r.search(desc):
                score += db
        if pref:
            score += pref[0]
            reasons.append(pref[1])
        elif is_remote:
            score += self.remote_bonus
            reasons.append("Remote")
        else:
            score += self.other_bonus
        found = [k for k, (r, _) in self.vocab.items() if r.search(desc) or r.search(title)]
        have = [k for k in found if self.vocab[k][1] is True]
        sec = [k for k in found if self.vocab[k][1] == "secondary"]
        gaps = [k for k in found if k not in have and k not in sec]
        coverage = (len(have) + 0.6 * len(sec)) / max(1, len(found))
        score += round(38 * coverage)
        if age is not None and age <= 3:
            score += 5
        if hi is not None and self.floor and hi >= self.floor:
            score += 3
        i = desc.lower().find("sponsor")
        if i >= 0 and not re.search(r"no |not |unable|cannot", desc[max(0, i - 60):i], re.I):
            reasons.append("mentions sponsorship (check)")
        if self.needs_sponsor and self.h1b_cfg.get("enabled", True):
            from .h1b import score_adjust  # USCIS sponsor history (no-op until h1b-import has run)
            pts, flag = score_adjust(self.ws, job.get("company", ""), self.h1b_cfg)
            score += pts
            if flag:
                reasons.append(flag)
        job.update({"score": max(0, min(100, score)), "keyword_coverage": f"{coverage:.0%}", "keywords_have": have,
                    "keywords_secondary": sec, "keywords_gap": gaps, "age_days": age,
                    "salary": f"${lo:,}-${hi:,}" if hi else "", "flags": "; ".join(reasons)})
        return job, "ok"


# ---------------------------------------------------------------- run
def run_fetch(companies, cfg, workers=16, exclude_ats=()):
    skip = {a.lower() for a in exclude_ats}
    tasks = [(ats, t) for ats, toks in companies.items() if ats in FETCHERS and ats not in skip for t in toks]
    jobs, status = [], {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(lambda a, t: list(FETCHERS[a](t, cfg)), a, t): (a, t) for a, t in tasks}
        for f in as_completed(futs):
            a, t = futs[f]
            try:
                res = f.result()
                status[f"{a}:{t}"] = len(res)
                jobs.extend(res)
            except Exception as e:
                status[f"{a}:{t}"] = f"ERR {type(e).__name__}"
    return jobs, status


def validate_boards(ws: Workspace):
    companies = {k: v for k, v in ws.cfg("companies", {}).items() if not k.startswith("_")}
    _, status = run_fetch(companies, ws.cfg("search", {}))
    bad = {k: v for k, v in status.items() if isinstance(v, str) or v == 0}
    return {"valid": len(status) - len(bad), "invalid": bad}


def search(ws: Workspace, limit=None, analyze_only=False, dry_run=False, log=print, exclude_ats=(), preferred_location=False):
    cfg = ws.cfg("search", {})
    companies = {k: v for k, v in ws.cfg("companies", {}).items() if not k.startswith("_")}
    scorer = Scorer(ws)
    raw, status = run_fetch(companies, cfg, exclude_ats=exclude_ats)
    boards_ok = sum(1 for v in status.values() if not isinstance(v, str))
    log(f"Fetched {len(raw)} postings from {boards_ok} boards")

    need = [j for j in raw if j["ats"] in DETAIL_FETCHERS and scorer.title_ok(j["title"])
            and (j.get("age_override") is None or j["age_override"] <= scorer.max_age)]
    with ThreadPoolExecutor(max_workers=12) as ex:
        list(ex.map(lambda j: DETAIL_FETCHERS[j["ats"]](j), need))

    tracker = ws.load_tracker()
    known_ids = {r["job_id"] for r in tracker}
    known_keys = {(r["company"].lower(), r["title"].lower()) for r in tracker}
    scored, rejects = [], {}
    for j in raw:
        res, why = scorer.score(j)
        if res:
            scored.append(res)
        else:
            rejects[reject_key(why)] = rejects.get(reject_key(why), 0) + 1
    if preferred_location:  # keep only the profile's preferred locations or remote (country already checked by score)
        scored = [j for j in scored if scorer.in_preferred_or_remote(j.get("location") or "")]
    best = {}
    for j in scored:
        k = (j["company"].lower(), j["title"].lower())
        if k not in best or j["score"] > best[k]["score"]:
            best[k] = j
    scored = sorted(best.values(), key=lambda j: j["score"], reverse=True)
    fresh = [j for j in scored if j["job_id"] not in known_ids and (j["company"].lower(), j["title"].lower()) not in known_keys
             and j["score"] >= cfg.get("min_score", 50)]
    n = limit or (24 if preferred_location else cfg.get("daily_shortlist", 20))
    cap = cfg.get("max_per_company_per_day", 2)
    per_co, shortlist = {}, []
    for j in fresh:
        c = j["company"].lower()
        if per_co.get(c, 0) < cap:
            per_co[c] = per_co.get(c, 0) + 1
            shortlist.append(j)
        if len(shortlist) >= n:
            break
    summary = {"fetched": len(raw), "boards": boards_ok, "passed_filters": len(scored), "new_above_min": len(fresh),
               "shortlisted": 0 if analyze_only else len(shortlist), "rejected_by": rejects,
               "top": [{"score": j["score"], "company": j["company"], "title": j["title"], "location": j["location"][:60],
                        "salary": j["salary"], "job_id": j["job_id"]} for j in shortlist]}
    if dry_run:
        return summary
    stamp = today()
    save_json(ws.jobs / f"{stamp}_all_matches.json",
              [{k: v for k, v in j.items() if k not in ("description", "_detail")} for j in scored])
    if not analyze_only:
        for j in shortlist:
            save_json(ws.jd / (j["job_id"].replace(":", "_") + ".json"), {k: v for k, v in j.items() if k != "_detail"})
            tracker.append({"job_id": j["job_id"], "date_found": stamp, "company": j["company"], "title": j["title"],
                            "location": j["location"], "ats": j["ats"], "url": j["url"], "apply_url": j["apply_url"],
                            "score": j["score"], "keyword_coverage": j["keyword_coverage"], "salary": j["salary"],
                            "flags": j["flags"], "status": "new"})
        ws.save_tracker(tracker)
        ws.export_xlsx()
    try:
        from . import skills_gap
        summary["skills_gap"] = skills_gap.run(ws)
    except Exception as e:
        summary["skills_gap"] = f"skipped: {e}"
    return summary
