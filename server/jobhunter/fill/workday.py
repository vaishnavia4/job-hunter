"""Workday: Apply Manually, candidate-account auth, My Information, My Experience, questions, disclosures; stops at Review.

Auth is a small state machine (classify_auth_page + next_auth_action): sign in -> on rejection create the account
-> activation email -> sign in; or, when the account already exists, Forgot password -> reset email -> sign in.
Verification / reset links come from Gmail over IMAP (jobhunter.mail_verify); they are opened but never logged,
and the password never appears in the report.

Pure helpers (classify_auth_page, next_auth_action, record_auth_action, tenant_hint, link_ok, pick_heard_option,
match_cert, earned_certs, _dates) are unit-tested offline in tests/test_fill_workday.py.
"""
import json
import re
import time
from urllib.parse import urlparse

from .. import answers as A
from . import common as C

MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}

AID = lambda a: f'[data-automation-id="{a}"]'
NOT_HONEYPOT = ':not([data-automation-id="beecatcher"])'  # hidden anti-bot input: never fill it
LEGAL_RX = re.compile(r"agree|terms|conditions|arbitrat|certif|acknowledg|consent|privacy", re.I)
ATTEST_RX = re.compile(r"arbitrat|non[- ]?disclosure|\bnda\b|certif|truthful|true and complete", re.I)


def consent_kind(label):
    """Which profile consent_policy key governs a consent checkbox label."""
    if re.search(r"creat\w* (this |an )?account", label or "", re.I):
        return "account_creation_agreement"
    return "legal_attestations" if ATTEST_RX.search(label or "") else "terms_and_conditions"


def _consent_preauthorized(ctx, kind):
    """profile.json consent_policy.<kind> == 'preauthorized' (or 'preauthorized_report_each'). Default: ask the user."""
    pol = (getattr(ctx, "profile", None) or {}).get("consent_policy") or {}
    return str(pol.get(kind, "ask")).startswith("preauthorized")


def _consent_box(ctx, cb, label):
    """Tick a consent box only under the user's recorded standing consent; otherwise leave it for them."""
    if _consent_preauthorized(ctx, consent_kind(label)):
        cb.check(force=True)
        ctx.fill_record(label[:80], "ticked (user's standing consent in profile consent_policy)")
    else:
        ctx.problem(f"user must tick: {label}")
MAINT_RX = re.compile(r"currently unavailable|undergoing (scheduled )?maintenance|scheduled maintenance", re.I)
REJECT_RX = re.compile(r"wrong email|wrong password|incorrect (email|password)|email (address )?or password|account (is |has been )?locked|"
                       r"not recognized|invalid (email|password|credentials|username)|could not sign you in|sign[- ]in failed", re.I)
EXISTS_RX = re.compile(r"already (exists|in use|registered|been registered)|account with this email|email address is already", re.I)
RESET_ERR_RX = re.compile(r"(cannot reuse|previously used|does not meet|expired|invalid|error)[^\n]{0,140}", re.I)
ACTIVATE_RX = re.compile(r"/activate/", re.I)
RESET_LINK_RX = re.compile(r"/passwordreset", re.I)
WORKDAY_HOST_RX = re.compile(r"(^|\.)(myworkdayjobs\.com|myworkdaysite\.com|myworkday\.com|workday\.com)$", re.I)
IN_PROGRESS_RX = re.compile(r"in progress|pursuing|expected|planned|studying|preparing", re.I)
CERT_CODE_RX = re.compile(r"\b([A-Za-z]{2,5}-?\d{2,4}[A-Za-z]?)\b")
SIGNIN_LIMIT = 3  # initial + after activation + after reset; the same password is never tried twice in a row


# ================================================================== pure helpers
def tenant_hint(url):
    """'https://acme.wd1.myworkdayjobs.com/...' -> 'acme.wd1'; myworkdaysite.com/recruiting/<tenant>/... -> '<tenant>'."""
    p = urlparse(url or "")
    host = (p.hostname or "").lower()
    if host.endswith("myworkdayjobs.com"):
        return ".".join(host.split(".")[:2])
    if host.endswith("myworkdaysite.com"):
        parts = [x for x in p.path.split("/") if x]
        if "recruiting" in parts and parts.index("recruiting") + 1 < len(parts):
            return parts[parts.index("recruiting") + 1].lower()
    return host


def link_ok(link, hint):
    """Only open verification/reset links that are https on a Workday host AND mention this tenant."""
    p = urlparse(link or "")
    if p.scheme != "https" or not WORKDAY_HOST_RX.search(p.hostname or ""):
        return False
    return not hint or hint.lower() in (link or "").lower()


def classify_auth_page(text="", signed_in=False, in_application=False, captcha=False, chooser=False,
                       create_form=False, password=False):
    """Name what the auth wall currently shows, from body text + a few DOM flags.
    -> signed_in | captcha | maintenance | exists | rejected | create_form | chooser | signin_form | unknown"""
    t = text or ""
    if captcha:
        return "captcha"
    if signed_in or (in_application and not password):
        return "signed_in"
    if MAINT_RX.search(t) and not password:
        return "maintenance"
    if EXISTS_RX.search(t):
        return "exists"
    if REJECT_RX.search(t):
        return "rejected"
    if create_form:
        return "create_form"
    if chooser:
        return "chooser"
    if password:
        return "signin_form"
    return "unknown"


def new_auth_history():
    # signin/create/chooser: attempts; activation: '' | opened | none; reset: '' | done | failed;
    # last: the last real action (choose_email is not counted, it only reveals the form)
    return {"signin": 0, "create": 0, "chooser": 0, "activation": "", "reset": "", "last": ""}


