"""Workspace layout, JSON helpers and the application tracker.

Workspace layout (created by init_workspace):
  CLAUDE.md                     rules Claude follows in this workspace
  profile/profile.json          applicant answers (contact, work auth, EEO, salary, policy)
  profile/master_resume.json    the ONLY source of resume facts
  profile/credentials.json      local-only ATS passwords (gitignored)
  profile/answer_bank.json      answers to application questions (setup + learned corrections)
  config/search.json            titles, locations, scoring, blockers
  config/skills.json            skill vocabulary with have-flags (true | "secondary" | false)
  config/companies.json         job boards to search
  config/learning.json          optional learning paths per skill
  data/tracker.csv              source of truth;  tracker.xlsx = formatted export
  jobs/                         daily matches + saved job descriptions (jobs/jd/)
  applications/<Company_Role>/  one folder per application
"""
import csv
import json
import os
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = PLUGIN_ROOT / "templates"

TRACKER_FIELDS = [
    "job_id", "date_found", "company", "title", "location", "ats", "url", "apply_url",
    "score", "keyword_coverage", "salary", "flags", "status", "folder",
    "date_applied", "submitted_by", "follow_up", "notes",
]
STATUSES = ["new", "tailored", "ready", "applied", "interview", "offer", "rejected", "skipped", "blocked", "error"]
COLORS = {"new": "DDEBF7", "tailored": "FFF2CC", "ready": "FCE4D6", "applied": "C6EFCE", "interview": "A9D08E",
          "offer": "70AD47", "rejected": "F4B6B6", "skipped": "D9D9D9", "blocked": "FFC7CE", "error": "FFC7CE"}


def resolve_workspace(ws=None) -> Path:
    ws = ws or os.environ.get("JOB_HUNTER_WORKSPACE") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    # ${CLAUDE_PROJECT_DIR} may arrive literally if the host didn't substitute it
    if "${" in str(ws):
        ws = os.getcwd()
    return Path(ws).expanduser().resolve()


class Workspace:
    def __init__(self, root=None):
        self.root = resolve_workspace(root)
        self.profile_dir = self.root / "profile"
        self.config = self.root / "config"
        self.data = self.root / "data"
        self.jobs = self.root / "jobs"
        self.jd = self.jobs / "jd"
        self.apps = self.root / "applications"
        self.tracker_csv = self.data / "tracker.csv"
        self.tracker_xlsx = self.root / "tracker.xlsx"

    # ---------- files
    def ensure_dirs(self):
        for d in (self.profile_dir, self.config, self.data, self.jobs, self.jd, self.apps):
            d.mkdir(parents=True, exist_ok=True)

    def is_initialized(self):
        return (self.profile_dir / "profile.json").exists()

    def profile(self):
        return load_json(self.profile_dir / "profile.json", {})

    def master(self):
        return load_json(self.profile_dir / "master_resume.json", {})

    def cfg(self, name, default=None):
        """config/<name>.json, falling back to the plugin's template default."""
        p = self.config / f"{name}.json"
        if p.exists():
            return load_json(p)
        t = TEMPLATES / "config" / f"{name}.json"
        return load_json(t, default)

    # ---------- tracker
    def load_tracker(self):
        if not self.tracker_csv.exists():
            return []
        with open(self.tracker_csv, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def save_tracker(self, rows):
        self.data.mkdir(parents=True, exist_ok=True)
        with open(self.tracker_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=TRACKER_FIELDS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in TRACKER_FIELDS})

    def set_fields(self, job_id, **fields):
        rows = self.load_tracker()
        hit = [r for r in rows if r["job_id"] == job_id]
        if not hit:
            raise KeyError(f"job_id not in tracker: {job_id}")
        for r in hit:
            r.update({k: str(v) for k, v in fields.items()})
            if fields.get("status") == "applied" and not r.get("date_applied"):
                r["date_applied"] = today()
        self.save_tracker(rows)
        self.export_xlsx()
        return hit[0]

    def stats(self):
        return dict(Counter(r["status"] for r in self.load_tracker()).most_common())

    def export_xlsx(self):
        try:
            from openpyxl import Workbook, load_workbook
            from openpyxl.styles import Font, PatternFill
            from openpyxl.utils import get_column_letter
        except ImportError:
            return "openpyxl missing - xlsx not written"
        rows = self.load_tracker()
        keep = None
        if self.tracker_xlsx.exists():  # keep extra sheets (e.g. Skills Gap)
            try:
                keep = load_workbook(self.tracker_xlsx)
            except Exception:
                keep = None
        wb = keep or Workbook()
        for name in ("Applications", "Summary"):
            if name in wb.sheetnames:
                del wb[name]
        if not keep:
            del wb[wb.sheetnames[0]]
        ws = wb.create_sheet("Applications", 0)
        ws.append(TRACKER_FIELDS)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="305496")
        st_col = TRACKER_FIELDS.index("status") + 1
        url_col = TRACKER_FIELDS.index("url") + 1
        for r in rows:
            ws.append([r.get(k, "") for k in TRACKER_FIELDS])
            row = ws.max_row
            if r.get("status") in COLORS:
                ws.cell(row, st_col).fill = PatternFill("solid", fgColor=COLORS[r["status"]])
            if r.get("url"):
                ws.cell(row, url_col).hyperlink = r["url"]
                ws.cell(row, url_col).style = "Hyperlink"
        widths = {"job_id": 18, "company": 20, "title": 42, "location": 28, "url": 30, "flags": 26, "notes": 40, "folder": 30}
        for i, k in enumerate(TRACKER_FIELDS, 1):
            ws.column_dimensions[get_column_letter(i)].width = widths.get(k, 12)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        s = wb.create_sheet("Summary", 1)
        s.append(["status", "count"])
        for k, v in Counter(r.get("status", "") for r in rows).most_common():
            s.append([k, v])
        try:
            wb.save(self.tracker_xlsx)
            return "ok"
        except PermissionError:
            return "tracker.xlsx is open in Excel - CSV is up to date"


# ---------- helpers
def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def today():
    return datetime.now().strftime("%Y-%m-%d")


def slug(s, maxlen=60):
    s = re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_")
    return s[:maxlen].rstrip("_")


def init_workspace(root=None, overwrite=False):
    """Create the folder layout and copy templates (never overwrites existing files unless overwrite=True)."""
    ws = Workspace(root)
    ws.ensure_dirs()
    created = []
    copies = {
        TEMPLATES / "CLAUDE.md": ws.root / "CLAUDE.md",
        TEMPLATES / "gitignore.txt": ws.root / ".gitignore",
        TEMPLATES / "profile.template.json": ws.profile_dir / "profile.json",
        TEMPLATES / "master_resume.template.json": ws.profile_dir / "master_resume.json",
        TEMPLATES / "credentials.template.json": ws.profile_dir / "credentials.json",
        TEMPLATES / "answer_bank.template.json": ws.profile_dir / "answer_bank.json",
    }
    for name in ("search", "companies", "skills", "learning", "skills_library"):
        copies[TEMPLATES / "config" / f"{name}.json"] = ws.config / f"{name}.json"
    for src, dst in copies.items():
        if src.exists() and (overwrite or not dst.exists()):
            shutil.copyfile(src, dst)
            created.append(str(dst.relative_to(ws.root)))
    return ws, created
