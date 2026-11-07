"""Browser launch, ATS detection, shared context object and the run() entry point."""
import json
import re
import sys
from pathlib import Path

from .. import answers as A
from ..workspace import Workspace, load_json


def detect_ats(url):
    u = (url or "").lower()
    if "greenhouse.io" in u or "gh_jid=" in u:
        return "greenhouse"
    if "ashbyhq.com" in u:
        return "ashby"
    if "lever.co" in u:
        return "lever"
    if "myworkdayjobs.com" in u or "myworkdaysite.com" in u or "workday" in u:
        return "workday"
    return "unknown"


class Ctx:
    """Everything a filler needs, plus the report being built."""

    def __init__(self, ws, folder, job, page, url):
        self.ws, self.folder, self.job, self.page, self.url = ws, folder, job, page, url
        self.profile = ws.profile() or {}
        self.master = ws.master() or {}
        self.creds = load_json(ws.profile_dir / "credentials.json", {}) or {}
        self.resume = next(iter(sorted(folder.glob("Resume_*.pdf"))), None)
        self.cover = next(iter(sorted(folder.glob("CoverLetter_*.pdf"))), None)
        self.steps, self.filled, self.unknown, self.problems = [], [], [], []
        self.auth_state = None  # Workday: signed_in | needs_user_checkbox | captcha | signin_failed | ...
        self.readback = {}  # step name -> [{label, value, required, error}] read from the page after filling

    # ---- reporting
    def step(self, s):
        if s not in self.steps:
            self.steps.append(s)

    def problem(self, s):
        if s not in self.problems:
            self.problems.append(s)

    def fill_record(self, label, value):
        v = str(value)
        self.filled.append({"label": label if len(label) < 90 else label[:87] + "...", "value": v if len(v) < 160 else v[:157] + "..."})

    def unknown_q(self, label, res=None, options=None):
        st = (res or {}).get("status", "ASK_USER")
        if st == A.TICK:
            self.problem(f"user must tick: {label}")
            return
        e = {"label": label, "status": st}
        if (res or {}).get("note"):
            e["note"] = res["note"][:200]
        if options and len(options) <= 25:
            e["options"] = options
        if not any(u["label"] == label for u in self.unknown):
            self.unknown.append(e)

    # ---- answers
    def answer(self, label, options=None):
        """Returns (value, res). value None means: leave blank (already recorded in the report)."""
        label = re.sub(r"\s+", " ", label or "").strip()
        res = A.lookup(self.ws, label, options or None)
        if res["status"] != A.STATUS_OK:
            self.unknown_q(label, res, options)
            return None, res
        val = res.get("option") if options else res.get("answer")
        if options and not val:
            self.unknown_q(label, res, options)
            return None, res
        return val, res

    def p(self, *path, default=""):
        cur = self.profile
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
        return cur if cur not in (None, "") else default

    @property
    def source(self):
        return (self.job.get("source") or "").lower()


def run(workspace, folder, url=None, headed=True, profile_dir=None, keep_open=False, max_steps=12):
    from playwright.sync_api import sync_playwright
    ws = Workspace(workspace)
    folder = Path(folder)
    if not folder.is_absolute():
        folder = ws.root / folder
    job = load_json(folder / "job.json", {}) or {}
    url = url or job.get("apply_url") or job.get("url")
    summary = {"ats": detect_ats(url), "steps_completed": [], "filled": [], "unknown_questions": [], "problems": [], "screenshot": None}
    if not url:
        summary.update(fatal=True, problems=["no url (job.json has no url/apply_url and --url not given)"])
        return summary
    pdir = Path(profile_dir) if profile_dir else ws.root / ".browser-profile"
    pdir.mkdir(parents=True, exist_ok=True)
    shot = folder / "fill_screenshot.png"
    from . import ashby, greenhouse, lever, workday
    modules = {"greenhouse": greenhouse, "ashby": ashby, "lever": lever, "workday": workday}
    with sync_playwright() as pw:
        ctxb = pw.chromium.launch_persistent_context(str(pdir), headless=not headed, viewport={"width": 1366, "height": 900},
                                                     args=["--disable-blink-features=AutomationControlled"])
        page = ctxb.pages[0] if ctxb.pages else ctxb.new_page()
        ctx = Ctx(ws, folder, job, page, url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
            ats = detect_ats(page.url) if summary["ats"] == "unknown" else summary["ats"]
            if ats == "unknown":  # company site embedding an ATS iframe
                for fr in page.frames:
                    if detect_ats(fr.url) != "unknown":
                        ats = detect_ats(fr.url)
                        ctx.problem(f"form is in an iframe ({fr.url}); re-run with that URL")
                        break
            summary["ats"] = ats
            if ats == "unknown":
                ctx.problem("unsupported ATS - fill manually or have Claude drive it")
            else:
                if not ctx.resume:
                    ctx.problem("no Resume_*.pdf in application folder")
                if ats == "workday":
                    workday.fill(ctx, max_steps=max_steps)
                else:
                    modules[ats].fill(ctx)
        except Exception as e:  # never lose the report
            ctx.problem(f"{type(e).__name__}: {str(e)[:300]}")
        try:
            page.screenshot(path=str(shot), full_page=True)
            summary["screenshot"] = str(shot)
        except Exception:
            pass
        summary.update(steps_completed=ctx.steps, filled=ctx.filled, unknown_questions=ctx.unknown, problems=ctx.problems)
        if ctx.auth_state:
            summary["auth_state"] = ctx.auth_state
        if ctx.readback:
            summary["readback"] = ctx.readback
        try:
            (folder / "fill_report.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        if keep_open and headed:
            print("browser left open; close it when done", file=sys.stderr)
            try:
                page.wait_for_event("close", timeout=0)
            except Exception:
                pass
        try:
            ctxb.close()
        except Exception:
            pass
    return summary
