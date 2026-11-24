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
from datetime import datetime
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

# A second batch can run beside the first: JH_SLOT=2 gets its own Chrome (profile + debugging port) and its own
# helper ports, so the two never share a login server or a browser tab.
SLOT = max(1, int(os.environ.get("JH_SLOT") or 1))
CDP_PORT = 9377 + (SLOT - 1)
BASE_PORTS = {"answer": 47615, "cred": 47613, "url": 47614}
ANSWER_PORT, CRED_PORT, URL_PORT = (BASE_PORTS[k] + 10 * (SLOT - 1) for k in ("answer", "cred", "url"))


def _slot_ports(text):
    """Page scripts name the helper ports literally; point them at this slot's servers."""
    if SLOT == 1:
        return text
    for k, port in (("answer", ANSWER_PORT), ("cred", CRED_PORT), ("url", URL_PORT)):
        text = text.replace(str(BASE_PORTS[k]), str(port))
    return text
RUNNER = Path(__file__).resolve().parent / "runner"


def detect_ats(url):
    u = (url or "").lower()
    if "myworkdayjobs.com" in u or "myworkdaysite.com" in u:
        return "workday"
    if "greenhouse.io" in u or "gh_jid=" in u:
        return "greenhouse"
    if "ashbyhq.com" in u:
        return "ashby"
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


_CTX = {}
PRIOR_APP_RX = re.compile("have you (ever |previously |already )?(applied|submitted an application)|previously applied (to|for|with)", re.I)


def _prior_application(ws, label):
    """'Have you ever applied to this company?' is a fact the tracker knows. -> (yes: bool, count) or None."""
    if not PRIOR_APP_RX.search(label or "") or not _CTX.get("company"):
        return None
    n = sum(1 for r in ws.load_tracker() if r["company"] == _CTX["company"] and r["job_id"] != _CTX.get("job_id")
            and (r["status"] in ("applied", "rejected", "assessment", "interview", "offer") or r.get("date_applied")))
    return n > 0, n


def _prior_detail(ws, label):
    """Follow-up to the prior-application question ("When and where?"): month, year and role of the last one."""
    if not re.match("(if (yes|so),? )?(please )?(tell us |specify |indicate )?when and where", (label or "").strip(), re.I) or not _CTX.get("company"):
        return None
    prev = sorted((r for r in ws.load_tracker() if r["company"] == _CTX["company"] and r["job_id"] != _CTX.get("job_id") and r.get("date_applied")),
                  key=lambda r: r["date_applied"])
    if not prev:
        return None
    r = prev[-1]
    try:
        when = datetime.strptime(r["date_applied"][:10], "%Y-%m-%d").strftime("%B %Y")
    except ValueError:
        when = r["date_applied"]
    return f"{when}, online application for {r['title'].strip()}"


def _company_answer(ws, label):
    """Answers that are right for ONE employer only (answer_bank.json -> "company_answers": {"humana": {label: answer}}),
    e.g. a bare "If yes, please explain." that follows the sponsorship question on that employer's form."""
    co = (_CTX.get("company") or "").lower()
    if not co or not label:
        return None
    table = (load_json(ws.profile_dir / "answer_bank.json", {}) or {}).get("company_answers") or {}
    key = re.sub(" +", " ", str(label)).strip().rstrip("*").strip().lower()
    for name, answers_ in table.items():
        if co.startswith(name.lower()) or name.lower() in co:
            hit = next((v for k, v in answers_.items() if k.strip().lower() == key), None)
            if hit:
                return hit
    return None


