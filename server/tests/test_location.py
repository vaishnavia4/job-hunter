"""Location filter: foreign places must not pass as US state codes ('Toronto, Ontario, CA' is not California).
Run: python tests/test_location.py (from server/)"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobhunter.search import Scorer  # noqa: E402
from jobhunter.workspace import Workspace, init_workspace  # noqa: E402

CASES = [
    ("Toronto, Ontario, CA", False), ("Vancouver, BC, Canada", False), ("London, UK", False),
    ("San Francisco, CA", True), ("Denver, CO", True), ("Remote - USA", True),
    ("San Francisco | Toronto", True), ("United States | Canada", True),
]


def main():
    fails = 0
    with tempfile.TemporaryDirectory() as tmp:
        init_workspace(tmp)
        ws = Workspace(tmp)
        sc = Scorer(ws)
        sc.include = __import__("re").compile(".")  # title filter is not under test
        for loc, want in CASES:
            r, why = sc.score({"title": "Site Reliability Engineer", "company": "Contoso", "location": loc,
                               "description": "Kubernetes", "age_override": 0})
            got = r is not None
            ok = got == want
            fails += not ok
            print(("PASS" if ok else "FAIL") + f" | {loc!r} -> {'kept' if got else why}")
    print(f"\n{len(CASES) - fails}/{len(CASES)} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
