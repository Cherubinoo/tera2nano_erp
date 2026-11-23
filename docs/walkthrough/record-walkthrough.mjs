// Records ONE continuous process walkthrough video of the Tera2Nano ERP (see docs/walkthrough/README.md).
//
// Run: node record-walkthrough.mjs   (all settings are environment variables, listed in README.md)
// Needs: Node 18+, playwright-core (PLAYWRIGHT_CORE = path to its index.mjs, or install it next to this file), a Chromium based browser (BROWSER_EXE), the backend virtualenv python (PYTHON),
// a throwaway PostgreSQL database that is migrated and has one superuser (see README), and the project's frontend dependencies installed.
// The script starts its own backend and Vite dev server, seeds demo data through the API, then drives the real UI while recording.
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const env = process.env;
// the script lives in docs/walkthrough, two levels below the project root
const REPO = env.REPO_ROOT || path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const OUT = env.WT_OUT || path.resolve('walkthrough-out');
const API_PORT = env.API_PORT || '8037';
const WEB_PORT = env.WEB_PORT || '5237';
if (!env.DB_NAME) throw new Error('Set DB_NAME to a throwaway, migrated database (never your real one).');
const DB = { DB_NAME: env.DB_NAME, DB_HOST: env.DB_HOST || '127.0.0.1', DB_PORT: env.DB_PORT || '5432', DB_USER: env.DB_USER || 'postgres', DB_PASSWORD: env.DB_PASSWORD ?? '' };
const WIN_PY = path.join(REPO, 'backend/.venv/Scripts/python.exe');
const PYTHON = env.PYTHON || (fs.existsSync(WIN_PY) ? WIN_PY : path.join(REPO, 'backend/.venv/bin/python'));
const BROWSER_EXE = env.BROWSER_EXE || ''; // empty: use the installed Microsoft Edge through Playwright's channel
const ADMIN_USER = env.ADMIN_USER || 'e2eadmin';
const ADMIN_PASS = env.ADMIN_PASS || 'E2e-Test-Pass-77!';
const FAST = env.WT_FAST === '1'; // quick dry run: no video, tiny pauses
const SCALE = FAST ? 0.08 : Number(env.WT_PACE || 0.75); // 1 = slowest; lower is faster
const REUSE = env.WT_REUSE_SERVERS === '1'; // servers are already running