def record_auth_action(h, action, result=None):
    """Update the history after performing `action` (result = activation/reset outcome)."""
    if action == "choose_email":
        h["chooser"] += 1
        return h
    h["last"] = action
    if action == "sign_in":
        h["signin"] += 1
    elif action == "create":
        h["create"] += 1
    elif action == "await_activation":
        h["activation"] = result or "none"
    elif action == "reset":
        h["reset"] = result or "failed"
    return h


def next_auth_action(state, h):
    """Next auth action from the observed state and the history.
    -> done | choose_email | sign_in | create | await_activation | reset | stop:<reason>"""
    if state == "signed_in":
        return "done"
    if state in ("captcha", "maintenance"):
        return "stop:" + state
    if state == "chooser":  # Apple / Google / LinkedIn / email chooser
        return "choose_email" if h["chooser"] < 3 else "stop:chooser_loop"
    if state == "exists":  # Create Account says the email is taken -> reset the password
        return "reset" if not h["reset"] else "stop:signin_failed"
    if state == "create_form" and h["last"] != "create":
        return "create" if not h["create"] else "stop:create_failed"
    if h["signin"] >= SIGNIN_LIMIT:
        return "stop:signin_failed"
    form = state in ("signin_form", "rejected", "create_form")
    last = h["last"]
    if last == "":
        return "sign_in" if form else "stop:unclear"
    if last == "sign_in":  # still on the form after submitting = rejected
        if not form:
            return "stop:unclear"
        if not h["create"] and not h["reset"] and h["activation"] != "opened":
            return "create"
        return "reset" if not h["reset"] else "stop:signin_failed"
    if last == "create":
        return "await_activation"
    if last == "await_activation":
        if h["activation"] == "opened":
            return "sign_in" if form else "stop:unclear"
        if form and not h["reset"]:
            return "reset"  # no activation mail and back at sign-in = the account already existed
        return "stop:verification_missing"
    if last == "reset":
        if h["reset"] != "done":
            return "stop:reset_failed"
        return "sign_in" if form else "stop:unclear"
    return "stop:unclear"


HEARD_WEB = [r"careers? (web ?)?(site|page)", r"(company|corporate|employer) (web ?)?(site|page)",
             r"^(company |corporate )?web ?site$", r"\bweb ?site\b"]
HEARD_LINKEDIN = [r"linked ?in", r"social (media|network)", r"job (board|site|posting)s?"]
HEARD_NEVER = re.compile(r"\bfair\b|\bevent\b|referr|agency|recruiter", re.I)


def pick_heard_option(options, answer="", source="", company=""):
    """'How did you hear' is a 2-level list (category -> item). Pick the option at the current level for the
    answer-bank answer, else the default: LinkedIn when the job came from LinkedIn, otherwise the careers site."""
    opts = [C.clean(o) for o in options or [] if C.clean(o)]
    if not opts:
        return None
    if answer:
        hit = C.exact_option(opts, answer)
        if hit:
            return hit
    pats = []
    if company:
        co = re.escape(C.clean(company))
        pats += [co + r".{0,20}careers?", r"careers?.{0,20}" + co]
    a = (answer or "").lower()
    if a and "linkedin" in a.replace(" ", ""):
        pats += HEARD_LINKEDIN
    elif a and re.search(r"career|web ?site|company|corporate", a):
        pats += HEARD_WEB
    elif a:
        pats.append(re.escape(answer))
    elif "linkedin" in (source or "").lower():
        pats += HEARD_LINKEDIN
    else:
        pats += HEARD_WEB
    for p in pats:
        for o in opts:
            if re.search(p, o, re.I) and not HEARD_NEVER.search(o):
                return o
    return None


def _cert_core(s):
    return C.norm(re.sub(r"\(.*?\)", " ", s or ""))


def match_cert(options, cert):
    """Workday certification option for `cert`: exact, then the same exam code (AZ-104), then the same name.
    None when nothing matches (the row is deleted rather than picking a wrong certificate)."""
    opts = [C.clean(o) for o in options or [] if C.clean(o)]
    hit = C.exact_option(opts, cert)
    if hit:
        return hit
    for code in CERT_CODE_RX.findall(cert or ""):
        m = re.match(r"([A-Za-z]+)-?(\d.*)", code)  # 'AZ-104' also matches 'AZ104' and 'az-104'
        rx = re.compile(r"(?<![A-Za-z0-9])" + re.escape(m.group(1)) + "-?" + re.escape(m.group(2)) + r"(?![A-Za-z0-9])", re.I)
        hits = [o for o in opts if rx.search(o)]
        if hits:
            return hits[0]
    core = _cert_core(cert)
    if not core:
        return None
    for o in opts:
        if _cert_core(o) == core:
            return o
    for o in opts:
        short, long_ = sorted((_cert_core(o), core), key=len)
        if len(short) >= 12 and len(short) >= 0.6 * len(long_) and short in long_:
            return o
    return None


def earned_certs(profile, master):
    """Earned certifications as names: profile.certifications, else the master resume's; drops in-progress ones."""
    raw = (profile or {}).get("certifications") or (master or {}).get("certifications") or []
    out = []
    for c in raw:
        if isinstance(c, dict):
            if IN_PROGRESS_RX.search(json.dumps(c)):
                continue
            c = c.get("name") or c.get("title") or ""
        c = C.clean(str(c))
        if c and not IN_PROGRESS_RX.search(c):
            out.append(c)
    return list(dict.fromkeys(out))


def _mmyyyy(s):
    s = (s or "").strip()
    m = re.match(r"([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{4})", s)
    if m and m.group(1).lower() in MONTHS:
        return f"{MONTHS[m.group(1).lower()]:02d}{m.group(2)}"
    m = re.match(r"(\d{1,2})/(\d{4})", s)
    return f"{int(m.group(1)):02d}{m.group(2)}" if m else None