def _answer_post(ws):
    def post(path, req):
        ca = _company_answer(ws, req.get("q", "")) if path != "/save_review" else None
        if ca:
            return {"question": req.get("q"), "question_id": "company_answer", "status": "ok", "answer": ca, "option": None,
                    "confidence": 0.95, "source": "user_correction", "note": "company-specific answer"}
        pd = _prior_detail(ws, req.get("q", "")) if path != "/save_review" else None
        if pd:
            return {"question": req.get("q"), "question_id": "prior_application_detail", "status": "ok", "answer": pd, "option": None,
                    "confidence": 0.95, "source": "tracker", "note": "from the tracker"}
        pa = _prior_application(ws, req.get("q", "")) if path != "/save_review" else None
        if pa:
            want = "yes" if pa[0] else "no"
            opt = next((o for o in (req.get("options") or []) if str(o).strip().lower().startswith(want)), None)
            if opt or not req.get("options"):
                return {"question": req.get("q"), "question_id": "prior_application", "status": "ok", "answer": want.capitalize(),
                        "option": opt, "confidence": 0.95, "source": "tracker", "note": f"{pa[1]} earlier application(s) to this company in the tracker"}
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
    prof = ws.root / (".browser-apply" if SLOT == 1 else f".browser-apply-{SLOT}")
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

    def page(self, script=None, goto=None, screenshot=None, timeout=600, text=None, name="step", keep=False, close=False):
        """Run one page function (file path, or `text` written to the work dir). Returns the script's result."""
        if text is None and script and SLOT > 1:
            text, name = Path(script).read_text(encoding="utf-8"), Path(script).stem  # rewritten for this slot's ports below
        if text is not None:
            script = self.work / f"_{name}.js"
            script.write_text(_slot_ports(text), encoding="utf-8")
        cmd = ["node", str(RUNNER / "run.mjs"), "--cdp", f"http://127.0.0.1:{CDP_PORT}"]
        if self.host and not goto:
            cmd += ["--host", self.host]
        if script:
            cmd += ["--script", str(script)]
        if screenshot:
            cmd += ["--screenshot", str(screenshot)]
        if close:
            cmd += ["--close"]
        env = dict(os.environ)
        for k in ("JH_GOTO", "JH_KEEP", "JH_TAB"):
            env.pop(k, None)
        if goto:
            env["JH_GOTO"] = goto
        if keep:
            env["JH_KEEP"] = "1"  # same form already open in this job's tab: continue on it, do not reload
        if getattr(self, "row", None):
            env["JH_TAB"] = "jh:" + self.row["job_id"]  # one browser tab per job
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
  if (/My Experience/.test(await step())) { const r = await EXP(page); out.push('EXP: ' + r.join('; ')); if (r.some(x => /NOT (SET|FOUND)|FAILED/.test(x))) return out.join('\n');
    if (!(await next()) || /My Experience/.test(await step())) {
      // a saved draft already holds the website links: Workday then rejects the same links typed again
      // ("You can't add duplicate website URLs", TD and Nasdaq). Remove the typed rows and continue; the saved ones stay.
      const dup = (await page.locator('[data-automation-id="errorMessage"]:visible, [role=alert]:visible, [data-automation-id*="error" i]:visible').allInnerTexts()).some(t => /duplicate website/i.test(t));
      if (!dup) return out.join('\n');
      for (let k = 0; k < 6; k++) { const d = page.locator('[role=group][aria-labelledby^="Websites-"] button:has-text("Delete")').first(); if (!(await d.count())) break; await d.evaluate(e => e.click()); await page.waitForTimeout(1200); }
      out.push('removed website rows already saved in the draft');
      if (!(await next())) return out.join('\n');
    } }
  // required tick-box groups (CheckboxGroup fieldsets), which wd_auto does not handle: answer from the bank (TD, Humana)
  for (const fs of await page.locator('fieldset[data-automation-id$="-CheckboxGroup"]').all()) {
    if (await fs.locator('input:checked').count()) continue;
    const legend = (await fs.locator('xpath=ancestor::fieldset[1]/legend').innerText().catch(() => '')).replace(/\s+/g, ' ').replace(/\*\s*$/, '').trim();
    const labs = await fs.locator('label').all(); const ot = [];
    for (const x of labs) ot.push((await x.innerText()).replace(/\s+/g, ' ').trim());
    if (!legend || !ot.length) continue;
    let a = {}; try { a = await (await page.request.post('http://127.0.0.1:47615/lookup', { data: { q: legend, options: ot } })).json(); } catch (e) {}
    let k = a.status === 'ok' && a.option ? ot.indexOf(a.option) : -1;
    if (k < 0 && a.status === 'ok' && /^no$/i.test(String(a.answer || ''))) k = ot.findIndex(t => /^(no|none of the above)$/i.test(t));
    if (k >= 0) { await labs[k].click(); await page.waitForTimeout(400); out.push('CHECKGROUP: ' + legend.slice(0, 60) + ' => ' + ot[k]); }
    else out.push('CHECKGROUP NEEDS ANSWER: ' + legend.slice(0, 120) + ' [' + ot.join(' | ') + ']');
  }
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
    # A wrapper element is sometimes read as one extra field whose label is "<question>* <answer>" and whose value is
    # the NEXT question with its answer (Nordstrom 2026-11-24). The real pair exists as its own field: drop the merged one.
    flds = review.get("fields") or []
    keep = []
    for f in flds:
        lab = sq(f.get("label"))
        merged = any(g is not f and sq(g.get("label")) and lab.startswith(sq(g.get("label"))) and len(lab) > len(sq(g.get("label")))
                     and sq(lab[len(sq(g.get("label"))):]).lstrip("* ").strip() == sq(g.get("value")) for g in flds)
        if merged:
            fixed += 1
        else:
            keep.append(f)
    if fixed:
        review["fields"] = keep
    for f in review.get("fields") or []:
        v = sq(f.get("value"))
        if len(v) < 100 or not (v.endswith("*") or v.endswith(".") or v.endswith("?")):
            continue
        i = next((k for k, ln in enumerate(lines) if ln == v or (len(v) > 60 and ln.startswith(v[:60]))), -1)
        if 0 <= i < len(lines) - 1 and len(lines[i + 1]) < 120:
            f["value"] = lines[i + 1]
            f["required"] = bool(f.get("required")) or v.endswith("*")
            fixed += 1
    # The last question of a step is sometimes paired with the NEXT step's heading ("Voluntary Disclosures") as its
    # value (S&P Global 2026-11-24). The real answer is the line that follows the question in the page text.
    heads = {"voluntary disclosures", "self identify", "review", "my information", "my experience", "application questions"}
    for f in review.get("fields") or []:
        if sq(f.get("value")).lower() not in heads:
            continue
        lab = sq(f.get("label")).rstrip("*").strip()
        i = next((k for k, ln in enumerate(lines) if ln.rstrip("*").strip() == lab), -1)
        if 0 <= i < len(lines) - 1 and sq(lines[i + 1]).lower() not in heads:
            f["value"] = lines[i + 1]
            fixed += 1
    return fixed


