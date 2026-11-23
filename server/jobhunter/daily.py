"""`python -m jobhunter daily`: the morning check. No model tokens.

1. reads employer replies from Gmail and updates the tracker (responses.read_responses)
2. analyses outcomes: who rejected, who moved forward, which groups of applications get rejected more often and how
   fast (a rejection within two days is almost always an automatic knock-out, not a person reading the resume)
3. writes ONE page, tracker.html in the workspace: totals, shortlisted jobs with their next step, rejected jobs with
   what to do better, applications in progress, jobs waiting on the user, and the queue

The page is written as an HTML fragment (title + style + content, no <html>/<body> wrapper) so the same file opens in a
browser and can be published as a claude.ai artifact unchanged.

Rejection emails never give a reason, so "why" is inferred from patterns across all applications and is labelled
as a hint whenever a group is small.
"""
import html
import re
from collections import defaultdict
from datetime import datetime, timedelta

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
SHORTLIST = ("assessment", "interview", "offer")
NOISE_RX = re.compile(r"reset your password|password reset|verify your candidate account|security code|one[- ]time|sign[- ]in code", re.I)
NEXT_STEP = {
    "interview": "Reply to the recruiter today with two or three time slots. Then prepare: reread the posting and the "
                 "resume you sent (in the job folder), and run the interview prep for this job.",
    "assessment": "Open the email, note the deadline and complete the assessment yourself before it. Check whether the "
                  "employer restricts AI tools for it.",
    "offer": "Read the offer terms (base, bonus, start date, sponsorship wording) and compare the base with your $140,000 "
             "floor before replying.",
}


def _d(s):
    try:
        return datetime.strptime((s or "")[:10], "%Y-%m-%d")
    except ValueError:
        return None


def family(title):
    t = (title or "").lower()
    return next((name for name, rx in FAMILIES if re.search(rx, t)), "Other")


def fit_value(row):
    m = re.search(r"fit (\d{2,3})%", row.get("notes") or "")
    try:
        return int(m.group(1)) if m else int(float(row.get("score") or 0))
    except ValueError:
        return 0


def fit_band(row):
    v = fit_value(row)
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


def attention_mail(ws, now, days=14):
    """Recent employer emails that ask for something (not password/verification noise), newest first."""
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for m in ((load_json(ws.data / "responses.json", {}) or {}).get("messages") or {}).values():
        if m.get("class") in ("interview", "offer", "action_needed") and (m.get("date") or "") >= since \
                and not NOISE_RX.search(m.get("subject") or ""):
            out.append({"date": m["date"], "who": ", ".join(m.get("companies") or []) or (m.get("from") or "").split("@")[-1],
                        "subject": m.get("subject") or "", "class": m["class"]})
    return sorted(out, key=lambda x: x["date"], reverse=True)


def likely_reason(r):
    d = r.get("_days_to_reject")
    if d is not None and d <= 2:
        return "auto", f"Automatic filter: answered {'the same day' if d <= 0 else 'in ' + str(d) + (' day' if d == 1 else ' days')}"
    if r["_senior"] != "standard title":
        return "level", "Level stretch: title above your current level"
    if fit_value(r) < 65:
        return "fit", "Weak skills match for this posting"
    return "human", "Reviewed, no reason given"


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
        r["_reply_date"] = max((v for k, v in dates.get(r["job_id"], {}).items() if k in ("interview", "offer")), default="")
    rej = [r for r in applied if r["_rejected"]]
    fwd = [r for r in applied if not r["_rejected"] and (r["_interview"] or r["status"] in SHORTLIST)]
    waiting = [r for r in applied if r["status"] == "applied" and r not in fwd]
    for r in rej:
        r["_why"] = likely_reason(r)
    overall = len(rej) / len(applied) if applied else 0
    groups = []
    for key, label in (("_family", "Role type"), ("_loc", "Location"), ("_fit", "Fit"), ("_senior", "Seniority"),
                       ("_pay", "Posted pay"), ("_ctype", "Sponsor history"), ("_source", "Site")):
        g = defaultdict(list)
        for r in applied:
            g[r[key]].append(r)
        for name, v in sorted(g.items(), key=lambda kv: -len(kv[1])):
            groups.append({"dimension": label, "group": name, "applied": len(v), "rejected": sum(x["_rejected"] for x in v),
                           "forward": sum(1 for x in v if x in fwd)})
    fast = [r for r in rej if r["_days_to_reject"] is not None and r["_days_to_reject"] <= 2]
    slow = [r for r in rej if r["_days_to_reject"] is not None and r["_days_to_reject"] > 2]
    return {"now": now, "rows": rows, "applied": applied, "rejected": rej, "forward": fwd, "waiting": waiting,
            "confirmed": sum(1 for r in applied if r["_confirmed"]),
            "overall_reject_rate": overall, "groups": groups, "fast": fast, "slow": slow,
            "followups": sorted((r for r in waiting if (r["_age"] or 0) >= 14), key=lambda r: -(r["_age"] or 0)),
            "on_her": [r for r in rows if r["status"] in ("ready", "blocked")],
            "queue": [r for r in rows if r["status"] in ("tailored", "new")],
            "mail": attention_mail(ws, now)}


