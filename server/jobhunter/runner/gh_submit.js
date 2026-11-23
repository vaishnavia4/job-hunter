// Greenhouse single-page form: click Submit application and report what happened (confirmation, captcha, code prompt, errors).
async (page) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const b = page.locator('button[type=submit]:has-text("Submit"), button:has-text("Submit application")').first();
  if (!(await b.count())) return JSON.stringify({ submitted: false, detail: 'no Submit button' });
  await b.click();
  let t = '';
  for (let i = 0; i < 12; i++) {
    await page.waitForTimeout(1500);
    t = norm(await page.locator('body').innerText().catch(() => ''));
    if (/confirmation/i.test(page.url()) || /thank you for applying|application (has been|was) (received|submitted)|we('ve| have) received your application/i.test(t)) break;
  }
  const submitted = /confirmation/i.test(page.url()) || /thank you for applying|application (has been|was) (received|submitted)|we('ve| have) received your application/i.test(t);
  const captcha = (await page.locator('iframe[title*="challenge" i]:visible, iframe[src*="hcaptcha"][title*="challenge" i]').count()) > 0;
  const code = /verification code|security code|enter the code/i.test(t);
  const errors = (await page.locator('[class*="error" i]:visible, [id$="-error"]:visible').allInnerTexts()).map(norm).filter(Boolean).slice(0, 6);
  return JSON.stringify({ submitted, captcha: !submitted && captcha, needs_code: !submitted && code, errors: submitted ? [] : errors, text: t.slice(0, 200) });
}
