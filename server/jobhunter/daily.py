"""`python -m jobhunter daily`: the morning check. No model tokens.

1. reads employer replies from Gmail and updates the tracker (responses.read_responses)
2. analyses outcomes: who rejected, who moved forward, which groups of applications get rejected more often and how
   fast (a rejection within two days is almost always an automatic knock-out, not a person reading the resume)
3. writes reports/daily_<date>.md and a self-contained application tracker page (tracker.html in the workspace)

Rejection emails never give a reason, so "why" is inferred from patterns across all applications and is labelled
as a hint whenever a group is small.
"""
import html
import json
import re
from collections import defaultdict
from datetime import datetime

from . import report
from .workspace import Workspace, load_json

MIN_N = 4  # smallest group worth comparing
FAMILIES = [  # first match wins
    ("Security", r"secur|iam\b|identity|grc|compliance|zero trust"),
    ("SRE / reliability", r"site reliability|\bsre\b|reliability"),
    ("Platform", r"platform"),
    ("DevOps", r"devops|devsecops|build|release|ci/cd"),
    ("Cloud / infrastructure", r"cloud|infrastructure|infra\b|azure|aws|network|systems"),
]
SENIOR_RX = re.compile(r"\b(staff|principal|lead|architect|distinguished|head|manager|iv|v)\b", re.I)
OPEN = ("applied", "assessment", "interview")


def _d(s):
    try:
        return datetime.strptime((s or "")[:10], "%Y-%m-%d")
    except ValueError:
        return None


def family(title):
    t = (title or "").lower()
    return next((name for name, rx in FAMILIES if re.search(rx, t)), "Other")


def fit_band(row):
    m = re.search(r"fit (\d{2,3})%", row.get("notes") or "")
    try:
        v = int(m.group(1)) if m else int(float(row.get("score") or 0))
    except ValueError:
        v = 0
    return "fit 80+" if v >= 80 else "fit 65-79" if v >= 65 else "fit below 65"


def pay_band(row):
    n = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]{4,}", row.get("salary") or "")]
    if not n:
        return "pay not posted"
    return "pay top 200k+" if max(n) >= 200000 else "pay top 170-200k" if max(n) >= 170000 else "pay top under 170k"


def reply_dates(ws):
    """job_id -> {class: first date} from data/responses.json (single-job matches only)."""
    out = defaultdict(dict)
    for m in ((load_json(ws.data / "responses.json", {}) or {}).get("messages") or {}).values():
        ids = m.get("job_ids") or []
        if len(ids) == 1 and m.get("date"):
            cur = out[ids[0]].get(m["class"])
            if not cur or m["date"] < cur:
                out[ids[0]][m["class"]] = m["date"]
    return out


def analyse(ws, now=None):
    now = now or datetime.now()
    rows = report.enrich(ws, ws.load_tracker())
    dates = reply_dates(ws)
    applied = [r for r in rows if r["_applied"]]
    for r in applied:
        r["_family"], r["_fit"], r["_pay"] = family(r["title"]), fit_band(r), pay_band(r)
        r["_senior"] = "senior-stretch title" if SENIOR_RX.search(r["title"] or "") else "standard title"
        r["_rejected"] = r["status"] == "rejected"
        rd, ad = _d(dates.get(r["job_id"], {}).get("rejected")), _d(r.get("date_applied"))
        r["_days_to_reject"] = (rd - ad).days if r["_rejected"] and rd and ad else None
        r["_age"] = (now - ad).days if ad else None
    rej = [r for r in applied if r["_rejected"]]
    fwd = [r for r in applied if r["_interview"] or r["status"] in ("assessment", "interview", "offer")]
    waiting = [r for r in applied if r["status"] == "applied"]
    overall = len(rej) / len(applied) if applied else 0
    groups = []
    for key, label in (("_family", "Role type"), ("_loc", "Location"), ("_fit", "Fit"), ("_senior", "Seniority"),
                       ("_pay", "Posted pay"), ("_ctype", "Sponsor history"), ("_source", "Site"), ("_level", "Tailoring")):
        g = defaultdict(list)
        for r in applied:
            g[r[key]].append(r)
        for name, v in sorted(g.items(), key=lambda kv: -len(kv[1])):
            groups.append({"dimension": label, "group": name, "applied": len(v), "rejected": sum(x["_rejected"] for x in v),
                           "forward": sum(1 for x in v if x in fwd)})
    fast = [r for r in rej if r["_days_to_reject"] is not None and r["_days_to_reject"] <= 2]
    slow = [r for r in rej if r["_days_to_reject"] is not None and r["_days_to_reject"] > 2]
    return {"now": now, "rows": rows, "applied": applied, "rejected": rej, "forward": fwd, "waiting": waiting,
            "overall_reject_rate": overall, "groups": groups, "fast": fast, "slow": slow,
            "followups": sorted((r for r in waiting if (r["_age"] or 0) >= 14), key=lambda r: -(r["_age"] or 0)),
            "on_her": [r for r in rows if r["status"] in ("ready", "blocked")]}


