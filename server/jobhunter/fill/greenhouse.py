"""Greenhouse (new job-boards.greenhouse.io UI): react-select comboboxes, separate Resume / Cover Letter uploads."""
import re

from . import common as C

SKIP_LABELS = re.compile(r"^(attach|enter manually|dropbox|google drive|search)$", re.I)


def _combo_pick_text(ctx, el, label, want_rx, exact=None):
    """Type-ahead combobox where we already know the wanted text (country, location)."""
    page = ctx.page
    C.open_combobox(page, el)
    el.fill("")
    el.press_sequentially(want_rx[0], delay=40)
    page.wait_for_timeout(1500)
    opts = C.visible_options(page)
    pick = next((o for o in opts if exact and o.lower() == exact.lower()), None) or \
        next((o for o in opts if re.search(want_rx[1], o, re.I)), None)
    if pick and C.click_option(page, pick):
        ctx.fill_record(label, pick)
        return True
    page.keyboard.press("Escape")
    ctx.problem(f"no option for '{label}' ~ '{want_rx[0]}' (saw {len(opts)} options)")
    return False


def _education(ctx, el, label, fid):
    school, degree = C.education(ctx)
    if fid.startswith("school"):
        if not school:
            return False
        return _combo_pick_text(ctx, el, label, (school, re.escape(school.split()[0])), exact=school) or False
    if fid.startswith("degree"):
        C.open_combobox(ctx.page, el)
        opts = C.visible_options(ctx.page)
        pick = C.pick_degree(opts)
        if pick and C.click_option(ctx.page, pick):
            ctx.fill_record(label, pick)
            return True
        ctx.page.keyboard.press("Escape")
        ctx.problem("degree: no Bachelor option found in " + str(opts[:8]))
    return False


def fill(ctx):
    page = ctx.page
    try:
        page.wait_for_selector("form input, form textarea", timeout=20000)
    except Exception:
        txt = C.clean(page.inner_text("body"))[:300]
        ctx.problem("no application form found: " + txt)
        return
    if re.search(r"no longer (accepting|available)|position (has been|is) (closed|filled)|job not found|applications? (are )?closed", page.inner_text("body"), re.I) \
            and not page.locator("#first_name").count():
        ctx.problem("posting appears closed")
        return
    ctx.step("form loaded")

    # 1. files first (some ATSs autofill from the resume and overwrite fields)
    if ctx.resume and C.upload_by_label(ctx, "resume", ctx.resume):
        ctx.step("resume uploaded")
    elif ctx.resume:
        ctx.problem("resume upload input not found")
    if ctx.cover:
        if C.upload_by_label(ctx, "cover", ctx.cover):
            ctx.step("cover letter uploaded")
        else:
            ctx.problem("cover letter input not found (skipped)")

    # 2. every labelled control
    labels = page.evaluate(
        r"""()=>[...document.querySelectorAll('form label[for]')].filter(l=>l.htmlFor&&!l.closest('.iti__dropdown-content'))
              .map(l=>({id:l.htmlFor,text:l.innerText.replace(/\s+/g,' ').trim()}))""")
    seen = set()
    for lab in labels:
        fid, raw = lab["id"], lab["text"]
        label = C.label_text(raw)
        if not label or SKIP_LABELS.match(label) or fid in seen or fid.startswith("iti") or fid in ("resume", "cover_letter", "resume_text", "cover_letter_text"):
            continue
        seen.add(fid)
        el = page.locator(f'[id="{fid}"]')
        if not el.count():
            continue
        el = el.first
        before = len(ctx.unknown)
        try:
            tag = el.evaluate("e=>e.tagName.toLowerCase()")
            typ = (el.get_attribute("type") or "").lower()
            role = (el.get_attribute("role") or "").lower()
            if fid == "country":
                _combo_pick_text(ctx, el, "Phone country", ("United States", r"^United States(\s*\+1)?$|^United States"), exact="United States +1") \
                    if C.combobox_value(el) == "" else None
            elif fid == "phone":
                C.fill_text(ctx, el, "Phone", ctx.p("personal", "phone_digits") or ctx.p("personal", "phone"))
            elif fid == "candidate-location":
                city, st = ctx.p("personal", "city"), ctx.p("personal", "state")
                _combo_pick_text(ctx, el, label, (f"{city}, {st}" if city else "", re.escape(city or "zzzz")))
            elif fid.startswith(("school", "degree")):
                _education(ctx, el, label, fid)
            elif typ == "file":
                continue
            elif role == "combobox":
                C.pick_combobox(ctx, el, label)
            elif tag == "select":
                C.fill_native_select(ctx, el, label)
            elif typ == "checkbox":
                C.handle_checkbox(ctx, el, label)
            elif tag in ("input", "textarea"):
                C.fill_text(ctx, el, label)
        except Exception as e:
            ctx.problem(f"{label}: {type(e).__name__} {str(e)[:120]}")
        if len(ctx.unknown) > before:
            ctx.unknown[-1]["required"] = C.is_required(raw)
    ctx.step("fields filled")

    # radio / checkbox fieldsets (custom questions, multi-selects)
    groups = page.locator("form fieldset:visible")
    for i in range(groups.count()):
        g = groups.nth(i)
        if not g.locator('input[type="radio"],input[type="checkbox"]').count():
            continue
        leg = g.locator("legend")
        label = C.label_text(leg.first.inner_text()) if leg.count() else ""
        if label:
            try:
                C.fill_choice_group(ctx, g, label)
            except Exception as e:
                ctx.problem(f"{label}: {type(e).__name__}")

    # 3. read-back: required controls still empty
    try:
        rb = C.readback(page)
        reported = {u["label"] for u in ctx.unknown}
        for raw, v in rb.items():
            if C.is_required(raw) and not v and C.label_text(raw) not in reported \
                    and not any(C.label_text(raw) in p for p in ctx.problems):
                ctx.problem(f"required field empty after fill: {C.label_text(raw)}")
    except Exception:
        pass
    ctx.step("read-back done; stopped before Submit (not clicked)")
