"""'Which countries are you a citizen of?' must never get the Yes/No 'are you a US citizen' answer.
Run: python tests/test_citizenship.py (from server/)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobhunter import answers  # noqa: E402

COUNTRY_LIST = [
    "If the answer to the preceding question is “no” please list each country of which you are a lawful citizen "
    "(i.e., either single or dual citizenship).",
    "Country of citizenship",
    "Citizenship",
    "Countries of citizenship",
    "Nationality / country of citizenship",
]
US_CITIZEN = [
    "Are you a U.S. citizen?",
    "Are you a U.S. Citizen, a U.S. Lawful Permanent Resident (i.e. green card holder), or a Protected Individual "
    "under 8 U.S.C. Sec. 1324b (e.g. Refugee or Asylum Holder)?",
    "Do you have US citizenship?",
    "Are you a United States citizen?",
]


def main():
    fails = total = 0
    for q in COUNTRY_LIST:
        total += 1
        got = answers.match_question(q)
        if not got or got["id"] != "citizenship_countries":
            fails += 1
            print("FAIL country-list:", q[:70], "->", got and got["id"])
    for q in US_CITIZEN:
        total += 1
        got = answers.match_question(q)
        if not got or got["id"] != "us_citizen":
            fails += 1
            print("FAIL us-citizen:", q[:70], "->", got and got["id"])
    for qid in ("citizenship_countries", "us_citizen"):
        total += 1
        q = next(x for x in answers._bank()["questions"] if x["id"] == qid)
        if any("\x08" in p for p in q["patterns"]):
            fails += 1
            print("FAIL backspace character in", qid, "patterns")
    print(f"{total - fails}/{total} passed")
    return 1 if fails else 0


def test_citizenship():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