def _final_check(ws, run):
    review = load_json(run.folder / "review.json", None)
    if not review:
        return None, ["review.json was not written"]
    if _repair_review(review):
        (run.folder / "review.json").write_text(json.dumps(review, indent=1, ensure_ascii=False), encoding="utf8")
    for f in review.get("fields") or []:  # tracker-backed facts are verified here, not in the answer bank
        ca = _company_answer(ws, f.get("label"))
        if ca and str(f.get("value") or "").strip() == ca.strip():
            review.setdefault("verified_overrides", {})[f["label"]] = {"value": f["value"], "source": "answer bank: company-specific answer"}
        pd = _prior_detail(ws, f.get("label"))
        if pd and str(f.get("value") or "").strip() == pd:
            review.setdefault("verified_overrides", {})[f["label"]] = {"value": f["value"], "source": "tracker: last application to this company"}
        pa = _prior_application(ws, f.get("label"))
        v = str(f.get("value") or "").strip().lower()
        if pa and v.startswith("yes" if pa[0] else "no"):
            review.setdefault("verified_overrides", {})[f["label"]] = {"value": f["value"], "source": f"tracker: {pa[1]} earlier application(s) to this company"}
    res = answers.final_review(ws, review)
    probs = list(res.get("problems") or [])
    if res.get("hand_to_user"):
        probs.append("HAND TO USER (never auto-answered): " + "; ".join(str(x)[:200] for x in res["hand_to_user"]))
    return res, probs


def _already_confirmed(ws, run):
    """The user may have submitted this form by hand a moment ago (Upgrade 2026-11-23 was sent twice): if a
    confirmation email for the company just arrived, record it and do not submit again."""
    try:
        from . import mail_verify
        # one confirmation is expected for every application to this company already recorded today (max 2 a day);
        # only an extra one means this very form was just submitted by hand
        known = sum(1 for r in ws.load_tracker() if r["company"] == run.row["company"] and r["job_id"] != run.row["job_id"]
                    and r["status"] == "applied" and r.get("date_applied") == today())
        if mail_verify.recent_confirmation(ws, run.row.get("company") or "") <= known:
            return None
    except Exception:
        return None
    note = (run.row.get("notes") or "").strip()
    ws.set_fields(run.row["job_id"], status="applied", submitted_by="vaishnavi",
                  notes=(note + " | " if note else "") + "confirmation email already received " + today() + " - not submitted again by the runner")
    return run.done("applied", detail="a confirmation email for this company arrived in the last hours: already submitted, not sent again")


def _mark_applied(ws, run, extra=""):
    shots = run.folder / "screenshots"
    shots.mkdir(exist_ok=True)
    try:
        run.page(screenshot=shots / "submitted_confirmation.png", name="shot", close=True)  # done: free the tab
    except Exception:
        pass
    note = (run.row.get("notes") or "").strip()
    add = "submitted by apply runner " + today() + (" - " + extra if extra else "")
    ws.set_fields(run.row["job_id"], status="applied", submitted_by="claude", notes=(note + " | " if note else "") + add)


# ---------------------------------------------------------------- Workday
_URL = {}


