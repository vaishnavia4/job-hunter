"""Ashby: Yes/No buttons, autocomplete location, radio fieldsets, file inputs by label. (Not live-tested.)"""
import re

from . import common as C

NAME_RX = re.compile(r"^(full |legal )?name$", re.I)


def _container(page):
    return page.locator('[class*="fieldEntry" i]:visible, .ashby-application-form-field-entry:visible')


def fill(ctx, **kw):
    page = ctx.page
    if "/application" not in page.url:
        tab = page.get_by_role("tab", name=re.compile("application", re.I))
        if tab.count():
            tab.first.click()
        else:
            page.goto(page.url.split("?")[0].rstrip("/") + "/application", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
    try:
        page.wait_for_selector("input, textarea", timeout=20000)
    except Exception:
        ctx.problem("no application form found")
        return
    ctx.step("form loaded")
    if ctx.resume and C.upload_by_label(ctx, "resume", ctx.resume):
        ctx.step("resume uploaded")
        page.wait_for_timeout(3000)  # autofill may rewrite fields; we fill afterwards
    elif ctx.resume:
        ctx.problem("resume input not found")
    if ctx.cover:
        C.upload_by_label(ctx, "cover", ctx.cover)  # most Ashby forms have no cover-letter box
    boxes = _container(page)
    seen = set()
    for i in range(boxes.count()):
        b = boxes.nth(i)
        label = "?"
        try:
            lab = b.locator("label, legend").first
            raw = lab.inner_text() if lab.count() else ""
            label = C.label_text(raw)
            if not label or label in seen:
                continue
            seen.add(label)
            before = len(ctx.unknown)
            if b.locator('input[type="file"]').count():
                continue
            yn = C.fill_yes_no_buttons(ctx, b, label)
            if yn is not None:
                pass
            elif b.locator('input[type="radio"], input[type="checkbox"]').count():
                C.fill_choice_group(ctx, b, label)
            elif b.locator("select").count():
                C.fill_native_select(ctx, b.locator("select").first, label)
            else:
                el = b.locator("input:not([type=hidden]):not([type=file]), textarea").first
                if not el.count():
                    continue
                if NAME_RX.match(label):
                    C.fill_text(ctx, el, label, ctx.p("personal", "full_name"))
                elif re.search(r"location|city", label, re.I):
                    _location(ctx, el, label)
                elif el.get_attribute("role") == "combobox":
                    C.pick_combobox(ctx, el, label)
                else:
                    C.fill_text(ctx, el, label)
            if len(ctx.unknown) > before:
                ctx.unknown[-1]["required"] = C.is_required(raw)
        except Exception as e:
            ctx.problem(f"{label}: {type(e).__name__} {str(e)[:100]}")
    ctx.step("fields filled")
    ctx.step("read-back done; stopped before Submit (not clicked)")


def _location(ctx, el, label):
    page = ctx.page
    city, st = ctx.p("personal", "city"), ctx.p("personal", "state")
    el.click()
    el.fill("")
    el.press_sequentially(city, delay=120)
    page.wait_for_timeout(2000)
    opts = C.visible_options(page)
    pick = next((o for o in opts if city.lower() in o.lower() and (st.lower() in o.lower() or ctx.p("personal", "state_code") in o)), None)
    if pick and C.click_option(page, pick):
        ctx.fill_record(label, pick)
    else:
        page.keyboard.press("Escape")
        ctx.problem(f"location: no option with {city} and {st}")