def _dates(d):
    """'Apr 2024 - Present' -> ('042024', None, True); en dash and 'to' also work."""
    parts = re.split(r"\s*[-–]\s*|\s+to\s+", (d or "").strip(), maxsplit=1)
    a, b = (parts + [""])[:2]
    cur = bool(re.search(r"present|current", b, re.I))
    return _mmyyyy(a), (None if cur else _mmyyyy(b)), cur


# ================================================================== page helpers
def _top_skills(ctx, n=10):
    """Workday skill tags: profile.workday_skills if set, else the first items of the resume's skill categories."""
    custom = ctx.profile.get("workday_skills") or []
    if custom:
        return custom[:n]
    tailored = {}
    folder = getattr(ctx, "folder", None)
    if folder and (folder / "tailored.json").exists():  # JD-ordered skills for this job
        tailored = json.loads((folder / "tailored.json").read_text(encoding="utf-8")).get("skills") or {}
    out = []

    def walk(x):
        if isinstance(x, str):
            out.append(re.sub(r"\s*\(.*?\)", "", x).strip())
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(tailored or (ctx.master.get("skills") or {}).get("primary") or ctx.master.get("skills") or {})
    return [s for s in dict.fromkeys(out) if s][:n]


def _creds(ctx):
    c = ctx.creds.get("workday") or ctx.creds.get("job_sites_default") or {}
    return c.get("email") or ctx.p("personal", "email"), c.get("password")


def _active_step(page):
    for sel in (AID("progressBarActiveStep"), '[aria-current="step"]'):
        loc = page.locator(sel)
        if loc.count():
            return C.clean(loc.first.inner_text())
    return ""


def _visible(page, selectors):
    """First visible element among `selectors` (tried in order), or None."""
    for sel in selectors:
        loc = page.locator(sel)
        try:
            for i in range(min(loc.count(), 10)):
                if loc.nth(i).is_visible():
                    return loc.nth(i)
        except Exception:
            continue
    return None


def _body(page, secret=None):
    try:
        t = page.inner_text("body")
    except Exception:
        return ""
    return t.replace(secret, "***") if secret else t


def _start(ctx):
    page = ctx.page
    for name in (r"^apply$", r"apply manually"):
        btn = page.get_by_role("link", name=re.compile(name, re.I)).or_(page.get_by_role("button", name=re.compile(name, re.I)))
        if btn.count() and btn.first.is_visible():
            btn.first.click()
            page.wait_for_timeout(2500)
    am = page.locator(AID("applyManually"))
    if am.count() and am.first.is_visible():
        am.first.click()
        page.wait_for_timeout(3000)


# ================================================================== auth
def _observe(ctx, pw=None):
    page = ctx.page
    page.wait_for_timeout(800)
    signed = page.locator(f'{AID("utilityButtonSignOut")}, {AID("navigationItem-Sign Out")}, button:has-text("Sign Out")').count() > 0
    try:
        signed = signed or "candidate home" in page.title().lower()
    except Exception:
        pass
    return classify_auth_page(
        _body(page, pw),
        signed_in=signed,
        in_application=bool(_active_step(page)),
        captcha=page.locator('iframe[src*="captcha"], iframe[title*="challenge" i], .g-recaptcha, #px-captcha').count() > 0,
        chooser=_visible(page, ['button:has-text("Sign in with email")']) is not None,
        create_form=_visible(page, [AID("verifyPassword")]) is not None,
        password=_visible(page, [f"input[type=password]{NOT_HONEYPOT}"]) is not None,
    )


def _goto_auth(ctx):
    """Reload the job and get back to the auth wall."""
    ctx.page.goto(ctx.url, wait_until="domcontentloaded", timeout=60000)
    ctx.page.wait_for_timeout(2500)
    _start(ctx)


def _choose_email(ctx):
    b = _visible(ctx.page, ['button:has-text("Sign in with email")'])
    if b:
        b.click()
        ctx.page.wait_for_timeout(2000)


def _to_signin_form(page):
    """On Create Account: switch to the form's own Sign In link (the header 'Sign In' is only a nav link)."""
    if _visible(page, [AID("verifyPassword")]):
        link = _visible(page, [AID("signInLink"), 'form button:has-text("Sign In")'])
        if link:
            link.click()
            page.wait_for_timeout(2000)


def _fill_email_pw(page, email, pw, verify=False):
    em = _visible(page, [f'input{AID("email")}{NOT_HONEYPOT}', f"input[type=email]{NOT_HONEYPOT}"])
    pws = _visible(page, [f'input{AID("password")}{NOT_HONEYPOT}', f"input[type=password]{NOT_HONEYPOT}"])
    if not em or not pws:
        return False
    em.fill(email)
    pws.fill(pw)
    if verify:
        vp = _visible(page, [AID("verifyPassword")])
        if not vp:
            return False
        vp.fill(pw)
    return True


def _sign_in(ctx, email, pw):
    page = ctx.page
    _to_signin_form(page)
    if not _fill_email_pw(page, email, pw):
        ctx.problem("Workday: sign-in form fields not found")
        return
    btn = _visible(page, [AID("signInSubmitButton"), f'{AID("click_filter")}[aria-label="Sign In"]'])
    if btn:
        btn.click(force=True)
    else:
        page.get_by_role("button", name=re.compile(r"^(Submit|Sign In)$")).last.click(force=True)
    page.wait_for_timeout(6000)


