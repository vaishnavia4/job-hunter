// Token-free page-script runner: attaches to the apply browser over CDP and runs ONE page function
// (the same `async (page) => {...}` files the Playwright MCP runs), then prints a single JSON line.
//   node run.mjs --cdp http://127.0.0.1:9377 [--host H] [--script file.js] [--screenshot out.png]
// Navigation target comes from the JH_GOTO env var (kept off argv/logs: it can be a secret activation link).
import { createRequire } from 'module';
import { readFileSync } from 'fs';
import path from 'path';

const require = createRequire(import.meta.url);
const arg = (k, d = '') => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const out = (o) => { process.stdout.write(JSON.stringify(o) + '\n'); };

function loadPlaywright() {
  const nodeDir = path.dirname(process.execPath);
  const tries = [process.env.JH_PLAYWRIGHT, 'playwright-core', 'playwright',
    path.join(nodeDir, 'node_modules', 'playwright-core'), path.join(nodeDir, 'node_modules', 'playwright'),
    path.join(nodeDir, 'node_modules', '@playwright', 'mcp', 'node_modules', 'playwright-core')].filter(Boolean);
  for (const t of tries) { try { return require(t); } catch (e) { /* next */ } }
  throw new Error('playwright not found for node (npm i -g playwright-core, or set JH_PLAYWRIGHT)');
}

(async () => {
  let code = 0;
  try {
    const { chromium } = loadPlaywright();
    const browser = await chromium.connectOverCDP(arg('cdp', 'http://127.0.0.1:9377'));
    const ctx = browser.contexts()[0];
    const pages = ctx.pages().filter(p => !/^(devtools|chrome-extension):/.test(p.url()));
    const host = arg('host');
    // One tab per job (JH_TAB = "jh:<job_id>", kept in window.name, which survives navigation): an application that
    // is waiting for an answer or for the user is never navigated away by the next job, and a rerun continues on the
    // page as it stands instead of reloading a filled form (Vaishnavi 2026-11-23).
    const go = process.env.JH_GOTO;
    const tab = process.env.JH_TAB || (go ? 'jh:scratch' : '');
    const nameOf = async p => { try { return await p.evaluate(() => window.name); } catch (e) { return ''; } };
    let page = null, navigated = false;
    if (process.argv.includes('--newtab')) page = await ctx.newPage();
    if (!page && tab) {
      const names = []; for (const p of pages) names.push(await nameOf(p));
      page = pages[names.indexOf(tab)] || null;
      if (!page) {
        const blank = pages.find((p, k) => !names[k].startsWith('jh:') && /^(about:blank|chrome:\/\/new-?tab)/.test(p.url()));
        page = blank || await ctx.newPage();
        if (!go) page = (host && [...pages].reverse().find(p => p.url().includes(host))) || page;  // tab lost its name (confirmation page): fall back to the host
      }
    }
    if (!page) page = (host && [...pages].reverse().find(p => p.url().includes(host))) || pages[pages.length - 1] || await ctx.newPage();
    const samePage = u => u.split(/[?#]/)[0].replace(/\/$/, '') === go.split(/[?#]/)[0].replace(/\/$/, '') && (!go.includes('token=') || u === go);
    if (go && !(process.env.JH_KEEP && samePage(page.url()))) { await page.goto(go, { waitUntil: 'domcontentloaded', timeout: 60000 }); await page.waitForTimeout(2500); navigated = true; }
    if (tab) await page.evaluate(t => { window.name = t; }, tab).catch(() => {});
    await page.bringToFront().catch(() => {});
    let result = null;
    const file = arg('script');
    if (file) {
      const src = readFileSync(file, 'utf8').trim().replace(/;\s*$/, '');
      const fn = (0, eval)('(' + src + '\n)');
      result = await fn(page);
      if (typeof result === 'string') { try { result = JSON.parse(result); } catch (e) { /* plain text log */ } }
    }
    const shot = arg('screenshot');
    if (shot) await page.screenshot({ path: shot, fullPage: true }).catch(() => {});
    if (process.argv.includes('--close')) { if (ctx.pages().length < 2) await ctx.newPage(); await page.close().catch(() => {}); }
    out({ ok: true, host: (page.url().split('/')[2] || ''), path: page.url().split('?')[0].split('/').slice(3).join('/').slice(0, 160), navigated, result });
  } catch (e) {
    out({ ok: false, error: String(e && e.message || e).slice(0, 500) });
    code = 1;
  }
  process.exit(code);  // exit without closing: the browser stays open for the next step / for the user
})();