def _reset_password(ws, run, sd, url):
    """Forgot-password flow: request the mail, read the reset link from her mailbox (served to the page script over
    127.0.0.1, never logged), set this tenant's saved password, go back to the application. One try per run."""
    from . import mail_verify
    run.reset_done = True
    run.page(goto=url, name="back to sign-in")
    r = run.page(RUNNER / "wd_forgot.js") or {}
    if not isinstance(r, dict) or r.get("state") == "error" or r.get("captcha"):
        run.reset_note = "forgot-password form: " + str(r)[:160]
        return False
    hit = mail_verify.find_verification(ws, 6, ".".join(run.host.split(".")[:2]), "", 180, log=lambda *a: None) or {}
    link = hit.get("value") if hit.get("type") == "link" else None
    if not link or "passwordreset" not in link.lower().replace("-", "").replace("_", ""):
        run.reset_note = "no password-reset mail within 3 min"
        return False
    _URL.update(url=link)
    _serve(URL_PORT, _json_handler(get=lambda p: dict(_URL)))
    r = run.page(sd / "wd_reset.js", name="reset") or {}
    _URL.update(url="")
    ok = isinstance(r, dict) and r.get("state") == "password_reset_done"
    run.note("password reset: " + (str(r.get("state")) if isinstance(r, dict) else "unclear"))
    run.reset_note = str(r)[:160]
    if ok:
        run.page(goto=url, name="back to application")
    return ok


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
    for attempt in range(4):
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
            # no activation mail = the account already exists with another password (CLAUDE.md Workday step 4)
            if not run.__dict__.get("reset_done") and _reset_password(ws, run, sd, url):
                continue
            return run.done("needs_claude", detail="account created but no activation link found in Gmail within 3 min, and the password reset did not complete: " + str(run.__dict__.get("reset_note", ""))[:200], auth=st)
        if st in ("captcha", "needs_user_checkbox"):
            return run.done("blocked", detail=state.get("detail") or st, auth=st)
        if st == "account_exists_wrong_password" and not run.__dict__.get("reset_done") and _reset_password(ws, run, sd, url):
            continue
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
    done = _already_confirmed(ws, run)
    if done:
        return done
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
    r = run.page(text=fill, goto=url, name="fill", timeout=600, keep=True) or {}
    # some boards redirect the job link to the company's own careers page, where the form sits in a frame
    # (PathAI, SambaNova, Oscar Health): open Greenhouse's direct form for the same job instead
    m = re.match("gh:([^:]+):([0-9]+)$", run.row.get("job_id") or "") or re.search("greenhouse[.]io/([^/?]+)/jobs/([0-9]+)", url)
    if isinstance(r, dict) and not r.get("filled") and m and "embed/job_app" not in url:
        url = f"https://job-boards.greenhouse.io/embed/job_app?for={m.group(1)}&token={m.group(2)}"
        run.note("job link redirects to the company site: using the direct Greenhouse form")
        r = run.page(text=fill, goto=url, name="fill", timeout=600, keep=True) or {}
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
    done = _already_confirmed(ws, run)
    if done:
        return done
    sb = run.page(RUNNER / "gh_submit.js") or {}
    if sb.get("needs_code") and not sb.get("submitted"):
        # Greenhouse emails a security code after Submit (her decision 2026-11-23: read it from her mailbox and enter it)
        from . import mail_verify
        try:
            code = mail_verify.find_greenhouse_code(ws, run.row.get("company") or "", since_minutes=30, wait_seconds=150)
        except Exception as e:
            code = None
            run.note("security code mail lookup failed: " + type(e).__name__)
        if code:
            run.note("security code received by email and entered")
            sb = run.page(text=(RUNNER / "gh_code.js").read_text(encoding="utf8").replace("__CODE__", _js(code)), name="code") or {}
            try:
                (run.work / "_code.js").unlink()
            except Exception:
                pass
    if sb.get("submitted"):
        _mark_applied(ws, run)
        return run.done("applied", fields_checked=len(table))
    if sb.get("captcha"):
        return run.done("blocked", detail="captcha on submit")
    return run.done("needs_claude", detail=("email security code requested: " if sb.get("needs_code") else "submit not confirmed: ") + json.dumps(sb)[:400])


# ---------------------------------------------------------------- Ashby
def _contact(ws):
    prof = ws.profile() or {}
    c = prof.get("contact") or prof.get("personal") or prof
    name = c.get("full_name") or c.get("name") or " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x)
    return name or "", c.get("email") or ""


