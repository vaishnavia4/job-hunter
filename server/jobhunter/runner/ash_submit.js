// Ashby: click "Submit Application" and report what happened (confirmation, captcha, validation errors).
async (page) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const b = page.locator('button').filter({ hasText: /^Submit Application$/i }).first();
  if (!(await b.count())) return JSON.stringify({ submitted: false, detail: 'no Submit Application button' });
  await b.scrollIntoViewIfNeeded().catch(() => {});
  await b.click();
  const okRx = /application (was |has been )?(successfully )?(submitted|received)|thank you for applying|thanks for applying|we('ve| have) received your application|successfully submitted/i;
  let t = '';
  for (let i = 0; i < 16; i++) {
    await page.waitForTimeout(1500);
    t = norm(await page.locator('body').innerText().catch(() => ''));
    if (okRx.test(t)) break;
  }
  const submitted = okRx.test(t);
  const captcha = (await page.locator('iframe[title*="challenge" i]:visible, iframe[src*="hcaptcha"][title*="challenge" i]:visible, iframe[src*="recaptcha/api2/bframe"]:visible, iframe[src*="recaptcha/enterprise/bframe"]:visible').count()) > 0;
  const errors = submitted ? [] : (await page.locator('[class*="error" i]:visible, [role=alert]:visible').allInnerTexts()).map(norm).filter(Boolean).slice(0, 6);
  const i = t.search(okRx);
  return JSON.stringify({ submitted, captcha: !submitted && captcha, spam_flag: !submitted && /flagged as possible spam|couldn.t submit your application/i.test(t), errors, text: (i >= 0 ? t.slice(Math.max(0, i - 60), i + 160) : t.slice(-260)) });
}
