"""Tailoring levels, change report and the number truth check (no Word/PDF needed).

Run: python tests/test_resume_levels.py   (from server/)
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobhunter.resume import LEVELS, change_report, set_tailor_level, tailor_level, truth_check  # noqa: E402
from jobhunter.workspace import Workspace, save_json  # noqa: E402

MASTER = {
    "headline": "Cloud Engineer",
    "summary": "Cloud engineer with 6+ years.",
    "skills": {"primary": {"Cloud": ["Azure", "AWS"]}},
    "experience": [{"company": "Acme", "title": "Engineer", "dates": "2020 - Present", "bullets": [
        "Cut Azure costs by 30-40% by right-sizing resources.",
        "Built 66 private endpoints across 2 tenants.",
        "Own CI/CD pipelines in Azure DevOps.",
    ]}],
    "certifications": ["AZ-104"],
}


def tailored(bullets, summary="Cloud engineer with 6+ years."):
    return {"headline": "Senior Cloud Engineer", "summary": summary, "skills": {"Cloud": ["Azure"]},
            "experience": [{"company": "Acme", "title": "Engineer", "dates": "2020 - Present", "bullets": bullets}],
            "certifications": ["AZ-104"]}


def main():
    fails, total = 0, 0

    def check(ok, msg):
        nonlocal fails, total
        total += 1
        fails += not ok
        print(("PASS" if ok else "FAIL") + " | " + msg)

    # truth check: true numbers pass, an invented metric fails, phrases like 0-to-1 / 24x7 are not metrics
    ok = tailored(["Cut Azure costs by **30-40%** through right-sizing.", "Led a 0-to-1 platform with 24x7 on-call."])
    check(not truth_check(ok, MASTER), "real metrics and phrases (0-to-1, 24x7) pass the number check")
    bad = tailored(["Cut Azure costs by 55% by right-sizing resources."])
    check(any("55%" in i for i in truth_check(bad, MASTER)), "an invented metric (55%) is flagged")
    check(any("10" in i for i in truth_check(tailored([], summary="Cloud engineer with 10+ years."), MASTER)),
          "an inflated years-of-experience number in the summary is flagged")

    # change report: kept vs reworded vs dropped
    t = tailored(["Cut Azure costs by 30-40% by right-sizing resources.",          # identical
                  "Reduced Azure spend 30-40% through right-sizing of resources."])  # reworded (same master bullet)
    rep = change_report(t, MASTER)
    check(rep["kept"] == 1 and rep["reworded"] == 1 and rep["dropped"] == 2, f"change report kept/reworded/dropped = 1/1/2 (both map to the cost bullet) (got {rep['kept']}/{rep['reworded']}/{rep['dropped']})")
    check(rep["headline_changed"] and not rep["summary_changed"], "headline change detected, unchanged summary not flagged")

    # levels: default, profile default, job override, invalid
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(tmp)
        ws.ensure_dirs()
        folder = ws.apps / "Acme_Engineer"
        folder.mkdir()
        save_json(folder / "job.json", {"company": "Acme"})
        check(tailor_level(ws, folder) == "medium", "default level is medium")
        save_json(ws.profile_dir / "profile.json", {"tailoring": {"default_level": "low"}})
        check(tailor_level(ws, folder) == "low", "profile tailoring.default_level is used")
        set_tailor_level(folder, "high")
        check(tailor_level(ws, folder) == "high", "per-job level in job.json overrides the profile")
        try:
            tailor_level(ws, folder, "extreme")
            check(False, "invalid level rejected")
        except ValueError:
            check(True, "invalid level rejected")
    check(LEVELS["low"] < LEVELS["medium"] and LEVELS["high"] is None, "budgets: low < medium, high unlimited")

    print(f"\n{total - fails}/{total} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
