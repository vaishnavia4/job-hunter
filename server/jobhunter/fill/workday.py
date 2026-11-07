"""Workday: Apply Manually, sign-in, My Information, My Experience, questions, disclosures; stops at Review.
(Ported from the reference workday_experience.js; not live-tested.)"""
import json
import re

from . import common as C

MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}

AID = lambda a: f'[data-automation-id="{a}"]'
LEGAL_RX = re.compile(r"agree|terms|conditions|arbitrat|certif|acknowledg|consent|privacy", re.I)


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


def _mmyyyy(s):
    m = re.match(r"([A-Za-z]{3})\w*\s+(\d{4})", (s or "").strip())
    return f"{MONTHS[m.group(1).lower()]:02d}{m.group(2)}" if m else None


def _dates(d):
    a, _, b = (d or "").partition("-")
    cur = bool(re.search(r"present|current", b, re.I))
    return _mmyyyy(a), (None if cur else _mmyyyy(b)), cur


def _creds(ctx):
    c = ctx.creds.get("workday") or ctx.creds.get("job_sites_default") or {}
    return c.get("email") or ctx.p("personal", "email"), c.get("password")


def _active_step(page):
    for sel in (AID("progressBarActiveStep"), '[aria-current="step"]'):
        loc = page.locator(sel)
        if loc.count():
            return C.clean(loc.first.inner_text())
    return ""


def _start(ctx):
    page = ctx.page
    for name in (r"^apply$", r"apply manually"):
        btn = page.get_by_role("link", name=re.compile(name, re.I)).or_(page.get_by_role("button", name=re.compile(name, re.I)))
        if btn.count() and btn.first.is_visible():
            btn.first.click()
            page.wait_for_timeout(2500)
    am = page.locator(AID("applyManually"))
    if am.count():
        am.first.click()
        page.wait_for_timeout(3000)


def _auth(ctx):
    """Sign in (or detect that an account must be created). Returns True when past the auth wall."""
    page = ctx.page
    if not page.locator(AID("email")).count() and not page.locator("input[type=password]").count():
        return True
    if re.search(r"currently unavailable|maintenance", page.inner_text("body"), re.I):
        ctx.problem("Workday is in a maintenance window - retry later (do not mark blocked)")
        return False
    email, pw = _creds(ctx)
    if not pw:
        ctx.problem("no Workday password in profile/credentials.json (workday or job_sites_default)")
        return False
    si = page.get_by_role("button", name=re.compile(r"^sign in$", re.I))
    if page.locator(AID("signInContent")).count() == 0 and page.get_by_text("Create Account", exact=False).count() and si.count() == 0:
        pass
    em = page.locator(f'{AID("email")}:visible, input[type=email]:visible').first
    em.fill(email)
    page.locator("input[type=password]:visible").first.fill(pw)
    if page.locator(AID("verifyPassword")).count():  # we are on Create Account
        ctx.problem("Workday asked to create an account: password filled; user must tick the terms box, submit, and verify the email")
        return False
    cf = page.locator(AID("click_filter"))
    (cf.first if cf.count() else page.locator(f'{AID("signInSubmitButton")}')).click()
    page.wait_for_timeout(4000)
    if page.locator(AID("errorMessage")).count() or page.locator("input[type=password]:visible").count():
        ctx.problem("Workday sign-in failed (no account yet, unverified, or wrong password) - do not retry repeatedly (locks account)")
        return False
    ctx.step("signed in")
    return True


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
        st = ctx.p("personal", "state")
        pick = next((o for o in opts if o.lower() == st.lower()), None)
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


def _how_heard(ctx, box, label):
    page = ctx.page
    box.locator("input, button").first.click()
    page.wait_for_timeout(800)
    want = r"linkedin" if "linkedin" in ctx.source else r"careers? (web)?site|company (web)?site|corporate website|^website|career"
    for _ in range(3):
        opts = page.locator('[data-automation-id="promptOption"]:visible, [role=option]:visible')
        texts = [C.clean(opts.nth(i).inner_text()) for i in range(opts.count())]
        k = next((i for i, t in enumerate(texts) if re.search(want, t, re.I)), None)
        if k is None:
            k = next((i for i, t in enumerate(texts) if re.search(r"website|careers", t, re.I)), None)
        if k is None:
            break
        opts.nth(k).click()
        page.wait_for_timeout(800)
        ctx.fill_record(label, texts[k])
        if not page.locator('[data-automation-id="promptOption"]:visible').count():
            return True
    page.keyboard.press("Escape")
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
            if re.search(r"how did you hear", label, re.I):
                _how_heard(ctx, b, label)
            elif b.locator("button[aria-haspopup]").count():
                btn = b.locator("button[aria-haspopup]").first
                if re.search(r"select one", btn.inner_text(), re.I):
                    _dropdown_choice(ctx, btn, label)
            elif b.locator('input[type="radio"]').count():
                C.fill_choice_group(ctx, b, label)
            elif b.locator('input[type="checkbox"]').count():
                if LEGAL_RX.search(label) and not b.locator("input[type=checkbox]").first.is_checked():
                    ctx.problem(f"user must tick: {label}")
                else:
                    C.handle_checkbox(ctx, b.locator("input[type=checkbox]").first, label)
            else:
                el = b.locator("input[type=text], input[type=tel], input[type=email], input:not([type]), textarea").first
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
                ctx.problem(f"user must tick: {t[:90]}")
        except Exception:
            pass