def recommendations(a):
    out, applied, rej = [], a["applied"], a["rejected"]
    if not applied:
        return ["No applications yet."]
    if not a["forward"]:
        out.append(f"No interview or assessment invitations yet from {len(applied)} applications ({len(rej)} rejections, "
                   f"{len(a['waiting'])} still open). The resume is not yet converting; the points below are where to look first.")
    known = len(a["fast"]) + len(a["slow"])
    if known >= MIN_N and len(a["fast"]) / known >= 0.4:
        out.append(f"{len(a['fast'])} of {known} dated rejections came within 2 days of applying. That speed means an automatic "
                   "filter, not a recruiter: usually the sponsorship answer (Yes), location, or a knock-out question. "
                   "Prefer employers with H-1B history and postings that do not say 'no sponsorship'; these fast rejections "
                   "say little about the resume itself.")
    base = a["overall_reject_rate"]
    worse = [g for g in a["groups"] if g["applied"] >= MIN_N and g["rejected"] / g["applied"] >= base + 0.12 and g["rejected"] >= 3]
    better = [g for g in a["groups"] if g["applied"] >= MIN_N and g["rejected"] / g["applied"] <= base - 0.12]
    for g in sorted(worse, key=lambda g: -(g["rejected"] / g["applied"]))[:3]:
        small = " Small sample, so treat it as a hint." if g["applied"] < 20 else ""
        advice = ("Location is her own priority, so keep applying; check these postings for hybrid or local-candidate wording."
                  if g["dimension"] == "Location" else "Apply to fewer of these, or only when fit is 80+.")
        out.append(f"{g['dimension']} '{g['group']}': {g['rejected']}/{g['applied']} rejected ({g['rejected'] / g['applied']:.0%}) "
                   f"against {base:.0%} overall. {advice}{small}")
    for g in sorted(better, key=lambda g: g["rejected"] / g["applied"])[:2]:
        out.append(f"{g['dimension']} '{g['group']}': only {g['rejected']}/{g['applied']} rejected so far "
                   f"({g['rejected'] / g['applied']:.0%} against {base:.0%}). Send more applications here."
                   + (" Small sample, so treat it as a hint." if g["applied"] < 20 else ""))
    if a["followups"]:
        out.append(f"{len(a['followups'])} applications are 14+ days old with no reply: a short follow-up note to the recruiter "
                   f"is worth it for the best-fit ones ({', '.join(r['company'] for r in a['followups'][:4])}).")
    if a["on_her"]:
        out.append(f"{len(a['on_her'])} applications are waiting on you (ready/blocked, see BLOCKERS.md): "
                   f"{', '.join(r['company'] for r in a['on_her'][:6])}.")
    return out or ["Not enough outcomes yet to see a pattern; keep the daily check running."]


