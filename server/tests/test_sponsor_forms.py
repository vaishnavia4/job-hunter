"""Sponsorship blockers also catch wording that appears only on application forms.
Run: python tests/test_sponsor_forms.py (from server/)"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobhunter import search  # noqa: E402

CASES = [
    ("This role requires you to already be legally authorized to work in the countries listed in the job posting and "
     "1Password will not be offering work authorization, relocation assistance, or sponsorship/transferring of a visa.", True),
    ("We are unable to support H-1B transfers for this role.", False),  # not covered yet: documents the boundary
    ("Will you now, or in the future, require employer visa sponsorship (e.g., H-1B, TN, E-3, or similar)?", False),
    ("No transfer of H-1B visas is possible for this position.", True),
    ("Do you require visa sponsorship? Yes / No", False),
]


def main():
    fails = 0
    for text, want in CASES:
        got = any(re.search(p, text, re.I) for p in search.SPONSORSHIP_BLOCKERS)
        ok = got == want
        fails += not ok
        print(("PASS" if ok else "FAIL") + f" | blocker={got} | {text[:70]}")
    print(f"\n{len(CASES) - fails}/{len(CASES)} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