def ashby(ws, run, submit):
    """Single-page Ashby form (jobs.ashbyhq.com/<org>/<job>/application). Same stages as Greenhouse."""
    url = (run.row.get("apply_url") or run.row.get("url") or "").split("?")[0].rstrip("/")
    if not url.endswith("/application"):
        url += "/application"
    run.host = url.split("/")[2]
    name, email = _contact(ws)
    if not name or not email:
        return run.done("error", detail="name/email not found in profile.json (contact)")
    fill = (RUNNER / "ash_fill.js").read_text(encoding="utf8").replace("__RESUME__", str(run.resume).replace(chr(92), "/"))         .replace("__COVER__", str(run.cover).replace(chr(92), "/") if run.cover else "").replace("__NAME__", _js(name)).replace("__EMAIL__", _js(email))
    r = run.page(text=fill, goto=url, name="fill", timeout=600, keep=True) or {}
    if not isinstance(r, dict):
        return run.done("needs_claude", detail="unexpected fill output: " + str(r)[:300])
    run.note(f"filled {len(r.get('filled') or [])} fields")
    needs = [{k: (v[:12] if isinstance(v, list) else v) for k, v in q.items() if v not in (None, "", [])} for q in (r.get("needs_user") or [])]
    if needs or r.get("errors"):
        return run.done("needs_answers", questions=needs, errors=[str(e)[:160] for e in (r.get("errors") or [])][:8],
                        how="record answers, then run apply again: it continues on the open form")
    rev = (RUNNER / "ash_review.js").read_text(encoding="utf8")
    for k, v in (("__FOLDER__", run.folder.name), ("__COMPANY__", _js(run.row["company"])), ("__TITLE__", _js(run.row["title"])), ("__JOBID__", run.row["job_id"])):
        rev = rev.replace(k, v)
    rv = run.page(text=rev, name="review") or {}
    res, probs = _final_check(ws, run)
    if isinstance(rv, dict) and rv.get("empty_required"):
        probs.append("empty required fields: " + "; ".join(rv["empty_required"])[:300])
    if probs:
        return run.done("final_check_failed", problems=[p[:300] for p in probs][:12])
    table = [f"{x['field'][:60]} = {str(x['value'])[:50]}" for x in (res.get("summary_table") or [])][:40]
    if not submit:
        run.page(screenshot=run.folder / "review_before_submit.png", name="shot")
        return run.done("ready", files=rv.get("files") if isinstance(rv, dict) else None, summary=table, skipped=r.get("skipped"),
                        detail="final check passed; stopped before Submit (--no-submit)")
    done = _already_confirmed(ws, run)
    if done:
        return done
    sb = run.page(RUNNER / "ash_submit.js") or {}
    if sb.get("submitted"):
        _mark_applied(ws, run)
        return run.done("applied", fields_checked=len(table), skipped=r.get("skipped"))
    if sb.get("captcha") or sb.get("spam_flag"):
        return run.done("blocked", detail="Ashby stopped the submission (captcha / spam check): she submits this one from her own browser. " + str(sb.get("text"))[:200])
    return run.done("needs_claude", detail="submit not confirmed: " + json.dumps(sb)[:400])


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
    _CTX.update(company=row["company"], job_id=row["job_id"])
    run.resume = next(iter(sorted(folder.glob("Resume_*.pdf"))), None)
    run.cover = next(iter(sorted(folder.glob("CoverLetter_*.pdf"))), None)
    if not run.resume:
        return run.done("error", detail="no Resume_*.pdf in the folder - run build first")
    if submit is None:
        submit = (prof.get("submit_policy") or {}).get("mode") == "auto_after_final_check"
    ats = detect_ats(row.get("apply_url") or row.get("url"))
    if ats == "other":
        return run.done("needs_claude", detail=f"no token-free runner for this site yet ({row.get('ats')}): use the browser tools")
    missing = [f for f in (("wd_auth.js", "wd_myinfo.js", "wd_auto.js", "wd_review.js") if ats == "workday" else () if ats == "ashby" else ("gh_fill.js", "gh_review.js"))
               if not (_scripts(ws) / f).exists()]
    if missing:
        return run.done("error", detail="page scripts missing in .playwright-mcp: " + ", ".join(missing))
    if not ensure_browser(ws):
        return run.done("error", detail="could not start Chrome with remote debugging (set JH_CHROME to chrome.exe)")
    _serve(ANSWER_PORT, _json_handler(post=_answer_post(ws)))
    try:
        return {"workday": workday, "ashby": ashby}.get(ats, greenhouse)(ws, run, submit)
    except Exception as e:
        return run.done("error", detail=f"{type(e).__name__}: {str(e)[:400]}")
