"""H-1B sponsorship history from the USCIS H-1B Employer Data Hub (public, per employer per fiscal year).

"Initial" approvals are new H-1B employment; "Continuing" approvals include extensions, amendments and
change of employer (transfers). Used only when the profile says the user needs sponsorship: known sponsors
get a score bonus, employers with no record a small penalty (names do not always match, so never a skip).

  python -m jobhunter --workspace WS h1b-import [--years 2022,2023]
  python -m jobhunter --workspace WS h1b "Plaid"
"""
import csv
import io
import re

import requests

from .workspace import Workspace, load_json, save_json

URL = "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-{year}.csv"
DEFAULT_YEARS = (2022, 2023)
_SUFFIX = re.compile(
    r"\b(INC|INCORPORATED|LLC|L L C|LLP|LP|PLLC|PC|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|THE|USA|US|NA|N A|"
    r"HOLDINGS?|GROUP|TECHNOLOGIES|TECHNOLOGY|SERVICES|SOLUTIONS|ENTERPRISES?|AMERICA|AMERICAS|OF)\b")


# brand name -> legal filing name (USCIS lists legal names). Extend per workspace in config/search.json h1b.aliases.
ALIASES = {
    "freddie mac": "Federal Home Loan Mortgage Corp",
    "freddiemac": "Federal Home Loan Mortgage Corp",
    "fannie mae": "Federal National Mortgage Association",
    "nrel": "Alliance for Sustainable Energy LLC",
    "national renewable energy laboratory": "Alliance for Sustainable Energy LLC",
    "intermountain health": "IHC Health Services Inc DBA Intermountain Healthcare",
    "ucar": "Univ Corp for Atmospheric Research",
    "amazon": "Amazon.com Services LLC",
    "aws": "Amazon Web Services Inc",
    "tiaa": "Teachers Insurance Annuity Assn TIAA",
    "meta": "Meta Platforms Inc",
    "facebook": "Meta Platforms Inc",
    "ncar": "Univ Corp for Atmospheric Research",
    "mastercard": "Mastercard International Inc",
    "mastercard technologies": "Mastercard International Inc",
    "mastercard technologies llc": "Mastercard International Inc",
    "us: mastercard technologies, llc": "Mastercard International Inc",
}
_ABBR = [(r"\bUNIVERSITY\b", "UNIV"), (r"\bHEALTHCARE\b", "HEALTH CARE"), (r"\bHOSPITALS?\b", "HOSP"),
         (r"\bINTERNATIONAL\b", "INTL"), (r"\bNATIONAL\b", "NATL"), (r"\bASSOCIATION\b", "ASSN"), (r"\bCOLLEGE\b", "COLL")]


def normalize(name):
    s = re.sub(r"[^A-Z0-9 ]+", " ", (name or "").upper().replace("&", " AND "))
    for pat, rep in _ABBR:
        s = re.sub(pat, rep, s)
    s = _SUFFIX.sub(" ", s)
    s = re.sub(r"\b(AND|FOR|DBA)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _path(ws):
    return ws.data / "h1b_sponsors.json"


def import_data(ws: Workspace, years=DEFAULT_YEARS, log=print):
    """Download the USCIS CSVs and aggregate per normalized employer name."""
    agg = {}
    for year in years:
        r = requests.get(URL.format(year=year), headers={"User-Agent": "Mozilla/5.0 (job-hunter)"}, timeout=120)
        r.raise_for_status()
        rows = csv.DictReader(io.StringIO(r.content.decode("utf-8-sig", "replace")))
        n = 0
        for row in rows:
            key = normalize(row.get("Employer"))
            if not key:
                continue
            num = lambda k: int(re.sub(r"\D", "", row.get(k) or "0") or 0)
            a = agg.setdefault(key, {"name": row.get("Employer", "").strip(), "initial": 0, "continuing": 0,
                                     "denied": 0, "states": []})
            a["initial"] += num("Initial Approval")
            a["continuing"] += num("Continuing Approval")
            a["denied"] += num("Initial Denial") + num("Continuing Denial")
            st = (row.get("State") or "").strip()
            if st and st not in a["states"]:
                a["states"].append(st)
            n += 1
        log(f"FY{year}: {n} employer rows")
    data = {"years": list(years), "source": "USCIS H-1B Employer Data Hub", "employers": agg}
    save_json(_path(ws), data)
    return {"employers": len(agg), "years": list(years), "file": str(_path(ws))}


_CACHE = {}


def _load(ws):
    p = _path(ws)
    key = str(p)
    if key not in _CACHE:
        _CACHE[key] = (load_json(p, {}) or {}).get("employers", {})
    return _CACHE[key]


def lookup(ws: Workspace, company, aliases=None):
    """Best sponsor record for a company name: alias -> exact normalized match -> (multi-word names only) the
    largest employer whose normalized name starts with it ('Prysm Health' would never match 'PRYSM CAPITAL').
    One-word names need an exact match ('Harvey' must not become 'HARVEY NASH')."""
    emp = _load(ws)
    if not emp:
        return None
    raw = re.split(r"\s+-\s+", company or "")[0].strip()  # 'State of Colorado - OIT' -> 'State of Colorado'
    al = {k.lower(): v for k, v in {**ALIASES, **(aliases or {})}.items()}
    raw = al.get(raw.lower(), raw)
    key = normalize(raw)
    if not key:
        return None
    if key in emp:
        rec = dict(emp[key])
    elif len(key.split()) >= 2 and len(key) >= 6:
        cands = [v for k, v in emp.items() if k == key or k.startswith(key + " ")]
        if not cands:
            return None
        rec = dict(max(cands, key=lambda v: v["initial"] + v["continuing"]))
    else:
        return None
    rec["total"] = rec["initial"] + rec["continuing"]
    return rec


def score_adjust(ws: Workspace, company, cfg=None):
    """(points, flag) for the search scorer. cfg = config/search.json 'h1b' section (optional)."""
    c = cfg or {}
    rec = lookup(ws, company, c.get("aliases"))
    if not _load(ws):
        return 0, ""
    if rec is None:
        return -int(c.get("penalty_none", 3)), "no H-1B record found"
    strong = int(c.get("strong_min", 5))
    if rec["total"] >= strong:
        return int(c.get("bonus_strong", 8)), f"H-1B sponsor ({rec['total']} approvals, {rec['continuing']} continuing)"
    if rec["total"] > 0:
        return int(c.get("bonus_some", 3)), f"some H-1B history ({rec['total']} approvals)"
    return -int(c.get("penalty_none", 3)), "no H-1B approvals on record"
