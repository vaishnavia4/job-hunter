"""Weekly report math on a fake tracker (offline).

Run: python tests/test_report.py   (from server/)   or   pytest tests/test_report.py
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobhunter import report  # noqa: E402
from jobhunter.workspace import Workspace, save_json  # noqa: E402

NOW = datetime(2026, 11, 14, 9, 0)


def row(i, status, ats="ashby", applied="2026-11-10", found="2026-11-09", loc="Remote", flags="", notes=""):
    return {"job_id": f"x:{i}", "date_found": found, "company": f"Co{i}", "title": "SRE", "location": loc, "ats": ats,
            "status": status, "date_applied": applied if status in ("applied", "interview", "offer", "rejected") else "",
            "flags": flags, "notes": notes, "folder": ""}


def make_ws(tmp):
    ws = Workspace(tmp)
    ws.ensure_dirs()
    rows = [
        row(1, "applied", "ashby", loc="Denver, CO", flags="H-1B sponsor (40 approvals, 30 continuing)"),
        row(2, "interview", "ashby"),
        row(3, "rejected", "ashby", applied="2026-11-01"),
        row(4, "applied", "workday", applied="2026-10-20", found="2026-10-19"),
        row(5, "applied", "workday", loc="Boston, MA"),
        row(6, "applied", "workday", loc="Austin, TX", flags="some H-1B history (2 approvals)"),
        row(7, "ready", "greenhouse"),
        row(8, "blocked", "workday", notes="CAPTCHA"),
        row(9, "skipped", notes="Must be a U.S. Citizen (ITAR)"),
        row(10, "skipped", notes="No visa sponsorship offered"),
        row(11, "skipped", notes="does not sponsor H-1B"),
        row(12, "new"),
    ]
    ws.save_tracker(rows)
    save_json(ws.data / "responses.json", {"messages": {
        "<1>": {"date": "2026-11-11", "class": "application_received", "job_ids": ["x:1"], "match_score": 9},
        "<2>": {"date": "2026-11-12", "class": "interview", "job_ids": ["x:2"], "match_score": 9},
        "<3>": {"date": "2026-11-12", "class": "rejected", "job_ids": ["x:4"], "match_score": 9},  # status still 'applied'
        "<4>": {"date": "2026-11-12", "class": "rejected", "job_ids": ["x:5", "x:6"], "match_score": 9},  # ambiguous: ignored
        "<5>": {"date": "2026-11-12", "class": "rejected", "job_ids": ["x:6"], "match_score": 2},  # weak match: ignored
        "<6>": {"date": "2026-11-13", "class": "action_needed", "job_ids": ["x:5"], "match_score": 9,
                "companies": ["Co5"], "subject": "A Task Awaits You"},
    }})
    return ws


def run(tmp):
    ws = make_ws(tmp)
    return ws, report.weekly_report(ws, days=7, out_dir="reports", now=NOW, write_xlsx=False)


def test_funnel():
    with tempfile.TemporaryDirectory() as tmp:
        _, r = run(tmp)
        f = r["funnel"]
        assert f["found"] == 12
        assert f["past screening (tailored+)"] == 8, f  # 6 applied-ish + ready + blocked
        assert f["applied"] == 6
        assert f["auto-confirmed"] == 1
        assert f["responded (human reply)"] == 3, f  # interview(x2), rejected(x3), rejected email (x4)
        assert f["interview"] == 1 and f["offer"] == 0


def test_period():
    with tempfile.TemporaryDirectory() as tmp:
        _, r = run(tmp)
        assert r["applied_total"] == 6
        assert r["applied_period"] == 4, r["applied_period"]  # x3 (11-01) and x4 (10-20) are older than 7 days
        assert r["found_period"] == 11


def test_breakdowns_and_small_n():
    with tempfile.TemporaryDirectory() as tmp:
        ws, r = run(tmp)
        md = Path(r["report"]).read_text(encoding="utf-8")
        assert "| ashby | 3 | 1/3 | 2/3 | 1/3 |" in md, "small groups show counts, no percentage"
        assert "| workday | 3 | 0/3 | 1/3 | 0/3 |" in md
        assert "3/6 (50%)" in md, "percentages once n >= 5"
        assert "| Colorado | 1 |" in md and "| Remote |" in md and "| Other US | 2 |" in md
        assert "H-1B sponsor (strong)" in md and "some H-1B history" in md and "| unknown | 4 |" in md
        assert "| auto | 6 |" in md, "tailor level defaults to auto"


def test_skips_waiting_suggestions():
    with tempfile.TemporaryDirectory() as tmp:
        _, r = run(tmp)
        md = r["markdown"]
        assert "- no visa sponsorship: 2" in md and "- citizenship / clearance / ITAR: 1" in md
        assert sorted(r["waiting"]) == ["blocked: Co8", "ready: Co7"]
        assert "A Task Awaits You" in md
        assert len(r["suggestions"]) == 3
        assert any("waiting on Vaishnavi" in s for s in r["suggestions"])


def test_frac():
    assert report.frac(1, 4) == "1/4"
    assert report.frac(2, 8) == "2/8 (25%)"
    assert report.frac(0, 0) == "-"


def test_location_bucket():
    assert report.location_bucket({"location": "Englewood, CO"}) == "Colorado"
    assert report.location_bucket({"location": "United States - Remote"}) == "Remote"
    assert report.location_bucket({"location": "Dallas, TX", "flags": ""}) == "Other US"


def main():
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    fails = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS | {name}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL | {name}: {e}")
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
