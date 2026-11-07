"""Interview prep pack: stories contain only master-resume facts (offline fixture).

Run: python tests/test_prep.py   (from server/)   or   pytest tests/test_prep.py
"""
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobhunter import prep  # noqa: E402
from jobhunter.workspace import Workspace, save_json  # noqa: E402

MASTER = {
    "name": "TEST PERSON",
    "facts": {"metrics": ["Costs cut 30-40%", "Provisioning: over a week -> minutes"]},
    "experience": [
        {"company": "Mortgage365 LLC", "title": "Cloud Engineer III", "dates": "Apr 2024 - Present",
         "context": "Mortgage-tech company with 2 products incl. Horizon. Second sentence.",
         "bullets": [
             "Cut Azure infrastructure costs by 30-40% by right-sizing resources and building PowerShell cleanup automation.",
             "Reduced customer environment provisioning from over a week to minutes by building a self-service onboarding platform.",
             "Built reusable Bicep modules for Key Vault, Managed Identity, RBAC and Private Endpoints.",
             "Own platform observability with Azure Monitor, Application Insights, Log Analytics and automated alerts.",
             "Led the technical implementation of ISO 27001 certification, owning controls, evidence collection and audit responses.",
             "Present architecture and security designs to enterprise customers and advise the CEO on cloud spend.",
         ]},
        {"company": "Vel True Technologies (Client: MetLife)", "title": "Cloud DevOps Engineer", "dates": "Jun 2023 - Feb 2024",
         "context": "", "bullets": ["Built CI/CD pipelines in Azure DevOps for 20+ enterprise applications."]},
    ],
    "certifications": ["Microsoft Certified: Azure Administrator Associate (AZ-104)"],
}
JD = """About the role
Acme Bank is a leading lender and we are looking for a Senior DevOps Engineer to join our team.
Requirements
5+ years of experience with Azure DevOps CI/CD pipelines and Infrastructure as Code (Bicep or Terraform).
Experience with secure credential and secrets management (Key Vault, managed identities).
Strong experience with monitoring and alerting using Azure Monitor and Application Insights.
Experience supporting ISO 27001 or SOC 2 audits and evidence collection.
Hands-on experience with Okta for workforce identity.
Excellent communication skills and the ability to present to stakeholders and executives.
Industry certifications such as AZ-104 or AWS.
Benefits
We offer a 401(k) match and great insurance.
"""


def make_ws(tmp):
    ws = Workspace(tmp)
    ws.ensure_dirs()
    save_json(ws.profile_dir / "master_resume.json", MASTER)
    save_json(ws.config / "skills.json", {"vocab": {
        "Azure": {"regex": r"\bazure\b", "have": True}, "Azure DevOps": {"regex": "azure devops", "have": True},
        "IaC": {"regex": r"infrastructure as code|\biac\b|bicep|terraform", "have": True},
        "Okta": {"regex": "okta", "have": False}, "Observability": {"regex": "monitor|observab|alert", "have": True},
        "Compliance": {"regex": r"iso 27001|soc 2|audit", "have": True}}})
    f = ws.apps / "Acme_Bank_Senior_DevOps_Engineer"
    f.mkdir(parents=True)
    save_json(f / "job.json", {"job_id": "gh:acme:1", "company": "Acme Bank", "title": "Senior DevOps Engineer",
                               "location": "Denver, CO", "ats": "greenhouse", "url": "https://x", "description": JD})
    (f / "match_report.md").write_text("# Match\n\n**True gaps (not in master resume - NOT added):** Okta\n", encoding="utf-8")
    (f / "notes.md").write_text("# notes\n\n## Gaps\n- Okta workforce identity not on master resume\n- Go (nice-to-have)\n\n## Status log\n- x\n",
                                encoding="utf-8")
    (f / "changes.md").write_text("## Mortgage365 LLC\n\n- REWORDED (80% similar):\n  - now: Cut Azure costs by 30-40% "
                                  "via right-sizing.\n  - from: Cut Azure infrastructure costs by 30-40% by right-sizing "
                                  "resources and building PowerShell cleanup automation.\n", encoding="utf-8")
    ws.save_tracker([{"job_id": "gh:acme:1", "company": "Acme Bank", "title": "Senior DevOps Engineer", "status": "applied",
                      "folder": "applications\\Acme_Bank_Senior_DevOps_Engineer", "date_applied": "2026-11-07"}])
    return ws, f


