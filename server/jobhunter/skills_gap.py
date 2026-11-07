"""Market demand vs. the user's skills -> skills_gap.md + 'Skills Gap' sheet in tracker.xlsx."""
import glob
from collections import Counter, defaultdict

from .workspace import Workspace, load_json, today

GENERIC = ("", "Official docs, free vendor academies (Microsoft Learn, Google Skillshop, Coursera audit, LinkedIn Learning) "
               "or a short course; then practise on a real problem.", "A small public project/write-up (GitHub, portfolio or LinkedIn post).")


def run(ws: Workspace):
    vocab = (ws.cfg("skills", {}) or {}).get("vocab", {})
    learning = ws.cfg("learning", {}) or {}
    jobs = {}
    for f in sorted(glob.glob(str(ws.jobs / "*_all_matches.json"))):
        for j in load_json(f, []):
            jobs[j["job_id"]] = j
    total = len(jobs)
    demand = Counter()
    for j in jobs.values():
        for k in j.get("keywords_have", []) + j.get("keywords_secondary", []) + j.get("keywords_gap", []):
            demand[k] += 1
    noted = defaultdict(list)
    n_dec = 0
    for d in ws.apps.iterdir() if ws.apps.exists() else []:
        dec = load_json(d / "decision.json")
        if dec:
            n_dec += 1
            if dec.get("decision") == "apply":
                for g in dec.get("gaps", []):
                    noted[g].append(d.name.split("_")[0])

    rows = []
    for k, cnt in demand.most_common():
        have = (vocab.get(k) or {}).get("have", False)
        status = "HAVE" if have is True else "LIGHT (secondary)" if have == "secondary" else "GAP"
        pct = cnt / max(1, total)
        prio = "-" if status == "HAVE" else ("HIGH" if pct >= 0.15 else "MEDIUM" if pct >= 0.06 else "LOW")
        if status.startswith("LIGHT") and prio == "HIGH":
            prio = "HIGH (deepen)"
        why, path, proj = learning.get(k) or GENERIC
        rows.append({"skill": k, "jobs": cnt, "pct": f"{pct:.0%}", "status": status, "priority": prio,
                     "why": why, "learn": path, "proof": proj})
    order = {"HIGH": 0, "HIGH (deepen)": 0, "MEDIUM": 1, "LOW": 2, "-": 3}
    gaps = sorted([r for r in rows if r["status"] != "HAVE"], key=lambda r: (order[r["priority"]], -r["jobs"]))
    have = [r for r in rows if r["status"] == "HAVE"]

    md = [f"# Skills Gap & Market Demand - updated {today()}", "",
          f"Based on **{total} matching job postings** from the daily searches, plus gaps noted on {n_dec} screened applications.", "",
          "*Demand* = share of matching jobs mentioning the skill. Close HIGH gaps first. Add a skill to the master resume "
          "only after you have genuinely used it (work or a real project).", "",
          "## Gaps to close (ranked)", "", "| Priority | Skill | Demand | Status | Why it matters | Learn | Proof project |",
          "|---|---|---|---|---|---|---|"]
    md += [f"| {r['priority']} | **{r['skill']}** | {r['jobs']} jobs ({r['pct']}) | {r['status']} | {r['why']} | {r['learn']} | {r['proof']} |" for r in gaps]
    md += ["", "## Gaps noted on jobs being applied to", ""]
    md += [f"- {g}: *{', '.join(sorted(set(c)))}*" for g, c in sorted(noted.items(), key=lambda x: -len(x[1]))] or ["- none yet"]
    md += ["", "## Strengths the market wants (keep these prominent)", "",
           ", ".join(f"{r['skill']} ({r['pct']})" for r in have[:25]) or "-", ""]
    (ws.root / "skills_gap.md").write_text("\n".join(md), encoding="utf-8")

    try:
        from openpyxl import Workbook, load_workbook
        from openpyxl.styles import Font, PatternFill
        wb = load_workbook(ws.tracker_xlsx) if ws.tracker_xlsx.exists() else Workbook()
        if "Skills Gap" in wb.sheetnames:
            del wb["Skills Gap"]
        sh = wb.create_sheet("Skills Gap")
        hdr = ["priority", "skill", "jobs", "pct", "status", "why", "learn", "proof"]
        sh.append(hdr)
        for c in sh[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="305496")
        fills = {"HIGH": "F8CBAD", "HIGH (deepen)": "F8CBAD", "MEDIUM": "FFE699", "LOW": "E2EFDA"}
        for r in gaps + have:
            sh.append([r[k] for k in hdr])
            if r["priority"] in fills:
                sh.cell(sh.max_row, 1).fill = PatternFill("solid", fgColor=fills[r["priority"]])
        for col, w in zip("ABCDEFGH", [14, 28, 7, 7, 16, 50, 60, 60]):
            sh.column_dimensions[col].width = w
        wb.save(ws.tracker_xlsx)
    except Exception:
        pass
    return {"jobs_analysed": total, "gap_skills": len(gaps),
            "top_gaps": [f"{r['skill']} ({r['pct']}, {r['priority']})" for r in gaps[:10]]}
