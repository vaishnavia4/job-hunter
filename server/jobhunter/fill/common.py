"""Shared page helpers: visible-only option pickers, label-driven control filling, upload by label, read-back.

Rules baked in (see skills/apply/lessons.md): never guess (unknown -> report + leave blank), only look at VISIBLE
[role=option] lists, upload resume/cover by label never by position, never click anything that looks like Submit.
"""
import re

BIG_LIST = 60  # lists longer than this (country, phone code, location) are typed into, not read
YES_RX = re.compile(r"^(yes|true|y|i agree|i acknowledge|agree)\b", re.I)
SUBMIT_RX = re.compile(r"^\s*(submit|submit application|send application|finish|complete application)\s*$", re.I)


def clean(s):
    return re.sub(r"\s+", " ", (s or "").replace("\u200b", "")).strip()


def label_text(s):
    return clean(s).rstrip("*").strip(" :*")


def is_required(raw):
    return "*" in (raw or "")


def safe_click(loc, **kw):
    """Click, refusing anything that is a final-submit button."""
    try:
        t = clean(loc.first.inner_text(timeout=1500))
    except Exception:
        t = ""
    if SUBMIT_RX.match(t):
        raise RuntimeError("refusing to click submit: " + t)
    loc.first.click(**kw)


def visible_options(page, limit=400):
    loc = page.locator('[role="option"]:visible')
    try:
        n = min(loc.count(), limit)
        return [clean(loc.nth(i).inner_text()) for i in range(n)]
    except Exception:
        return []


def click_option(page, text):
    """Click the visible [role=option] whose text equals `text` (fall back to unique containment)."""
    loc = page.locator('[role="option"]:visible')
    n = loc.count()
    texts = [clean(loc.nth(i).inner_text()) for i in range(n)]
    for i, t in enumerate(texts):
        if t == text:
            loc.nth(i).click()
            return True
    hits = [i for i, t in enumerate(texts) if text.lower() in t.lower()]
    if len(hits) == 1:
        loc.nth(hits[0]).click()
        return True
    return False


def open_combobox(page, el):
    el.scroll_into_view_if_needed()
    el.click()
    page.wait_for_timeout(350)
    if not page.locator('[role="option"]:visible').count():
        el.press("ArrowDown")
        page.wait_for_timeout(350)


def combobox_value(el):
    """Text currently selected in a react-select (greenhouse) or the input's own value."""
    try:
        return clean(el.evaluate(
            """e=>{const c=e.closest('[class*=select__control],[class*=-control],.select-shell,[class*=select]');
                   const sv=c&&c.querySelector('[class*=singleValue],[class*=single-value]');
                   return (sv&&sv.innerText)||e.value||''}"""))
    except Exception:
        return ""


def pick_combobox(ctx, el, label, raw_label=""):
    """Fill a combobox using answers.lookup with the VISIBLE option texts. Returns True when set."""
    page = ctx.page
    open_combobox(page, el)
    opts = visible_options(page)
    if len(opts) > BIG_LIST or not opts:
        # type-ahead list (country / location / school): ask without options, type, then match visible options
        val, res = ctx.answer(label)
        if val is None:
            page.keyboard.press("Escape")
            return False
        el.fill("")
        el.press_sequentially(str(val), delay=45)
        page.wait_for_timeout(1500)
        opts = visible_options(page)
        pick = next((o for o in opts if o.lower() == str(val).lower()), None) or \
            next((o for o in opts if str(val).lower() in o.lower()), None) or \
            (opts[0] if len(opts) == 1 else None)
        if pick and click_option(page, pick):
            ctx.fill_record(label, pick)
            return True
        page.keyboard.press("Escape")
        ctx.problem(f"no matching option for '{label}' = '{val}'")
        return False
    val, res = ctx.answer(label, opts)
    if val is None:
        page.keyboard.press("Escape")
        return False
    if click_option(page, val):
        ctx.fill_record(label, val)
        return True
    page.keyboard.press("Escape")
    ctx.problem(f"could not click option '{val}' for '{label}'")
    return False


