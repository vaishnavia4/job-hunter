"""Weekly report: what was found, applied to and answered, and what to change.

  python -m jobhunter --workspace WS report [--days 7] [--out reports]

Reads data/tracker.csv, data/responses.json (written by `responses`) and applications/*/job.json and writes
reports/<YYYY-MM-DD>-weekly.md plus a "Weekly" sheet in tracker.xlsx (when openpyxl is installed).
Small numbers are shown as counts ("1/4"), and a percentage only once a group has at least MIN_N jobs, so a
single reply never looks like a "25% response rate" trend.
"""
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .workspace import Workspace, load_json

MIN_N = 5  # smallest group for which a percentage is printed / compared
APPLIED = {"applied", "interview", "offer", "rejected"}
PAST_SCREEN = {"tailored", "ready", "blocked"} | APPLIED
HUMAN_REPLY = {"rejected", "interview", "offer"}
CO_RX = re.compile(r"\bCO\b|Colorado|Denver|Boulder|Englewood|Broomfield|Aurora|Lakewood|Littleton|Golden|"
                   r"Westminster|Fort Collins|Colorado Springs|Greenwood Village|Louisville, CO", re.I)
SKIP_BUCKETS = [  # (label, regex over the skip note) - first match wins
    ("citizenship / clearance / ITAR", r"citizen|clearance|ITAR|TS/SCI|polygraph|public trust|US Persons"),
    ("no visa sponsorship", r"sponsor|without sponsorship|H-1B"),
    ("salary below floor", r"salary|pay max|\$1[0-3]\dk|< ?\$?140"),
    ("really SWE / data / ML engineering", r"software[- ]engineer|SWE|backend|data (science|engineer)|\bML\b|pipelines|"
                                           r"Go is the primary|Go primary|Java|coding role"),
    ("too senior (staff/principal, 8-10+ yrs)", r"staff|principal|10\+|9\+|8\+|too senior|director"),
    ("specialist skill she lacks", r"lacks|she has no|requires|core is|core of the role|specialist|HPC|SOC|DFIR|"
                                   r"application security|product security|detection"),
    ("company / account issue", r"NVIDIA|aggregator|account"),
    ("location / on-site", r"on-site|onsite|hybrid|residents only|relocat"),
]


def _date(s):
    try:
        return datetime.strptime((s or "")[:10], "%Y-%m-%d")
    except ValueError:
        return None


def location_bucket(row):
    loc = row.get("location", "") or ""
    if CO_RX.search(loc) or "Colorado" in (row.get("flags") or ""):
        return "Colorado"
    if re.search(r"remote", loc + " " + (row.get("flags") or ""), re.I):
        return "Remote"
    return "Other US"


def company_type(row):
    f = row.get("flags") or ""
    if re.search(r"H-1B sponsor \(", f):
        return "H-1B sponsor (strong)"
    if "some H-1B history" in f:
        return "some H-1B history"
    if re.search(r"no H-1B", f):
        return "no H-1B record"
    return "unknown"


def source(row, job):
    ats = (row.get("ats") or "").lower()
    if ats in ("manual", "other", "") and job.get("ats"):
        ats = job["ats"].lower()
    return ats or "unknown"


def skip_reason(note):
    for label, rx in SKIP_BUCKETS:
        if re.search(rx, note or "", re.I):
            return label
    return "other / not recorded"


def frac(k, n):
    """'2/7 (29%)' when n >= MIN_N, else '2/3' - no fake precision on tiny groups."""
    if not n:
        return "-"
    return f"{k}/{n} ({k / n:.0%})" if n >= MIN_N else f"{k}/{n}"


def responses_by_job(ws):
    """job_id -> set of classes from data/responses.json (only confident single-job matches)."""
    from .responses import MIN_MATCH
    out = defaultdict(set)
    for m in ((load_json(ws.data / "responses.json", {}) or {}).get("messages") or {}).values():
        if len(m.get("job_ids") or []) == 1 and m.get("match_score", 0) >= MIN_MATCH:
            out[m["job_ids"][0]].add(m["class"])
    return out


def enrich(ws, rows):
    resp = responses_by_job(ws)
    out = []
    for r in rows:
        job = {}
        if r.get("folder"):
            job = load_json(ws.root / r["folder"].replace("\\", "/") / "job.json", {}) or {}
        cls = resp.get(r["job_id"], set())
        st = r.get("status", "")
        applied = st in APPLIED or bool(r.get("date_applied"))
        out.append({
            **r, "_applied": applied, "_past_screen": st in PAST_SCREEN or applied,
            "_confirmed": "application_received" in cls,
            "_responded": applied and (st in HUMAN_REPLY or bool(cls & HUMAN_REPLY)),
            "_interview": st in ("interview", "offer") or "interview" in cls or "offer" in cls,
            "_offer": st == "offer" or "offer" in cls,
            "_source": source(r, job), "_level": job.get("tailor_level") or "auto",
            "_ctype": company_type(r), "_loc": location_bucket(r),
        })
    return out


