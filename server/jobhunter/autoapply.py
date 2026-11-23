"""Token-free application runner: `python -m jobhunter --workspace WS apply <job_id|folder> [--no-submit]`.

Drives one whole application with the proven page scripts (workspace `.playwright-mcp/*.js`) through a small Node
shim (runner/run.mjs) attached to a dedicated Chrome over CDP: sign in -> fill -> read back -> final-check -> submit ->
screenshot -> tracker. No model is involved; the command prints ONE compact JSON result. A model (or the user) is only
needed when the result is not `applied`/`ready`:

  needs_answers       questions the answer bank cannot answer -> record the answers, run `apply` again (it resumes)
  final_check_failed  final-check problems (includes AI-agent disclosure STOP) -> fix / hand to the user
  blocked             captcha or a box only the user may tick -> BLOCKERS.md
  needs_claude        a path this runner does not automate yet (password reset, unsupported ATS, unclear page)
  error               something broke; see `detail` and the log file

The browser window stays open between runs, so a re-run continues from the step the form is on.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import answers
from .workspace import Workspace, load_json, today

CDP_PORT = 9377
ANSWER_PORT, CRED_PORT, URL_PORT = 47615, 47613, 47614
RUNNER = Path(__file__).resolve().parent / "runner"


def detect_ats(url):
    u = (url or "").lower()
    if "myworkdayjobs.com" in u or "myworkdaysite.com" in u:
        return "workday"
    if "greenhouse.io" in u or "gh_jid=" in u:
        return "greenhouse"
    return "other"


# ---------------------------------------------------------------- local helper servers (127.0.0.1 only)
def _serve(port, handler):
    try:
        s = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError:
        return None  # already running (e.g. scripts/answer_server.py) - reuse it
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def _json_handler(get=None, post=None):
    class H(BaseHTTPRequestHandler):
        def _send(self, obj, code=200):
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                self._send(get(self.path) if get else {})
            except Exception as e:
                self._send({"status": "ERROR", "error": str(e)[:200]}, 500)

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", 0))
                self._send(post(self.path, json.loads(self.rfile.read(n) or b"{}")) if post else {})
            except Exception as e:
                self._send({"status": "ERROR", "error": str(e)[:200]}, 500)

        def log_message(self, *a):
            pass
    return H


def _answer_post(ws):
    def post(path, req):
        if path == "/save_review":
            apps = (ws.root / "applications").resolve()
            folder = (apps / str(req.get("folder", ""))).resolve()
            if folder.parent != apps or not folder.is_dir():
                raise ValueError("unknown application folder")
            (folder / "review.json").write_text(json.dumps(req["review"], indent=1, ensure_ascii=False), encoding="utf8")
            return {"saved": str(folder / "review.json"), "fields": len(req["review"].get("fields", []))}
        return answers.lookup(ws, req.get("q", ""), req.get("options") or None)
    return post


_CRED = {}


def _cred_for(ws, host):
    c = load_json(ws.profile_dir / "credentials.json", {}) or {}
    tenant = (host or "").split(".")[0].lower()
    for key in ("job_sites_long", "job_sites_16"):
        used = [str(u).lower().split(" ")[0].split(".")[0] for u in ((c.get(key) or {}).get("used_on") or [])]
        if tenant and tenant in used:
            return c[key]
    return c.get("job_sites_default") or c.get("workday") or {}


# ---------------------------------------------------------------- browser
def _chrome_exe():
    cands = [os.environ.get("JH_CHROME"), shutil.which("chrome"),
             r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", shutil.which("google-chrome"), shutil.which("chromium")]
    return next((c for c in cands if c and Path(c).exists()), None)


def _cdp_up():
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2).read()
        return True
    except Exception:
        return False


def ensure_browser(ws):
    if _cdp_up():
        return True
    exe = _chrome_exe()
    if not exe:
        return False
    prof = ws.root / ".browser-apply"
    prof.mkdir(exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([exe, f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={prof}", "--no-first-run",
                      "--no-default-browser-check", "--window-size=1366,900", "about:blank"],
                     creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    for _ in range(40):
        if _cdp_up():
            return True
        time.sleep(0.5)
    return False


class Run:
    """One application run: shared state + the page-script caller."""

    def __init__(self, ws, row, folder):
        self.ws, self.row, self.folder = ws, row, folder
        self.work = folder / ".apply"
        self.work.mkdir(exist_ok=True)
        self.log = []
        self.steps = []
        self.host = ""
        self.t0 = time.time()

    def note(self, s):
        self.steps.append(s)

    def page(self, script=None, goto=None, screenshot=None, timeout=600, text=None, name="step"):
        """Run one page function (file path, or `text` written to the work dir). Returns the script's result."""
        if text is not None:
            script = self.work / f"_{name}.js"
            script.write_text(text, encoding="utf-8")
        cmd = ["node", str(RUNNER / "run.mjs"), "--cdp", f"http://127.0.0.1:{CDP_PORT}"]
        if self.host and not goto:
            cmd += ["--host", self.host]
        if script:
            cmd += ["--script", str(script)]
        if screenshot:
            cmd += ["--screenshot", str(screenshot)]
        env = dict(os.environ)
        env.pop("JH_GOTO", None)
        if goto:
            env["JH_GOTO"] = goto
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
            line = (p.stdout or "").strip().splitlines()[-1] if (p.stdout or "").strip() else ""
            res = json.loads(line) if line else {"ok": False, "error": (p.stderr or "no output")[-400:]}
        except subprocess.TimeoutExpired:
            res = {"ok": False, "error": f"page script timed out after {timeout}s"}
        except Exception as e:
            res = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
        if res.get("host"):
            self.host = res["host"]
        self.log.append({"t": round(time.time() - self.t0), "name": name if text is not None else (Path(script).name if script else "goto"),
                         "ok": res.get("ok"), "path": res.get("path"), "result": res.get("result"), "error": res.get("error")})
        if not res.get("ok"):
            raise RuntimeError(res.get("error") or "page script failed")
        return res.get("result")

    def done(self, status, **kw):
        out = {"status": status, "job_id": self.row["job_id"], "company": self.row["company"], "title": self.row["title"][:80],
               "steps": self.steps, **{k: v for k, v in kw.items() if v not in (None, "", [], {})},
               "seconds": round(time.time() - self.t0)}
        logf = self.folder / "apply_log.json"
        try:
            logf.write_text(json.dumps({"result": out, "log": self.log}, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
            out["log"] = str(logf)
        except Exception:
            pass
        return out


# ---------------------------------------------------------------- script building (same templates the MCP flow used)
def _scripts(ws):
    return ws.root / ".playwright-mcp"


def _fn(path):
    s = Path(path).read_text(encoding="utf8")
    s = "\n".join(line for line in s.splitlines() if not line.lstrip().startswith("//"))
    return s.strip().rstrip(";")


def _js(s):
    return str(s).replace("\\", "\\\\").replace("'", "\\'")


def _review_script(ws, tpl, run):
    s = (_scripts(ws) / tpl).read_text(encoding="utf8")
    for k, v in (("__FOLDER__", run.folder.name), ("__COMPANY__", _js(run.row["company"])), ("__TITLE__", _js(run.row["title"])),
                 ("__JOBID__", run.row["job_id"])):
        s = s.replace(k, v)
    return s


def _skills(folder):
    t = load_json(folder / "tailored.json", {}) or {}
    sk = []
    for v in (t.get("skills") or {}).values():
        for s in v:
            s = s.split(" (")[0].strip()
            if len(s) <= 30 and s not in sk:
                sk.append(s)
    return sk[:12]


WD_COMBO = r"""async (page) => {
  const MYINFO = %s;
  const EXP = %s;
  const AUTO = %s;
  const out = [];
  const step = async () => (await page.locator('[data-automation-id="progressBarActiveStep"]').innerText().catch(() => '')).replace(/\s+/g, ' ');
  const next = async () => { await page.keyboard.press('Escape').catch(() => {}); await page.evaluate(() => document.querySelector('[data-automation-id="pageFooterNextButton"]').click()); await page.waitForTimeout(6500); const e = await page.locator('[data-automation-id="errorMessage"]:visible').allInnerTexts(); if (e.length) out.push('ERRORS after next: ' + e.join(' | ')); return !e.length; };
  if (/My Information/.test(await step())) { const r = await MYINFO(page); out.push('MYINFO: ' + r.join('; ')); if (r.some(x => /^(NOT SET|SOURCE NOT SET|EMPTY REQUIRED: (?!none))/.test(x))) return out.join('\n'); if (!(await next())) return out.join('\n'); }
  if (/My Experience/.test(await step())) { const r = await EXP(page); out.push('EXP: ' + r.join('; ')); if (r.some(x => /NOT (SET|FOUND)|FAILED/.test(x))) return out.join('\n'); if (!(await next())) return out.join('\n'); }
  out.push('AUTO: ' + await AUTO(page));
  return out.join('\n');
}"""


def _wd_combo(ws, run):
    sd = _scripts(ws)
    exp_tpl = ws.root / "scripts" / "workday_experience.js"
    pdf = str(run.resume).replace("\\", "/")
    exp = exp_tpl.read_text(encoding="utf8").replace("__RESUME__", pdf).replace("__SKILLS__", json.dumps(_skills(run.folder)))
    tmp = run.work / "_exp.js"
    tmp.write_text(exp, encoding="utf8")
    return WD_COMBO % (_fn(sd / "wd_myinfo.js"), _fn(tmp), _fn(sd / "wd_auto.js"))


def _needs(text):
    m = re.search(r"NEEDS_USER: (\[.*\])", text or "")
    if not m:
        return []
    try:
        qs = json.loads(m.group(1))
    except Exception:
        return [{"q": m.group(1)[:600]}]
    return [{k: (v[:12] if isinstance(v, list) else (v[:300] if isinstance(v, str) else v)) for k, v in q.items() if v not in (None, "", [])} for q in qs]


def _ticked(text):
    return [m.strip()[:120] for m in re.findall(r"TICKED \(standing consent\): ([^\n]+)", text or "")]


def _tail(text, n=700):
    lines = [ln for ln in (text or "").splitlines() if re.search(r"NOT SET|NOT FOUND|FAILED|ERRORS(?!: none)|EMPTY REQUIRED: (?!none)|NOT SELECTED|STEP:|not handled", ln)]
    return " | ".join(ln.strip()[:240] for ln in lines)[-n:] or (text or "")[-n:]


# ---------------------------------------------------------------- final check + submit (shared)
def _repair_review(review):
    """A question followed by an explanation paragraph is read as label=question, value=paragraph (Salesforce
    export-control question, 2026-11-16). The real answer is the next line of the page text: use it."""
    sq = lambda x: re.sub(r"\s+", " ", str(x or "").replace(chr(160), " ")).strip()
    lines = [sq(ln) for ln in str(review.get("page_text") or "").splitlines()]
    lines = [ln for ln in lines if ln]
    fixed = 0
    for f in review.get("fields") or []:
        v = sq(f.get("value"))
        if len(v) < 100 or not (v.endswith("*") or v.endswith(".") or v.endswith("?")):
            continue
        i = next((k for k, ln in enumerate(lines) if ln == v or (len(v) > 60 and ln.startswith(v[:60]))), -1)
        if 0 <= i < len(lines) - 1 and len(lines[i + 1]) < 120:
            f["value"] = lines[i + 1]
            f["required"] = bool(f.get("required")) or v.endswith("*")
            fixed += 1
    return fixed


def _final_check(ws, run):
    review = load_json(run.folder / "review.json", None)
    if not review:
        return None, ["review.json was not written"]
    if _repair_review(review):
        (run.folder / "review.json").write_text(json.dumps(review, indent=1, ensure_ascii=False), encoding="utf8")
    res = answers.final_review(ws, review)
    probs = list(res.get("problems") or [])
    if res.get("hand_to_user"):
        probs.append("HAND TO USER (never auto-answered): " + "; ".join(str(x)[:200] for x in res["hand_to_user"]))
    return res, probs


def _mark_applied(ws, run, extra=""):
    shots = run.folder / "screenshots"
    shots.mkdir(exist_ok=True)
    try:
        run.page(screenshot=shots / "submitted_confirmation.png", name="shot")
    except Exception:
        pass
    note = (run.row.get("notes") or "").strip()
    add = "submitted by apply runner " + today() + (" - " + extra if extra else "")
    ws.set_fields(run.row["job_id"], status="applied", submitted_by="claude", notes=(note + " | " if note else "") + add)


# ---------------------------------------------------------------- Workday
def _apply_url(u):
    u = (u or "").split("?")[0].rstrip("/")
    return u if u.endswith("/apply") or "/apply/" in u else u + "/apply"


def workday(ws, run, submit):
    sd = _scripts(ws)
    url = _apply_url(run.row.get("apply_url") or run.row.get("url"))
    run.host = url.split("/")[2]
    cred = _cred_for(ws, run.host)
    if not cred.get("email"):
        return run.done("error", detail="no job-site credentials in profile/credentials.json")
    _CRED.update(e=cred["email"], p=cred["password"])  # one server for the whole process; each job swaps in its own login
    _serve(CRED_PORT, _json_handler(get=lambda p: dict(_CRED)))

    # 0. is the posting still there?
    run.page(goto=url, name="open posting")
    s0 = run.page(RUNNER / "wd_state.js") or {}
    if s0.get("not_found"):
        ws.set_fields(run.row["job_id"], status="closed", notes=((run.row.get("notes") or "") + " | posting closed (checked " + today() + ")").strip(" |"))
        return run.done("closed", detail="the posting no longer exists on the company site")

    # 1. sign in (create the account + verify by email when the tenant is new)
    state = {}
    for attempt in range(3):
        state = run.page(sd / "wd_auth.js", goto=url if attempt == 0 else None) or {}
        st = state.get("state") if isinstance(state, dict) else None
        run.note(f"auth: {st}")
        if st == "signed_in":
            break
        if st == "verification_sent":
            from . import mail_verify
            r = mail_verify.find_verification(ws, 15, ".".join(run.host.split(".")[:2]), "", 180, log=lambda *a: None) or {}
            if r.get("type") == "link" and r.get("value"):
                run.page(goto=r["value"], name="open activation link")
                run.note("account verified by email link")
                run.page(goto=url, name="back to application")
                continue
            return run.done("needs_claude", detail="account created but no activation link found in Gmail within 3 min (account may already exist: use Forgot your password)", auth=st)
        if st in ("captcha", "needs_user_checkbox"):
            return run.done("blocked", detail=state.get("detail") or st, auth=st)
        if st == "account_exists_wrong_password":
            return run.done("needs_claude", detail="account exists with a different password: run the password-reset flow (CLAUDE.md Workday step 4), then run apply again", auth=st)
        if attempt:
            return run.done("needs_claude", detail="sign-in unclear: " + str(state.get("detail") or state)[:300], auth=st)
    else:
        return run.done("needs_claude", detail="could not sign in after 3 tries", auth=str(state)[:200])

    # 2. get onto the application steps
    s = run.page(RUNNER / "wd_state.js") or {}
    for _ in range(2):  # Workday's own "Something went wrong - please refresh" page (Salesforce 2026-11-16): reload and retry
        if not s.get("broken"):
            break
        run.note("workday error page: refreshed")
        time.sleep(5)
        run.page(sd / "wd_auth.js", goto=url, name="refresh")
        s = run.page(RUNNER / "wd_state.js") or {}
    if s.get("broken"):
        return run.done("error", detail="Workday shows 'Something went wrong - please refresh' even after two reloads; try again later")
    if s.get("already_applied"):
        return run.done("needs_claude", detail="Workday says an application already exists for this job - check Candidate Home, then set the tracker")
    if not s.get("step") and not s.get("submitted"):
        run.page(sd / "wd_auth.js", goto=url + "/applyManually", name="open form")
        s = run.page(RUNNER / "wd_state.js") or {}
    if not s.get("step"):
        return run.done("needs_claude", detail="signed in but the application form did not open" + (" (posting closed?)" if s.get("not_found") else ""), state=s)

    # 3. fill every step up to Review
    text = ""
    for _ in range(3):
        text = run.page(text=_wd_combo(ws, run), name="fill", timeout=900) or ""
        text = text if isinstance(text, str) else json.dumps(text)
        for ln in re.findall(r"STEP: ([^\n]+)", text):
            run.note("step: " + ln.strip()[:40])
        if "AT REVIEW" in text or _needs(text) or re.search(r"NOT SET|NOT FOUND|FAILED|ERRORS(?!: none)|EMPTY REQUIRED: (?!none)|CLICKED BUT NOT SELECTED", text):
            break
    ticked = _ticked(text)
    if _needs(text):
        return run.done("needs_answers", questions=_needs(text), consents_ticked=ticked,
                        how="record each answer (record_answer / `jobhunter record`), then run apply again - it resumes on this step")
    if "AT REVIEW" not in text:
        return run.done("needs_claude", detail=_tail(text), consents_ticked=ticked)

    # 4. read back the Review page and run the final check
    rv = run.page(text=_review_script(ws, "wd_review.js", run), name="review") or {}
    res, probs = _final_check(ws, run)
    if probs:
        return run.done("final_check_failed", problems=[p[:300] for p in probs][:12], consents_ticked=ticked)
    files = (rv.get("files") if isinstance(rv, dict) else None) or []
    table = [f"{r['field'][:60]} = {str(r['value'])[:50]}" for r in (res.get("summary_table") or [])][:40]
    if not submit:
        run.page(screenshot=run.folder / "review_before_submit.png", name="shot")
        return run.done("ready", files=files, consents_ticked=ticked, attestations=res.get("user_must_tick"), summary=table,
                        detail="final check passed; stopped before Submit (--no-submit)")

    # 5. submit + confirm
    sb = run.page(RUNNER / "wd_submit.js") or {}
    if sb.get("submitted"):
        _mark_applied(ws, run)
        return run.done("applied", files=files, consents_ticked=ticked, attestations=res.get("user_must_tick"), fields_checked=len(table))
    if sb.get("captcha"):
        return run.done("blocked", detail="captcha on submit", consents_ticked=ticked)
    return run.done("needs_claude", detail="clicked Submit but no confirmation page: " + json.dumps(sb)[:400], consents_ticked=ticked)


def precheck(ws, row):
    """Before spending anything on tailoring: open the posting in the apply browser. Returns {open, closed, jd}.
    (Workday's JSON endpoints answer 429/403 to scripts, so this goes through the real browser.)"""
    url = (row.get("apply_url") or row.get("url") or "").split("?")[0]
    if detect_ats(url) != "workday":
        return {"open": True, "closed": False, "jd": ""}
    url = re.sub(r"/apply(/.*)?$", "", url.rstrip("/"))
    if not ensure_browser(ws):
        return {"open": True, "closed": False, "jd": ""}
    env = dict(os.environ, JH_GOTO=url)
    try:
        p = subprocess.run(["node", str(RUNNER / "run.mjs"), "--cdp", f"http://127.0.0.1:{CDP_PORT}", "--script", str(RUNNER / "wd_posting.js")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env)
        res = json.loads((p.stdout or "").strip().splitlines()[-1]).get("result") or {}
        return res if isinstance(res, dict) else {"open": True, "closed": False, "jd": ""}
    except Exception:
        return {"open": True, "closed": False, "jd": ""}


# ---------------------------------------------------------------- Greenhouse
def greenhouse(ws, run, submit):
    sd = _scripts(ws)
    url = run.row.get("apply_url") or run.row.get("url")
    run.host = url.split("/")[2]
    fill = (sd / "gh_fill.js").read_text(encoding="utf8").replace("__RESUME__", str(run.resume).replace("\\", "/")) \
        .replace("__COVER__", str(run.cover).replace("\\", "/") if run.cover else "")
    r = run.page(text=fill, goto=url, name="fill", timeout=600) or {}
    if not isinstance(r, dict):
        return run.done("needs_claude", detail="unexpected fill output: " + str(r)[:300])
    run.note(f"filled {len(r.get('filled') or [])} fields")
    needs = [{k: (v[:12] if isinstance(v, list) else v) for k, v in q.items() if v not in (None, "", [])} for q in (r.get("needs_user") or [])]
    if needs or r.get("errors"):
        return run.done("needs_answers", questions=needs, errors=[str(e)[:160] for e in (r.get("errors") or [])][:8],
                        how="record answers (or fill the checkbox groups listed as FIELDSET in the browser), then run apply again")
    rv = run.page(text=_review_script(ws, "gh_review.js", run), name="review") or {}
    res, probs = _final_check(ws, run)
    if isinstance(rv, dict) and rv.get("empty_required"):
        probs.append("empty required fields: " + "; ".join(rv["empty_required"])[:300])
    if probs:
        return run.done("final_check_failed", problems=[p[:300] for p in probs][:12])
    table = [f"{x['field'][:60]} = {str(x['value'])[:50]}" for x in (res.get("summary_table") or [])][:40]
    if not submit:
        run.page(screenshot=run.folder / "review_before_submit.png", name="shot")
        return run.done("ready", files=rv.get("files") if isinstance(rv, dict) else None, summary=table,
                        detail="final check passed; stopped before Submit (--no-submit)")
    sb = run.page(RUNNER / "gh_submit.js") or {}
    if sb.get("submitted"):
        _mark_applied(ws, run)
        return run.done("applied", fields_checked=len(table))
    if sb.get("captcha"):
        return run.done("blocked", detail="captcha on submit")
    return run.done("needs_claude", detail=("email security code requested: " if sb.get("needs_code") else "submit not confirmed: ") + json.dumps(sb)[:400])


# ---------------------------------------------------------------- entry point
def apply(ws, target, submit=None):
    ws = ws if isinstance(ws, Workspace) else Workspace(ws)
    rows = ws.load_tracker()
    key = str(target).replace("\\", "/").rstrip("/").split("/")[-1]
    row = next((r for r in rows if r["job_id"] == target), None) or \
        next((r for r in rows if r.get("folder") and r["folder"].replace("\\", "/").rstrip("/").split("/")[-1] == key), None)
    if not row:
        return {"status": "error", "detail": f"not in tracker: {target}"}
    base = {"job_id": row["job_id"], "company": row["company"]}
    if row["status"] in ("applied", "rejected", "closed"):
        return {"status": "error", **base, "detail": f"tracker status is already '{row['status']}' - never apply twice"}
    prof = ws.profile() or {}
    excl = [str(x).lower() for x in ((prof.get("job_preferences") or {}).get("excluded_companies") or prof.get("excluded_companies") or [])]
    if any(x and x in row["company"].lower() for x in excl):
        return {"status": "error", **base, "detail": "company is on the excluded list"}
    cap = int((ws.cfg("search", {}) or {}).get("max_per_company_per_day") or 2)
    same = [r for r in rows if r["company"] == row["company"] and r["status"] == "applied" and r.get("date_applied") == today()]
    if len(same) >= cap:
        return {"status": "error", **base, "detail": f"already {len(same)} applications to this company today (max {cap})"}
    folder = ws.root / (row.get("folder") or "").replace("\\", "/")
    if not row.get("folder") or not (folder / "tailored.json").exists():
        return {"status": "error", **base, "detail": "no tailored application folder - tailor first"}
    run = Run(ws, row, folder)
    run.resume = next(iter(sorted(folder.glob("Resume_*.pdf"))), None)
    run.cover = next(iter(sorted(folder.glob("CoverLetter_*.pdf"))), None)
    if not run.resume:
        return run.done("error", detail="no Resume_*.pdf in the folder - run build first")
    if submit is None:
        submit = (prof.get("submit_policy") or {}).get("mode") == "auto_after_final_check"
    ats = detect_ats(row.get("apply_url") or row.get("url"))
    if ats == "other":
        return run.done("needs_claude", detail=f"no token-free runner for this site yet ({row.get('ats')}): use the browser tools")
    missing = [f for f in (("wd_auth.js", "wd_myinfo.js", "wd_auto.js", "wd_review.js") if ats == "workday" else ("gh_fill.js", "gh_review.js"))
               if not (_scripts(ws) / f).exists()]
    if missing:
        return run.done("error", detail="page scripts missing in .playwright-mcp: " + ", ".join(missing))
    if not ensure_browser(ws):
        return run.done("error", detail="could not start Chrome with remote debugging (set JH_CHROME to chrome.exe)")
    _serve(ANSWER_PORT, _json_handler(post=_answer_post(ws)))
    try:
        return (workday if ats == "workday" else greenhouse)(ws, run, submit)
    except Exception as e:
        return run.done("error", detail=f"{type(e).__name__}: {str(e)[:400]}")