def fill_text(ctx, el, label, value=None):
    """Type a value (looked up when not given). Uses pressSequentially+blur so framework bindings register."""
    if value is None:
        value, _ = ctx.answer(label)
        if value is None:
            return False
    el.scroll_into_view_if_needed()
    el.fill("")
    el.fill(str(value))
    try:
        if clean(el.input_value()) != clean(str(value)):
            el.fill("")
            el.press_sequentially(str(value), delay=20)
    except Exception:
        pass
    el.press("Tab")
    ctx.fill_record(label, value)
    return True


def fill_native_select(ctx, el, label):
    opts = [clean(o) for o in el.locator("option").all_inner_texts()]
    opts = [o for o in opts if o and not re.match(r"^(select|choose|--|please)", o, re.I)]
    val, _ = ctx.answer(label, opts)
    if val is None:
        return False
    el.select_option(label=val)
    ctx.fill_record(label, val)
    return True


def handle_checkbox(ctx, el, label):
    """Single checkbox: legal/consent boxes are never ticked here."""
    val, res = ctx.answer(label)
    if val is None:
        return False
    if YES_RX.match(str(val)):
        if not el.is_checked():
            el.check()
        ctx.fill_record(label, "checked")
    return True


def fill_choice_group(ctx, group, label):
    """Radio / checkbox group (fieldset or container): options are the visible input labels."""
    kind = "radio" if group.locator('input[type="radio"]').count() else "checkbox"
    inputs = group.locator(f'input[type="{kind}"]')
    n = inputs.count()
    opts, els = [], []
    for i in range(n):
        inp = inputs.nth(i)
        t = ""
        try:
            iid = inp.get_attribute("id")
            if iid:
                lab = group.locator(f'label[for="{iid}"]')
                if lab.count():
                    t = clean(lab.first.inner_text())
            if not t:
                t = clean(inp.evaluate("e=>(e.closest('label')||e.parentElement).innerText"))
        except Exception:
            pass
        if t:
            opts.append(t)
            els.append(inp)
    if not opts:
        return False
    if kind == "checkbox" and len(opts) == 1:
        return handle_checkbox(ctx, els[0], label + " " + opts[0] if len(label) < 5 else label)
    val, _ = ctx.answer(label, opts)
    if val is None:
        return False
    i = opts.index(val)
    try:
        els[i].check(force=True)
    except Exception:
        group.locator(f'label:has-text("{val[:40]}")').first.click()
    ctx.fill_record(label, val)
    return True


def fill_yes_no_buttons(ctx, container, label):
    """Ashby-style Yes/No button pair. Returns True/False, or None when the container has no such pair."""
    btns = container.locator("button:visible")
    texts = [clean(btns.nth(i).inner_text()) for i in range(btns.count())]
    if "Yes" in texts and "No" in texts:
        val, _ = ctx.answer(label, ["Yes", "No"])
        if val is None:
            return False
        btns.nth(texts.index(val)).click()
        ctx.fill_record(label, val)
        return True
    return None


# ------------------------------------------------------------------ files
def file_inputs(page):
    """[(kind, locator)] for every file input, classified by its label/name: resume | cover | other."""
    out = []
    loc = page.locator('input[type="file"]')
    for i in range(loc.count()):
        el = loc.nth(i)
        try:
            info = el.evaluate(
                """e=>{const t=[e.id,e.name,e.getAttribute('aria-label')||''];
                       if(e.id){document.querySelectorAll('label').forEach(l=>{if(l.htmlFor===e.id)t.push(l.innerText)})}
                       const lb=e.getAttribute('aria-labelledby'); if(lb){const x=document.getElementById(lb); if(x)t.push(x.innerText)}
                       let c=e.parentElement; for(let k=0;k<4&&c;k++,c=c.parentElement){
                         const h=c.querySelector('label,legend,h3,h4,[class*=label],[class*=heading]'); if(h&&!h.contains(e)){t.push(h.innerText);break}}
                       return t.join(' | ')}""")
        except Exception:
            info = ""
        low = info.lower()
        kind = "cover" if "cover" in low else ("resume" if re.search(r"resume|\bcv\b", low) else "other")
        out.append((kind, el, info))
    return out


