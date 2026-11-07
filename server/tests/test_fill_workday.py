"""Offline tests for the Workday filler's pure helpers (no browser): exact option matching, the auth state machine
driven by page-text fixtures, 'How did you hear' selection, certificate matching, read-back checks.

  python server/tests/test_fill_workday.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobhunter.fill import common as C  # noqa: E402
from jobhunter.fill import workday as W  # noqa: E402

FAILS = []
TOTAL = [0]


def check(name, got, want):
    TOTAL[0] += 1
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


# ------------------------------------------------------------------ page-text fixtures (trimmed real wording)
CHOOSER = "Sign In\nSign in with Apple\nSign in with Google\nSign in with LinkedIn\nSign in with email\nDon't have an account yet? Create Account"
SIGNIN = "Sign In\nEmail Address*\nPassword*\nForgot your password?\nSign In\nDon't have an account yet? Create Account"
REJECTED = "Sign In\nERROR: Wrong email or password, or your account may be locked.\nEmail Address*\nPassword*\nSign In"
LOCKED = "Sign In\nYour account has been locked. Please reset your password.\nEmail Address*\nPassword*"
CREATE = "Create Account\nEmail Address*\nPassword*\nVerify New Password*\nYes, I have read and consent to the terms\nCreate Account\nAlready have an account? Sign In"
EXISTS = "Create Account\nERROR: An account with this email address already exists.\nEmail Address*\nPassword*\nVerify New Password*"
MAINT = "Workday is currently unavailable.\nWe are performing scheduled maintenance. Please try again later."
APPLYING = "current step 1 of 6\nMy Information\nHow Did You Hear About Us?*\nSelect One"


def test_exact_option():
    schools = ["Arts College, Example University", "B.Com Computers, Example University", "Example University", "Example University College"]
    check("exact school, case-insensitive", C.exact_option(schools, "example university"), "Example University")
    check("never the first partial match", C.exact_option(schools[:2], "Example University"), None)
    check("apostrophe is not ignored", C.exact_option(["St. Mary's College"], "St Marys College"), None)
    check("punctuation as space", C.exact_option(["Example - University"], "Example University"), "Example - University")
    check("empty want", C.exact_option(schools, ""), None)


def test_readback_checks():
    fields = [
        {"label": "Country", "value": "United States of America", "required": True},
        {"label": "How Did You Hear About Us?", "value": "", "required": True},
        {"label": "State", "value": "Select One", "required": True},
        {"label": "Address Line 2", "value": "", "required": False},
        {"label": "Phone Number", "value": "5550100", "required": True, "error": "Error: enter a valid phone number"},
    ]
    check("empty required (blank + Select One)", C.empty_required(fields), ["How Did You Hear About Us?", "State"])
    check("field errors", C.field_errors(fields), [("Phone Number", "Error: enter a valid phone number")])
    check("placeholder is empty", C.is_empty_value("Select One"), True)
    check("value not empty", C.is_empty_value("No"), False)


def test_tenant_and_links():
    check("tenant from myworkdayjobs", W.tenant_hint("https://acme.wd1.myworkdayjobs.com/en-US/External/job/Denver/X_R1"), "acme.wd1")
    check("tenant from myworkdaysite", W.tenant_hint("https://wd5.myworkdaysite.com/recruiting/globex/careers/job/1"), "globex")
    ok = "https://acme.wd1.myworkdayjobs.com/External/activate/abc123"
    check("own tenant link ok", W.link_ok(ok, "acme.wd1"), True)
    check("other tenant refused", W.link_ok("https://other.wd3.myworkdayjobs.com/x/activate/abc", "acme.wd1"), False)
    check("http refused", W.link_ok(ok.replace("https", "http"), "acme.wd1"), False)
    check("non-workday host refused", W.link_ok("https://evil.example.com/acme.wd1/activate/x", "acme.wd1"), False)


def test_classify():
    check("chooser", W.classify_auth_page(CHOOSER, chooser=True), "chooser")
    check("sign-in form", W.classify_auth_page(SIGNIN, password=True), "signin_form")
    check("rejected", W.classify_auth_page(REJECTED, password=True), "rejected")
    check("locked = rejected", W.classify_auth_page(LOCKED, password=True), "rejected")
    check("create form", W.classify_auth_page(CREATE, create_form=True, password=True), "create_form")
    check("email taken", W.classify_auth_page(EXISTS, create_form=True, password=True), "exists")
    check("maintenance", W.classify_auth_page(MAINT), "maintenance")
    check("captcha wins", W.classify_auth_page(SIGNIN, captcha=True, password=True), "captcha")
    check("in application", W.classify_auth_page(APPLYING, in_application=True), "signed_in")
    check("unknown page", W.classify_auth_page("Job description text"), "unknown")


def simulate(script):
    """script: [(state, result-for-the-action-taken-or-None)] -> list of actions decided."""
    h = W.new_auth_history()
    actions = []
    for state, result in script:
        a = W.next_auth_action(state, h)
        actions.append(a)
        if a == "done" or a.startswith("stop:"):
            break
        W.record_auth_action(h, a, result)
    return actions


def test_auth_machine():
    new_tenant = [("chooser", None), ("signin_form", None), ("rejected", None), ("signin_form", "opened"),
                  ("chooser", None), ("signin_form", None), ("signed_in", None)]
    check("new tenant: create -> activation -> sign in", simulate(new_tenant),
          ["choose_email", "sign_in", "create", "await_activation", "choose_email", "sign_in", "done"])
    exists = [("signin_form", None), ("rejected", None), ("exists", "done"), ("signin_form", None), ("signed_in", None)]
    check("email taken -> reset -> sign in", simulate(exists), ["sign_in", "create", "reset", "sign_in", "done"])
    no_mail = [("signin_form", None), ("rejected", None), ("signin_form", "none"), ("signin_form", "done"),
               ("signin_form", None), ("signed_in", None)]
    check("no activation mail + back at sign-in -> reset", simulate(no_mail),
          ["sign_in", "create", "await_activation", "reset", "sign_in", "done"])
    stubborn = [("signin_form", None), ("rejected", None), ("signin_form", "opened"), ("signin_form", None),
                ("rejected", "done"), ("signin_form", None), ("rejected", None)]
    acts = simulate(stubborn)
    check("still rejected after reset -> stop", acts,
          ["sign_in", "create", "await_activation", "sign_in", "reset", "sign_in", "stop:signin_failed"])
    retried = any(a == b == "sign_in" for a, b in zip(acts, acts[1:]))
    check("never two sign-ins in a row", retried, False)
    check("captcha stops", simulate([("signin_form", None), ("captcha", None)]), ["sign_in", "stop:captcha"])
    check("maintenance stops", simulate([("maintenance", None)]), ["stop:maintenance"])
    check("no mail, unknown page", simulate([("create_form", None), ("unknown", "none"), ("unknown", None)]),
          ["create", "await_activation", "stop:verification_missing"])
    check("reset failed stops", simulate([("signin_form", None), ("rejected", None), ("exists", "failed"), ("signin_form", None)]),
          ["sign_in", "create", "reset", "stop:reset_failed"])
    check("unknown first page", simulate([("unknown", None)]), ["stop:unclear"])
    check("already signed in", simulate([("signed_in", None)]), ["done"])


def test_how_heard():
    cats = ["Career Fair", "Employee Referral", "Job Board", "Social Media", "Website"]
    items = ["Acme Careers Website", "Indeed", "Glassdoor"]
    check("bank answer -> Website category", W.pick_heard_option(cats, "Company careers page"), "Website")
    check("bank answer -> company careers item", W.pick_heard_option(items, "Company careers page", company="Acme"), "Acme Careers Website")
    check("no company name: careers site item", W.pick_heard_option(["Careers Site", "Indeed"], "Company careers page"), "Careers Site")
    check("LinkedIn source, no bank: category", W.pick_heard_option(["Job Board", "Social Media", "Website"], "", "linkedin"), "Social Media")
    check("LinkedIn source, no bank: item", W.pick_heard_option(["Facebook", "LinkedIn", "Twitter"], "", "linkedin"), "LinkedIn")
    check("bank beats source", W.pick_heard_option(cats, "Company careers page", "linkedin"), "Website")
    check("exact bank answer", W.pick_heard_option(["Indeed", "Glassdoor"], "Indeed"), "Indeed")
    check("default careers site", W.pick_heard_option(["Corporate Website", "Indeed"]), "Corporate Website")
    check("never a fair / referral", W.pick_heard_option(["Career Fair", "Employee Referral"], "Company careers page"), None)


def test_certs():
    opts = ["Microsoft Certified: Azure Fundamentals", "AZ-1040 Something Else", "Microsoft Azure Administrator (AZ-104)",
            "AWS Certified Solutions Architect - Associate", "Kubernetes"]
    check("exam code", W.match_cert(opts, "Microsoft Certified: Azure Administrator Associate (AZ-104)"), "Microsoft Azure Administrator (AZ-104)")
    check("code without dash", W.match_cert(["AZ104 Administrator"], "Azure Administrator (AZ-104)"), "AZ104 Administrator")
    check("name with en dash", W.match_cert(opts, "AWS Certified Solutions Architect – Associate"), "AWS Certified Solutions Architect - Associate")
    check("no match -> None", W.match_cert(opts, "Certified Kubernetes Administrator (CKA)"), None)
    check("short partial refused", W.match_cert(["Azure"], "Azure Solutions Architect Expert"), None)
    prof = {"certifications": ["Cert A (AB-100)", {"name": "Cert B"}, "Cert C (in progress)", {"name": "Cert D", "status": "In progress"}, "Cert A (AB-100)"]}
    check("earned certs only, deduped", W.earned_certs(prof, {}), ["Cert A (AB-100)", "Cert B"])
    check("master fallback", W.earned_certs({}, {"certifications": ["X", "Y - expected 2027"]}), ["X"])


def test_dates():
    check("present", W._dates("Apr 2024 - Present"), ("042024", None, True))
    check("en dash", W._dates("Jun 2023 – Feb 2024"), ("062023", "022024", False))
    check("numeric + to", W._dates("11/2020 to 04/2023"), ("112020", "042023", False))
    check("full month", W._dates("September 2019 - Current"), ("092019", None, True))


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:  # a crash is a failure, keep going
                FAILS.append(f"{name} crashed: {type(e).__name__}: {e}")
    if FAILS:
        print("FAIL")
        for f in FAILS:
            print("  -", f.encode("ascii", "backslashreplace").decode())
        sys.exit(1)
    print(f"fill/workday tests: {TOTAL[0]} checks passed")


if __name__ == "__main__":
    main()
