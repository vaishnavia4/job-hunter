"""Application folders and screening decisions."""
from .workspace import Workspace, load_json, save_json, slug

NOTES = """# {company} - {title}

- **Job ID:** {job_id}
- **ATS / application type:** {ats}
- **Posting:** {url}
- **Apply:** {apply_url}
- **Location:** {location}
- **Salary listed:** {salary}
- **Fit score:** {score} (keyword coverage {keyword_coverage})
- **Flags:** {flags}

## Why this role fits
(filled during screening/tailoring: fit %, gaps, location, salary)

## Tailoring changes
(filled during tailoring)

## Application answers given
(filled during apply)

## Status log
- {date_found}: found
"""


def prepare(ws: Workspace, job_id):
    rows = ws.load_tracker()
    row = next((r for r in rows if r["job_id"] == job_id), None)
    if not row:
        raise KeyError(f"Not in tracker: {job_id}")
    job = load_json(ws.jd / (job_id.replace(":", "_") + ".json"), {})
    folder = ws.apps / f"{slug(row['company'], 30)}_{slug(row['title'], 50)}"
    folder.mkdir(parents=True, exist_ok=True)
    save_json(folder / "job.json", job)
    (folder / "JD.md").write_text(
        f"# {job.get('title', row['title'])} - {job.get('company', row['company'])}\n\n{job.get('location', '')}\n\n"
        f"{job.get('url', row.get('url', ''))}\n\n---\n\n{job.get('description', '(description not saved)')}", encoding="utf-8")
    if not (folder / "notes.md").exists():
        (folder / "notes.md").write_text(NOTES.format(**{k: row.get(k, "") for k in row}), encoding="utf-8")
    row["folder"] = str(folder.relative_to(ws.root))
    ws.save_tracker(rows)
    return folder


def prepare_next(ws: Workspace, n):
    todo = sorted((r for r in ws.load_tracker() if r["status"] == "new"), key=lambda r: -int(r.get("score") or 0))
    return [prepare(ws, r["job_id"]) for r in todo[:n]]


def sync_decisions(ws: Workspace):
    """applications/*/decision.json -> tracker (apply -> tailored, skip -> skipped)."""
    rows = ws.load_tracker()
    by_folder = {r.get("folder", "").replace("/", "\\"): r for r in rows}
    by_folder.update({r.get("folder", "").replace("\\", "/"): r for r in rows})
    changed = []
    for d in ws.apps.iterdir() if ws.apps.exists() else []:
        dec = load_json(d / "decision.json")
        if not dec:
            continue
        rel = str(d.relative_to(ws.root))
        r = by_folder.get(rel) or by_folder.get(rel.replace("\\", "/"))
        if not r or r["status"] not in ("new", "tailored"):
            continue
        if dec.get("decision") == "skip":
            r["status"], r["notes"] = "skipped", (dec.get("reason") or "")[:300]
        else:
            r["status"] = "tailored"
            r["notes"] = f"fit {dec.get('fit')}% | gaps: {', '.join(dec.get('gaps', []))[:200]}"
            if dec.get("coverage"):
                r["keyword_coverage"] = dec["coverage"]
        changed.append(r["job_id"])
    ws.save_tracker(rows)
    ws.export_xlsx()
    return changed
