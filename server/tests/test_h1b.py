"""H-1B sponsor matching (offline, tiny fixture). Run: python tests/test_h1b.py  (from server/)"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobhunter import h1b  # noqa: E402
from jobhunter.workspace import Workspace, save_json  # noqa: E402

ROWS = {  # legal name -> (initial, continuing)
    "PLAID INC": (3, 70), "HARVEY NASH INC": (30, 9), "FEDERAL HOME LOAN MORTGAGE CORP": (18, 238),
    "UNIV CORP FOR ATMOSPHERIC RESEARCH": (16, 33), "UNIVERSITY OF COLORADO BOULDER": (60, 70),
    "STACK INFRASTRUCTURE INC": (0, 1), "PRYSM CAPITAL LLC": (0, 1),
}


def main():
    fails = total = 0

    def check(ok, msg):
        nonlocal fails, total
        total += 1
        fails += not ok
        print(("PASS" if ok else "FAIL") + " | " + msg)

    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(tmp)
        ws.ensure_dirs()
        emp = {h1b.normalize(n): {"name": n, "initial": a, "continuing": c, "denied": 0, "states": ["CO"]} for n, (a, c) in ROWS.items()}
        save_json(ws.data / "h1b_sponsors.json", {"employers": emp})
        h1b._CACHE.clear()
        L = lambda c, al=None: (h1b.lookup(ws, c, al) or {}).get("name")
        check(L("Plaid") == "PLAID INC", "brand name matches legal name with suffix (Plaid -> PLAID INC)")
        check(L("Harvey") is None, "one-word name needs an exact match (Harvey is not HARVEY NASH)")
        check(L("Freddie Mac") == "FEDERAL HOME LOAN MORTGAGE CORP", "built-in alias (Freddie Mac)")
        check(L("University Corporation for Atmospheric Research") == "UNIV CORP FOR ATMOSPHERIC RESEARCH", "UNIVERSITY/UNIV abbreviation normalised")
        check(L("University of Colorado Boulder - Office of IT") == "UNIVERSITY OF COLORADO BOULDER", "department after ' - ' is ignored")
        check(L("Prysm Health") is None, "multi-word prefix must match whole words (Prysm Health is not PRYSM CAPITAL)")
        check(L("Contoso", {"contoso": "Plaid Inc"}) == "PLAID INC", "workspace alias from config")
        pts, flag = h1b.score_adjust(ws, "Plaid")
        check(pts == 8 and "73 approvals" in flag, f"strong sponsor bonus +8 ({pts}, {flag})")
        check(h1b.score_adjust(ws, "STACK Infrastructure")[0] == 3, "occasional sponsor +3")
        check(h1b.score_adjust(ws, "Unknown Co Ltd")[0] == -3, "no record -3 (not a skip)")
        check(h1b.score_adjust(ws, "Plaid", {"bonus_strong": 12})[0] == 12, "bonus configurable")
    print(f"\n{total - fails}/{total} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