def _md(a, res, tips):
    now = a["now"]
    L = [f"# Daily application check - {now:%Y-%m-%d}", ""]
    ch = (res or {}).get("status_changes") or []
    by = (res or {}).get("by_class") or {}
    L += ["## New since the last check", ""]
    if res is None:
        L.append("Mail could not be read this run (see the console output).")
    elif not res.get("processed"):
        L.append("No new employer emails.")
    else:
        L.append(", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in by.items() if v))
        for c in ch:
            L.append(f"- **{c['company']}**: {c['status_from']} -> **{c['status_to']}**")
    need = [r for r in ((res or {}).get("results") or []) if r["class"] in ("interview", "offer", "action_needed")
            and not re.search(r"reset your password|verify your candidate account|security code", r["subject"], re.I)]
    if need:
        L += ["", "### Needs your attention", ""] + [f"- {r['date']} {', '.join(r['companies']) or r['from']}: {r['subject']}" for r in need[:15]]
    L += ["", "## Where things stand", "",
          f"- Applied: **{len(a['applied'])}** | still open: **{len(a['waiting'])}** | rejected: **{len(a['rejected'])}** "
          f"({a['overall_reject_rate']:.0%}) | interview / assessment / offer: **{len(a['forward'])}**",
          f"- Rejections with a known date: {len(a['fast'])} within 2 days (automatic filter), {len(a['slow'])} later (reviewed)"]
    if a["forward"]:
        L += ["", "### Moving forward", ""] + [f"- {r['company']} - {r['title']} ({r['status']})" for r in a["forward"]]
    L += ["", "## What to do better", ""] + [f"{i}. {t}" for i, t in enumerate(tips, 1)]
    L += ["", "## Rejection rate by group", "", "| Group | Applied | Rejected | Rate | Moved forward |", "|---|---|---|---|---|"]
    for g in a["groups"]:
        if g["applied"] >= MIN_N:
            L.append(f"| {g['dimension']}: {g['group']} | {g['applied']} | {g['rejected']} | {g['rejected'] / g['applied']:.0%} | {g['forward']} |")
    L += ["", "Groups with fewer than %d applications are left out. Employers do not state a reason, so these are patterns, not causes." % MIN_N]
    if a["rejected"]:
        L += ["", "## Rejections", "", "| Company | Role | Applied | Days to reject | Fit | Location |", "|---|---|---|---|---|---|"]
        for r in sorted(a["rejected"], key=lambda r: r.get("date_applied") or "", reverse=True):
            dd = "" if r["_days_to_reject"] is None else str(r["_days_to_reject"])
            L.append(f"| {r['company'][:32]} | {r['title'][:46]} | {r.get('date_applied', '')} | {dd} | {r['_fit'].replace('fit ', '')} | {r['_loc']} |")
    return "\n".join(L) + "\n"


CSS = """:root{--bg:#f7f7f5;--card:#fff;--ink:#1c1c1a;--mut:#6b6b66;--line:#e3e2dd;--acc:#2f6f5e}
@media(prefers-color-scheme:dark){:root{--bg:#161615;--card:#201f1e;--ink:#ecebe6;--mut:#a09f98;--line:#35342f;--acc:#7cc4ae}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,Segoe UI,sans-serif}
main{max-width:1180px;margin:0 auto;padding:20px 16px 60px}h1{font-size:22px;margin:0 0 4px}.sub{color:var(--mut);margin-bottom:16px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:16px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.tile b{display:block;font-size:26px}
.tile span{color:var(--mut);font-size:13px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:16px}
.card h2{font-size:16px;margin:0 0 8px}.card li{margin-bottom:6px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}input,select{font:inherit;padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}
input{flex:1;min-width:180px}.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
td.t{white-space:normal;min-width:220px}th{position:sticky;top:0;background:var(--card);cursor:pointer;color:var(--mut);font-weight:600}
a{color:var(--acc)}.s{display:inline-block;padding:2px 8px;border-radius:99px;font-size:12px;border:1px solid var(--line)}
.s-applied{background:#2f6f5e22}.s-rejected{background:#b4443322}.s-interview,.s-offer,.s-assessment{background:#d9a52155;font-weight:600}
.s-blocked,.s-ready{background:#c9772233}.s-new,.s-tailored{background:#5b7fb522}"""

JS = """const rows=[...document.querySelectorAll('tbody tr')],q=document.getElementById('q'),st=document.getElementById('st'),n=document.getElementById('n');
function f(){const t=q.value.toLowerCase(),s=st.value;let c=0;rows.forEach(r=>{const ok=(!s||r.dataset.s===s)&&(!t||r.textContent.toLowerCase().includes(t));r.style.display=ok?'':'none';c+=ok});n.textContent=c+' shown'}
q.oninput=f;st.onchange=f;f();
document.querySelectorAll('th').forEach((th,i)=>th.onclick=()=>{const tb=document.querySelector('tbody'),d=th.dataset.d=th.dataset.d==='1'?'-1':'1';
[...tb.rows].sort((a,b)=>{const x=a.cells[i].dataset.v||a.cells[i].textContent,y=b.cells[i].dataset.v||b.cells[i].textContent;return (isNaN(x-y)?x.localeCompare(y):x-y)*d}).forEach(r=>tb.appendChild(r))});"""


def _html(a, tips):
    e = html.escape
    now = a["now"]
    shown = [r for r in a["rows"] if r["status"] != "skipped"]
    order = {"offer": 0, "interview": 1, "assessment": 2, "ready": 3, "blocked": 4, "applied": 5, "tailored": 6, "new": 7, "rejected": 8, "closed": 9}
    shown.sort(key=lambda r: (order.get(r["status"], 9), -(_d(r.get("date_applied")) or _d(r.get("date_found")) or datetime(2000, 1, 1)).timestamp()))
    counts = defaultdict(int)
    for r in a["rows"]:
        counts[r["status"]] += 1
    tiles = [("Applied in total", len(a["applied"])), ("Still open", len(a["waiting"])), ("Interview / offer", len(a["forward"])),
             ("Rejected", len(a["rejected"])), ("Waiting on you", len(a["on_her"])), ("Tailored, not sent", counts["tailored"]),
             ("New in queue", counts["new"])]
    trs = []
    for r in shown:
        url = r.get("apply_url") or r.get("url") or ""
        link = f'<a href="{e(url)}" target="_blank" rel="noopener">{e(r["title"][:70])}</a>' if url.startswith("http") else e(r["title"][:70])
        note = (r.get("notes") or "").split(" | ")[-1][:90]
        trs.append(f'<tr data-s="{e(r["status"])}"><td><span class="s s-{e(r["status"])}">{e(r["status"])}</span></td><td>{e(r["company"][:34])}</td>'
                   f'<td class="t">{link}</td><td>{e((r.get("location") or "")[:28])}</td><td>{e(r.get("salary") or "")}</td>'
                   f'<td>{e(str(r.get("score") or ""))}</td><td>{e(r.get("date_applied") or "")}</td><td>{e(r.get("submitted_by") or "")}</td>'
                   f'<td class="t">{e(note)}</td></tr>')
    opts = "".join(f'<option value="{s}">{s} ({counts[s]})</option>' for s in order if counts[s])
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Application Tracker</title><style>{CSS}</style></head><body><main><h1>Application tracker</h1>'
            f'<div class="sub">Updated {now:%A %d %B %Y, %H:%M}. Skipped jobs are hidden.</div><div class="tiles">'
            + "".join(f'<div class="tile"><b>{v}</b><span>{e(k)}</span></div>' for k, v in tiles) + "</div>"
            + '<div class="card"><h2>What to do better</h2><ol>' + "".join(f"<li>{e(t)}</li>" for t in tips) + "</ol></div>"
            + f'<div class="bar"><input id="q" placeholder="Search company, role, location"><select id="st"><option value="">All statuses</option>{opts}</select>'
            f'<span id="n" class="sub" style="align-self:center;margin:0"></span></div><div class="wrap"><table><thead><tr><th>Status</th><th>Company</th>'
            f'<th>Role</th><th>Location</th><th>Posted pay</th><th>Score</th><th>Applied</th><th>By</th><th>Latest note</th></tr></thead><tbody>'
            + "".join(trs) + f"</tbody></table></div></main><script>{JS}</script></body></html>")