def recommendations(a):
    """What to do better, drawn from the rejections. Ordered by how much they should change her next applications."""
    out, applied, rej = [], a["applied"], a["rejected"]
    if not applied:
        return ["No applications yet."]
    if not rej:
        return ["No rejections recorded yet, so there is nothing to learn from them so far."]
    if not a["forward"]:
        out.append(f"No interview or assessment invitations yet from {len(applied)} applications ({len(rej)} rejections, "
                   f"{len(a['waiting'])} still open). The points below are where to look first.")
    known = len(a["fast"]) + len(a["slow"])
    if known >= MIN_N and len(a["fast"]) / known >= 0.4:
        out.append(f"{len(a['fast'])} of {known} dated rejections came within 2 days of applying. That speed means an automatic "
                   "filter, not a recruiter: usually the sponsorship answer (Yes), location, or a knock-out question. "
                   "Prefer employers with H-1B history and postings that do not say 'no sponsorship'. These fast rejections "
                   "say little about the resume itself.")
    base = a["overall_reject_rate"]
    worse = [g for g in a["groups"] if g["applied"] >= MIN_N and g["rejected"] / g["applied"] >= base + 0.12 and g["rejected"] >= 3]
    better = [g for g in a["groups"] if g["applied"] >= MIN_N and g["rejected"] / g["applied"] <= base - 0.12]
    for g in sorted(worse, key=lambda g: -(g["rejected"] / g["applied"]))[:3]:
        small = " Small sample, so treat it as a hint." if g["applied"] < 20 else ""
        advice = ("Location is your own priority, so keep applying; check these postings for hybrid or local-candidate wording."
                  if g["dimension"] == "Location" else "Apply to fewer of these, or only when fit is 80+.")
        out.append(f"{g['dimension']} '{g['group']}': {g['rejected']}/{g['applied']} rejected ({g['rejected'] / g['applied']:.0%}) "
                   f"against {base:.0%} overall. {advice}{small}")
    for g in sorted(better, key=lambda g: g["rejected"] / g["applied"])[:2]:
        out.append(f"{g['dimension']} '{g['group']}': only {g['rejected']}/{g['applied']} rejected so far "
                   f"({g['rejected'] / g['applied']:.0%} against {base:.0%}). Send more applications here."
                   + (" Small sample, so treat it as a hint." if g["applied"] < 20 else ""))
    lvl = sum(1 for r in rej if r["_why"][0] == "level")
    if lvl >= 3:
        out.append(f"{lvl} rejections were for titles above your current level (staff, principal, lead, architect). "
                   "Keep these for postings where your fit is 80+ and the years asked match yours.")
    return out


