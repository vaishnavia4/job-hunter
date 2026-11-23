// Greenhouse "security code" step: type the emailed code into the code boxes and submit again.
// __CODE__ is filled in by autoapply (read from the applicant's mailbox); it is never logged.
async (page) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const code = '__CODE__';
  const boxes = page.locator('input[id^="security-input-"]');
  const n = await boxes.count();
  if (!n) return JSON.stringify({ submitted: false, detail: 'no security code boxes on the page' });
  if (n === 1) await boxes.first().fill(code);
  else for (let i = 0; i < Math.min(n, code.length); i++) { await boxes.nth(i).fill(code[i]); await page.waitForTimeout(60); }
  const b = page.locator('button[type=submit]:has-text("Submit"), button:has-text("Submit application")').first();
  await b.click();
  let t = '';
  const done = () => /confirmation/i.test(page.url()) || /thank you for applying|application (has been|was) (received|submitted)|we('ve| have) received your application/i.test(t);
  for (let i = 0; i < 14; i++) { await page.waitForTimeout(1500); t = norm(await page.locator('body').innerText().catch(() => '')); if (done()) break; }
  const errors = done() ? [] : (await page.locator('[class*="error" i]:visible, [id$="-error"]:visible').allInnerTexts()).map(norm).filter(Boolean).slice(0, 4);
  return JSON.stringify({ submitted: done(), boxes: n, errors, text: t.slice(0, 160) });
}