const { chromium } = await import(env.PLAYWRIGHT_CORE ? pathToFileURL(env.PLAYWRIGHT_CORE).href : 'playwright-core');
fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- servers ----------
const procs = [];
const srvEnv = { ...env, ...DB, DJANGO_DEBUG: 'true', PRIVATE_MEDIA_ROOT: path.join(OUT, 'media') };
if (!REUSE) {
  const cfg = path.join(REPO, 'frontend', `vite.walkthrough.${WEB_PORT}.config.js`);
  fs.writeFileSync(cfg, `import { defineConfig } from 'vite';\nimport react from '@vitejs/plugin-react';\nexport default defineConfig({ plugins: [react()], server: { port: ${WEB_PORT}, proxy: { '/api': { target: 'http://127.0.0.1:${API_PORT}', changeOrigin: true } } } });\n`);
  procs.push(spawn(PYTHON, ['manage.py', 'runserver', `127.0.0.1:${API_PORT}`, '--noreload'], { cwd: path.join(REPO, 'backend'), env: srvEnv, stdio: 'ignore' }));
  procs.push(spawn('node', ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', WEB_PORT, '--config', path.basename(cfg), '--strictPort'], { cwd: path.join(REPO, 'frontend'), env: srvEnv, stdio: 'ignore' }));
  process.on('exit', () => { try { fs.unlinkSync(cfg); } catch {} });
}
const stopAll = () => procs.forEach((p) => { try { p.kill(); } catch {} });
for (let i = 0; i < 120; i++) {
  try { const a = await fetch(`http://127.0.0.1:${WEB_PORT}/`); const b = await fetch(`http://127.0.0.1:${API_PORT}/api/v1/auth/me/`); if (a.ok && b.status === 401) break; } catch {}
  await sleep(500);
}
const BASE = `http://127.0.0.1:${WEB_PORT}`;

// ---------- quiet seeding through the API ----------
const api = async (method, p, body, token) => {
  const r = await fetch(`http://127.0.0.1:${API_PORT}/api/v1` + p, { method, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Token ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, body: await r.json().catch(() => null) };
};
const DEMO_PASS = 'Demo-Walk-Pass-2026!';
const tokens = {};
const tokenFor = async (u, p) => (tokens[u] ||= (await api('POST', '/auth/login/', { username: u, password: p })).body.token);
const adminTok = await tokenFor(ADMIN_USER, ADMIN_PASS);
const call = async (m, p, b, t = adminTok) => { const r = await api(m, p, b, t); if (r.status >= 300) console.log('seed', m, p, r.status, JSON.stringify(r.body).slice(0, 200)); return r.body; };
const iso = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const plus = (n) => iso(new Date(Date.now() + n * 86400000));

const chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ';
const gstin = (state, pan) => { const f = `${state}${pan}1Z`; let t = 0; [...f].forEach((ch, i) => { const v = chars.indexOf(ch) * (i % 2 === 0 ? 1 : 2); t += Math.floor(v / 36) + (v % 36); }); return f + chars[(36 - (t % 36)) % 36]; };
await call('POST', '/company/setup/start/', { legal_name: 'Tera2Nano Solutions LLP', display_name: 'Tera2Nano Solutions' });
await call('PATCH', '/company/profile/', { short_code: 'T2N', pan: 'ABCDE1234F', gst_registration_status: 'registered', gstin: gstin('29', 'ABCDE1234F') });
await call('PATCH', '/company/tax-settings/', { gst_applicable: true, gst_treatment: 'regular', tax_codes: [{ code: 'GST18', rate: 18 }], default_tax_code: 'GST18' });
await call('POST', '/company/addresses/', { label: 'Registered office', address_type: 'registered_office', line1: '12 Industrial Road', city: 'Bengaluru', state: 'Karnataka', state_code: '29', postal_code: '560001', is_primary: true });
await call('POST', '/company/setup/complete/', {});
await call('POST', '/auth/users/', { username: 'meera', first_name: 'Meera', last_name: 'Pillai', role: 'managing_partner', password: DEMO_PASS });
await call('POST', '/auth/users/', { username: 'farah', first_name: 'Farah', last_name: 'Nair', role: 'finance_manager', password: DEMO_PASS });
// the starting approval rules already exist; one extra rule so a modest purchase order also shows the approval step
await call('POST', '/approvals/rules/', { kind: 'purchase_order', name: 'Every purchase order needs a finance check', priority: 5, amount_from: '0', amount_to: '50000', steps: [{ approver_type: 'role', role: 'finance_manager', mode: 'any' }] });
const vendor = await call('POST', '/purchases/vendors/', { name: 'Bharat Office Supplies', gst_treatment: 'registered', gstin: gstin('29', 'AAAPA1234A'), payment_terms_days: 30, contact_person: 'Anil Shah' });
const partnerA = await call('POST', '/company/partners/', { full_name: 'Asha Rao', designation: 'Designated Partner', partner_type: 'partner' });
await call('POST', '/company/partners/', { full_name: 'Bala Menon', designation: 'Partner', partner_type: 'partner' });
const dept = await call('POST', '/hr/departments/', { name: 'Engineering' });
const emp1 = await call('POST', '/hr/employees/', { first_name: 'Ravi', last_name: 'Kumar', date_of_joining: '2025-04-01', department: dept.id, work_email: 'ravi@example.com' });
await call('POST', `/hr/employees/${emp1.id}/salary/`, { effective_from: '2025-04-01', basic: '25000', hra: '10000', special_allowance: '15000' });

// ---------- browser ----------
const VIDEO_DIR = path.join(OUT, 'video');
fs.rmSync(VIDEO_DIR, { recursive: true, force: true });
const browser = await chromium.launch({ ...(BROWSER_EXE ? { executablePath: BROWSER_EXE } : { channel: 'msedge' }), headless: true });
const ctx = await browser.newContext({
  viewport: { width: 1280, height: 720 }, acceptDownloads: true, colorScheme: 'light',
  ...(FAST ? {} : { recordVideo: { dir: VIDEO_DIR, size: { width: 1280, height: 720 } } }),
});

// Caption bar, chapter chip and gold cursor, injected into every page load. State lives in sessionStorage so it survives reloads.
await ctx.addInitScript(() => {
  if (window.top !== window) return;
  const KEY = '__wt';
  const get = () => { try { return JSON.parse(sessionStorage.getItem(KEY) || '{}'); } catch { return {}; } };
  const put = (patch) => { try { sessionStorage.setItem(KEY, JSON.stringify({ ...get(), ...patch })); } catch {} };
  const css = `
  #wt-root{position:fixed;inset:0;pointer-events:none;z-index:2147483647;font-family:'Segoe UI',system-ui,sans-serif}
  #wt-chip{position:absolute;top:13px;left:520px;background:#26282d;color:#f3efe6;border:1px solid #c9a24b;border-radius:999px;padding:5px 16px 5px 12px;font-size:14px;font-weight:600;letter-spacing:.2px;box-shadow:0 2px 8px rgba(0,0,0,.35);opacity:0;transition:opacity .4s;white-space:nowrap}
  #wt-chip b{color:#c9a24b;margin-right:6px}
  #wt-cap{position:absolute;left:0;right:0;bottom:0;background:#26282d;border-top:4px solid #c9a24b;color:#f6f1e6;padding:13px 36px 15px 36px;display:flex;gap:16px;align-items:center;min-height:68px;opacity:0;transition:opacity .35s;box-shadow:0 -6px 18px rgba(0,0,0,.25)}
  #wt-cap .mark{flex:none;width:12px;height:12px;border-radius:50%;background:#c9a24b;box-shadow:0 0 0 5px rgba(201,162,75,.25)}
  #wt-cap p{margin:0;font-size:22px;line-height:1.35;font-weight:500}
  #wt-cap p em{font-style:normal;color:#e0bd6a;font-weight:700}
  #wt-chip.wide{left:18px;top:10px}
  #wt-cur{position:absolute;left:0;top:0;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;background:rgba(201,162,75,.88);border:2px solid #26282d;box-shadow:0 0 0 2px rgba(255,255,255,.85),0 2px 6px rgba(0,0,0,.4);opacity:0;transition:transform .12s}
  #wt-cur.down{transform:scale(.7)}
  .wt-ripple{position:absolute;width:20px;height:20px;margin:-10px 0 0 -10px;border-radius:50%;border:3px solid #c9a24b;animation:wtr .65s ease-out forwards}
  @keyframes wtr{from{transform:scale(.6);opacity:.95}to{transform:scale(3.2);opacity:0}}`;
  const install = () => {
    if (document.getElementById('wt-root')) return;
    const root = document.createElement('div');
    root.id = 'wt-root';
    root.innerHTML = `<style>${css}</style><div id="wt-chip"></div><div id="wt-cap"><span class="mark"></span><p></p></div><div id="wt-cur"></div>`;
    document.documentElement.appendChild(root);
    const s = get();
    const cur = root.querySelector('#wt-cur');
    if (s.x != null) { cur.style.left = s.x + 'px'; cur.style.top = s.y + 'px'; cur.style.opacity = '1'; }
    const chip = root.querySelector('#wt-chip');
    setInterval(() => chip.classList.toggle('wide', /^\/(apps|login|setup)/.test(location.pathname)), 200);
    if (s.chip) { chip.innerHTML = s.chip; chip.style.opacity = '1'; }
    const cap = root.querySelector('#wt-cap');
    if (s.cap) { cap.querySelector('p').innerHTML = s.cap; cap.style.opacity = '1'; }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
  window.addEventListener('mousemove', (e) => {
    install();
    const cur = document.getElementById('wt-cur');
    cur.style.left = e.clientX + 'px'; cur.style.top = e.clientY + 'px'; cur.style.opacity = '1';
    put({ x: e.clientX, y: e.clientY });
  }, true);
  window.addEventListener('mousedown', (e) => {
    const cur = document.getElementById('wt-cur'); if (cur) cur.classList.add('down');
    const r = document.createElement('div'); r.className = 'wt-ripple'; r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px';
    document.getElementById('wt-root')?.appendChild(r); setTimeout(() => r.remove(), 800);
  }, true);
  window.addEventListener('mouseup', () => document.getElementById('wt-cur')?.classList.remove('down'), true);
  window.__wtSet = (kind, html) => {
    install();
    const root = document.getElementById('wt-root');
    if (kind === 'chip') { const c = root.querySelector('#wt-chip'); c.innerHTML = html; c.style.opacity = html ? '1' : '0'; put({ chip: html }); return; }
    const cap = root.querySelector('#wt-cap');
    if (!html) { cap.style.opacity = '0'; put({ cap: '' }); return; }
    cap.style.opacity = '0';
    setTimeout(() => { cap.querySelector('p').innerHTML = html; cap.style.opacity = '1'; }, 330);
    put({ cap: html });
  };
});

const page = await ctx.newPage();
const t0 = Date.now();
const secs = () => (Date.now() - t0) / 1000;
page.setDefaultTimeout(FAST ? 7000 : 12000);
const errors = [];
page.on('console', (m) => { if (m.type() === 'error' && !m.location().url.endsWith('favicon.ico') && !m.text().includes('status of 4')) errors.push(m.text().slice(0, 200)); });
page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 300)));
page.on('response', (r) => { if (r.status() >= 500) errors.push(`SERVER ${r.status()} ${r.request().method()} ${r.url()}`); });

// ---------- pacing helpers ----------
let readyAt = 0;
const pause = (ms) => sleep(ms * SCALE);
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
const html = (text) => esc(text).replace(/\*\*(.+?)\*\*/g, '<em>$1</em>');
// Show a caption. It waits until the previous caption has been on screen long enough, then returns at once so the action can run while it is read.
const cap = async (text) => {
  const wait = readyAt - Date.now();
  if (wait > 0) await sleep(wait);
  await page.evaluate(([h]) => window.__wtSet('cap', h), [html(text)]).catch(() => {});
  const words = text.split(/\s+/).length;
  readyAt = Date.now() + (900 + words * 330) * SCALE;
};
const settle = async () => { const wait = readyAt - Date.now(); if (wait > 0) await sleep(wait); };
const marks = [];
let chapterNo = 0;
const TOTAL = 12;
const chapter = async (title) => {
  await settle();
  chapterNo += 1;
  marks.push({ n: chapterNo, title, start_seconds: Math.round(secs() * 10) / 10 });
  await page.evaluate(([h]) => window.__wtSet('chip', h), [`<b>${chapterNo} / ${TOTAL}</b>${esc(title)}`]).catch(() => {});
  console.log(`CHAPTER ${chapterNo} ${title} @ ${secs().toFixed(1)}s`);
};
const shot = (name) => page.screenshot({ path: path.join(OUT, `${name}.png`) }).catch(() => {});
const pos = { x: 640, y: 360 };
const glide = async (x, y) => {
  const steps = FAST ? 2 : Math.max(8, Math.min(40, Math.round(Math.hypot(x - pos.x, y - pos.y) / 22)));
  await page.mouse.move(x, y, { steps });
  pos.x = x; pos.y = y;
};
const glideTo = async (loc) => {
  await loc.first().waitFor({ state: 'visible' });
  await loc.first().scrollIntoViewIfNeeded().catch(() => {});
  const b = await loc.first().boundingBox();
  if (!b) return;
  // keep the pointer clear of the caption bar
  await glide(b.x + Math.min(b.width / 2, 60 + Math.random() * 20), Math.min(b.y + b.height / 2, 600));
};
const click = async (loc) => { await glideTo(loc); await pause(260); await loc.first().click(); await pause(450); };
const type = async (loc, text, delay = 24) => {
  await glideTo(loc); await pause(200);
  await loc.first().click();
  await loc.first().fill('');
  if (FAST) await loc.first().fill(text); else await loc.first().pressSequentially(text, { delay });
  await pause(250);
};
const setDate = async (loc, v) => { await glideTo(loc); await loc.first().fill(v); await pause(250); };
const choose = async (loc, opt) => { await glideTo(loc); await pause(250); await loc.first().selectOption(opt); await pause(350); };
const optionValue = async (sel, text) => page.locator(`${sel} option`, { hasText: text }).first().getAttribute('value');
const chooseText = async (sel, text) => choose(page.locator(sel), await optionValue(sel, text));
// move inside the SPA without a reload where possible
const nav = async (p, wait = 1000) => {
  await settle();
  await page.evaluate((to) => { window.history.pushState({}, '', to); window.dispatchEvent(new PopStateEvent('popstate')); }, p);
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.getByText(/^Loading/).first().waitFor({ state: 'detached', timeout: 6000 }).catch(() => {});
  await pause(wait);
};
// scroll an element to the middle of the screen, clear of the caption bar
const focusOn = async (loc, wait = 900) => {
  await loc.first().waitFor({ state: 'visible' });
  await loc.first().evaluate((el) => el.scrollIntoView({ block: 'center', behavior: 'smooth' }));
  await pause(wait + 500);
};
const scrollPage = async (px, wait = 900) => { await page.evaluate((y) => window.scrollBy({ top: y, behavior: 'smooth' }), px); await pause(wait); };
const hardGo = async (p) => { await page.goto(BASE + p); await page.waitForLoadState('networkidle').catch(() => {}); await pause(900); };
const dlg = () => page.getByRole('dialog');
const toast = async () => pause(500);

const signIn = async (user, pass, fresh = true) => {
  if (fresh) await hardGo('/login');
  await type(page.getByLabel(/Username/), user);
  await type(page.getByLabel(/Password/), pass);
  await click(page.getByRole('button', { name: /Sign in/ }));
  await page.waitForURL((u) => !/login/.test(u.pathname));
  await page.waitForLoadState('networkidle').catch(() => {});
  await pause(900);
};
const signOut = async () => {
  await settle();
  await click(page.locator('.user-chip'));
  await click(page.getByRole('menuitem', { name: 'Sign out' }));
  await page.waitForURL(/login/);
  await pause(600);
};
// A chapter step never stops the recording: when it breaks the nearest real screen is shown and the failure is logged.
const failures = [];
const attempt = async (name, fn, fallbackPath, fallbackCaption) => {
  try { await fn(); } catch (e) {
    failures.push(`${name}: ${e.message.split('\n')[0]}`);
    console.log('STEP FAILED', name, e.message.split('\n')[0]);
    await shot(`fail-${name.replace(/\W+/g, '_').slice(0, 50)}`);
    try { await page.keyboard.press('Escape'); } catch {}
    if (fallbackPath) { await nav(fallbackPath).catch(() => hardGo(fallbackPath)); }
    if (fallbackCaption) await cap(fallbackCaption);
    await pause(1500);
  }
};
const dealCaps = {};
const ids = {};

// =====================================================================================================================
// 1  Sign in and the module launcher
// =====================================================================================================================
await hardGo('/login');
await chapter('Sign in and choose a module');
await cap('Welcome to the **Tera2Nano ERP**. This tour follows one customer from the first enquiry to the partners\' reports, using demo data only.');
await signIn(ADMIN_USER, ADMIN_PASS, false);
await nav('/apps', 1200);
await cap('After sign-in you **choose a module**. The tiles run in process order: CRM, Sales, Purchases, Accounting, GST & Tax, Projects, People, Partners ...');
for (const name of ['CRM', 'Sales', 'Purchases', 'Accounting', 'GST & Tax', 'Projects', 'People (HR)', 'Partners', 'Reports']) {
  const t = page.locator('a, button, [role=link]').filter({ hasText: new RegExp(`^\\s*${name.replace(/[()&]/g, '\\$&')}`) }).first();
  await glideTo(t).catch(() => {});
  await pause(280);
}
await cap('Everything is connected: a won deal becomes a customer, an invoice becomes a journal entry, and the journal feeds GST, projects and reports.');
await shot('ch01-launcher');
await settle();

// =====================================================================================================================
// 2  CRM
// =====================================================================================================================
await chapter('CRM: lead, deal and customer');
await nav('/crm/leads');
await cap('**CRM** starts with a lead: a possible customer. We record who they are, where they came from and what they might buy.');
await attempt('crm-create-lead', async () => {
  await click(page.getByRole('button', { name: /New lead/ }));
  await dlg().waitFor();
  await type(dlg().getByLabel('Company'), 'Sunrise Dairy Pvt Ltd');
  await type(dlg().getByLabel('Contact person'), 'Meena Iyer');
  await dlg().getByLabel('Email').fill('meena@sunrise-dairy.example').catch(() => {});
  await dlg().getByLabel(/Estimated value/).fill('590000').catch(() => {});
  await pause(500);
  await click(dlg().getByRole('button', { name: /Save|Create/ }).first());
  await page.getByText('Sunrise Dairy Pvt Ltd').first().waitFor();
  await pause(900);
}, '/crm/leads', 'The lead form is shown here; the new lead appears in the list.');
await cap('The system scores the lead from 0 to 100 and checks for duplicates. A qualified lead is **converted** into an account, a contact and a deal.');
await attempt('crm-convert', async () => {
  await click(page.getByText('Sunrise Dairy Pvt Ltd').first());
  await click(page.getByRole('button', { name: /Convert/ }).first());
  await dlg().waitFor();
  await pause(900);
  await click(dlg().getByRole('button', { name: /Convert/ }).last());
  await page.getByText(/Created/).first().waitFor();
  await pause(1200);
}, '/crm/leads');
await shot('ch02-convert');
await nav('/crm/deals');
await cap('The deal now sits on the **pipeline board**. Moving it across the stages changes its probability and the sales forecast.');
await attempt('crm-board', async () => {
  const card = page.locator('.crm-card', { hasText: 'Sunrise Dairy' }).first();
  await card.waitFor();
  for (const stage of ['Needs Analysis', 'Proposal/Quotation', 'Negotiation']) {
    const c = page.locator('.crm-card', { hasText: 'Sunrise Dairy' }).first();
    await click(c.getByRole('button', { name: /Move .* to another stage/ }));
    await click(page.getByRole('menuitem', { name: new RegExp(stage) }));
    await page.locator('.crm-col', { hasText: stage }).getByText('Sunrise Dairy').first().waitFor();
    await pause(900);
  }
}, '/crm/deals');
await shot('ch02-board');
await cap('The customer agrees, so we mark the deal **Won**. That creates a numbered customer in Sales, with no re-typing.');
await attempt('crm-won', async () => {
  const c = page.locator('.crm-card', { hasText: 'Sunrise Dairy' }).first();
  await click(c.getByRole('button', { name: /Move .* to another stage/ }));
  await click(page.getByRole('menuitem', { name: /Won/ }));
  await dlg().getByText(/is won for/).waitFor();
  await pause(2800);
  await shot('ch02-won');
  await click(dlg().getByRole('button', { name: 'Open deal' }));
  await page.getByRole('heading', { name: /Sunrise Dairy Pvt Ltd - opportunity/ }).waitFor();
  ids.dealUrl = page.url().replace(BASE, '');
  await pause(1800);
  await shot('ch02-deal');
}, '/crm/deals');
await nav('/sales/customers');
await cap('Sales now lists **Sunrise Dairy** as a customer, created from the won deal. Next, the quotation.');
await shot('ch02-customer');
await pause(1800);
await settle();

// =====================================================================================================================
// 3  Sales: quotation, approval, invoice, payment
// =====================================================================================================================
await chapter('Sales: quotation, approval, invoice, payment');
await cap('From the deal we create a **quotation**. The customer, contact and deal reference are filled in from CRM.');
await attempt('sales-quote-form', async () => {
  if (ids.dealUrl) await nav(ids.dealUrl); else await nav('/company/quotations');
  await click(page.getByRole('link', { name: 'Create quotation' }).first());
  await page.locator('#q-name').waitFor();
  await pause(1200);
  await type(page.locator('#q-subject'), 'ERP rollout for the dairy plant');
  await type(page.locator('#qi-0-description'), 'ERP implementation and training');
  await type(page.locator('#qi-0-qty'), '1');
  await type(page.locator('#qi-0-rate'), '500000');
  await page.getByText('Total').first().waitFor();
  await pause(900);
  await shot('ch03-quote-form');
}, '/company/quotations', 'The quotation form is shown here.');
await cap('This quotation is **5,90,000 with GST**. That is above the 5,00,000 approval rule, so it will wait for the Managing Partner and is not numbered until approved.');
await attempt('sales-quote-submit', async () => {
  await click(page.getByRole('button', { name: 'Issue quotation' }));
  await dlg().waitFor();
  await pause(1500);
  await click(dlg().getByRole('button', { name: 'Issue quotation' }));
  await page.locator('#q-held').waitFor();
  await pause(2500);
  await shot('ch03-quote-held');
  await click(page.getByRole('link', { name: 'Follow it here' }));
  await page.waitForURL(/\/company\/quotations\/[0-9a-f-]{36}/, { timeout: 15000 });
  ids.quoteUrl = page.url().replace(BASE, '');
  await pause(1500);
  await shot('ch03-quote-pending');
}, '/company/quotations');
await cap('Now the approver signs in. We sign out and sign in as **Meera Pillai, Managing Partner**.');
await signOut();
await signIn('meera', DEMO_PASS, false);
await nav('/approvals');
await cap('The request is in her **Approvals inbox**. Approvals is shared by every module: quotations, purchase orders, bills, leave, payroll and more.');
await shot('ch03-inbox');
await attempt('sales-approve', async () => {
  await click(page.locator('tr', { hasText: /Quotation/ }).getByRole('link', { name: /APR/ }).first());
  await page.getByRole('heading', { name: /APR/ }).waitFor();
  await pause(1500);
  await click(page.getByRole('button', { name: 'Approve', exact: true }));
  await dlg().waitFor();
  const box = dlg().locator('textarea, input[type=text], input:not([type])').first();
  if (await box.count()) await type(box, 'Scope and price agreed with the customer');
  await click(dlg().getByRole('button', { name: /^(Approve|Continue|Confirm|Save)$/ }).last());
  await page.getByText(/Approved/).first().waitFor();
  await pause(1200);
  await shot('ch03-approved');
}, '/approvals');
await signOut();
await signIn(ADMIN_USER, ADMIN_PASS, false);
await cap('Approved. The quotation module now issues it with its own number and a letterhead PDF. From it we **create the invoice**.');
await attempt('sales-invoice', async () => {
  await nav(ids.quoteUrl);
  await page.getByRole('heading').first().waitFor();
  await pause(1500);
  await shot('ch03-quote-issued');
  await click(page.getByRole('link', { name: 'Create invoice' }));
  await page.locator('#d-customer').waitFor();
  await pause(1500);
  await shot('ch03-invoice-form');
  await click(page.getByRole('button', { name: 'Save and issue' }));
  await click(dlg().getByRole('button', { name: 'Issue', exact: true }));
  await page.waitForURL(/\/sales\/documents\/[0-9a-f-]{36}$/, { timeout: 15000 });
  ids.invoiceUrl = page.url().replace(BASE, '');
  await pause(1500);
}, '/sales/documents');
await cap('The invoice is issued and numbered. **GST is split by place of supply**: CGST and SGST inside the state, IGST between states.');
await attempt('sales-gst', async () => {
  await focusOn(page.getByText('CGST @ 9%').first(), 1800);
  await shot('ch03-invoice');
}, null);
await cap('Issuing the invoice posted a journal entry: debit Accounts receivable, credit Sales and GST payable. Now the customer pays.');
await attempt('sales-payment', async () => {
  await page.evaluate(() => window.scrollTo(0, 0));
  await click(page.getByRole('button', { name: 'Record payment' }));
  await dlg().waitFor();
  const amt = dlg().getByLabel('Amount received');
  await pause(700);
  await type(dlg().getByLabel('Reference (cheque no., UTR ...)'), 'UTR-DEMO-4471');
  await shot('ch03-payment-dialog');
  await click(dlg().getByRole('button', { name: 'Record payment' }));
  await page.locator('h1').getByText(/Paid/).first().waitFor();
  await pause(1500);
  await shot('ch03-paid');
}, ids.invoiceUrl, 'The payment dialog is shown on the invoice page.');
await cap('The receipt is recorded against the invoice, so it shows **Paid**. A second journal entry moved the money from receivables to the bank.');
await settle();

// =====================================================================================================================
// 4  Accounting
// =====================================================================================================================
await chapter('Accounting: journal, trial balance, profit and loss');
await nav('/accounting/journal');
await cap('**Accounting** holds one set of books. Every module posts here by itself, so nobody re-enters the invoice or the receipt.');
await shot('ch04-journal');
await pause(1200);
await attempt('acct-journal-detail', async () => {
  await page.locator('tbody tr').first().waitFor();
  await pause(900);
  const inv = page.locator('tbody tr', { hasText: /INV/ }).locator('a').first();
  await click((await inv.count()) ? inv : page.locator('tbody a').first());
  await page.getByText('Total', { exact: true }).first().waitFor();
  await pause(2500);
  await shot('ch04-journal-detail');
}, '/accounting/journal');
await cap('Each entry balances: total debits equal total credits, and every line names the account, the customer and any project.');
await nav('/accounting/reports/trial-balance').catch(() => {});
await cap('The **trial balance** adds up every account. If debits and credits agree, the books are in balance.');
await shot('ch04-tb');
await pause(1200);
await nav('/accounting/reports/profit-loss');
await cap('The **profit and loss** is read from the same lines: sales on one side, costs on the other.');
await shot('ch04-pl');
await settle();

// =====================================================================================================================
// 5  GST & Tax
// =====================================================================================================================
await chapter('GST and Tax');
await nav('/tax/gst');
await cap('**GST & Tax** reads the books. The summary shows the GST collected on sales, the credit available on purchases and what is payable.');
await shot('ch05-gst');
await pause(1500);
await nav('/tax/sales-register');
await cap('The **sales register** is built from issued invoices: customer, place of supply, taxable value and the CGST, SGST or IGST. Our invoice is here.');
await shot('ch05-register');
await settle();

// =====================================================================================================================
// 6  Purchases
// =====================================================================================================================
await chapter('Purchases: order, approval, bill, payment');
await nav('/purchases/vendors');
await cap('**Purchases** works the same way in reverse. The vendor, Bharat Office Supplies, was added beforehand. We raise a **purchase order**.');
await attempt('po-create', async () => {
  await nav('/purchases/orders/new');
  await chooseText('#vendor', 'Bharat');
  await type(page.locator('#po-0-description'), 'Office chairs');
  await type(page.locator('#po-0-quantity'), '20');
  await type(page.locator('#po-0-rate'), '1500');
  await page.getByText(/CGST @ 9%/).waitFor();
  await pause(900);
  await shot('ch06-po-form');
}, '/purchases/orders');
await cap('GST splits live while we type. Our approval rule says every order needs a finance check, so we **send it for approval** instead of issuing it.');
await attempt('po-submit', async () => {
  const labels = await page.getByRole('button').allInnerTexts();
  console.log('po buttons:', labels.join(' | '));
  await click(page.getByRole('button', { name: 'Save as draft' }));
  await page.waitForURL(/\/purchases\/orders\/[0-9a-f-]{36}$/, { timeout: 15000 });
  ids.poUrl = page.url().replace(BASE, '');
  await click(page.getByRole('button', { name: 'Send for approval' }));
  await page.getByText('Sent for approval.').first().waitFor();
  await pause(1500);
  await shot('ch06-po-pending');
}, '/purchases/orders');
await signOut();
await signIn('farah', DEMO_PASS, false);
await nav('/approvals');
await cap('**Farah Nair, Finance Manager**, sees the order in her inbox and approves it.');
await attempt('po-approve', async () => {
  await click(page.locator('tr', { hasText: /Purchase order/ }).getByRole('link', { name: /APR/ }).first());
  await page.getByRole('heading', { name: /APR/ }).waitFor();
  await pause(1200);
  await click(page.getByRole('button', { name: 'Approve', exact: true }));
  await dlg().waitFor();
  const box = dlg().locator('textarea, input[type=text], input:not([type])').first();
  if (await box.count()) await type(box, 'Price matches the vendor quotation');
  await click(dlg().getByRole('button', { name: /^(Approve|Continue|Confirm|Save)$/ }).last());
  await page.getByText(/Approved/).first().waitFor();
  await pause(1000);
}, '/approvals');
await signOut();
await signIn(ADMIN_USER, ADMIN_PASS, false);
await attempt('po-bill', async () => {
  await nav(ids.poUrl);
  await page.getByRole('heading').first().waitFor();
  await cap('The order is approved. When the goods arrive we convert it to a **vendor bill**. The bill records whether the GST is eligible **input tax credit**.');
  await shot('ch06-po-approved');
  await click(page.getByRole('button', { name: 'Issue', exact: true }));
  if (await dlg().count()) await click(dlg().getByRole('button', { name: /^(Issue|Issue order|Confirm)$/ }).last());
  await page.getByText(/Issued|Sent to vendor/).first().waitFor();
  await pause(1500);
  console.log('po detail buttons:', (await page.getByRole('button').allInnerTexts()).join(' | '));
  await shot('ch06-po-issued');
  await click(page.getByRole('button', { name: 'Convert to bill' }));
  await page.waitForURL(/\/purchases\/bills\/[0-9a-f-]{36}$/, { timeout: 15000 });
  await pause(1200);
  await click(page.getByRole('button', { name: 'Record bill' }));
  await page.getByRole('heading', { name: /BILL/ }).waitFor();
  ids.billUrl = page.url().replace(BASE, '');
  await pause(1500);
  await shot('ch06-bill');
}, '/purchases/bills');
await cap('Recording the bill posts the expense and the input GST against Accounts payable. Then we **pay the vendor**.');
await attempt('po-pay', async () => {
  await click(page.getByRole('link', { name: 'Record payment' }));
  await dlg().waitFor();
  await pause(1000);
  await type(dlg().getByLabel('Reference (UTR, cheque no.)'), 'UTR-DEMO-8802');
  await click(dlg().getByRole('button', { name: 'Record payment' }));
  await page.waitForURL(/\/purchases\/payments/);
  await pause(1500);
  await shot('ch06-payment');
}, '/purchases/payments');
await cap('The payment is applied to the bill and posts Accounts payable against the bank. Purchases are now in the books too.');
await settle();

// =====================================================================================================================
// 7  Projects
// =====================================================================================================================
await chapter('Projects: tasks, time, profitability');
await nav('/projects/projects');
await cap('**Projects** track delivery and profit. A project has a client (a Sales customer), a team and a budget.');
await attempt('proj-create', async () => {
  await click(page.getByRole('button', { name: /New project/ }).first());
  await type(page.locator('#pf-name'), 'Sunrise Dairy ERP rollout');
  await chooseText('#pf-client', 'Sunrise');
  await choose(page.locator('#pf-status'), 'active');
  await setDate(page.locator('#pf-start'), plus(-7));
  await type(page.locator('#pf-value'), '500000');
  await click(page.getByRole('button', { name: 'Create project' }));
  await page.waitForURL(/\/projects\/projects\/[0-9a-f-]{36}/, { timeout: 12000 }).catch(() => {});
  await pause(1200);
  if (!/\/projects\/projects\/[0-9a-f-]{36}/.test(page.url())) await click(page.getByText('Sunrise Dairy ERP rollout').first());
  await page.waitForURL(/\/projects\/projects\/[0-9a-f-]{36}/);
  ids.projectId = page.url().match(/projects\/([0-9a-f-]{36})/)[1];
  await pause(1000);
  await shot('ch07-project');
}, '/projects/projects');
await cap('Work is split into **tasks** that move across a Kanban board: To do, In progress, In review, Done.');
await attempt('proj-task', async () => {
  await nav(`/projects/projects/${ids.projectId}?tab=work`);
  await click(page.getByRole('button', { name: 'Add task' }));
  await type(page.locator('#tk-title'), 'Configure the chart of accounts');
  await type(page.locator('#tk-est'), '8');
  await click(page.getByRole('button', { name: 'Save task' }));
  await page.getByText('Configure the chart of accounts').first().waitFor();
  await pause(800);
  await click(page.getByRole('button', { name: /Move Configure the chart of accounts on to In progress/ }));
  await page.getByRole('group', { name: /In progress, 1 task/ }).waitFor();
  await focusOn(page.getByRole('group', { name: /In progress, 1 task/ }), 1500);
  await shot('ch07-kanban');
}, null);
await cap('People log hours against the project. Hours at the team member\'s rate give the billable value of unbilled work.');
await attempt('proj-time', async () => {
  await nav(`/projects/projects/${ids.projectId}?tab=timesheets`);
  await click(page.getByRole('button', { name: 'Log time' }).last());
  await type(page.locator('#ts-hours'), '6.5');
  await type(page.locator('#ts-rate'), '1000');
  await click(page.getByRole('button', { name: 'Save', exact: true }));
  await page.getByText('6.5').first().waitFor();
  await pause(1200);
  await shot('ch07-time');
}, null);
await cap('A milestone with an amount can raise a **draft invoice** for the client, already tagged to this project.');
await attempt('proj-milestone-invoice', async () => {
  await nav(`/projects/projects/${ids.projectId}?tab=work`);
  await click(page.getByRole('button', { name: 'Add milestone' }));
  await type(page.locator('#ms-name'), 'Phase 1 delivered');
  await setDate(page.locator('#ms-due'), plus(-1));
  await type(page.locator('#ms-amount'), '150000');
  await click(page.getByRole('button', { name: 'Save', exact: true }));
  await page.getByText('Phase 1 delivered').first().waitFor();
  await pause(700);
  await click(page.getByRole('button', { name: 'Mark done' }).first());
  await page.getByText('Marked done.').first().waitFor();
  await pause(900);
  await click(page.getByRole('button', { name: 'Create invoice' }).first());
  await page.waitForURL(/\/sales\/documents\/[0-9a-f-]{36}/, { timeout: 12000 });
  await pause(1500);
  await shot('ch07-draft-invoice');
  await pause(1500);
  await click(page.getByRole('button', { name: 'Save and issue' }));
  await click(dlg().getByRole('button', { name: 'Issue', exact: true }));
  await page.waitForURL(/\/sales\/documents\/[0-9a-f-]{36}$/, { timeout: 15000 });
  await page.locator('h1').getByText(/Unpaid/).first().waitFor();
  await pause(1200);
}, null);
await attempt('proj-profit', async () => {
  await nav('/projects/profitability');
  await cap('**Project profitability** is never typed in. It is read from journal lines tagged with the project, so profit always agrees with the books.');
  await page.getByText('Sunrise Dairy ERP rollout').first().waitFor();
  await pause(1500);
  await shot('ch07-profit');
}, '/projects/profitability');
await settle();

// =====================================================================================================================
// 8  People
// =====================================================================================================================
await chapter('People: employee and payroll');
await nav('/hr/employees');
await cap('**People (HR)** keeps employees, attendance, leave and payroll. Bank and PAN details are masked in lists.');
await attempt('hr-employee', async () => {
  await click(page.getByRole('button', { name: 'New employee' }));
  await dlg().waitFor();
  await type(dlg().getByLabel('First name'), 'Chitra');
  await type(dlg().getByLabel('Last name'), 'Nair');
  await setDate(dlg().getByLabel('Date of joining'), plus(-30));
  await choose(dlg().getByLabel('Department'), { label: 'Engineering' });
  await click(dlg().getByRole('button', { name: 'Add employee' }));
  await page.waitForURL(/\/hr\/employees\/[0-9a-f-]{36}/);
  await pause(1200);
  await shot('ch08-employee');
}, '/hr/employees');
await attempt('hr-salary', async () => {
  await click(page.getByRole('tab', { name: 'Job & Salary' }));
  await click(page.getByRole('button', { name: 'New structure' }));
  await dlg().waitFor();
  await setDate(dlg().getByLabel('Effective from'), plus(-30));
  await type(dlg().getByLabel('Basic (monthly)'), '22000');
  await type(dlg().getByLabel('House rent allowance'), '8800');
  await type(dlg().getByLabel('Special allowance'), '9200');
  await click(dlg().getByRole('button', { name: 'Save structure' }));
  await page.getByText('In force').first().waitFor();
  await pause(1200);
}, null);
await cap('A payroll run generates payslips with PF, ESI and professional tax. It is locked, approved, posted to the books and then paid.');
await attempt('hr-payroll', async () => {
  await nav('/hr/payroll');
  await click(page.getByRole('button', { name: 'New payroll run' }));
  await dlg().waitFor();
  await dlg().getByLabel('Month').fill(iso().slice(0, 7));
  await pause(600);
  await click(dlg().getByRole('button', { name: 'Generate payslips' }));
  await page.waitForURL(/\/hr\/payroll\/runs\/[0-9a-f-]{36}/);
  await page.getByRole('heading', { name: /Payroll for/ }).waitFor();
  await pause(1800);
  await shot('ch08-payroll');
}, '/hr/payroll');
await cap('This is the **draft run**: nothing reaches the books until the run is locked, approved and posted.');
await settle();

// =====================================================================================================================
// 9  Partners
// =====================================================================================================================
await chapter('Partners: capital and statements');
await nav('/partners/transactions');
await cap('**Partners** keeps each partner\'s capital account. Capital introduced, drawings, interest and loans all post to the books.');
await attempt('partner-tx', async () => {
  await click(page.getByRole('button', { name: 'Record transaction' }).first());
  await dlg().waitFor();
  await choose(dlg().locator('#tx-partner'), { label: 'Asha Rao' });
  const kinds = await dlg().locator('#tx-kind option').allInnerTexts();
  console.log('kinds:', kinds.join(' | '));
  const capKind = kinds.find((k) => /capital/i.test(k) && /introduc|contribut/i.test(k)) || kinds.find((k) => /capital/i.test(k));
  await choose(dlg().locator('#tx-kind'), { label: capKind });
  await type(dlg().locator('#tx-amount'), '400000');
  await pause(800);
  await click(dlg().getByRole('button', { name: 'Record transaction' }));
  await page.getByText(/recorded/).first().waitFor();
  await pause(1500);
  await shot('ch09-tx');
}, '/partners/transactions');
await cap('Capital of 4,00,000 is recorded and posts a journal entry tagged to the partner. The **capital statement** shows opening, movements and closing balances.');
await attempt('partner-statement', async () => {
  await nav(`/partners/statements?partner=${partnerA.id}`);
  await page.getByRole('region', { name: 'Capital account statement' }).waitFor();
  await focusOn(page.locator('tr', { hasText: 'Closing balance' }), 2500);
  await shot('ch09-statement');
}, '/partners/statements');
await settle();

// =====================================================================================================================
// 10  Compliance and Workplace
// =====================================================================================================================
await chapter('Compliance and Workplace');
await nav('/compliance/calendar');
await cap('**Compliance** keeps the due dates for LLP forms, income tax, GST, TDS, PF and ESI in one calendar. Nothing is filed online; it records and reminds.');
await pause(1500);
await attempt('compliance-agenda', async () => {
  await click(page.getByRole('tab', { name: 'Agenda' }));
  await page.getByText(/GSTR-3B/).first().waitFor();
  await cap('The GST due dates shown here are read from the **Tax calendar** in GST & Tax, so the two never disagree.');
  await pause(1500);
  await shot('ch10-calendar');
}, null);
await attempt('wp-meeting', async () => {
  await nav('/workplace/meetings');
  await cap('**Workplace** handles meetings. We schedule a partners\' meeting, then record a decision and an **action item** that becomes a task.');
  await click(page.getByRole('button', { name: 'Schedule meeting' }));
  await dlg().waitFor();
  await type(dlg().locator('#mt-title'), 'Quarterly planning review');
  await setDate(dlg().locator('#mt-date'), plus(2));
  await dlg().locator('#mt-start').fill('10:00');
  await dlg().locator('#mt-end').fill('11:00');
  await click(dlg().getByRole('button', { name: 'Schedule meeting' }));
  await page.waitForURL(/\/workplace\/meetings\/[0-9a-f-]{36}/);
  await pause(1200);
  await type(page.locator('#dc-text'), 'Start the dairy ERP rollout in December');
  await click(page.getByRole('button', { name: 'Add', exact: true }));
  await pause(900);
  await click(page.getByRole('button', { name: 'Add action item' }));
  await dlg().waitFor();
  await type(dlg().locator('#tk-title'), 'Send the rollout plan to the customer');
  await setDate(dlg().locator('#tk-due'), plus(7));
  await click(dlg().getByRole('button', { name: 'Create task' }));
  await pause(1500);
  await shot('ch10-meeting');
}, '/workplace/meetings');
await attempt('wp-circular', async () => {
  await nav('/workplace/circulars');
  await cap('A **circular** tells everyone about a policy. It is issued as a numbered PDF, and each person acknowledges that they have read it.');
  await click(page.getByRole('button', { name: 'New circular' }));
  await dlg().waitFor();
  await type(dlg().locator('#ci-subject'), 'Updated leave and attendance policy');
  await type(dlg().locator('#ci-body'), 'Please read the revised policy. Leave is applied in advance and attendance is marked daily.', 18);
  await click(dlg().getByRole('button', { name: 'Save draft' }));
  await click(page.getByRole('button', { name: 'Issue circular' }));
  await click(dlg().getByRole('button', { name: 'Issue circular' }));
  await pause(2000);
  await shot('ch10-circular');
}, '/workplace/circulars');
await settle();

// =====================================================================================================================
// 11  Reports
// =====================================================================================================================
await chapter('Reports: dashboard and export');
await nav('/reports/dashboard', 1800);
await cap('**Reports** reads every module. The executive dashboard shows revenue, receivables, payables, cash and project profit at a glance.');
await shot('ch11-dashboard');
await pause(2500);
await scrollPage(420, 2500);
await attempt('reports-csv', async () => {
  await nav('/reports/revenue', 1500);
  await cap('Each report has a financial-year filter and **CSV export**, so the figures can go into a spreadsheet.');
  await shot('ch11-revenue');
  const [dl] = await Promise.all([page.waitForEvent('download', { timeout: 10000 }), click(page.getByRole('button', { name: /CSV/ }).first())]);
  console.log('downloaded', dl.suggestedFilename());
  await pause(1000);
}, '/reports/revenue');
await settle();

// =====================================================================================================================
// 12  Guide
// =====================================================================================================================
await chapter('The in-app Guide');
await nav('/guide', 1500);
await cap('The **Guide** explains every module inside the app. It shows the whole process as a flow, lane by lane.');
await shot('ch12-guide');
await attempt('guide-play', async () => {
  await click(page.getByRole('button', { name: /Play the process/ }));
  await pause(2500);
  await cap('**Play the process** walks each step in order and says which screen it uses and what data moves.');
  await pause(9000);
  await shot('ch12-play');
}, '/guide');
await attempt('guide-map', async () => {
  await nav('/guide/integration', 1500);
  await page.evaluate(() => window.scrollTo(0, 0));
  await cap('The **integration map** shows what flows between modules, and which ones post to the books.');
  await shot('ch12-map');
  await pause(2500);
}, '/guide/integration');
await cap('In short: a lead becomes a customer, a quotation becomes an invoice, every posting lands in one set of books, and GST, projects, partners and reports all read from it.');
await settle();
await pause(2500);
await page.evaluate(() => window.__wtSet('cap', '')).catch(() => {});
await pause(900);

// ---------- finish ----------
const totalSeconds = secs();
const videoFile = page.video();
await ctx.close();
let videoPath = null;
if (videoFile) {
  const p = await videoFile.path();
  videoPath = path.join(OUT, 't2n-erp-process-walkthrough.webm');
  fs.copyFileSync(p, videoPath);
}
await browser.close();
stopAll();
if (videoPath && env.FFMPEG) {
  // optional: shrink the recording (the recorder's own bitrate is high); needs an ffmpeg that can encode VP9
  const small = videoPath.replace(/\.webm$/, '.small.webm');
  const r = spawnSync(env.FFMPEG, ['-y', '-v', 'error', '-i', videoPath, '-c:v', 'libvpx-vp9', '-b:v', env.WT_BITRATE || '600k', '-crf', '36', '-deadline', 'good', '-cpu-used', '4', '-row-mt', '1', '-an', small]);
  if (r.status === 0 && fs.existsSync(small) && fs.statSync(small).size > 0) { fs.renameSync(small, videoPath); console.log('re-encoded to', (fs.statSync(videoPath).size / 1e6).toFixed(1), 'MB'); } else console.log('re-encode failed; keeping the original recording');
}
fs.writeFileSync(path.join(OUT, 'chapters.json'), JSON.stringify({ total_seconds: Math.round(totalSeconds * 10) / 10, chapters: marks, failures, page_errors: errors }, null, 2));
console.log('video:', videoPath, 'total', totalSeconds.toFixed(1), 's');
console.log('failures:', failures.length ? '\n  ' + failures.join('\n  ') : 'none');
console.log('page errors:', errors.length ? '\n  ' + errors.join('\n  ') : 'none');
process.exit(0);
