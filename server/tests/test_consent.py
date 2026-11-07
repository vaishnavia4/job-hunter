"""Consent policy: boxes are ticked only under the user's recorded standing consent (default: ask).
Run: python tests/test_consent.py (from server/)"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobhunter.fill import workday as w  # noqa: E402


class Box:
    def __init__(self):
        self.checked = False

    def check(self, force=False):
        self.checked = True


class Ctx:
    def __init__(self, policy=None):
        self.profile = {"consent_policy": policy} if policy is not None else {}
        self.problems, self.records = [], []

    def problem(self, m):
        self.problems.append(m)

    def fill_record(self, label, value):
        self.records.append((label, value))


def main():
    fails = total = 0

    def check(ok, msg):
        nonlocal fails, total
        total += 1
        fails += not ok
        print(("PASS" if ok else "FAIL") + " | " + msg)

    check(w.consent_kind("I agree to creating this account to allow me to apply") == "account_creation_agreement", "account-creation box classified")
    check(w.consent_kind("Yes, I have read and consent to the terms and conditions.") == "terms_and_conditions", "terms box classified")
    check(w.consent_kind("I have read and agree to the Mutual Arbitration Agreement") == "legal_attestations", "arbitration classified as attestation")
    check(w.consent_kind("I certify that all information provided is true and complete") == "legal_attestations", "certify classified as attestation")

    c, b = Ctx(), Box()
    w._consent_box(c, b, "Yes, I have read and consent to the terms and conditions.")
    check(not b.checked and c.problems, "default policy (no consent_policy): box left for the user")

    c, b = Ctx({"terms_and_conditions": "preauthorized"}), Box()
    w._consent_box(c, b, "Yes, I have read and consent to the terms and conditions.")
    check(b.checked and c.records and not c.problems, "preauthorized terms: ticked and reported")

    c, b = Ctx({"terms_and_conditions": "preauthorized"}), Box()
    w._consent_box(c, b, "I have read and agree to the Mutual Arbitration Agreement")
    check(not b.checked and c.problems, "terms consent does NOT extend to arbitration (separate key)")

    check(w._consent_preauthorized(types.SimpleNamespace(profile={"consent_policy": {"legal_attestations": "preauthorized_report_each"}}), "legal_attestations"),
          "preauthorized_report_each counts as preauthorized")
    print(f"\n{total - fails}/{total} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