def _create(ctx, email, pw):
    """Fill and submit Create Account. Returns None when submitted, else 'needs_user_checkbox' | 'error'."""
    page = ctx.page
    if not _visible(page, [AID("verifyPassword")]):
        link = _visible(page, [AID("createAccountLink"), f'{AID("click_filter")}[aria-label="Create Account"]'])
        if not link:
            cands = page.locator('button:has-text("Create Account"):visible')
            link = cands.last if cands.count() else None
        if not link:
            ctx.problem("Workday: no Create Account link")
            return "error"
        link.click()
        page.wait_for_timeout(2500)
    if not _fill_email_pw(page, email, pw, verify=True):
        ctx.problem("Workday: create-account fields not found")
        return "error"
    box = _visible(page, [AID("createAccountCheckbox")])
    if box is not None and not box.is_checked():
        if not _consent_preauthorized(ctx, "account_creation_agreement"):
            return "needs_user_checkbox"  # consent belongs to the user: fields are filled, they tick and submit
        box.check(force=True)
        ctx.fill_record("Create account agreement", "ticked (user's standing consent in profile consent_policy)")
    btn = _visible(page, [AID("createAccountSubmitButton"), f'{AID("click_filter")}[aria-label="Create Account"]'])
    if not btn:
        ctx.problem("Workday: no Create Account submit button")
        return "error"
    btn.click(force=True)
    page.wait_for_timeout(7000)
    return None


def _await_mail(ctx, want_rx, started, wait_seconds=180):
    """Poll Gmail (jobhunter.mail_verify) for a link matching want_rx that arrived after `started`.
    Polls with wait_seconds=0 so an older unrelated mail cannot end the wait early. Never logs the link."""
    from .. import mail_verify
    host = tenant_hint(ctx.url) or tenant_hint(ctx.page.url)
    finder = getattr(ctx, "mail_finder", None) or mail_verify.find_verification
    deadline = time.time() + wait_seconds
    while True:
        since = (time.time() - started) / 60 + 2  # slack for clock skew; want_rx filters older mail
        try:
            hit = finder(ctx.ws, since_minutes=since, host_hint=host, wait_seconds=0, log=lambda *a, **k: None)
        except Exception as e:
            ctx.problem(f"Workday: cannot read the verification mailbox ({type(e).__name__}: {str(e)[:120]})")
            return None
        if hit and hit.get("type") == "link" and want_rx.search(hit.get("value", "")):
            if link_ok(hit["value"], host):
                return hit["value"]
            ctx.problem("Workday: verification link is not on this tenant's host - not opened")
            return None
        if time.time() >= deadline:
            if hit and hit.get("type") == "code":
                ctx.problem("Workday: a one-time code arrived instead of a link - enter it manually")
            return None
        ctx.page.wait_for_timeout(10000)


def _await_activation(ctx, started):
    link = _await_mail(ctx, ACTIVATE_RX, started)
    if not link:
        return "none"
    ctx.page.goto(link, wait_until="domcontentloaded", timeout=60000)
    ctx.page.wait_for_timeout(5000)
    ctx.step("auth: activation link opened")
    _goto_auth(ctx)
    return "opened"


