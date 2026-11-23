// Workday job page: is the posting still open, and what does the description say?
async (page) => {
  const norm = s => (s || '').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
  await page.locator('[data-automation-id="jobPostingDescription"], [data-automation-id="adventureButton"], [data-automation-id="errorMessage"]').first().waitFor({ timeout: 12000 }).catch(() => {});
  const body = (await page.locator('body').innerText().catch(() => '')).replace(/\s+/g, ' ');
  const closed = /page you are looking for (doesn't|does not) exist|job (is no longer|has been) (available|filled)|no longer accepting applications/i.test(body);
  const jd = norm(await page.locator('[data-automation-id="jobPostingDescription"]').first().innerText().catch(() => ''));
  return JSON.stringify({ open: !closed && (jd.length > 0 || /Apply/.test(body)), closed, jd: jd.slice(0, 30000) });
}
