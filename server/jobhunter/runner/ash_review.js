// Ashby form -> applications/<folder>/review.json (via 127.0.0.1:47615/save_review). Templated by autoapply.
async (page) => {
  const FOLDER = '__FOLDER__', COMPANY = '__COMPANY__', TITLE = '__TITLE__', JOBID = '__JOBID__';
  const data = await page.evaluate(() => {
    const norm = s => (s || '').replace(/\s+/g, ' ').trim();
    const yn = (window.__jh && window.__jh.yn) || {};
    const fields = [], files = [];
    for (const e of document.querySelectorAll('[class*="_fieldEntry_"], [class*="ashby-application-form-field-entry"]')) {
      const l = e.querySelector('label');
      const raw = norm(l ? l.innerText : '');
      const label = raw.replace(/\*$/, '').trim();
      if (!label) continue;
      const required = /\*$/.test(raw) || !!e.querySelector('input[required], textarea[required], select[required]');
      let v = '';
      const file = e.querySelector('input[type=file]');
      const boxes = [...e.querySelectorAll('input[type=radio], input[type=checkbox]')];
      const btns = [...e.querySelectorAll('button')].filter(b => /^(Yes|No)$/.test(norm(b.innerText)));
      if (file) {
        v = (norm(e.innerText).match(/[\w.-]+\.(pdf|docx?)/i) || [''])[0];
        if (v) files.push(v);
      } else if (btns.length >= 2) {
        const on = btns.find(b => /active|selected|checked/i.test(b.className) || b.getAttribute('aria-pressed') === 'true' || b.getAttribute('aria-checked') === 'true');
        v = on ? norm(on.innerText) : (yn[label] || '');
      } else if (boxes.length) {
        const on = boxes.filter(b => b.checked);
        if (!on.length && !required) continue;
        v = on.map(b => { const fl = b.id && e.querySelector('label[for="' + b.id + '"]'); return norm(fl ? fl.innerText : (b.closest('label') || b.parentElement).innerText); }).join('; ');
        if (boxes.length === 1 && on.length) v = 'checked';
      } else {
        const el = e.querySelector('select, textarea, input:not([type=hidden])');
        if (!el) continue;
        v = norm(el.tagName === 'SELECT' ? (el.selectedOptions[0] || {}).innerText : el.value);
        if (!v && !required) continue;
      }
      fields.push({ label, value: v, required });
    }
    // page text = questions with the CHOSEN values only: the raw form text lists every radio option ("Male", "I identify as
    // a protected veteran"), which the final check would read as selected
    const text = fields.map(f => f.label + String.fromCharCode(10) + f.value).join(String.fromCharCode(10, 10));
    return { fields, files: [...new Set(files)], text: text.slice(0, 20000) };
  });
  const review = { company: COMPANY, title: TITLE, job_id: JOBID, fields: data.fields, files: data.files, page_text: data.text };
  const r = await page.request.post('http://127.0.0.1:47615/save_review', { data: { folder: FOLDER, review } });
  const empty_required = data.fields.filter(f => f.required && !f.value).map(f => f.label);
  return JSON.stringify({ save: await r.json(), n: data.fields.length, files: data.files, empty_required });
}