def daily(ws, days=21, out_dir="reports", read_mail=True, log=print):
    ws = ws if isinstance(ws, Workspace) else Workspace(ws)
    res = None
    if read_mail:
        try:
            from . import responses
            res = responses.read_responses(ws, days=days, log=log)
        except Exception as ex:  # the report is still worth writing from what the tracker already knows
            log(f"mail check failed: {type(ex).__name__}: {str(ex)[:200]}")
    a = analyse(ws)
    tips = recommendations(a)
    out = ws.root / out_dir
    out.mkdir(exist_ok=True)
    md = out / f"daily_{a['now']:%Y-%m-%d}.md"
    md.write_text(_md(a, res, tips), encoding="utf-8")
    page = ws.root / "tracker.html"
    page.write_text(_html(a, tips), encoding="utf-8")
    try:
        ws.export_xlsx()
    except Exception:
        pass
    return {"report": str(md), "tracker_page": str(page), "applied": len(a["applied"]), "open": len(a["waiting"]),
            "rejected": len(a["rejected"]), "forward": [f"{r['company']} - {r['title']}" for r in a["forward"]],
            "fast_rejections": len(a["fast"]), "new_mail": (res or {}).get("by_class"),
            "status_changes": [f"{c['company']}: {c['status_from']} -> {c['status_to']}" for c in ((res or {}).get("status_changes") or [])],
            "recommendations": tips}