CSS = """/* layout: summary numbers first, then one section per question she asks: next steps, lessons, open, blocked, queue */
:root{--bg:#f3f5f6;--card:#ffffff;--ink:#17232b;--mut:#5d6b74;--line:#dbe1e5;--acc:#1f6f8b;--good:#1f7a52;--warn:#a8660a;--bad:#a83a34;
--good-bg:#e2f3ea;--warn-bg:#fbefd9;--bad-bg:#f8e3e1;--acc-bg:#e1eef3;--mute-bg:#eceff1;
--display:"Space Grotesk",system-ui,"Segoe UI",sans-serif;--body:Inter,system-ui,"Segoe UI",sans-serif}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#11181c;--card:#19232a;--ink:#e6edf0;--mut:#9aa9b2;--line:#2b3942;--acc:#6cc0dc;--good:#6fcf9f;--warn:#e6b25a;--bad:#ef8f88;
--good-bg:#173427;--warn-bg:#3a2c12;--bad-bg:#3d1f1d;--acc-bg:#16323d;--mute-bg:#222e36;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#11181c;--card:#19232a;--ink:#e6edf0;--mut:#9aa9b2;--line:#2b3942;--acc:#6cc0dc;--good:#6fcf9f;--warn:#e6b25a;--bad:#ef8f88;
--good-bg:#173427;--warn-bg:#3a2c12;--bad-bg:#3d1f1d;--acc-bg:#16323d;--mute-bg:#222e36;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 var(--body)}
.page{max-width:1160px;margin:0 auto;padding-block:24px 64px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:flex;flex-direction:column;gap:22px}
h1{font:600 26px/1.2 var(--display);margin:0;text-wrap:balance}
h2{font:600 18px/1.25 var(--display);margin:0;text-wrap:balance}
.sub{color:var(--mut);font-size:14px;margin:4px 0 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.tile b{display:block;font:600 28px/1.1 var(--display);font-variant-numeric:tabular-nums}
.tile span{color:var(--mut);font-size:13px}
.tile.good b{color:var(--good)}.tile.bad b{color:var(--bad)}.tile.warn b{color:var(--warn)}
.split{display:flex;height:12px;border-radius:99px;overflow:hidden;background:var(--mute-bg)}
.split i{display:block;height:100%}.split .g{background:var(--good)}.split .b{background:var(--bad)}.split .o{background:var(--acc)}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;color:var(--mut);font-size:13px;margin-top:6px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}
section{display:flex;flex-direction:column;gap:12px;min-width:0}
.head{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px}
.count{font:600 14px var(--display);color:var(--mut);font-variant-numeric:tabular-nums}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;min-width:0}
.box ol,.box ul{margin:0;padding-left:20px;display:flex;flex-direction:column;gap:8px;max-width:86ch}
.empty{color:var(--mut)}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}
table{border-collapse:collapse;width:100%;font:14px/1.4 var(--body)}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
th{color:var(--mut);font-weight:600;font-size:12px;letter-spacing:.04em;text-transform:uppercase;white-space:nowrap;background:var(--card)}
td.n{white-space:nowrap;font-variant-numeric:tabular-nums}td.w{min-width:220px}
a{color:var(--acc)}a:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.pill{display:inline-block;padding:1px 9px;border-radius:99px;font-size:12px;font-weight:600;white-space:nowrap;background:var(--mute-bg);color:var(--mut)}
.pill.good{background:var(--good-bg);color:var(--good)}.pill.bad{background:var(--bad-bg);color:var(--bad)}
.pill.warn{background:var(--warn-bg);color:var(--warn)}.pill.acc{background:var(--acc-bg);color:var(--acc)}
.rate{display:flex;align-items:center;gap:8px;min-width:140px}.rate u{flex:1;height:7px;border-radius:99px;background:var(--mute-bg);overflow:hidden;text-decoration:none}
.rate u i{display:block;height:100%;background:var(--bad)}
.search{font:inherit;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);width:100%;max-width:420px}
details>summary{cursor:pointer;color:var(--acc);font-weight:600;padding:4px 0}
.step{margin:4px 0 0;color:var(--ink);max-width:80ch}
"""

JS = """const q=document.getElementById('q');
if(q)q.addEventListener('input',()=>{const t=q.value.trim().toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>{r.hidden=!!t&&!r.textContent.toLowerCase().includes(t)})});"""


