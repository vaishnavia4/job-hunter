// Workday Review page: click Submit (only if the footer button really says Submit) and wait for the confirmation.
async (page) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const b = page.locator('[data-automation-id="pageFooterNextButton"]');
  const txt = norm(await b.innerText().catch(() => ''));
  if (txt !== 'Submit') return JSON.stringify({ submitted: false, detail: 'footer button is "' + txt + '", not Submit' });
  await b.click();
  for (let i = 0; i < 20; i++) {
    await page.waitForTimeout(1500);
    if (/\/jobTasks\/completed\//.test(page.url())) break;
    if (await page.locator('[data-automation-id="errorMessage"]:visible').count()) break;
  }
  const done = /\/jobTasks\/completed\//.test(page.url());
  const errors = (await page.locator('[data-automation-id="errorMessage"]:visible').allInnerTexts()).map(norm).slice(0, 5);
  const captcha = (await page.locator('iframe[src*="captcha"], iframe[title*="challenge" i], .g-recaptcha, #px-captcha').count()) > 0;
  return JSON.stringify({ submitted: done, errors, captcha, text: norm(await page.locator('main').innerText().catch(() => '')).slice(0, 200) });
}
