"""Offline tests for the Oracle Taleo fetcher (jobhunter/taleo.py).

  python server/tests/test_taleo.py

Fixtures in tests/fixtures/taleo/ are trimmed real responses from the University of Colorado
section (cu.taleo.net/2, captured 2026-11-07): jobsearch.ftl, rest/jobboard/searchjobs per organization,
and one jobdetail.ftl. No network is used; a fake requests.Session serves them.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobhunter import taleo  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "taleo"
FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


class FakeResp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    calls = []

    def __init__(self):
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        FakeSession.calls.append(("GET", url))
        if url.endswith("/careersection/2/jobsearch.ftl"):
            return FakeResp((FIX / "jobsearch.html").read_text(encoding="utf-8"))
        if "jobdetail.ftl?job=41645" in url:
            return FakeResp((FIX / "jobdetail_41645.html").read_text(encoding="utf-8"))
        return FakeResp("<html><title>Career Section Unavailable</title></html>")

    def post(self, url, params=None, json=None, headers=None, timeout=None):
        FakeSession.calls.append(("POST", url, params.get("portal"), (headers or {}).get("tz"), json))
        sel = json["advancedSearchFiltersSelectionParam"]["searchFilterSelections"][0]["selectedValues"]
        f = FIX / f"search_{sel[0] if sel else 'all'}_p{json['pageNo']}.json"
        if f.exists():
            return FakeResp(f.read_text(encoding="utf-8"))
        return FakeResp('{"requisitionList": [], "pagingData": {"currentPageNo": %d, "pageSize": 25, "totalCount": 27}}' % json["pageNo"])


taleo._session = FakeSession
taleo._sleep = lambda s: None


def test_tokens():
    check("token basic", taleo.parse_token("cu.taleo.net/2"), ("cu.taleo.net", "2", None))
    check("token org", taleo.parse_token("cu.taleo.net/2/C0001"), ("cu.taleo.net", "2", "C0001"))
    check("token url-ish", taleo.parse_token("https://cu.taleo.net/2/"), ("cu.taleo.net", "2", None))
    try:
        taleo.parse_token("cu.taleo.net")
        FAILS.append("token without section should raise")
    except ValueError:
        pass


def test_section_page():
    portal, title, orgs = taleo.parse_section_page((FIX / "jobsearch.html").read_text(encoding="utf-8"))
    check("portal", portal, "101430233")
    check("section title", title, "University of Colorado")
    check("orgs (deduped, page order)", orgs, [("2100103016", "B0001", "Boulder Campus"),
                                               ("4400103016", "C0001", "Colorado Springs Campus"),
                                               ("4300103016", "S0001", "System Administration"),
                                               ("23000103016", "U0001", "Anschutz Med Campus or Denver")])


def test_fetch_all():
    FakeSession.calls = []
    jobs = list(taleo.fetch_taleo("cu.taleo.net/2", {}))
    ids = [j["job_id"] for j in jobs]
    check("all ids", ids, ["tl:cu:41253", "tl:cu:41525", "tl:cu:41570", "tl:cu:41293", "tl:cu:41594",
                           "tl:cu:41645", "tl:cu:41644"])
    j = [x for x in jobs if x["job_id"] == "tl:cu:41645"][0]
    check("title (en dash kept)", j["title"], "Instructor – Genetics Counselor")
    check("company", j["company"], "University of Colorado - Anschutz Med Campus or Denver")
    check("location", j["location"], "Aurora")
    check("posted", j["posted"], "2026-11-06T00:00:00+00:00")
    check("url", j["url"], "https://cu.taleo.net/careersection/2/jobdetail.ftl?job=41645&lang=en")
    check("apply_url", j["apply_url"], j["url"])
    check("ats", j["ats"], "taleo")
    check("detail", j["_detail"], j["url"])
    check("required keys", sorted(k for k in j if not k.startswith("_")),
          sorted(["job_id", "ats", "company", "title", "location", "url", "apply_url", "posted", "description", "salary_text"]))
    uccs = [x for x in jobs if x["job_id"] == "tl:cu:41570"][0]
    check("page 2 company", uccs["company"], "University of Colorado - Colorado Springs Campus")
    posts = [c for c in FakeSession.calls if c[0] == "POST"]
    check("tz header sent", {c[3] for c in posts}, {"GMT-07:00"})
    check("portal param", {c[2] for c in posts}, {"101430233"})
    # Boulder 1 page (empty), Anschutz 1, System 1, UCCS pages 1+2 (totalCount 27 -> 2 pages, stops at 2)
    check("POST count", len(posts), 5)


def test_fetch_one_org_and_page_cap():
    FakeSession.calls = []
    jobs = list(taleo.fetch_taleo("cu.taleo.net/2/C0001", {}))
    check("uccs only", [j["job_id"] for j in jobs], ["tl:cu:41253", "tl:cu:41525", "tl:cu:41570"])
    FakeSession.calls = []
    jobs = list(taleo.fetch_taleo("cu.taleo.net/2/4400103016", {"taleo_max_pages": 1}))
    check("page cap", len(jobs), 2)
    try:
        list(taleo.fetch_taleo("cu.taleo.net/2/X9999", {}))
        FAILS.append("unknown org should raise")
    except ValueError:
        pass
    try:
        list(taleo.fetch_taleo("cu.taleo.net/7", {}))
        FAILS.append("unavailable section should raise")
    except RuntimeError:
        pass


def test_detail():
    job = {"_detail": "https://cu.taleo.net/careersection/2/jobdetail.ftl?job=41645&lang=en",
           "company": "University of Colorado", "salary_text": "", "description": ""}
    taleo.taleo_detail(job)
    d = job["description"]
    check("desc has summary", "provides genetic counseling services" in d, True)
    check("desc no html", "<" in d or "%3C" in d, False)
    check("desc colon unescaped", "Job Summary:" in d, True)
    check("salary", job["salary_text"], "$79,000 - $97,000 per year")
    check("company from detail org", job["company"], "University of Colorado - Anschutz Med Campus or Denver")
    bad = {"_detail": "https://cu.taleo.net/careersection/2/jobdetail.ftl?job=1&lang=en", "company": "X",
           "salary_text": "", "description": ""}
    taleo.taleo_detail(bad)  # no initialHistory -> unchanged, no exception
    check("detail missing", bad["description"], "")


def test_salary():
    check("annual range", taleo.salary_from_text("Salary: $79,000 - $97,000"), "$79,000 - $97,000 per year")
    check("hourly", taleo.salary_from_text("Hiring range: $25.00 - $30.00 per hour"), "$52,000 - $62,400 per year")
    check("monthly", taleo.salary_from_text("Pay: $6,000 to $8,000 monthly"), "$72,000 - $96,000 per year")
    check("heading above", taleo.salary_from_text("Benefits worth $10 - $20 per hour\nHIRING RANGE:\nIT Prof: $90,000 – $110,000"),
          "$90,000 - $110,000 per year")
    check("none", taleo.salary_from_text("No dollar figures here."), "")


def test_requisition_columns():
    row = {"contestNo": "9", "jobId": "1", "linkedColumn": 1, "locationsColumns": [0],
           "column": ['["Hybrid", "Aurora"]', "Network Engineer", "Posted Jan 5, 2026"]}
    check("custom columns", taleo.parse_requisition(row),
          ("9", "Network Engineer", "Hybrid | Aurora", "2026-01-05T00:00:00+00:00"))


if __name__ == "__main__":
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
    print("taleo tests: all passed")