def _html(a, tips):
    e = html.escape
    now = a["now"]
    sent, rej, fwd, opn = len(a["applied"]), len(a["rejected"]), len(a["forward"]), len(a["waiting"])
    back = rej + fwd

    def link(r, n=64):
        url = r.get("apply_url") or r.get("url") or ""
        t = e((r.get("title") or "")[:n])
        return f'<a href="{e(url)}" target="_blank" rel="noopener">{t}</a>' if url.startswith("http") else t

    def table(heads, rows_html, empty):
        if not rows_html:
            return f'<div class="box empty">{e(empty)}</div>'
        return ('<div class="wrap"><table><thead><tr>' + "".join(f"<th>{e(h)}</th>" for h in heads) + "</tr></thead><tbody>"
                + "".join(rows_html) + "</tbody></table></div>")

    def pct(k):
        return f"{(k / sent * 100) if sent else 0:.1f}%"

    tiles = [("Applications sent", sent, ""), ("Came back with an answer", back, ""), ("Shortlisted", fwd, "good"),
             ("Rejected", rej, "bad"), ("In progress, no answer yet", opn, ""), ("Receipt confirmed by email", a["confirmed"], ""),
             ("Waiting on you", len(a["on_her"]), "warn"), ("Not sent yet", len(a["queue"]), "")]
    P = ['<meta charset="utf-8">', "<title>Application Tracker</title>",
         '<meta name="claude:viewport-fit-sides" content="cover">',
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=Space+Grotesk:wght@500;600&display=swap">',
         f"<style>{CSS}</style>", '<div class="page">',
         f'<header><h1>Application tracker</h1><p class="sub">Updated {now:%A %d %B %Y, %H:%M}. Refreshed every day at 10:00 from your inbox and the job tracker.</p></header>',
         '<section aria-label="Totals"><div class="tiles">'
         + "".join(f'<div class="tile {c}"><b>{v}</b><span>{e(k)}</span></div>' for k, v, c in tiles) + "</div>"
         + f'<div><div class="split" role="img" aria-label="Of {sent} sent: {fwd} shortlisted, {rej} rejected, {opn} in progress">'
           f'<i class="g" style="width:{pct(fwd)}"></i><i class="b" style="width:{pct(rej)}"></i><i class="o" style="width:{pct(opn)}"></i></div>'
           f'<div class="legend"><span><i style="background:var(--good)"></i>Shortlisted {fwd}</span><span><i style="background:var(--bad)"></i>Rejected {rej}</span>'
           f'<span><i style="background:var(--acc)"></i>In progress {opn}</span></div></div></section>',
         '<input id="q" class="search" type="search" placeholder="Search every table: company, role, location" aria-label="Search every table">']

    # ---- shortlisted: what needs to be done
    P.append(f'<section id="shortlisted"><div class="head"><h2>Shortlisted: what you need to do</h2><span class="count">{fwd}</span></div>')
    if a["forward"]:
        rows_html = []
        for r in sorted(a["forward"], key=lambda r: r.get("_reply_date") or "", reverse=True):
            st = r["status"] if r["status"] in NEXT_STEP else "interview"
            rows_html.append(f'<tr><td><span class="pill good">{e(st)}</span></td><td>{e(r["company"][:36])}</td><td class="w">{link(r)}'
                             f'<p class="step">{e(NEXT_STEP[st])}</p></td><td class="n">{e(r.get("_reply_date") or "")}</td>'
                             f'<td class="n">{e(r.get("date_applied") or "")}</td></tr>')
        P.append(table(["Stage", "Company", "Role and next step", "Heard back", "Applied"], rows_html, ""))
    else:
        P.append('<div class="box empty">No interview, assessment or offer yet. When an employer invites you, the job appears here '
                 "with the exact next step (reply with times, complete the assessment, review the offer).</div>")
    if a["mail"]:
        rows_html = [f'<tr><td class="n">{e(m["date"])}</td><td>{e(m["who"][:40])}</td><td class="w">{e(m["subject"][:120])}</td></tr>' for m in a["mail"][:12]]
        P.append('<div class="head"><h2 style="font-size:15px">Employer emails that ask you for something</h2></div>'
                 + table(["Date", "From", "Subject"], rows_html, ""))
    P.append("</section>")

    # ---- rejected: what can be done better
    P.append(f'<section id="rejected"><div class="head"><h2>Rejected: what can be done better</h2><span class="count">{rej} of {sent} '
             f'({a["overall_reject_rate"]:.0%})</span></div>')
    P.append('<div class="box"><ol>' + "".join(f"<li>{e(t)}</li>" for t in tips) + "</ol></div>")
    grp = [g for g in a["groups"] if g["applied"] >= MIN_N]
    if rej and grp:
        rows_html = []
        for g in grp:
            rate = g["rejected"] / g["applied"]
            rows_html.append(f'<tr><td>{e(g["dimension"])}</td><td>{e(g["group"])}</td><td class="n">{g["applied"]}</td><td class="n">{g["rejected"]}</td>'
                             f'<td><div class="rate"><span class="n">{rate:.0%}</span><u><i style="width:{rate * 100:.0f}%"></i></u></div></td></tr>')
        P.append("<details><summary>Rejection rate by kind of job</summary>"
                 + table(["Compared by", "Group", "Sent", "Rejected", "Rejection rate"], rows_html, "")
                 + '<p class="sub">Employers do not give a reason. These are patterns across your applications, and groups under '
                   f"{MIN_N} applications are left out.</p></details>")
    cls = {"auto": "warn", "level": "acc", "fit": "bad", "human": ""}
    rows_html = []
    for r in sorted(a["rejected"], key=lambda r: r.get("date_applied") or "", reverse=True):
        k, why = r["_why"]
        rows_html.append(f'<tr><td>{e(r["company"][:34])}</td><td class="w">{link(r)}</td><td><span class="pill {cls[k]}">{e(why)}</span></td>'
                         f'<td class="n">{e(r.get("date_applied") or "")}</td><td class="n">{fit_value(r)}</td><td>{e(r["_loc"])}</td></tr>')
    P.append(table(["Company", "Role", "Likely reason", "Applied", "Fit", "Location"], rows_html, "No rejections recorded."))
    P.append("</section>")

    # ---- waiting on her
    P.append(f'<section id="waiting"><div class="head"><h2>Waiting on you</h2><span class="count">{len(a["on_her"])}</span></div>')
    rows_html = [f'<tr><td><span class="pill warn">{e(r["status"])}</span></td><td>{e(r["company"][:34])}</td><td class="w">{link(r)}</td>'
                 f'<td class="w">{e((r.get("notes") or "").split(" | ")[-1][:170])}</td></tr>' for r in a["on_her"]]
    P.append(table(["Status", "Company", "Role", "What is needed"], rows_html, "Nothing is waiting on you."))
    P.append("</section>")

    # ---- in progress
    P.append(f'<section id="progress"><div class="head"><h2>In progress: sent, no answer yet</h2><span class="count">{opn}</span></div>')
    rows_html = []
    for r in sorted(a["waiting"], key=lambda r: r.get("date_applied") or "", reverse=True):
        age = r["_age"]
        flag = '<span class="pill warn">follow up</span>' if (age or 0) >= 14 else ('<span class="pill acc">received</span>' if r["_confirmed"] else "")
        rows_html.append(f'<tr><td>{e(r["company"][:34])}</td><td class="w">{link(r)}</td><td>{e((r.get("location") or "")[:30])}</td>'
                         f'<td class="n">{e(r.get("salary") or "")}</td><td class="n">{e(r.get("date_applied") or "")}</td>'
                         f'<td class="n">{"" if age is None else age}</td><td>{flag}</td></tr>')
    P.append(table(["Company", "Role", "Location", "Posted pay", "Applied", "Days", ""], rows_html, "No open applications."))
    P.append("</section>")

    # ---- queue
    P.append(f'<section id="queue"><div class="head"><h2>Not sent yet</h2><span class="count">{len(a["queue"])}</span></div>')
    rows_html = [f'<tr><td><span class="pill">{e("resume ready" if r["status"] == "tailored" else "new")}</span></td><td>{e(r["company"][:34])}</td>'
                 f'<td class="w">{link(r)}</td><td>{e((r.get("location") or "")[:30])}</td><td class="n">{e(r.get("salary") or "")}</td>'
                 f'<td class="n">{e(str(r.get("score") or ""))}</td></tr>'
                 for r in sorted(a["queue"], key=lambda r: (r["status"] != "tailored", -float(r.get("score") or 0)))]
    P.append("<details><summary>Show the queue</summary>"
             + table(["Stage", "Company", "Role", "Location", "Posted pay", "Score"], rows_html, "The queue is empty.") + "</details>")
    P.append("</section></div>")
    P.append(f"<script>{JS}</script>")
    return "\n".join(P) + "\n"


def daily(ws, days=21, read_mail=True, log=print):
    ws = ws if isinstance(ws, Workspace) else Workspace(ws)
    res = None
    if read_mail:
        try:
            from . import responses
            res = responses.read_responses(ws, days=days, log=log)
        except Exception as ex:  # the page is still worth writing from what the tracker already knows
            log(f"mail check failed: {type(ex).__name__}: {str(ex)[:200]}")
    a = analyse(ws)
    tips = recommendations(a)
    page = ws.root / "tracker.html"
    page.write_text(_html(a, tips), encoding="utf-8")
    return {"tracker_page": str(page), "sent": len(a["applied"]), "came_back": len(a["rejected"]) + len(a["forward"]),
            "shortlisted": [f"{r['company']} - {r['title']}" for r in a["forward"]], "rejected": len(a["rejected"]),
            "in_progress": len(a["waiting"]), "waiting_on_user": len(a["on_her"]), "not_sent": len(a["queue"]),
            "fast_rejections": len(a["fast"]), "new_mail": (res or {}).get("by_class"),
            "status_changes": [f"{c['company']}: {c['status_from']} -> {c['status_to']}" for c in ((res or {}).get("status_changes") or [])],
            "recommendations": tips}
