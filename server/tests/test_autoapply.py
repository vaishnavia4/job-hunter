"""Offline tests for the pure helpers of the token-free apply runner and the one-call tailor."""
import json

from jobhunter import autoapply as A
from jobhunter import tailor as T


def test_detect_ats():
    assert A.detect_ats("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/X_R1") == "workday"
    assert A.detect_ats("https://job-boards.greenhouse.io/acme/jobs/123") == "greenhouse"
    assert A.detect_ats("https://jobs.ashbyhq.com/acme/1") == "ashby"
    assert A.detect_ats("https://jobs.lever.co/acme/1") == "other"


def test_apply_url():
    assert A._apply_url("https://a.wd1.myworkdayjobs.com/S/job/Loc/Title_R1?src=x") == "https://a.wd1.myworkdayjobs.com/S/job/Loc/Title_R1/apply"
    assert A._apply_url("https://a.wd1.myworkdayjobs.com/S/job/Loc/Title_R1/apply") == "https://a.wd1.myworkdayjobs.com/S/job/Loc/Title_R1/apply"
    assert A._apply_url("https://a.wd1.myworkdayjobs.com/S/job/Loc/Title_R1/apply/applyManually").endswith("/apply/applyManually")


def test_needs_parsed_and_trimmed():
    log = 'AUTO: STEP: Application Questions\nNEEDS_USER: ' + json.dumps(
        [{"q": "Are you an AI agent?", "options": [str(i) for i in range(30)], "status": "ASK_USER", "answer": None}])
    qs = A._needs(log)
    assert qs[0]["q"] == "Are you an AI agent?" and len(qs[0]["options"]) == 12 and "answer" not in qs[0]
    assert A._needs("AUTO: STEP: Review\nAT REVIEW") == []


def test_ticked_and_tail():
    log = "STEP: Questions\n  TICKED (standing consent): I agree to the terms\nERRORS: Phone is required\n"
    assert A._ticked(log) == ["I agree to the terms"]
    assert "Phone is required" in A._tail(log)


def test_skills_from_tailored(tmp_path):
    (tmp_path / "tailored.json").write_text(json.dumps({"skills": {
        "Cloud": ["Azure (AKS, VNets)", "AWS"], "IaC": ["Terraform", "AWS", "x" * 40]}}), encoding="utf-8")
    assert A._skills(tmp_path) == ["Azure", "AWS", "Terraform"]


def test_js_escape():
    assert A._js("Lowe's \\ Co") == "Lowe\\'s \\\\ Co"


def test_tailor_parse_strips_fences():
    assert T._parse('```json\n{"decision": {"decision": "apply"}}\n```')["decision"]["decision"] == "apply"
    assert T._parse('Here it is: {"a": 1} done')["a"] == 1


def test_bullet_counts():
    m = {"experience": [{"company": "A", "bullets": ["x"] * 30}, {"company": "B", "bullets": ["x"] * 7},
                        {"company": "C", "bullets": ["x"] * 8}, {"company": "D", "bullets": ["x"] * 6}]}
    assert T._bullet_counts(m) == {"A": "8-10", "B": "about 5", "C": "about 5", "D": "about 3"}


def test_repair_review_uses_line_after_explanation():
    para = "As a U.S. company we must comply with export control laws in every country where we operate. The information is used to decide on a license.*"
    text = chr(10).join(["Are you a citizen of Iran?", "", chr(160), chr(160) + " " + para, "", "No", "", "Gender", "Female"])
    rv = {"fields": [{"label": "Are you a citizen of Iran?", "value": para, "required": False}, {"label": "Gender", "value": "Female"}], "page_text": text}
    assert A._repair_review(rv) == 1
    assert rv["fields"][0] == {"label": "Are you a citizen of Iran?", "value": "No", "required": True} and rv["fields"][1]["value"] == "Female"