def breakdown(applied, key):
    groups = defaultdict(list)
    for r in applied:
        groups[r[key]].append(r)
    return sorted(((g, len(v), sum(x["_responded"] for x in v), sum(x["_interview"] for x in v),
                    sum(x["_confirmed"] for x in v)) for g, v in groups.items()), key=lambda t: -t[1])


def suggestions(rows, applied, period_days, now):
    out = []
    replies = sum(r["_responded"] for r in applied)
    if applied and not replies:
        ages = sorted((((now - d).days, r) for r in applied if (d := _date(r.get("date_applied")))), key=lambda t: t[0])
        oldest = ages[-1][0] if ages else 0
        conf = sum(r["_confirmed"] for r in applied)
        follow = [r["company"] for a, r in ages if a >= 14]
        out.append((90, f"No human replies yet across {len(applied)} applications (oldest is {oldest} day{'s' if oldest != 1 else ''} old; "
                        f"{conf} auto-confirmations received). Too early to judge what works: most ATS replies take 1-3 "
                        f"weeks. Run `responses` daily" + (f" and follow up with {', '.join(follow[:5])} (14+ days)."
                                                            if follow else "; follow up on anything older than 14 days.")))
    # Compare groups once there is something to compare.
    for key, label in (("_source", "applications via"), ("_level", "tailoring level"), ("_loc", "location"),
                       ("_ctype", "company type")):
        groups = [(g, n, k) for g, n, k, _, _ in breakdown(applied, key) if n >= 3]
        if len(groups) < 2 or not replies:
            continue
        best = max(groups, key=lambda t: t[2] / t[1])
        rest = [(g, n, k) for g, n, k in groups if g != best[0]]
        rn, rk = sum(n for _, n, _ in rest), sum(k for _, _, k in rest)
        rb, rr = best[2] / best[1], (rk / rn if rn else 0)
        if best[2] >= 2 and rb > rr:
            ratio = f"{rb / rr:.1f}x the" if rr else "the only"
            small = " (small sample - treat as a hint)" if best[1] < MIN_N or rn < MIN_N else ""
            out.append((80, f"{label} {best[0]}: {ratio} reply rate of the rest ({best[2]}/{best[1]} vs {rk}/{rn}){small}. "
                            f"Prioritise more of these."))
    skipped = [r for r in rows if r.get("status") == "skipped"]
    if rows and skipped:
        top = Counter(skip_reason(r.get("notes")) for r in skipped).most_common(1)[0]
        out.append((70, f"{len(skipped)} of {len(rows)} found jobs were skipped ({len(skipped) / len(rows):.0%}); top reason "
                        f"'{top[0]}' (n={top[1]}). If these are caught only at screening, add the phrases to "
                        f"config/search.json blockers so the search drops them for free."))
    co = [r for r in applied if r["_loc"] == "Colorado"]
    if applied:
        out.append((60, f"Colorado share of applications: {len(co)}/{len(applied)}. Colorado roles are her first "
                        f"preference; " + ("good mix." if len(co) * 3 >= len(applied) else
                                           "consider widening the Colorado company list in config/companies.json.")))
    waiting = [r for r in rows if r.get("status") in ("ready", "blocked")]
    if waiting:
        out.append((85, f"{len(waiting)} job(s) are waiting on Vaishnavi (ready/blocked): "
                        f"{', '.join(r['company'] for r in waiting[:5])}. Clearing these is the cheapest win this week."))
    return [t for _, t in sorted(out, key=lambda x: -x[0])[:3]]