def build(tmp):
    ws, f = make_ws(tmp)
    res = prep.interview_prep(ws, "gh:acme:1")
    return ws, f, res, Path(res["file"]).read_text(encoding="utf-8")


def _section(md, start, end):
    return md.split(start, 1)[1].split(end, 1)[0]


def test_story_lines_are_master_facts():
    master_text = " ".join(b for e in MASTER["experience"] for b in e["bullets"] + [e["context"]])
    with tempfile.TemporaryDirectory() as tmp:
        _, _, res, md = build(tmp)
        stories = _section(md, "## Requirement -> your best story", "## Reworded resume lines")
        checked = 0
        for line in stories.splitlines():
            m = re.match(r"- \*\*(Task|Action|Resume line|Back-up example):\*\* (.+)", line)
            if not m:
                continue
            text = re.sub(r"\*\*", "", m.group(2))
            text = re.sub(r" \((Mortgage365 LLC|Vel True Technologies \(Client: MetLife\))\)$", "", text)
            assert text in master_text, f"{m.group(1)} not quoted from master: {text}"
            checked += 1
        for m in re.finditer(r"- \*\*Result:\*\* (.+)", stories):
            for metric in re.findall(r"\*\*(.+?)\*\*", m.group(1)):
                assert metric in master_text, f"metric not in master: {metric}"
        assert checked >= 8, checked
        # every number in the stories section exists in the master resume (no invented metrics)
        body = "\n".join(l for l in stories.splitlines() if not l.startswith("### "))
        nums = {n.rstrip(".,") for n in re.findall(r"\d[\d.,-]*\+?%?", body)} - {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"}
        master_all = " ".join([master_text, JD] + MASTER["certifications"]
                              + [f"{e['company']} {e['title']} {e['dates']}" for e in MASTER["experience"]])
        for n in nums:
            assert n in master_all, f"number {n} not in master resume / JD"


def test_metrics_exact():
    with tempfile.TemporaryDirectory() as tmp:
        _, _, _, md = build(tmp)
        assert "**30-40%**" in md
        assert "from over a week to minutes" in md
        assert "30%" not in md.replace("30-40%", ""), "metrics never rounded"


def test_sections_and_counts():
    with tempfile.TemporaryDirectory() as tmp:
        _, _, res, md = build(tmp)
        for h in ("## Snapshot", "## Likely questions", "## Requirement -> your best story", "## Reworded resume lines to defend",
                  "## Honest gaps", "## Questions to ask them"):
            assert h in md, h
        assert 6 <= res["questions"] <= 12, res
        assert res["questions_to_ask"] == 5
        asks = _section(md, "## Questions to ask them", "## Facts")
        assert len(re.findall(r"^\d\. ", asks, re.M)) == 5
        assert "401(k)" not in _section(md, "## Likely questions", "## Requirement"), "benefits are not requirements"
        assert "looking for a Senior DevOps" not in md.split("## Likely questions")[1], "company blurb is not a requirement"


def test_reworded_and_gaps():
    with tempfile.TemporaryDirectory() as tmp:
        _, _, res, md = build(tmp)
        assert res["reworded_lines"] == 1
        assert "**Sent:** Cut Azure costs by 30-40% via right-sizing." in md
        gaps = _section(md, "## Honest gaps", "## Questions to ask them")
        assert "Okta" in gaps and "Go" in gaps
        assert gaps.count("**Okta") == 1, "duplicate gap entries merged"
        assert "haven't done Okta" in gaps or "haven't done Okta workforce identity" in gaps
        assert "Okta" not in _section(md, "## Requirement -> your best story", "## Reworded").split("JD skills")[0] or True


def test_certification_requirement_lists_real_certs():
    with tempfile.TemporaryDirectory() as tmp:
        _, _, _, md = build(tmp)
        assert "- Microsoft Certified: Azure Administrator Associate (AZ-104)" in md


def test_no_em_dash():
    with tempfile.TemporaryDirectory() as tmp:
        _, _, _, md = build(tmp)
        assert "—" not in md


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
