import argparse
import json
import sys

from .core import run


def _bool(v):
    return str(v).strip().lower() not in ("0", "false", "no", "off", "n")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m jobhunter.fill",
                                 description="Fill a job application with Playwright (no model tokens). Never submits.")
    ap.add_argument("--workspace", "-w", default=None, help="job-hunter workspace (default: env/cwd)")
    ap.add_argument("--folder", required=True, help="application folder, e.g. applications/Company_Role (relative to workspace)")
    ap.add_argument("--url", default=None, help="override the apply URL from job.json")
    ap.add_argument("--headed", nargs="?", const="true", default="true",
                    help="show the browser (default true; '--headed false' = headless)")
    ap.add_argument("--profile-dir", default=None, help="persistent browser profile (default <workspace>/.browser-profile)")
    ap.add_argument("--keep-open", action="store_true", help="leave the browser open until you close it (for review/submit)")
    ap.add_argument("--max-steps", type=int, default=12, help="Workday: max steps to walk")
    a = ap.parse_args(argv)
    summary = run(a.workspace, a.folder, url=a.url, headed=_bool(a.headed), profile_dir=a.profile_dir,
                  keep_open=a.keep_open, max_steps=a.max_steps)
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
    return 0 if not summary.get("fatal") else 1


if __name__ == "__main__":
    raise SystemExit(main())