def _add_row(page, group_name, n):
    g = page.get_by_role("group", name=group_name, exact=True)
    for i in range(n):
        if page.get_by_role("group", name=f"{group_name} {i + 1}", exact=True).count():
            continue
        g.get_by_role("button", name=re.compile(r"^(Add|Add Another)$")).first.click()
        page.wait_for_timeout(800)


def _experience(ctx):
    page = ctx.page
    jobs = ctx.master.get("experience") or []
    if ctx.resume:
        fi = page.locator('input[type="file"]')
        if fi.count():
            fi.first.set_input_files(str(ctx.resume))
            page.wait_for_timeout(4000)
            ctx.fill_record("resume upload", ctx.resume.name)
    if page.get_by_role("group", name="Work Experience", exact=True).count():
        _add_row(page, "Work Experience", len(jobs))
    for i, j in enumerate(jobs):
        g = page.get_by_role("group", name=f"Work Experience {i + 1}", exact=True)
        if not g.count():
            continue
        frm, to, cur = _dates(j.get("dates"))
        C.fill_text(ctx, g.get_by_label("Job Title").first, f"Job Title {i + 1}", j["title"])
        C.fill_text(ctx, g.get_by_label("Company").first, f"Company {i + 1}", j["company"])
        loc = g.get_by_label("Location", exact=True)
        if loc.count():
            C.fill_text(ctx, loc.first, f"Location {i + 1}", j.get("location", ""))
        if cur:
            cw = g.get_by_label("I currently work here")
            if cw.count() and not cw.first.is_checked():
                cw.first.check()
                page.wait_for_timeout(400)
        months = g.locator('input[aria-label="Month"]')
        if frm and months.count():
            months.nth(0).click()
            months.nth(0).press_sequentially(frm, delay=40)
        if to and months.count() > 1:
            months.nth(1).click()
            months.nth(1).press_sequentially(to, delay=40)
        desc = g.get_by_label("Role Description")
        if desc.count():
            text = " ".join((j.get("bullets") or [])[:4])
            desc.first.fill(text)
        ctx.fill_record(f"Work Experience {i + 1}", f"{j['title']} @ {j['company']} {j.get('dates', '')}")
    # education
    school, _ = C.education(ctx)
    if page.get_by_role("group", name="Education", exact=True).count():
        _add_row(page, "Education", 1)
    ed = page.get_by_role("group", name="Education 1", exact=True)
    if ed.count():
        sc = ed.get_by_label("School or University")
        if sc.count() and school:
            sc.first.fill(school)
            sc.first.press("Enter")
            page.wait_for_timeout(1200)
            o = page.locator('[data-automation-id="promptOption"]:visible', has_text=re.compile(re.escape(school.split()[0]), re.I))
            if o.count():
                o.first.click()
                ctx.fill_record("School", school)
            else:
                ctx.problem(f"school '{school}' not in Workday list - user must pick/type it")
        btn = ed.locator("button[aria-haspopup]")
        if btn.count():
            _dropdown_choice(ctx, btn.first, "Degree")
        fos = ed.get_by_label("Field of Study")
        field = (ctx.profile.get("education") or {}).get("field") or ""
        if not field:
            deg = ((ctx.master.get("education") or [{}])[0]).get("degree", "")
            field = deg.split(",", 1)[1].strip() if "," in deg else ""
        if fos.count() and field:
            fos.first.fill(field)
            fos.first.press("Enter")
            page.wait_for_timeout(1500)
            o = page.locator('[data-automation-id="promptOption"]:visible', has_text=re.compile(re.escape(field.split()[0]), re.I))
            if o.count():
                o.first.click()
                ctx.fill_record("Field of Study", C.clean(o.first.inner_text()) if hasattr(C, "clean") else field)
            else:
                ctx.problem(f"field of study '{field}' not in Workday list - user must pick it")
    # skills
    sbox = page.get_by_role("textbox", name=re.compile("Type to Add Skills", re.I))
    if sbox.count():
        skills = _top_skills(ctx)
        for s in skills:
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
            page.wait_for_timeout(300)
        page.keyboard.press("Escape")
        ctx.fill_record("Skills", ", ".join(skills))
    # websites
    web = page.get_by_role("group", name="Websites", exact=True)
    urls = [u for u in (ctx.p("personal", "linkedin"), ctx.p("personal", "github")) if u]
    if web.count() and urls:
        for i, u in enumerate(urls):
            web.get_by_role("button", name=re.compile(r"^(Add|Add Another)$")).first.click()
            page.wait_for_timeout(700)
            web.locator("input[type=text]").nth(i).fill(u)
        ctx.fill_record("Websites", ", ".join(urls))


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
            ctx.step("reached Review (Submit not clicked)")
            return
        if step in done:
            ctx.problem(f"stuck on step '{step}' - a required field is probably missing")
            return
        done.add(step)
        if "experience" in low:
            _experience(ctx)
        _fill_boxes(ctx)
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
            return
    ctx.problem("max steps reached before Review")
