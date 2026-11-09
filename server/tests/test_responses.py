"""Employer-reply classification, matching and tracker monotonicity (offline, no Gmail).

Run: python tests/test_responses.py   (from server/)   or   pytest tests/test_responses.py
"""
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobhunter import responses as R  # noqa: E402
from jobhunter.workspace import Workspace, load_json  # noqa: E402

ROWS = [
    {"job_id": "wd:freddiemac:JR17721", "company": "Freddiemac", "title": "Senior DevOps Engineer", "status": "applied",
     "url": "https://freddiemac.wd5.myworkdayjobs.com/x/job/JR17721", "folder": "", "notes": ""},
    {"job_id": "wd:freddiemac:JR17712", "company": "Freddiemac", "title": "Network Security & Automation Engineer",
     "status": "applied", "url": "https://freddiemac.wd5.myworkdayjobs.com/x/job/JR17712", "folder": "", "notes": ""},
    {"job_id": "manual:pultegroup:JR9610", "company": "PulteGroup", "title": "Senior DevOps Engineer (PulteMortgage)",
     "status": "applied", "url": "https://pultegroup.wd1.myworkdayjobs.com/PGI/job/x_JR9610", "folder": "", "notes": ""},
    {"job_id": "ab:harvey:1", "company": "Harvey", "title": "Senior Infrastructure Engineer", "status": "applied",
     "url": "https://jobs.ashbyhq.com/harvey/1", "folder": "", "notes": ""},
    {"job_id": "gh:reddit:2", "company": "Reddit", "title": "Senior Software Engineer, GRC", "status": "offer",
     "url": "", "folder": "", "notes": ""},
    {"job_id": "gh:acme:3", "company": "Acme Robotics", "title": "Platform Engineer", "status": "new",
     "url": "", "folder": "", "notes": ""},
    {"job_id": "gh:globex:4", "company": "Globex", "title": "SRE", "status": "rejected", "url": "", "folder": "", "notes": ""},
]


def mail(frm, subject, body, mid, day=7):
    return {"message_id": mid, "sender": R.sender_info(frm), "subject": subject, "body": body,
            "when": datetime(2026, 11, day, 12, tzinfo=timezone.utc)}


def make_ws(tmp):
    ws = Workspace(tmp)
    ws.ensure_dirs()
    ws.save_tracker([dict(r) for r in ROWS])
    return ws


# ---------------------------------------------------------------- classification
def test_confirmation():
    c, ok, _ = R.classify("Thank you for applying to Harvey", "We have received your application. Our team will "
                          "review it and if there is a match we will reach out to schedule an interview. Next steps...")
    assert (c, ok) == ("application_received", True), (c, ok)
    c, _, _ = R.classify("Got it! Application received for: REQ367236 Sr. Engineer", "")
    assert c == "application_received"


def test_rejection():
    c, ok, _ = R.classify("Your application to Harvey", "Thank you for applying. Unfortunately, we have decided to "
                          "move forward with other candidates whose experience more closely matches the role.")
    assert (c, ok) == ("rejected", True), (c, ok)
    c, ok, _ = R.classify("Update on your application", "After careful consideration, we will not be moving forward.")
    assert (c, ok) == ("rejected", True)
    c, ok, _ = R.classify("Unfortunately the position is closed", "")
    assert (c, ok) == ("rejected", True), "weak signal in the subject is decisive"
    # Engine 2026-11-08: curly apostrophe + "won't", inside a "thank you for applying" email
    c, ok, _ = R.classify("Update Regarding your Application – Engine", "Thank you for your interest in Engine! We truly "
                          "appreciate the time and thought you put into applying. Unfortunately, we won’t be moving "
                          "forward with your candidacy for the Senior Software Engineer, Infrastructure role at this time.")
    assert (c, ok) == ("rejected", True), (c, ok)


def test_confirmation_with_rejection_boilerplate_is_not_rejection():
    # Two weak phrases inside a confirmation must not turn it into a rejection.
    c, ok, _ = R.classify("Thanks for applying!", "We received your application. If not selected now, we will keep "
                          "your resume on file and encourage you to apply for other roles.")
    assert c == "application_received" and ok is False, (c, ok)


def test_interview():
    c, ok, _ = R.classify("Next steps with Reddit", "Hi Vaishnavi, we'd like to schedule a 30 minute phone screen. "
                          "Please share your availability for next week.")
    assert (c, ok) == ("interview", True), (c, ok)
    c, ok, _ = R.classify("Online assessment", "Please complete the HackerRank coding challenge within 5 days.")
    assert c == "interview" and ok


def test_weak_interview_words_stay_other():
    c, ok, _ = R.classify("RE: PERM Certification Status and Next Steps", "We will update you on the next steps.")
    assert (c, ok) == ("other", False), (c, ok)


def test_offer_and_action():
    c, ok, _ = R.classify("Offer letter", "We are pleased to extend you an offer for the role of SRE.")
    assert (c, ok) == ("offer", True)
    c, ok, _ = R.classify("A Task Awaits You: Please Complete the Work Opportunity Tax Credit Form", "")
    assert (c, ok) == ("action_needed", True)
    c, _, _ = R.classify("Verify your candidate account", "Click the link to verify your email.")
    assert c == "action_needed"
    # "we offer great benefits" is not an offer
    c, _, _ = R.classify("Thanks for applying", "We received your application. We offer great benefits.")
    assert c == "application_received"


