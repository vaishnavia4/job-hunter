// Workday: ask for a password-reset email. The address comes from the local credential server (never in code or logs).
async (page) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const c = await (await page.request.get('http://127.0.0.1:47613/')).json();
  const tryClick = async (loc) => { if (await loc.count()) { await loc.first().evaluate(e => e.click()); await page.waitForTimeout(2500); return true; } return false; };
  let link = page.locator('[data-automation-id="forgotPasswordLink"], button:has-text("Forgot your password"), a:has-text("Forgot your password")');
  for (let i = 0; i < 3 && !(await link.count()); i++) {
    // get to the email sign-in form first
    await tryClick(page.locator('[data-automation-id="SignInWithEmailButton"], button:has-text("Sign in with email")'))
      || await tryClick(page.locator('[data-automation-id="signInLink"], button:has-text("Sign In"), a:has-text("Sign In")'))
      || await tryClick(page.locator('[data-automation-id="adventureButton"], a:has-text("Apply")').first());
    await tryClick(page.locator('[data-automation-id="applyManually"], a:has-text("Apply Manually")'));
  }
  if (!(await link.count())) return JSON.stringify({ state: 'error', detail: 'no "Forgot your password?" link', text: norm(await page.locator('body').innerText()).slice(0, 300) });
  await link.first().evaluate(e => e.click()); await page.waitForTimeout(3000);
  const em = page.locator('input[data-automation-id="email"]:visible, input[type=email]:visible, input[type=text]:visible').first();
  if (!(await em.count())) return JSON.stringify({ state: 'error', detail: 'no email field on the reset form' });
  await em.fill(c.e);
  const sub = page.locator('[data-automation-id="click_filter"][aria-label*="Reset" i], [data-automation-id="click_filter"][aria-label*="Submit" i], button:has-text("Reset Password"), button[type=submit]:visible');
  if (await sub.count()) await sub.first().evaluate(e => e.click()); else await em.press('Enter');
  await page.waitForTimeout(5000);
  const t = norm(await page.locator('body').innerText()).split(c.e).join('<email>');
  return JSON.stringify({ state: /email|sent|check your/i.test(t) ? 'reset_requested' : 'unclear', captcha: (await page.locator('iframe[title*="challenge" i]:visible').count()) > 0, text: t.slice(0, 260) });
}
