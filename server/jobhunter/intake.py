"""Manual job intake (paste a link) and skipped-folder archiving."""
import html as _html
import re
import shutil
from urllib.parse import urlparse

from .search import Scorer, strip_html
from .workspace import Workspace, save_json, today


def _guess_ats(url):
    h = urlparse(url).netloc.lower()
    for key, name in (("greenhouse", "greenhouse"), ("lever.co", "lever"), ("ashbyhq", "ashby"),
                      ("myworkdayjobs", "workday"), ("smartrecruiters", "smartrecruiters"),
                      ("linkedin", "linkedin"), ("indeed", "indeed"), ("amazon.jobs", "amazon")):
        if key in h:
            return name
    return "other"


def _fetch_page(url):
    """Best-effort fetch of a public posting: title (og:title or <title>), site name and plain text."""
    import requests
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (job-hunter personal job search)"}, timeout=25)
    r.raise_for_status()
    t = r.text

    def og(prop):
        m = re.search(r"<meta[^>]+property=[\"']og:" + prop + r"[\"'][^>]+content=[\"']([^\"']*)", t, re.I)
        return m.group(1) if m else ""

    m = re.search(r"<title[^>]*>(.*?)</title>", t, re.S | re.I)
    title = og("title") or (m.group(1) if m else "")
    body = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", "", t)
    return _html.unescape(title).strip(), _html.unescape(og("site_name")).strip(), strip_html(body)[:20000]


def add_job(ws: Workspace, url, title="", company="", location="", description=""):
    """Save jobs/jd/<id>.json and a tracker row (status new). Missing fields are fetched from the URL when possible."""
    note = ""
    if not (title and company and description):
        try:
            pt, site, text = _fetch_page(url)
            title, company, description = title or pt, company or site, description or text
        except Exception as e:
            note = f"page fetch failed ({type(e).__name__}); fill title/company/description manually"
    title, company = title or "Unknown title", company or "Unknown company"
    p = urlparse(url)
    jid = "man:" + re.sub(r"[^a-z0-9]+", "", (p.netloc + p.path).lower())[-40:]
    ats = _guess_ats(url)
    job = {"job_id": jid, "ats": ats, "company": company, "title": title, "location": location, "url": url,
           "apply_url": url, "posted": None, "description": description, "salary_text": ""}
    score, flags, cov, sal = "", "", "", ""
    try:
        res, why = Scorer(ws).score(dict(job))
        if res:
            score, cov, sal, flags = res["score"], res["keyword_coverage"], res["salary"], res["flags"]
            job.update(score=score, keyword_coverage=cov, keywords_have=res["keywords_have"], keywords_gap=res["keywords_gap"])
        else:
            flags = f"auto-filter would reject: {why} (kept because added manually)"
    except Exception:
        pass
    if ats in ("linkedin", "indeed"):
        flags = (flags + "; discovery only - apply on the company's own site").strip("; ")
    if note:
        flags = (flags + "; " + note).strip("; ")
    save_json(ws.jd / (jid.replace(":", "_") + ".json"), job)
    rows = ws.load_tracker()
    if not any(r["job_id"] == jid for r in rows):
        rows.append({"job_id": jid, "date_found": today(), "company": company, "title": title, "location": location,
                     "ats": ats, "url": url, "apply_url": url, "score": score, "keyword_coverage": cov, "salary": sal,
                     "flags": flags, "status": "new"})
        ws.save_tracker(rows)
        ws.export_xlsx()
    return {"job_id": jid, "title": title, "company": company, "score": score, "flags": flags,
            "jd": "jobs/jd/" + jid.replace(":", "_") + ".json"}


def archive_skipped(ws: Workspace):
    """Move application folders of skipped jobs to applications/_skipped (skips are tracker-only going forward)."""
    rows = ws.load_tracker()
    dest = ws.apps / "_skipped"
    moved = []
    for r in rows:
        if r["status"] != "skipped" or not r.get("folder"):
            continue
        src = ws.root / r["folder"]
        if not src.exists() or src.parent == dest:
            continue
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / src.name
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(src), str(target))
        r["folder"] = str(target.relative_to(ws.root))
        moved.append(src.name)
    ws.save_tracker(rows)
    ws.export_xlsx()
    return moved