def upload_by_label(ctx, kind, path):
    """Upload `path` into the file input classified as `kind` (resume|cover). Never by position."""
    if not path:
        return False
    page = ctx.page
    cands = [(k, e, i) for k, e, i in file_inputs(page) if k == kind]
    # prefer the real field over an 'autofill' helper (Ashby)
    cands.sort(key=lambda c: ("autofill" in c[2].lower()))
    if not cands:
        return False
    k, el, info = cands[0]
    el.set_input_files(str(path))
    page.wait_for_timeout(2500)
    ok = path.name in page.inner_text("body")
    ctx.fill_record(f"{kind} upload", path.name + ("" if ok else " (name not shown - verify)"))
    return True


def readback(page):
    """{label: value} of every labelled, visible form control (for the final check)."""
    return page.evaluate(
        r"""()=>{const o={};document.querySelectorAll('label[for]').forEach(l=>{
              const e=document.getElementById(l.htmlFor); if(!e||e.type==='file'||!e.offsetParent&&e.type!=='checkbox')return;
              let v=e.type==='checkbox'?(e.checked?'checked':''):(e.value||'');
              const c=e.closest('[class*=select__control],[class*=-control]'); const sv=c&&c.querySelector('[class*=singleValue],[class*=single-value]');
              if(sv)v=sv.innerText; o[l.innerText.trim().replace(/\s+/g,' ')]=v});return o}""")


DEGREE_PREF = [r"^bachelor of engineering", r"^bachelor'?s? degree", r"^bachelor'?s?$", r"^bachelor of science", r"^bachelor of technology", r"^b\.?e\.?\b", r"^b\.?s\.?\b"]


def pick_degree(options):
    """Preferred bachelor option: Bachelor of Engineering > Bachelor's Degree > Bachelor of Science. Never Arts."""
    for rx in DEGREE_PREF:
        for o in options:
            if re.search(rx, o.strip(), re.I) and not re.search(r"\barts?\b", o, re.I):
                return o
    return None


def education(ctx):
    ed = (ctx.master.get("education") or [{}])[0]
    school = clean((ed.get("school") or "").split(",")[0])
    return school, ed.get("degree", "")


# ------------------------------------------------------------------ pure helpers (unit-tested offline)
PLACEHOLDER_RX = re.compile(r"^(select one|select\.*|choose\.*|choose one|please select\.*|--+|-select-|select an option|no items\.?)$", re.I)


def norm(s):
    """Case/space/punctuation-insensitive form used for exact option matching."""
    return re.sub(r"[^a-z0-9&+#]+", " ", clean(s).lower()).strip()


def exact_option(options, want):
    """The option equal to `want` (case, space and punctuation insensitive) or None. Never a partial match:
    'X College, Some University' is NOT 'Some University'."""
    w = norm(want)
    if not w:
        return None
    return next((o for o in options or [] if norm(o) == w), None)


def is_empty_value(v):
    v = clean(v if isinstance(v, str) else ("" if v is None else str(v)))
    return not v or bool(PLACEHOLDER_RX.match(v))


def empty_required(fields):
    """Labels of read-back fields that are required but empty / still on a placeholder ('Select One')."""
    out = []
    for f in fields or []:
        if f.get("required") and is_empty_value(f.get("value")):
            lab = clean(f.get("label")) or "?"
            if lab not in out:
                out.append(lab)
    return out


def field_errors(fields):
    """[(label, error text)] for read-back fields that show a validation message."""
    return [(clean(f.get("label")) or "?", clean(f.get("error"))) for f in fields or [] if clean(f.get("error"))]
