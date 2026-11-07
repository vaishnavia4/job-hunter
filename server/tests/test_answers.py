"""Regression tests for the answer bank, using real form labels seen in production.

  python server/tests/test_answers.py [WORKSPACE]      (default: a temp workspace with a sample bank)

With a real workspace it checks your stored answers; with no argument it builds a fixture.
"""
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobhunter import answers  # noqa: E402
from jobhunter.workspace import Workspace  # noqa: E402

RACE_GH = ["Asian: East Asian", "Asian: South Asian", "Asian: Southeast Asian", "Black or African American", "Hispanic or Latinx", "White", "Decline To Self Identify"]
ETH_BENCHLING = ["East Asian (Chinese, Japanese, Korean or Mongolian)", "South Asian (Bangladeshi, Bhutanese, Indian, Nepali, Pakistani, & Sri Lankan)",
                 "Southeast Asian (Burmese, Cambodian, Filipino, Hmong, Indonesian, Laotian, Malaysian, Mien, Singaporean, Thai & Vietnamese)", "Middle Eastern, North African or Arab"]
WD_SALARY = ["Select One", "$120,000-$130,000", "$130,000-$140,000", "$140,000-$150,000", "$150,000-$160,000"]
DOORDASH_WORKED = ["I am a previous employee", "I am a previous contractor", "I am a previous DoorDash Dasher", "I am a current contractor",
                   "I am a current DoorDash Dasher", "I am a current DoorDash employee", "I have not worked at DoorDash"]
VET_TMO = ["Select One", "I AM NOT A VETERAN", "I IDENTIFY AS A VETERAN, JUST NOT A VEVRAA VETERAN", "I IDENTIFY AS ONE OR MORE OF THE CLASSIFICATIONS OF VEVRAA VETERANS LISTED ABOVE", "I DO NOT WISH TO SELF-IDENTIFY"]