def weekly_report(ws: Workspace, days=7, out_dir="reports", now=None, write_xlsx=True):
    now = now or datetime.now()
    cutoff = now - timedelta(days=days)
    rows = enrich(ws, ws.load_tracker())
    applied = [r for r in rows if r["_applied"]]
    in_period = lambda r, f: (d := _date(r.get(f))) is not None and d >= cutoff.replace(hour=0, minute=0, second=0)
    p_applied = [r for r in applied if in_period(r, "date_applied")]
    p_found = [r for r in rows if in_period(r, "date_found")]
    resp_all = ((load_json(ws.data / "responses.json", {}) or {}).get("messages") or {}).values()
    p_emails = Counter(m["class"] for m in resp_all if (d := _date(m.get("date"))) and d >= cutoff)
    funnel = [("found", len(rows)), ("past screening (tailored+)", sum(r["_past_screen"] for r in rows)),
              ("applied", len(applied)), ("auto-confirmed", sum(r["_confirmed"] for r in applied)),
              ("responded (human reply)", sum(r["_responded"] for r in applied)),
              ("interview", sum(r["_interview"] for r in applied)), ("offer", sum(r["_offer"] for r in applied))]
    skipped = [r for r in rows if r.get("status") == "skipped"]
    skip_top = Counter(skip_reason(r.get("notes")) for r in skipped).most_common(6)
    waiting = [r for r in rows if r.get("status") in ("ready", "blocked")]
    tips = suggestions(rows, applied, days, now)

    L = [f"# Weekly job-search report - {now:%Y-%m-%d}", "",
         f"Period: last {days} days ({cutoff:%Y-%m-%d} to {now:%Y-%m-%d}). Percentages are shown only for groups of "
         f"{MIN_N}+ jobs; smaller groups show counts only.", "",
         "## This period", "",
         f"- Jobs found: **{len(p_found)}** (total tracked {len(rows)})",
         f"- Applications submitted: **{len(p_applied)}** (total {len(applied)})",
         "- Emails classified this period: " + (", ".join(f"{k} {v}" for k, v in p_emails.most_common()) or "none "
                                                "(run `responses`)"), "",
         "## Funnel (all time)", "", "| stage | jobs | of applied |", "|---|---:|---:|"]
    for name, n in funnel:
        L.append(f"| {name} | {n} | {frac(n, len(applied)) if name not in ('found', 'past screening (tailored+)', 'applied') else ''} |")
    L += ["", f"Response rate: {frac(funnel[4][1], len(applied))} | interview rate: {frac(funnel[5][1], len(applied))}", ""]
    for key, title in (("_source", "By ATS / source"), ("_ctype", "By company type (H-1B history from tracker flags)"),
                       ("_level", "By tailoring level"), ("_loc", "By location")):
        L += [f"## {title}", "", "| group | applied | confirmed | replied | interview |", "|---|---:|---:|---:|---:|"]
        for g, n, k, iv, cf in breakdown(applied, key):
            L.append(f"| {g} | {n} | {frac(cf, n)} | {frac(k, n)} | {frac(iv, n)} |")
        L.append("")
    L += ["## Top skip reasons", ""] + [f"- {lab}: {n}" for lab, n in skip_top] + [""]
    L += ["## Waiting on you", ""]
    L += [f"- **{r['status']}** {r['company']} - {r['title']} ({(r.get('notes') or '')[:120]})" for r in waiting] or ["- nothing"]
    acts = [m for m in resp_all if m.get("class") == "action_needed" and (d := _date(m.get("date"))) and d >= cutoff]
    if acts:
        L += ["", "Emails asking for an action this period:"]
        L += [f"- {m['date']} {', '.join(m.get('companies') or []) or m.get('ats') or '?'}: {m['subject']}" for m in acts]
    L += ["", "## Suggestions", ""] + [f"{i}. {t}" for i, t in enumerate(tips, 1)] + [""]
    md = "\n".join(L)

    out = Path(out_dir)
    if not out.is_absolute():
        out = ws.root / out
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{now:%Y-%m-%d}-weekly.md"
    path.write_text(md, encoding="utf-8")
    xlsx = _write_sheet(ws, funnel, applied, skip_top, tips, now, days) if write_xlsx else "skipped"
    return {"report": str(path), "xlsx": xlsx, "period_days": days, "found_period": len(p_found),
            "applied_period": len(p_applied), "applied_total": len(applied), "funnel": dict(funnel),
            "waiting": [f"{r['status']}: {r['company']}" for r in waiting], "suggestions": tips, "markdown": md}


def _write_sheet(ws, funnel, applied, skip_top, tips, now, days):
    try:
        from openpyxl import Workbook, load_workbook
        from openpyxl.styles import Font
    except ImportError:
        return "openpyxl missing - Weekly sheet not written"
    if ws.tracker_xlsx.exists():
        try:
            wb = load_workbook(ws.tracker_xlsx)
        except Exception as e:
            return f"could not open tracker.xlsx ({type(e).__name__})"
    else:
        ws.export_xlsx()
        wb = load_workbook(ws.tracker_xlsx) if ws.tracker_xlsx.exists() else Workbook()
    if "Weekly" in wb.sheetnames:
        del wb["Weekly"]
    s = wb.create_sheet("Weekly")
    bold = Font(bold=True)
    s.append([f"Weekly report {now:%Y-%m-%d} (last {days} days)"]); s["A1"].font = bold
    s.append([])
    s.append(["stage", "jobs"]); s.append([])
    for name, n in funnel:
        s.append([name, n])
    for key, title in (("_source", "ATS / source"), ("_ctype", "company type"), ("_level", "tailoring level"),
                       ("_loc", "location")):
        s.append([]); s.append([title, "applied", "confirmed", "replied", "interview"])
        s.cell(s.max_row, 1).font = bold
        for g, n, k, iv, cf in breakdown(applied, key):
            s.append([g, n, cf, k, iv])
    s.append([]); s.append(["top skip reasons", "count"]); s.cell(s.max_row, 1).font = bold
    for lab, n in skip_top:
        s.append([lab, n])
    s.append([]); s.append(["suggestions"]); s.cell(s.max_row, 1).font = bold
    for t in tips:
        s.append([t])
    s.column_dimensions["A"].width = 44
    try:
        wb.save(ws.tracker_xlsx)
        return "Weekly sheet written"
    except PermissionError:
        return "tracker.xlsx is open in Excel - report .md written, sheet skipped"
