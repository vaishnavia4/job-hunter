"""`python -m jobhunter batch [--limit 10]`: tailor + apply the best `new` jobs with the cheap commands, one after another.
Only jobs on sites the token-free runner supports (Workday, Greenhouse). Prints a compact JSON summary and writes
reports/batch_<date>.json. Jobs that need a person or a model are left with their status and listed under `attention`."""
import json
import re
import time
from pathlib import Path

from . import autoapply, tailor
from .workspace import Workspace, today


def _tier(r):
    """Her priority (2026-11-23): Colorado/Denver and remote jobs go first, everything else after."""
    t = ((r.get("flags") or "") + " " + (r.get("location") or "")).lower()
    return 0 if re.search(r"colorado|denver|boulder|englewood|,\s*co\b|remote|work from home", t) else 1


def _pay(r):
    n = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]{4,}", r.get("salary") or "")]
    return max(n) if n else 0


def _claim(ws, job_id):
    """True when this process may work on the job: nobody else has claimed it today (two batches side by side)."""
    import os
    f = ws.data / "batch_claims.json"
    with ws.tracker_lock():
        d = {}
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
        d = {k: v for k, v in d.items() if v.get("date") == today()}
        me = f"{os.getpid()}"
        if job_id in d and d[job_id].get("by") != me:
            return False
        d[job_id] = {"date": today(), "by": me, "slot": autoapply.SLOT}
        f.write_text(json.dumps(d, indent=1), encoding="utf-8")
    return True


def run(ws, limit=10, model="sonnet", submit=None, log=print, any_location=False):
    ws = ws if isinstance(ws, Workspace) else Workspace(ws)
    cap = int((ws.cfg("search", {}) or {}).get("max_per_company_per_day") or 2)
    rows = ws.load_tracker()
    cand = [r for r in rows if r["status"] in ("new", "tailored") and autoapply.detect_ats(r.get("apply_url") or r.get("url")) != "other"]
    # default: Colorado/remote first (her priority); any_location: best score and pay first, wherever the job is
    cand.sort(key=lambda r: (r["status"] != "tailored", 0 if any_location else _tier(r), -float(r.get("score") or 0), -_pay(r)))
    out, per_co, t0, cost = [], {}, time.time(), 0.0
    for r in rows:
        if r["status"] == "applied" and r.get("date_applied") == today():
            per_co[r["company"]] = per_co.get(r["company"], 0) + 1
    for r in cand:
        if len(out) >= limit:
            break
        if per_co.get(r["company"], 0) >= cap:
            continue
        if not _claim(ws, r["job_id"]):
            continue  # another batch is on it
        cur = next((x for x in ws.load_tracker() if x["job_id"] == r["job_id"]), r)
        if cur["status"] not in ("new", "tailored"):
            continue  # changed since this batch started
        applied_today = sum(1 for x in ws.load_tracker() if x["company"] == r["company"] and x["status"] == "applied" and x.get("date_applied") == today())
        if applied_today >= cap:
            continue
        per_co[r["company"]] = per_co.get(r["company"], 0) + 1
        item = {"job_id": r["job_id"], "company": r["company"][:40], "title": r["title"][:60]}
        log(f"[{len(out) + 1}/{limit}] {item['company']} - {item['title']}")
        pre = autoapply.precheck(ws, r)
        if pre.get("closed"):
            ws.set_fields(r["job_id"], status="closed", notes="posting closed (checked " + today() + ")")
            item["result"] = "closed"
            out.append(item)
            log("    posting closed - skipped before tailoring")
            continue
        if r["status"] == "new":
            t = tailor.tailor(ws, r["job_id"], model=model, jd_text=pre.get("jd"))
            cost += t.get("cost_usd") or 0
            item.update(tailor=t.get("status"), fit=t.get("fit"), tailor_cost=t.get("cost_usd"),
                        note=(t.get("reason") or t.get("detail") or ("truth check: " + str(t.get("truth_check")) if t.get("status") == "needs_review" else ""))[:200])
            if t.get("status") != "tailored":
                item["result"] = t.get("status")
                out.append(item)
                log(f"    tailor: {t.get('status')} {item['note'][:100]}")
                continue
        a = autoapply.apply(ws, r["job_id"], submit=submit)
        item.update(result=a.get("status"), seconds=a.get("seconds"),
                    apply_note=(a.get("detail") or "; ".join(a.get("problems") or []) or json.dumps(a.get("questions") or "")[:400] if a.get("status") not in ("applied", "ready") else ""),
                    consents=a.get("consents_ticked"), attestations=a.get("attestations"))
        item = {k: v for k, v in item.items() if v not in (None, "", [], {})}
        out.append(item)
        log(f"    apply: {a.get('status')} {str(item.get('apply_note', ''))[:160]}")
    counts = {}
    for i in out:
        counts[i["result"]] = counts.get(i["result"], 0) + 1
    res = {"date": today(), "jobs": len(out), "counts": counts, "model_cost_usd": round(cost, 3), "minutes": round((time.time() - t0) / 60, 1),
           "attention": [i for i in out if i["result"] not in ("applied", "skipped")], "items": out}
    rep = ws.root / "reports"
    rep.mkdir(exist_ok=True)
    f = rep / f"batch_{today()}_{time.strftime('%H%M')}.json"
    f.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    res["report"] = str(f)
    return res
