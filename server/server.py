"""job-hunter MCP server (stdio).  Tools operate on a WORKSPACE folder:
explicit `workspace` arg > $JOB_HUNTER_WORKSPACE > $CLAUDE_PROJECT_DIR > cwd.
"""
import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # mcp SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server  # noqa: E402
except ImportError:  # mcp SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # noqa: E402

from jobhunter import answers, applications, extract, resume, search, skills_gap  # noqa: E402
from jobhunter.workspace import Workspace, init_workspace as _init  # noqa: E402

mcp = _Server("jobs")


def _ws(workspace):
    return Workspace(workspace or None)


def _j(obj):
    return json.dumps(obj, indent=2, default=str)


@contextlib.contextmanager
def _quiet():
    """Never let library prints corrupt the stdio protocol."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


@mcp.tool()
def init_workspace(workspace: str = "") -> str:
    """Create the job-hunter workspace layout (profile/, config/, data/, jobs/, applications/, CLAUDE.md) from templates.
    Never overwrites existing files. Run once per person, in the folder they use for their job search."""
    with _quiet():
        ws, created = _init(workspace or None)
    return _j({"workspace": str(ws.root), "created": created,
               "next": "Fill profile/profile.json, profile/master_resume.json and config/*.json (see the job-setup skill)."})


@mcp.tool()
def extract_resume_text(path: str) -> str:
    """Return the plain text of a resume file (.docx, .pdf, .txt, .md) so it can be parsed into master_resume.json."""
    return extract.extract_text(path)


@mcp.tool()
def search_jobs(workspace: str = "", limit: int = 0, analyze_only: bool = False, dry_run: bool = False) -> str:
    """Search all configured job boards, score jobs against the workspace config, add the top new ones to the tracker
    (status=new) and refresh the skills-gap report. analyze_only refreshes market data without adding jobs;
    dry_run touches nothing. Takes 1-3 minutes."""
    with _quiet():
        res = search.search(_ws(workspace), limit=limit or None, analyze_only=analyze_only, dry_run=dry_run)
    return _j(res)


@mcp.tool()
def validate_boards(workspace: str = "") -> str:
    """Check which job boards in config/companies.json respond (use after editing the company list)."""
    with _quiet():
        return _j(search.validate_boards(_ws(workspace)))


@mcp.tool()
def list_jobs(workspace: str = "", status: str = "") -> str:
    """List tracked jobs (optionally filtered by status: new, tailored, ready, applied, interview, offer, rejected, skipped, blocked)."""
    rows = _ws(workspace).load_tracker()
    keep = ["job_id", "status", "score", "company", "title", "location", "ats", "apply_url", "salary", "folder", "notes"]
    return _j([{k: r.get(k, "") for k in keep} for r in rows if not status or r["status"] == status])


@mcp.tool()
def prepare_application(workspace: str = "", job_id: str = "", next_n: int = 0) -> str:
    """Create applications/<Company>_<Role>/ with job.json, JD.md and notes.md for a tracked job
    (or for the next `next_n` highest-scoring 'new' jobs)."""
    ws = _ws(workspace)
    with _quiet():
        folders = applications.prepare_next(ws, next_n) if next_n else [applications.prepare(ws, job_id)]
    return _j([str(f) for f in folders])


@mcp.tool()
def build_resume(workspace: str = "", folder: str = "") -> str:
    """Render tailored.json (+ cover_letter.md) in an application folder to DOCX/PDF, run the truth check
    (no skill/cert/employer/date not in master_resume.json; single-title headline), check page count (<=2)
    and write match_report.md. Fix every issue it reports, then rebuild."""
    with _quiet():
        return _j(resume.build(_ws(workspace), folder))


@mcp.tool()
def update_status(workspace: str = "", job_id: str = "", fields: dict | None = None) -> str:
    """Update tracker fields for a job, e.g. {"status": "applied", "submitted_by": "user", "notes": "..."}.
    status=applied stamps date_applied automatically."""
    with _quiet():
        return _j(_ws(workspace).set_fields(job_id, **(fields or {})))


@mcp.tool()
def sync_decisions(workspace: str = "") -> str:
    """Copy screening decisions (applications/*/decision.json: apply -> tailored, skip -> skipped) into the tracker."""
    with _quiet():
        return _j({"synced": applications.sync_decisions(_ws(workspace))})


@mcp.tool()
def skills_gap_report(workspace: str = "") -> str:
    """Rebuild skills_gap.md (market demand vs. the user's skills, with learning paths) and return the top gaps."""
    with _quiet():
        return _j(skills_gap.run(_ws(workspace)))


@mcp.tool()
def stats(workspace: str = "") -> str:
    """Tracker status counts plus the workspace path and approval progress."""
    ws = _ws(workspace)
    pol = (ws.profile() or {}).get("submit_policy", {})
    return _j({"workspace": str(ws.root), "initialized": ws.is_initialized(), "counts": ws.stats(),
               "approvals": f"{pol.get('approved_so_far', 0)}/{pol.get('approve_first_n', 10)}"})


@mcp.tool()
def lookup_answer(workspace: str = "", question_text: str = "", options: list[str] | None = None) -> str:
    """Answer an application-form question from the user's answer bank. Call this for EVERY question before filling it.
    Pass the exact label text and, for dropdowns/radios, the list of visible option texts.
    status: ok (fill `option` if given, else `answer`) | ASK_USER (unknown: ask, then record_answer) |
    AMBIGUOUS_OPTIONS (several options fit: ask) | USER_MUST_TICK (legal attestation: user ticks it) |
    GENERATE_ESSAY (write a truthful job-specific answer). Learned user corrections always win."""
    return _j(answers.lookup(_ws(workspace), question_text, options or None))


@mcp.tool()
def record_answer(workspace: str = "", question_text: str = "", answer: str = "", source: str = "user_correction",
                  note: str = "", options_hint: list[str] | None = None) -> str:
    """Save an answer so the question is never asked (or gotten wrong) again. source: user_correction (the user changed
    or told you the answer), user_filled (the user filled it on the page themselves), setup (interview default).
    options_hint = regexes, most specific first, used to pick the right dropdown option (e.g. ["south asian","indian"])."""
    return _j(answers.record(_ws(workspace), question_text, answer, source, note, options_hint))


@mcp.tool()
def verify_form(workspace: str = "", fields: list[dict] | None = None) -> str:
    """Before saying 'ready to submit': read back EVERY field on the page as {label, value, options?, required?, kind?}
    (kind='file' for uploads, value = file name) and check them against the answer bank. Returns mismatches,
    missing_required, unknown (filled but not in the bank - confirm + record), user_must_tick, file_problems
    (e.g. resume in the Cover Letter box) and pass=true/false. Fix and re-verify until pass."""
    return _j(answers.verify_form(_ws(workspace), fields or []))


if __name__ == "__main__":
    mcp.run()