CASES = [  # (label, options, expected status, expected value (option if options else answer))
    ("Are you legally authorized to work in the country where this role is located, for any employer?", ["Yes", "No"], "ok", "Yes"),
    ("Do you currently possess unrestricted lawful authorization to work in the U.S.? (If you begin employment, you will be required to provide proof of your identity and eligibility to work in the United States as a condition of employment.)", ["Select One", "Yes", "No"], "ok", "No"),
    ("U.S. Work Authorization Status", ["Can work for any employer", "Can work for current employer", "Seeking work authorization"], "ok", "Can work for any employer"),
    ("Race (*Please select one option that best describes how you identify*)*", RACE_GH, "ok", "Asian: South Asian"),
    ("Ethnicity", ETH_BENCHLING, "ok", ETH_BENCHLING[1]),
    ("Please select the Race/Ethnicity which most accurately describes how you identify yourself*", ["American Indian or Alaskan Native (United States of America)", "Asian (United States of America)", "White (United States of America)"], "ok", "Asian (United States of America)"),
    ("Pronouns", ["She/Her/Hers", "He/Him/His", "They/Them/Theirs", "Prefer not to answer"], "ok", "She/Her/Hers"),
    ("Have you worked at DoorDash?*", DOORDASH_WORKED, "ok", "I have not worked at DoorDash"),
    ("Will you now require immigration sponsorship by our company to attain or maintain your employment eligibility (e.g., H-1B, E-3, TN, O-1, STEM OPT, or any immigration work authorization requiring a written application)?", ["Yes", "No"], "ok", "Yes"),
    ("Will you in the future require immigration sponsorship by our company to attain or maintain your employment eligibility (e.g., H-1B, E-3, TN, O-1, STEM OPT)?", ["Yes", "No"], "ok", "Yes"),
    ("Will you now or will you in the future require employment visa sponsorship?", ["Yes, I will require Harvey to sponsor my employment", "No, I do not require sponsorship to work in the country where this role is located"], "ok", "Yes, I will require Harvey to sponsor my employment"),
    ("Desired Base Salary?*", WD_SALARY, "ok", "$140,000-$150,000"),
    ("Would you like to receive communications via SMS and/or WhatsApp to the number provided about your application process?", ["Yes", "No"], "ok", "Yes"),
    ("If I am applying for a job based in the United States, by checking 'Yes, I agree' below and submitting this application, I agree to T-Mobile's applicant Arbitration Agreement", None, "USER_MUST_TICK", None),
    ("Please identify your Veteran status*", VET_TMO, "ok", "I AM NOT A VETERAN"),
    ("Are you a current or former employee of Freddie Mac's current outside auditor, Price Waterhouse Coopers (PwC)?*", ["Select One", "Yes", "No"], "ok", "No"),
    ("Is your spouse, domestic partner, dependent, child or sibling a current principal, shareholder or professional employee of Freddie Mac's current outside auditor, Price Waterhouse Coopers (PwC)?*", ["Yes", "No"], "ok", "No"),
    ("Freddie Mac does not permit dual employment. Dual employment is defined as outside employment ...", ["Select One", "Yes - I acknowledge", "No - I do not acknowledge"], "ok", "Yes - I acknowledge"),
    ("Are you located within 50 miles of one of the following office hubs", ["San Francisco (Union Square)", "New York (Manhattan)", "No, but willing to relocate", "No, and not willing to relocate"], "ok", "No, but willing to relocate"),
    ("Github Profile", None, "ok", None),
    ("Preferred First Name", None, "ok", None),
    ("How many years of experience do you have in software engineering?", None, "ok", None),
    ("Degree", ["Bachelor's Degree", "Master's Degree", "Associate's Degree"], "ok", "Bachelor's Degree"),
    ("School", None, "ok", None),
    ("How have you used AI in the last 2 weeks?", None, "GENERATE_ESSAY", None),
    ("Are you interested in relocating? If so, where?*", ["Local", "Statewide", "Nationwide", "Anywhere", "Not interested in relocating"], "ok", "Anywhere"),
    ("In order to be employed at T-Mobile, you must be at least 18 years of age and provide proof of age upon hire. Can you meet this requirement?*", ["Yes", "No"], "ok", "Yes"),
    ("Do you have any relatives employed by T-Mobile or its subsidiaries or affiliates?*", ["Yes", "No"], "ok", "No"),
    ("Some brand-new question nobody has seen before?", None, "ASK_USER", None),
]

VET_WORKDAY = ["Select One", "I IDENTIFY AS ONE OR MORE OF THE CLASSIFICATIONS OF PROTECTED VETERANS", "I IDENTIFY AS A VETERAN, JUST NOT A PROTECTED VETERAN", "I AM NOT A VETERAN", "I DO NOT WISH TO SELF-IDENTIFY"]
VET_GH = ["I am not a protected veteran", "I identify as one or more of the classifications of protected veterans", "I don't wish to answer"]
VET_REDDIT = ["Active duty", "Veteran", "Reservist", "Military spouse", "No military service", "Prefer not to say"]
DISAB = ["Yes, I have a disability, or have had one in the past", "No, I do not have a disability and have not had one in the past", "I do not want to answer"]
DEGREES_ARTS = ["Associate's Degree", "Bachelor of Arts", "Bachelor of Science", "Master of Science", "Doctorate"]


def fixture_workspace():
    tmp = Path(tempfile.mkdtemp(prefix="jh_test_"))
    (tmp / "profile").mkdir()
    src = Path(__file__).resolve().parents[2] / "templates" / "answer_bank.sample.json"
    shutil.copyfile(src, tmp / "profile" / "answer_bank.json")
    (tmp / "profile" / "profile.json").write_text("{}", encoding="utf-8")
    return tmp


