// Workday: where are we? Returns { step, submitted, already_applied, signed_out, button, errors }.
async (page) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  await page.waitForTimeout(1500);
  const step = norm(await page.locator('[data-automation-id="progressBarActiveStep"]').innerText().catch(() => ''));
  const body = norm(await page.locator('body').innerText().catch(() => ''));
  return JSON.stringify({
    step,
    submitted: /\/jobTasks\/completed\//.test(page.url()),
    already_applied: /you('ve| have) already applied|already submitted an application/i.test(body),
    not_found: /page you are looking for (doesn't|does not) exist|job (is no longer|has been) (available|filled|posted)|no longer accepting/i.test(body),
    broken: /something went wrong\s*please refresh/i.test(body),
    password_form: (await page.locator('input[type=password]:visible').count()) > 0,
    button: norm(await page.locator('[data-automation-id="pageFooterNextButton"]').innerText().catch(() => '')),
    errors: (await page.locator('[data-automation-id="errorMessage"]:visible').allInnerTexts()).map(norm).slice(0, 5),
  });
}