def _reset(ctx, email, pw):
    """Forgot password -> reset email -> set the configured password -> back to the auth wall."""
    page = ctx.page
    _goto_auth(ctx)
    _choose_email(ctx)
    _to_signin_form(page)
    fp = _visible(page, [AID("forgotPasswordLink"), 'button:has-text("Forgot your password")', 'a:has-text("Forgot your password")'])
    if not fp:
        ctx.problem("Workday: no 'Forgot your password?' link")
        return "failed"
    fp.click()
    page.wait_for_timeout(2500)
    em = _visible(page, [f'input{AID("email")}{NOT_HONEYPOT}', f"input[type=email]{NOT_HONEYPOT}"])
    if not em:
        ctx.problem("Workday: password-reset email field not found")
        return "failed"
    em.fill(email)
    started = time.time()
    btn = _visible(page, [AID("passwordResetSubmitButton"), AID("forgotPasswordSubmitButton"), AID("resetPasswordSubmitButton"),
                          'button:has-text("Submit")', 'button:has-text("Reset Password")'])
    if not btn:
        ctx.problem("Workday: password-reset submit button not found")
        return "failed"
    btn.click(force=True)
    page.wait_for_timeout(4000)
    link = _await_mail(ctx, RESET_LINK_RX, started)
    if not link:
        ctx.problem("Workday: no password-reset email within 3 minutes")
        return "failed"
    page.goto(link, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    pws = page.locator(f"input[type=password]{NOT_HONEYPOT}:visible")
    n = pws.count()
    if not n:
        ctx.problem("Workday: reset page has no password fields (link expired or already used?)")
        return "failed"
    for i in range(n):
        pws.nth(i).fill(pw)
    btn = _visible(page, [AID("passwordResetSubmitButton"), AID("resetPasswordSubmitButton"),
                          'button:has-text("Submit")', 'button:has-text("Reset")'])
    if btn:
        btn.click(force=True)
    page.wait_for_timeout(6000)
    m = RESET_ERR_RX.search(_body(page, pw))
    if m:
        ctx.problem("Workday: password reset error: " + C.clean(m.group(0))[:160])
        return "failed"
    ctx.step("auth: password reset done")
    _goto_auth(ctx)
    return "done"


STOP_MESSAGES = {
    "captcha": "CAPTCHA on the Workday auth page - the user must solve it (status=blocked)",
    "maintenance": "Workday is in a maintenance window - retry later (do not mark blocked)",
    "signin_failed": "Workday sign-in still rejected after create/activation/reset - stopped (retrying locks the account)",
    "verification_missing": "no Workday activation email arrived and the page did not return to sign-in",
    "reset_failed": "Workday password reset failed (see the other problems)",
}


def _auth(ctx, max_actions=14):
    """Run the auth state machine until signed in. Sets ctx.auth_state; returns True when past the wall."""
    page = ctx.page
    email, pw = _creds(ctx)
    h = new_auth_history()
    created_at = time.time()
    for _ in range(max_actions):
        state = _observe(ctx, pw)
        if state == "signed_in":
            if h["last"]:
                ctx.step("signed in")
            ctx.auth_state = "signed_in"
            return True
        action = next_auth_action(state, h)
        if not pw and action in ("sign_in", "create", "reset"):
            ctx.auth_state = "no_password"
            ctx.problem("no Workday password in profile/credentials.json (workday or job_sites_default)")
            return False
        if action.startswith("stop:"):
            reason = action[5:]
            ctx.auth_state = reason
            ctx.problem(STOP_MESSAGES.get(reason) or f"Workday auth stopped ({reason}); page: " + C.clean(_body(page, pw))[:200])
            return False
        ctx.step("auth: " + action)
        result = None
        if action == "choose_email":
            _choose_email(ctx)
        elif action == "sign_in":
            _sign_in(ctx, email, pw)
        elif action == "create":
            created_at = time.time()
            r = _create(ctx, email, pw)
            if r:
                ctx.auth_state = r
                if r == "needs_user_checkbox":
                    ctx.problem("Workday Create Account: email + password filled; the user must tick the agreement box "
                                "and click Create Account, then re-run")
                return False
        elif action == "await_activation":
            result = _await_activation(ctx, created_at)
        elif action == "reset":
            result = _reset(ctx, email, pw)
        record_auth_action(h, action, result)
    ctx.auth_state = "too_many_steps"
    ctx.problem("Workday auth did not finish within the step budget")
    return False


# ================================================================== fields
def _dropdown_choice(ctx, btn, label):
    page = ctx.page
    btn.click()
    page.wait_for_timeout(700)
    opts = C.visible_options(page)
    if not opts:
        return False
    low = label.lower()
    pick = None
    if re.search(r"^state|region", low):
        pick = C.exact_option(opts, ctx.p("personal", "state"))
    elif "phone device" in low or "device type" in low:
        pick = next((o for o in opts if re.search(r"mobile|cell", o, re.I)), None)
    elif "country phone code" in low or "phone code" in low:
        pick = next((o for o in opts if re.search(r"united states of america", o, re.I)), None)
    elif low.startswith("country"):
        pick = next((o for o in opts if re.match(r"united states( of america)?$", o, re.I)), None)
    elif re.search(r"degree", low):
        pick = C.pick_degree(opts)
    if pick is None and len(opts) <= 60:
        val, _ = ctx.answer(label, opts)
        pick = val
    if pick and C.click_option(page, pick):
        ctx.fill_record(label, pick)
        return True
    page.keyboard.press("Escape")
    if not ctx.unknown or ctx.unknown[-1]["label"] != label:
        ctx.problem(f"no option picked for '{label}'")
    return False


def _prompt_texts(page):
    loc = page.locator('[data-automation-id="promptOption"]:visible')
    if not loc.count():
        loc = page.locator('[role="option"]:visible')
    return loc, [C.clean(t) for t in loc.all_inner_texts()]


def _pills(box):
    try:
        return [C.clean(t) for t in box.locator(AID("selectedItem")).all_inner_texts() if C.clean(t)]
    except Exception:
        return []


def _how_heard(ctx, box, label):
    """2-level multiselect (category -> item). Bank answer first, else careers-site / LinkedIn default; verify the pill."""
    page = ctx.page
    if _pills(box):
        return True
    try:
        res = A.lookup(ctx.ws, label, None)
        bank = res.get("answer") if res.get("status") == A.STATUS_OK else ""
    except Exception:
        bank = ""
    box.locator("input, button").first.click()
    page.wait_for_timeout(900)
    chosen = []
    for _ in range(3):
        loc, texts = _prompt_texts(page)
        if not texts:
            break
        pick = None
        try:
            r = A.lookup(ctx.ws, label, texts)
            if r.get("status") == A.STATUS_OK and r.get("option") in texts:
                pick = r["option"]
        except Exception:
            pass
        pick = pick or pick_heard_option(texts, bank, ctx.source, ctx.job.get("company", ""))
        if not pick:
            break
        loc.nth(texts.index(pick)).click()
        chosen.append(pick)
        page.wait_for_timeout(900)
        if _pills(box):
            break
    page.keyboard.press("Escape")
    pills = _pills(box)
    if pills:
        ctx.fill_record(label, " > ".join(chosen) + f" (selected: {pills[0]})")
        return True
    ctx.problem(f"'{label}': nothing selected (tried: {' > '.join(chosen) or 'no matching option'})")
    return False


def _fill_boxes(ctx):
    """Generic pass over visible form fields on the current step."""
    page = ctx.page
    boxes = page.locator('[data-automation-id^="formField"]:visible')
    n = boxes.count()
    for i in range(n):
        b = boxes.nth(i)
        label = "?"
        try:
            lab = b.locator("label, legend").first
            raw = lab.inner_text() if lab.count() else ""
            label = C.label_text(raw)
            if not label or b.locator('input[type="file"]').count():
                continue
            before = len(ctx.unknown)
            if re.search(r"how did you (hear|learn|find)", label, re.I):
                _how_heard(ctx, b, label)
            elif b.locator("button[aria-haspopup]").count():
                btn = b.locator("button[aria-haspopup]").first
                if re.search(r"select one", btn.inner_text(), re.I):
                    _dropdown_choice(ctx, btn, label)
            elif b.locator('input[type="radio"]').count():
                C.fill_choice_group(ctx, b, label)
            elif b.locator('input[type="checkbox"]').count():
                if LEGAL_RX.search(label) and not b.locator("input[type=checkbox]").first.is_checked():
                    _consent_box(ctx, b.locator("input[type=checkbox]").first, label)
                else:
                    C.handle_checkbox(ctx, b.locator("input[type=checkbox]").first, label)
            else:
                el = b.locator(f"input[type=text]{NOT_HONEYPOT}, input[type=tel], input[type=email]{NOT_HONEYPOT}, "
                               f"input:not([type]){NOT_HONEYPOT}, textarea").first
                if not el.count() or el.input_value():
                    continue
                if re.search(r"preferred (first )?name", label, re.I):
                    C.fill_text(ctx, el, label, ctx.p("personal", "preferred_first_name") or ctx.p("personal", "first_name"))
                elif re.search(r"^phone number$|^phone$", label, re.I):
                    C.fill_text(ctx, el, label, ctx.p("personal", "phone_digits"))
                else:
                    C.fill_text(ctx, el, label)
            if len(ctx.unknown) > before:
                ctx.unknown[-1]["required"] = C.is_required(raw)
        except Exception as e:
            ctx.problem(f"{label}: {type(e).__name__} {str(e)[:100]}")
    # stand-alone consent checkboxes outside formField containers
    cbs = page.locator('input[type="checkbox"]:visible')
    for i in range(cbs.count()):
        cb = cbs.nth(i)
        try:
            t = C.clean(cb.evaluate("e=>(e.closest('label')||e.parentElement.parentElement).innerText"))
            if LEGAL_RX.search(t) and not cb.is_checked():
                _consent_box(ctx, cb, t[:90])
        except Exception:
            pass


# ================================================================== My Experience
def _group(page, name):
    return page.get_by_role("group", name=name, exact=True)


def _add_button(page, section):
    """The section's Add / Add Another button, found from the section heading (added rows are separate groups)."""
    h = page.locator("h2, h3, h4, [role=heading]").filter(has_text=re.compile(r"^\s*" + re.escape(section) + r"\s*$"))
    if h.count():
        b = h.first.locator('xpath=following::button[normalize-space()="Add" or normalize-space()="Add Another"][1]')
        if b.count():
            return b.first
    g = _group(page, section)
    if g.count():
        b = g.get_by_role("button", name=re.compile(r"^(Add|Add Another)$"))
        if b.count():
            return b.first
    return None


def _ensure_rows(page, section, n):
    """Make sure groups '<section> 1'..'<section> n' exist. Returns how many exist."""
    for i in range(1, n + 1):
        if _group(page, f"{section} {i}").count():
            continue
        btn = _add_button(page, section)
        if not btn:
            return i - 1
        btn.click()
        page.wait_for_timeout(1000)
        if not _group(page, f"{section} {i}").count():
            return i - 1
    return n


def _delete_row(page, group):
    d = group.locator('button[aria-label^="Delete"], button:has-text("Delete")')
    if d.count():
        d.first.click()
        page.wait_for_timeout(800)
        return True
    return False


def _row_is_empty(group):
    if _pills(group):
        return False
    try:
        vals = group.locator(f"input:not([type=hidden]):not([type=checkbox]):not([type=radio]){NOT_HONEYPOT}, textarea").evaluate_all(
            "els=>els.map(e=>e.value||'')")
    except Exception:
        return False
    return not any(v.strip() for v in vals)


def _delete_empty_rows(page, section):
    """Delete empty '<section> N' groups, highest N first (e.g. the required 'Specialties N' a certificate spawns)."""
    groups = page.get_by_role("group", name=re.compile(r"^" + re.escape(section) + r" \d+$"))
    removed = 0
    for i in reversed(range(groups.count())):
        g = groups.nth(i)
        if _row_is_empty(g) and _delete_row(page, g):
            removed += 1
    return removed


def _pick_from_prompt(ctx, input_loc, query, choose, scroll_tries=25):
    """Type `query` into a Workday search prompt, then scroll the virtualized list until choose(texts) returns
    an option, and click it. Returns the clicked text or None (never falls back to the first partial match)."""
    page = ctx.page
    input_loc.fill(query)
    input_loc.press("Enter")
    page.wait_for_timeout(2000)
    last_seen = None
    for _ in range(scroll_tries):
        loc, texts = _prompt_texts(page)
        if not texts:
            break
        pick = choose(texts)
        if pick:
            el = loc.nth(texts.index(pick))
            el.scroll_into_view_if_needed()
            el.click()
            page.wait_for_timeout(800)
            return pick
        if texts == last_seen:  # the list stopped moving: end of results
            break
        last_seen = texts
        try:
            loc.last.scroll_into_view_if_needed()
            loc.last.hover()
        except Exception:
            pass
        page.mouse.wheel(0, 500)
        page.wait_for_timeout(700)
    page.keyboard.press("Escape")
    return None


def _upload_resume(ctx):
    """Resume goes in FIRST so a later failure in optional sections cannot leave it missing. By label, not position."""
    page = ctx.page
    if not ctx.resume:
        return False
    g = page.get_by_role("group", name=re.compile(r"^(resume|cv|resume ?/ ?cv)$", re.I))
    if g.count() and g.first.locator('input[type="file"]').count():
        g.first.locator('input[type="file"]').first.set_input_files(str(ctx.resume))
    elif C.upload_by_label(ctx, "resume", ctx.resume):
        return True
    elif page.locator('input[type="file"]').count() == 1:  # the step's only, unlabelled file box
        page.locator('input[type="file"]').first.set_input_files(str(ctx.resume))
    else:
        ctx.problem("Workday: resume upload field not found")
        return False
    page.wait_for_timeout(4000)
    shown = ctx.resume.name in _body(page)
    ctx.fill_record("resume upload", ctx.resume.name + ("" if shown else " (name not shown - verify)"))
    return True


def _work_experience(ctx):
    page = ctx.page
    jobs = ctx.master.get("experience") or []
    if not jobs or not (_group(page, "Work Experience").count() or _add_button(page, "Work Experience")):
        return
    have = _ensure_rows(page, "Work Experience", len(jobs))
    for i, j in enumerate(jobs[:have]):
        g = _group(page, f"Work Experience {i + 1}")
        frm, to, cur = _dates(j.get("dates"))
        C.fill_text(ctx, g.get_by_label("Job Title").first, f"Job Title {i + 1}", j["title"])
        C.fill_text(ctx, g.get_by_label("Company").first, f"Company {i + 1}", j["company"])
        loc = g.get_by_label("Location", exact=True)
        if loc.count() and j.get("location"):
            C.fill_text(ctx, loc.first, f"Location {i + 1}", j["location"])
        if cur:
            cw = g.get_by_label("I currently work here")
            if cw.count() and not cw.first.is_checked():
                cw.first.check()
                page.wait_for_timeout(400)
        months = g.locator('input[aria-label="Month"]')
        if frm and months.count():
            months.nth(0).click()
            months.nth(0).press_sequentially(frm, delay=40)
        if to and not cur and months.count() > 1:
            months.nth(1).click()
            months.nth(1).press_sequentially(to, delay=40)
        desc = g.get_by_label("Role Description")
        if desc.count():
            desc.first.fill(" ".join((j.get("bullets") or [])[:4]))
        ctx.fill_record(f"Work Experience {i + 1}", f"{j['title']} @ {j['company']} {j.get('dates', '')}")
    if have < len(jobs):
        ctx.problem(f"Work Experience: only {have} of {len(jobs)} rows could be added")


def _education(ctx):
    page = ctx.page
    school, _ = C.education(ctx)
    if not (_group(page, "Education").count() or _add_button(page, "Education")):
        return
    if not _ensure_rows(page, "Education", 1):
        ctx.problem("Education: could not add a row")
        return
    ed = _group(page, "Education 1")
    sc = ed.get_by_label("School or University")
    if sc.count() and school and not _pills(ed):
        # school lists hold many '<College>, <University>' entries: only the EXACT school name is accepted
        got = _pick_from_prompt(ctx, sc.first, school, lambda texts: C.exact_option(texts, school))
        if got:
            ctx.fill_record("School", got)
        elif C.clean(sc.first.input_value()) == school and not page.locator('[data-automation-id="promptOption"]').count():
            ctx.fill_record("School", school + " (plain text field)")
        else:
            ctx.problem(f"school '{school}' has no exact option in the Workday list - user must pick it")
    btn = ed.locator("button[aria-haspopup]")
    if btn.count() and re.search(r"select one", btn.first.inner_text(), re.I):
        _dropdown_choice(ctx, btn.first, "Degree")
    fos = ed.get_by_label("Field of Study")
    field = (ctx.profile.get("education") or {}).get("field") or ""
    if not field:
        deg = ((ctx.master.get("education") or [{}])[0]).get("degree", "")
        field = deg.split(",", 1)[1].strip() if "," in deg else ""
    if fos.count() and field and not _pills(fos.first.locator("xpath=ancestor::*[@data-automation-id][1]")):
        def choose(texts):
            return C.exact_option(texts, field) or next((t for t in texts if C.norm(field) in C.norm(t)), None)
        got = _pick_from_prompt(ctx, fos.first, field, choose, scroll_tries=8)
        if got:
            ctx.fill_record("Field of Study", got)
        else:
            ctx.problem(f"field of study '{field}' not in Workday list - user must pick it")


def _certifications(ctx):
    page = ctx.page
    certs = earned_certs(ctx.profile, ctx.master)
    if not certs or not (_group(page, "Certifications").count() or _add_button(page, "Certifications")):
        return
    added, missing = [], []
    for cert in certs:
        k = len(added) + 1
        if _ensure_rows(page, "Certifications", k) < k:
            ctx.problem("Certifications: could not add a row")
            break
        g = _group(page, f"Certifications {k}")
        box = g.get_by_label(re.compile(r"^Certification", re.I))
        if not box.count():
            box = g.locator(f"input[type=text]{NOT_HONEYPOT}, input:not([type]){NOT_HONEYPOT}")
        got = None
        if box.count():
            got = _pick_from_prompt(ctx, box.first, cert, lambda texts, c=cert: match_cert(texts, c), scroll_tries=6)
        if got:
            added.append(got)
        else:
            missing.append(cert)
            _delete_row(page, g)  # never leave an empty (required) certificate row behind
        _delete_empty_rows(page, "Specialties")
    _delete_empty_rows(page, "Certifications")
    if _delete_empty_rows(page, "Specialties"):
        ctx.step("deleted empty Specialties rows")
    if added:
        ctx.fill_record("Certifications", "; ".join(added))
    if missing:
        ctx.problem("certifications not in this Workday list (left out): " + "; ".join(missing))


def _skills(ctx):
    page = ctx.page
    sbox = page.get_by_role("textbox", name=re.compile("Type to Add Skills", re.I))
    if not sbox.count():
        return
    added = []
    for s in _top_skills(ctx):
        sbox.fill(s)
        sbox.press("Enter")
        page.wait_for_timeout(1300)
        o = page.locator('[data-automation-id="promptOption"]:visible')
        t = [C.clean(x) for x in o.all_inner_texts()]
        k = next((i for i, x in enumerate(t) if x.lower() == s.lower()), None)
        if k is None:
            k = next((i for i, x in enumerate(t) if x.lower().startswith(s.lower().split()[0])), None)
        if k is not None:
            o.nth(k).click()
            added.append(t[k])
        page.wait_for_timeout(300)
    page.keyboard.press("Escape")
    ctx.fill_record("Skills", ", ".join(added) or "(none matched)")


def _websites(ctx):
    page = ctx.page
    linkedin = ctx.p("personal", "linkedin")
    social = _group(page, "Social Network URLs")
    if social.count() and linkedin:
        li = social.get_by_label("LinkedIn")
        if li.count():
            if not li.first.input_value():
                li.first.fill(linkedin)
                ctx.fill_record("LinkedIn", linkedin)
            linkedin = ""  # LinkedIn has its own field: keep it out of Websites
    urls = [u for u in dict.fromkeys([ctx.p("personal", "website"), ctx.p("personal", "github"), linkedin]) if u]
    if not urls or not (_group(page, "Websites").count() or _add_button(page, "Websites")):
        return
    have = _ensure_rows(page, "Websites", len(urls))
    for i, u in enumerate(urls[:have]):
        inp = _group(page, f"Websites {i + 1}").locator(f"input{NOT_HONEYPOT}").first
        if inp.count() and inp.input_value() != u:
            inp.fill(u)
    if have:
        ctx.fill_record("Websites", ", ".join(urls[:have]))
    if have < len(urls):
        ctx.problem(f"Websites: only {have} of {len(urls)} rows could be added")


def _experience(ctx):
    """My Experience. Resume first; each later section is isolated so one failure cannot undo the others."""
    try:
        _upload_resume(ctx)
    except Exception as e:
        ctx.problem(f"resume upload: {type(e).__name__} {str(e)[:100]}")
    for name, fn in (("Work Experience", _work_experience), ("Education", _education), ("Certifications", _certifications),
                     ("Skills", _skills), ("Websites", _websites)):
        try:
            fn(ctx)
        except Exception as e:
            ctx.problem(f"{name}: {type(e).__name__} {str(e)[:100]}")


# ================================================================== read-back
READBACK_JS = r"""() => {
  const vis = e => !!(e.offsetParent || e.getClientRects().length);
  const out = [];
  document.querySelectorAll('[data-automation-id^="formField"]').forEach(f => {
    if (!vis(f)) return;
    const lab = f.querySelector('label, legend'); const raw = lab ? lab.innerText : '';
    const req = /\*/.test(raw) || !!f.querySelector('[aria-required="true"], [required]');
    const pills = [...f.querySelectorAll('[data-automation-id="selectedItem"]')].map(p => p.innerText.trim()).filter(Boolean);
    const btn = f.querySelector('button[aria-haspopup]');
    const radios = [...f.querySelectorAll('input[type=radio]')], checks = [...f.querySelectorAll('input[type=checkbox]')];
    const inp = [...f.querySelectorAll('input:not([type=hidden]):not([type=file]):not([type=radio]):not([type=checkbox]):not([type=password]), textarea')]
      .filter(vis).filter(i => i.getAttribute('data-automation-id') !== 'beecatcher');
    let val = '';
    if (pills.length) val = pills.join('; ');
    else if (btn) val = btn.innerText.trim();
    else if (radios.length) { const c = radios.find(r => r.checked); if (c) { const l = c.id && document.querySelector(`label[for="${c.id}"]`); val = l ? l.innerText.trim() : 'checked'; } }
    else if (checks.length) val = checks.some(c => c.checked) ? 'checked' : '';
    else if (inp.length) val = inp.map(i => i.value).filter(Boolean).join(' / ');
    const err = [...f.querySelectorAll('[data-automation-id="errorMessage"], [data-automation-id="inputError"], [role=alert]')]
      .map(e => e.innerText.trim()).filter(Boolean).join(' ');
    out.push({label: raw.replace(/\s+/g, ' ').replace(/[\s*:]+$/, '').trim(), value: val, required: req, error: err,
              kind: f.querySelector('input[type=file]') ? 'file' : ''});
  });
  const banner = document.querySelector('[data-automation-id="errorBanner"]');
  if (banner && vis(banner)) out.push({label: 'Error banner', value: '', required: false, error: banner.innerText.trim(), kind: 'banner'});
  return out;
}"""


def readback_step(ctx, step):
    """Read back every visible field (value, selected pills, errors); report empty required fields and errors."""
    try:
        fields = ctx.page.evaluate(READBACK_JS)
    except Exception as e:
        ctx.problem(f"read-back failed on '{step}': {type(e).__name__}")
        return []
    fields = [f for f in fields if f.get("kind") != "file"]
    rb = getattr(ctx, "readback", None)
    if isinstance(rb, dict):
        rb[step] = fields
    for lab in C.empty_required(fields):
        ctx.problem(f"'{step}': required field empty: {lab}")
    for lab, err in C.field_errors(fields):
        ctx.problem(f"'{step}': {lab}: {err[:160]}")
    return fields


# ================================================================== main loop
def fill(ctx, max_steps=12, **kw):
    page = ctx.page
    _start(ctx)
    if not _auth(ctx):
        return
    done = set()
    for _ in range(max_steps):
        page.wait_for_timeout(2000)
        step = _active_step(page) or "?"
        low = step.lower()
        if "review" in low:
            readback_step(ctx, step)
            ctx.step("reached Review (Submit not clicked)")
            return
        if step in done:
            ctx.problem(f"stuck on step '{step}' - a required field is probably missing")
            readback_step(ctx, step)
            return
        done.add(step)
        if "experience" in low:
            _experience(ctx)
        _fill_boxes(ctx)
        readback_step(ctx, step)
        ctx.step(f"step filled: {step}")
        nxt = page.locator(AID("pageFooterNextButton"))
        if not nxt.count():
            ctx.problem("no Next/Save-and-Continue button found")
            return
        txt = C.clean(nxt.first.inner_text())
        if re.search(r"submit", txt, re.I):
            ctx.step("at final page (Submit not clicked)")
            return
        nxt.first.click()
        page.wait_for_timeout(2500)
        err = page.locator(AID("errorBanner"))
        if err.count() and err.first.is_visible():
            ctx.problem(f"validation errors on '{step}': " + C.clean(err.first.inner_text())[:200])
            readback_step(ctx, step)
            return
    ctx.problem("max steps reached before Review")