# ---------------------------------------------------------------- matching
def test_matching():
    rows = [dict(r) for r in ROWS]
    best, score, how = R.match_rows(rows, R.sender_info("Workday <freddiemac@myworkday.com>"),
                                    "Freddie Mac - Application Received", "Thank you for applying to JR17712 Network Security")
    assert [r["job_id"] for r in best] == ["wd:freddiemac:JR17712"] and score >= R.MIN_MATCH, (best, how)
    best, _, _ = R.match_rows(rows, R.sender_info("Workday <freddiemac@myworkday.com>"), "Freddie Mac - Application Received", "")
    assert len(best) == 2, "two Freddie Mac jobs and no req id -> ambiguous"
    best, _, _ = R.match_rows(rows, R.sender_info("pultegroup@myworkday.com"), "Thank You For Applying!", "")
    assert best and best[0]["job_id"] == "manual:pultegroup:JR9610", "Workday tenant from the posting URL"
    best, _, _ = R.match_rows(rows, R.sender_info("Harvey Hiring Team <no-reply@ashbyhq.com>"), "Thank You for Applying to Harvey", "")
    assert best and best[0]["job_id"] == "ab:harvey:1"
    best, _, _ = R.match_rows(rows, R.sender_info("Someone <x@maxeleven.com>"), "Senior SRE role", "")
    assert best == []


def test_candidate_filter():
    keys = [R.company_keys(r["company"]) for r in ROWS]
    assert R.is_candidate(R.sender_info("x@myworkday.com"), "anything", keys)
    assert R.is_candidate(R.sender_info("No-reply <no-reply@harvey.ai>"), "Thanks for applying to Harvey", keys)
    assert not R.is_candidate(R.sender_info("jobalerts-noreply@linkedin.com"), "Harvey is hiring", keys)
    assert not R.is_candidate(R.sender_info("Recruiter <a@maxeleven.com>"), "Senior SRE remote W2", keys)
    assert not R.is_candidate(R.sender_info("me@gmail.com"), "Harvey", keys, own_addr="me@gmail.com")


def test_safe_subject_masks_codes():
    assert "123456" not in R.safe_subject("123456 is your verification code")
    assert R.safe_subject("Thank you for applying to JR17712") == "Thank you for applying to JR17712"


# ---------------------------------------------------------------- tracker updates
def test_monotonic_status():
    assert R.next_status("applied", "rejected", True) == "rejected"
    assert R.next_status("applied", "interview", True) == "interview"
    assert R.next_status("interview", "rejected", True) == "rejected"
    assert R.next_status("interview", "offer", True) == "offer"
    assert R.next_status("offer", "rejected", True) is None, "offer is final"
    assert R.next_status("rejected", "interview", True) is None, "never reopen a rejection"
    assert R.next_status("new", "interview", True) is None, "never jump from new"
    assert R.next_status("skipped", "rejected", True) is None
    assert R.next_status("applied", "application_received", True) is None, "confirmation never changes status"
    assert R.next_status("applied", "rejected", False) is None, "unsure -> no change"


def test_process_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        ws = make_ws(tmp)
        mails = [
            mail("Harvey Hiring Team <no-reply@ashbyhq.com>", "Thank You for Applying to Harvey", "We received your application.", "<a>", 6),
            mail("Harvey Hiring Team <no-reply@ashbyhq.com>", "Your application to Harvey",
                 "Unfortunately, we have decided to pursue other candidates.", "<b>", 7),
            mail("Reddit <no-reply@us.greenhouse-mail.io>", "Reddit update", "We regret to inform you that we will not be moving forward.", "<c>"),
            mail("Workday <freddiemac@myworkday.com>", "Freddie Mac - Interview invitation",
                 "We'd like to schedule an interview for the Senior DevOps Engineer role.", "<d>"),
            mail("Workday <freddiemac@myworkday.com>", "Freddie Mac - Interview invitation", "We'd like to invite you to interview.", "<e>"),
        ]
        dry = R.process(ws, mails, dry_run=True)
        assert not (ws.data / "responses.json").exists(), "dry run writes nothing"
        assert all(r["status"] == o["status"] for r, o in zip(ws.load_tracker(), ROWS)), "dry run leaves tracker alone"
        assert dry["by_class"]["rejected"] == 2 and dry["by_class"]["interview"] == 2, dry["by_class"]

        R.process(ws, mails)
        t = {r["job_id"]: r for r in ws.load_tracker()}
        assert t["ab:harvey:1"]["status"] == "rejected"
        assert "email 2026-11-07: rejected - Your application to Harvey" in t["ab:harvey:1"]["notes"]
        assert "email 2026-11-06: application_received" in t["ab:harvey:1"]["notes"]
        assert t["gh:reddit:2"]["status"] == "offer", "offer is never overwritten by a rejection"
        assert t["wd:freddiemac:JR17721"]["status"] == "interview", "title words pick the right Freddie Mac job"
        assert t["wd:freddiemac:JR17712"]["status"] == "applied", "ambiguous email touches nothing"
        state = load_json(ws.data / "responses.json")
        assert set(state["messages"]) == {"<a>", "<b>", "<c>", "<d>", "<e>"}

        again = R.process(ws, mails)
        assert again["processed"] == 0 and again["already_seen"] == 5, "Message-ID dedupe"


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
