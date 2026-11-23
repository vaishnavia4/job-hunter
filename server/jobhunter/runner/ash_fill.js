// Ashby application form (jobs.ashbyhq.com/<org>/<id>/application): fill every field from the answer bank.
// Templated by autoapply: __RESUME__, __COVER__, __NAME__, __EMAIL__. Answers come from 127.0.0.1:47615/lookup.
// A rerun on the same tab continues: filled text fields, chosen radios and uploaded files are left alone.
async (page) => {
  const RESUME = '__RESUME__', COVER = '__COVER__', NAME = '__NAME__', EMAIL = '__EMAIL__';
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const sleep = ms => page.waitForTimeout(ms);
  const ask = async (q, options) => { try { return await (await page.request.post('http://127.0.0.1:47615/lookup', { data: { q, options } })).json(); } catch (e) { return { status: 'ERROR' }; } };
  const out = { filled: [], needs_user: [], skipped: [], errors: [] };
  const ENTRY = '[class*="_fieldEntry_"], [class*="ashby-application-form-field-entry"]';  // radio groups are fieldsets with the _fieldEntry_ class only
  await page.locator(ENTRY).first().waitFor({ timeout: 25000 }).catch(() => {});
  if (!(await page.locator(ENTRY).count())) { out.errors.push('no Ashby form fields on this page'); return JSON.stringify(out); }
  await page.evaluate(() => { window.__jh = window.__jh || { yn: {} }; });
  const rname = RESUME.split('/').pop();
  const resumeEntry = page.locator(ENTRY).filter({ has: page.locator('input[type=file]') }).filter({ hasText: /resume|cv/i }).first();
  const hasResume = async () => norm(await resumeEntry.innerText().catch(() => '')).includes(rname);
  // resume first: Ashby parses it and can overwrite name/email typed earlier
  if (await resumeEntry.count()) {
    if (!(await hasResume())) { await resumeEntry.locator('input[type=file]').first().setInputFiles(RESUME); await sleep(7000); out.filled.push('upload resume <- ' + rname); }
    else out.filled.push('resume already holds ' + rname);
  } else out.errors.push('no Resume upload field');
  out.slots = { resume: await hasResume() };
  if ((await resumeEntry.count()) && !out.slots.resume) out.errors.push('RESUME NOT IN RESUME SLOT - fix before submitting');

  const entries = await page.locator(ENTRY).all();
  for (const e of entries) {
    let lab = '';
    try {
      lab = norm(await e.locator('label').first().innerText().catch(() => '')).replace(/\*$/, '').trim();
      if (!lab) continue;
      const required = /\*\s*$/.test(norm(await e.locator('label').first().innerText().catch(() => ''))) || (await e.locator('input[required], textarea[required], select[required]').count()) > 0;
      await e.scrollIntoViewIfNeeded().catch(() => {});
      const file = e.locator('input[type=file]');
      if (await file.count()) {
        if (/cover/i.test(lab) && COVER && !COVER.startsWith('__')) {
          const cname = COVER.split('/').pop();
          if (!norm(await e.innerText()).includes(cname)) { await file.first().setInputFiles(COVER); await sleep(3000); out.filled.push('upload cover letter <- ' + cname); }
        }
        continue;
      }
      // Yes / No button pair
      const yn = e.locator('button').filter({ hasText: /^(Yes|No)$/ });
      if ((await yn.count()) >= 2) {
        const prev = await page.evaluate(l => (window.__jh.yn || {})[l], lab);
        if (prev) continue;
        const a = await ask(lab, ['Yes', 'No']);
        const pick = a.status === 'ok' && /^(yes|no)$/i.test(String(a.option || a.answer || '')) ? (/^yes$/i.test(String(a.option || a.answer)) ? 'Yes' : 'No') : null;
        if (pick) { await yn.filter({ hasText: new RegExp('^' + pick + '$') }).first().click(); await sleep(300); await page.evaluate(([l, v]) => { window.__jh.yn[l] = v; }, [lab, pick]); out.filled.push(lab.slice(0, 80) + ' => ' + pick); }
        else out.needs_user.push({ q: lab, options: ['Yes', 'No'], status: a.status, answer: a.answer });
        continue;
      }
      // radio / checkbox group
      const boxes = e.locator('input[type=radio], input[type=checkbox]');
      if (await boxes.count()) {
        if (await e.locator('input:checked').count()) continue;
        const n = await boxes.count(), opts = [];
        for (let i = 0; i < n; i++) {
          const b = boxes.nth(i), id = await b.getAttribute('id');
          let t = id ? norm(await e.locator('label[for="' + id + '"]').first().innerText().catch(() => '')) : '';
          if (!t) t = norm(await b.locator('xpath=ancestor::label[1]').innerText().catch(() => ''));
          if (!t) t = norm(await b.locator('xpath=..').innerText().catch(() => ''));
          opts.push(t);
        }
        if (n === 1) {  // single consent / acknowledgement box
          const a = await ask(lab + (opts[0] && opts[0] !== lab ? ' ' + opts[0] : ''), null);
          if (a.status === 'ok' && /^(checked|yes|true|i agree|agree)$/i.test(String(a.answer)) && a.source !== 'claude_default') { await boxes.first().check({ force: true }).catch(async () => { await boxes.first().click({ force: true }); }); out.filled.push(lab.slice(0, 80) + ' => ticked'); }
          else if (required) out.needs_user.push({ q: lab, status: 'CHECKBOX', note: 'required box: handle per answer bank / consent policy' });
          else out.skipped.push(lab.slice(0, 80) + ' (optional box left unticked)');
          continue;
        }
        const a = await ask(lab, opts);
        const k = a.status === 'ok' && a.option ? opts.findIndex(t => t === norm(a.option)) : -1;
        if (k >= 0) { await boxes.nth(k).check({ force: true }).catch(async () => { await boxes.nth(k).click({ force: true }); }); await sleep(250); out.filled.push(lab.slice(0, 80) + ' => ' + opts[k]); }
        else if (required) out.needs_user.push({ q: lab, options: opts.slice(0, 12), status: a.status, answer: a.answer });
        else out.skipped.push(lab.slice(0, 80) + ' (optional, no saved answer)');
        continue;
      }
      const sel = e.locator('select');
      if (await sel.count()) {
        if (norm(await sel.first().inputValue().catch(() => ''))) continue;
        const opts = (await sel.first().locator('option').allInnerTexts()).map(norm).filter(Boolean);
        const a = await ask(lab, opts);
        if (a.status === 'ok' && a.option) { await sel.first().selectOption({ label: a.option }); out.filled.push(lab.slice(0, 80) + ' => ' + a.option); }
        else if (required) out.needs_user.push({ q: lab, options: opts.slice(0, 12), status: a.status, answer: a.answer });
        continue;
      }
      const inp = e.locator('input:not([type=hidden]):not([type=file]), textarea').first();
      if (!(await inp.count())) continue;
      if (norm(await inp.inputValue().catch(() => ''))) continue;
      const nameAttr = (await inp.getAttribute('name')) || '';
      if (nameAttr === '_systemfield_name') { await inp.fill(NAME); out.filled.push('name'); continue; }
      if (nameAttr === '_systemfield_email') { await inp.fill(EMAIL); out.filled.push('email'); continue; }
      const ph = (await inp.getAttribute('placeholder')) || '';
      if (/^location/i.test(lab) && /start typing/i.test(ph)) {  // autocomplete: type the city, take the Colorado match
        await inp.click(); await inp.pressSequentially('Englewood, Colo', { delay: 60 }); await sleep(2500);
        const o = page.locator('[role=option]:visible').filter({ hasText: /Englewood.*(Colorado|CO)\b/i }).first();
        if (await o.count()) { const t = norm(await o.innerText()); await o.click(); out.filled.push('location => ' + t); }
        else { await inp.fill(''); if (required) out.needs_user.push({ q: lab, note: 'no Englewood, Colorado suggestion' }); }
        continue;
      }
      if (/start typing/i.test(ph)) {  // other autocomplete (school, etc.)
        const a = await ask(lab, null);
        if (a.status === 'ok' && a.answer && String(a.answer).length < 80) {
          await inp.click(); await inp.pressSequentially(String(a.answer), { delay: 40 }); await sleep(2500);
          const o = page.locator('[role=option]:visible').first();
          if (await o.count()) { const t = norm(await o.innerText()); await o.click(); out.filled.push(lab.slice(0, 80) + ' => ' + t); } else { await inp.fill(''); if (required) out.needs_user.push({ q: lab, status: 'NO OPTION', answer: a.answer }); }
        } else if (required) out.needs_user.push({ q: lab, status: a.status, answer: a.answer });
        continue;
      }
      const a = await ask(lab, null);
      if (a.status === 'ok' && a.answer != null && String(a.answer).length < 300) { await inp.fill(String(a.answer)); out.filled.push(lab.slice(0, 80) + ' => ' + String(a.answer).slice(0, 60)); }
      else if (required) out.needs_user.push({ q: lab, status: a.status, answer: a.answer && String(a.answer).length < 300 ? a.answer : undefined });
      else out.skipped.push(lab.slice(0, 80) + ' (optional, no saved answer)');
    } catch (err) { out.errors.push(lab.slice(0, 50) + ': ' + String(err.message).slice(0, 90)); await page.keyboard.press('Escape').catch(() => {}); }
  }
  return JSON.stringify(out, null, 1);
}
