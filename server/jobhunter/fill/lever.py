"""Lever (basic): standard fields by name + custom questions in .application-question. (Not live-tested.)"""
import re

from . import common as C


def fill(ctx, **kw):
    page = ctx.page
    if "/apply" not in page.url:
        page.goto(page.url.split("?")[0].rstrip("/") + "/apply", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
    try:
        page.wait_for_selector("form input", timeout=20000)
    except Exception:
        ctx.problem("no application form found")
        return
    ctx.step("form loaded")
    if ctx.resume and C.upload_by_label(ctx, "resume", ctx.resume):
        ctx.step("resume uploaded")
        page.wait_for_timeout(3000)
    p = lambda k: ctx.p("personal", k)
    std = {"name": p("full_name"), "email": p("email"), "phone": p("phone"), "location": p("city"),
           "urls[LinkedIn]": p("linkedin"), "urls[GitHub]": p("github"), "urls[Portfolio]": ""}
    for name, val in std.items():
        el = page.locator(f'input[name="{name}"]')
        if val and el.count() and el.first.is_visible():
            C.fill_text(ctx, el.first, name, val)
            if name == "location":
                page.wait_for_timeout(1500)
                opts = page.locator(".dropdown-results div:visible, [role=option]:visible")
                if opts.count():
                    opts.first.click()
    qs = page.locator(".application-question:visible")
    for i in range(qs.count()):
        q = qs.nth(i)
        try:
            lab = q.locator(".application-label, label").first
            raw = lab.inner_text() if lab.count() else ""
            label = C.label_text(raw)
            if not label or re.match(r"^(resume|full name|email|phone|current company|location|linkedin|github|portfolio)", label, re.I):
                continue
            before = len(ctx.unknown)
            if q.locator('input[type="radio"],input[type="checkbox"]').count():
                C.fill_choice_group(ctx, q, label)
            elif q.locator("select").count():
                C.fill_native_select(ctx, q.locator("select").first, label)
            else:
                el = q.locator("textarea, input[type=text], input:not([type])").first
                if el.count():
                    C.fill_text(ctx, el, label)
            if len(ctx.unknown) > before:
                ctx.unknown[-1]["required"] = C.is_required(raw)
        except Exception as e:
            ctx.problem(f"question {i}: {type(e).__name__}")
    ctx.step("fields filled")
    ctx.step("read-back done; stopped before Submit (not clicked)")