def main():
    ws_path = Path(sys.argv[1]) if len(sys.argv) > 1 else fixture_workspace()
    ws = Workspace(ws_path)
    fails = 0
    for label, opts, st, want in CASES:
        r = answers.lookup(ws, label, opts)
        got = r.get("option") if opts else r.get("answer")
        ok = r["status"] == st and (want is None or got == want)
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} | {label[:70]:70} -> {r['status']}: {got!r} [{r['question_id']}]" + ("" if ok else f"  (expected {st}: {want!r})"))
    # verify_form: wrong race + resume in cover-letter box + missing required
    v = answers.verify_form(ws, [
        {"label": "Race", "value": "Asian: East Asian", "options": RACE_GH},
        {"label": "Cover Letter", "value": "Resume_Jane_Doe_Acme_USA.pdf", "kind": "file"},
        {"label": "Resume/CV*", "value": "Resume_Jane_Doe_Acme_USA.pdf", "kind": "file", "required": True},
        {"label": "LinkedIn Profile*", "value": "", "required": True},
        {"label": "Pronouns", "value": "She/Her/Hers", "options": ["She/Her/Hers", "He/Him/His"]},
        {"label": "Phone*", "value": "(555) 010-0000"},
    ])
    vf_ok = (not v["pass"] and any(m["label"] == "Race" for m in v["mismatches"]) and v["file_problems"]
             and "LinkedIn Profile*" in v["missing_required"] and "Pronouns" in v["ok"])
    fails += not vf_ok
    print(("PASS" if vf_ok else "FAIL") + " | verify_form flags East Asian, resume-in-cover-letter, missing LinkedIn; accepts pronouns/phone")
    # equivalence: decline variants, yes/no prefixes, "<Company> careers website"
    EQ = [("I don't wish to answer", "Decline to self-identify", True),
          ("Prefer not to say", "I don’t wish to answer", True),
          ("No, I do not have a disability and have not had one in the past", "No", True),
          ("Yes, I have a disability", "No", False),
          ("Acme careers website", "Company careers website", True),
          ("LinkedIn", "Company careers website", False),
          ("Asian: East Asian", "Asian: South Asian", False),
          # amounts and range options (Greenhouse salary text, years-of-experience dropdowns)
          ("$140,000+", "140000", True), ("140k", "140000", True), ("$150,000", "140000", False),
          ("5-10", "6", True), ("0-5", "6", False), ("10+", "6", False), ("15+ years", "16", True),
          # non-veteran wordings are equivalent; "identify as a veteran, just not protected" is not
          ("I am not a protected veteran", "No military service", True), ("I am not a veteran.", "No military service", True),
          ("I identify as a veteran, just not a protected veteran.", "No military service", False)]
    eq_fail = [(a, b) for a, b, want in EQ if answers.equivalent(a, b) != want]
    fails += bool(eq_fail)
    print(("FAIL " + str(eq_fail)) if eq_fail else "PASS | equivalent(): decline variants, yes/no prefixes, careers website")
    # choose_option: negation-aware matching (veteran / disability / Hispanic / degree), independent of the bank
    NO_VET = "No military service"
    CO = [(NO_VET, VET_WORKDAY, "I AM NOT A VETERAN"), (NO_VET, VET_GH, "I am not a protected veteran"),
          (NO_VET, VET_REDDIT, "No military service"), (NO_VET, VET_TMO, "I AM NOT A VETERAN"),
          ("I am not a veteran", VET_WORKDAY, "I AM NOT A VETERAN"),
          ("No", DISAB, "No, I do not have a disability and have not had one in the past"),
          ("Yes", DISAB, "Yes, I have a disability, or have had one in the past"),
          ("No", ["Hispanic or Latino", "Not Hispanic or Latino", "Decline"], "Not Hispanic or Latino"),
          ("No", ["Yes, I identify as Hispanic or Latino", "No, I do not identify as Hispanic or Latino"], "No, I do not identify as Hispanic or Latino"),
          ("Bachelor of Engineering", DEGREES_ARTS, "Bachelor of Science"),
          ("Bachelor of Engineering", ["Bachelor of Arts", "Bachelor's Degree", "Master's Degree"], "Bachelor's Degree"),
          ("Bachelor of Engineering", ["Bachelor of Engineering", "Bachelor of Arts"], "Bachelor of Engineering")]
    co_fail = []
    for ans, opts, want in CO:
        opt, how = answers.choose_option(ans, opts, ["not a protected veteran", r"\bnot a veteran\b"] if "etera" in want.lower() or "VETERAN" in want else ["^no\b"] if ans == "No" else ["^bachelor"])
        if opt != want:
            co_fail.append((ans, want, opt, how))
    opt, _ = answers.choose_option("Bachelor of Engineering", ["Bachelor of Arts", "Master of Arts"], ["^bachelor"])
    if opt is not None:
        co_fail.append(("never Arts", opt))
    fails += bool(co_fail)
    print(("FAIL " + str(co_fail)) if co_fail else f"PASS | choose_option negation/veteran/disability/Hispanic/degree ({len(CO) + 1} cases)")
    # final_review: a clean review passes; the classic mistakes are all caught
    fws = Workspace(fixture_workspace())
    good = {"company": "Acme Corp", "title": "Engineer", "files": ["Resume_Jane_Doe_Acme_Corp.pdf", "CoverLetter_Jane_Doe_Acme_Corp.pdf"],
            "fields": [{"label": "Resume/CV*", "value": "Resume_Jane_Doe_Acme_Corp.pdf", "kind": "file", "required": True},
                       {"label": "Cover Letter", "value": "CoverLetter_Jane_Doe_Acme_Corp.pdf", "kind": "file"},
                       {"label": "Pronouns", "value": "She/Her/Hers", "options": ["She/Her/Hers", "He/Him/His"]}],
            "page_text": "Work experience\nEngineer 03/2021 - Present\nDegree: Bachelor of Science"}
    r1 = answers.final_review(fws, good)
    bad = dict(good, files=["Resume_Jane_Doe_Other.pdf"], page_text="Bachelor of Arts\nI IDENTIFY AS A VETERAN, JUST NOT A PROTECTED VETERAN\nEngineer 01/2020 - %02d/%d\nError" % (date.today().month, date.today().year),
               fields=[{"label": "Resume/CV*", "value": "Resume_Jane_Doe_Other.pdf", "kind": "file", "required": True},
                       {"label": "Cover Letter", "value": "Resume_Jane_Doe_Other.pdf", "kind": "file"},
                       {"label": "Degree*", "value": "Select One", "required": True}])
    r2 = answers.final_review(fws, bad)
    txt = " | ".join(r2["problems"]).lower()
    fr_ok = r1["ok"] and not r2["ok"] and all(k in txt for k in ("company name", "cover", "placeholder", "bachelor of arts", "identify as", "end date", "error")) and r2["summary_table"]
    fails += not fr_ok
    print(("PASS" if fr_ok else "FAIL " + json.dumps(r1["problems"] + r2["problems"], indent=1)) + " | final_review passes a clean form and flags every classic mistake")
    # record() then lookup() must match freshly recorded wordings, punctuation-insensitively
    tmp = fixture_workspace()
    tws = Workspace(tmp)
    rec = [("Please provide the name of your current (or most recent) company", "Acme Corp", "Please provide the name of your current or most recent company"),
           ("Select up to 2 ethnicities", "South Asian", "select up to 2 ethnicities:")]
    rec_fail = []
    for label, ans, relabel in rec:
        answers.record(tws, label, ans)
        for t in (label, relabel):
            r = answers.lookup(tws, t)
            if r["status"] != "ok" or r["answer"] != ans:
                rec_fail.append((t, r["status"], r["answer"]))
    fails += bool(rec_fail)
    print(("FAIL " + str(rec_fail)) if rec_fail else "PASS | record() + lookup() match fresh wordings punctuation-insensitively")
    v2 = answers.verify_form(tws, [{"label": "Please provide the name of your current (or most recent) company", "value": "Acme Corp"}])
    fails += not v2["pass"]
    print(("PASS" if v2["pass"] else "FAIL") + " | verify_form accepts a freshly recorded answer")
    shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps({k: v[k] for k in ("mismatches", "file_problems", "missing_required")}, indent=1))
    print(f"\n{len(CASES) + 6 - fails}/{len(CASES) + 6} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
