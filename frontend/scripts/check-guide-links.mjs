// Checks that the Guide's data is consistent with the app:
//  - every deep link in guideData.js points at a route that exists (App.jsx and src/modules/*/index.jsx);
//  - flows and steps only name modules that exist in moduleMeta.js, and every module has a guide entry.
// Run: node scripts/check-guide-links.mjs   (exit code 1 on any problem). The browser test runs it too.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');

// ---- routes -------------------------------------------------------------------------------------------------------------------
const routes = new Set();
const add = (...parts) => routes.add('/' + parts.join('/').split('/').filter(Boolean).join('/'));

// App.jsx: <Route path="/x" ...> opens a section; the <Route path="child" ...> lines that follow belong to it.
{
  const text = fs.readFileSync(path.join(src, 'App.jsx'), 'utf8');
  let base = '';
  for (const line of text.split('\n')) {
    const m = line.match(/<Route\s+(?:index\b|path="([^"]*)")/);
    if (!m) continue;
    if (/<Route\s+index\b/.test(line)) { if (base) add(base); continue; }
    const p = m[1];
    if (p.startsWith('/')) { base = p; add(p); } else if (p === '*') continue; else add(base, p);
  }
}
// modules: basePath plus the `routes` of each module definition.
const moduleDirs = fs.readdirSync(path.join(src, 'modules')).filter((d) => fs.existsSync(path.join(src, 'modules', d, 'index.jsx')));
for (const d of moduleDirs) {
  const text = fs.readFileSync(path.join(src, 'modules', d, 'index.jsx'), 'utf8');
  const base = (text.match(/basePath:\s*'([^']+)'/) || [])[1];
  if (!base) continue;
  add(base);
  const routesBlock = text.slice(text.indexOf('routes:'));
  for (const m of routesBlock.matchAll(/\{\s*path:\s*'([^']+)'/g)) add(base, m[1]);
}

const matches = (link) => {
  const segs = link.split('/').filter(Boolean);
  return [...routes].some((r) => {
    const rs = r.split('/').filter(Boolean);
    return rs.length === segs.length && rs.every((s, i) => s.startsWith(':') || s === segs[i]);
  });
};

// ---- data ---------------------------------------------------------------------------------------------------------------------
const data = await import(pathToFileURL(path.join(src, 'modules', 'guide', 'guideData.js')).href);
const { MODULE_META } = await import(pathToFileURL(path.join(src, 'moduleMeta.js')).href);
const problems = [];

for (const l of data.allLinks()) {
  const clean = l.to.split(/[?#]/)[0];
  if (!clean.startsWith('/')) problems.push(`${l.where}: "${l.to}" is not an absolute path`);
  else if (!matches(clean)) problems.push(`${l.where}: "${l.to}" matches no route`);
}
for (const f of data.FLOWS) {
  for (const k of [f.from, f.to]) if (!MODULE_META[k]) problems.push(`flow ${f.id}: unknown module "${k}"`);
}
for (const s of data.FLOW_STEPS) if (!MODULE_META[s.module]) problems.push(`flow step ${s.id}: unknown module "${s.module}"`);
for (const lane of data.FLOW_LANES) {
  if (lane.end && !matches(lane.end.to.split(/[?#]/)[0])) problems.push(`lane ${lane.id}: end link "${lane.end.to}" matches no route`);
  if (!data.FLOW_STEPS.some((s) => s.lane === lane.id)) problems.push(`lane ${lane.id} has no steps`);
}
for (const s of data.FLOW_STEPS) if (!data.FLOW_LANES.some((l) => l.id === s.lane)) problems.push(`flow step ${s.id}: unknown lane "${s.lane}"`);
for (const key of Object.keys(MODULE_META)) {
  if (key !== 'guide' && !data.guideFor(key)) problems.push(`module "${key}" has no guide entry`);
}
for (const key of data.RESERVED_KEYS) if (data.guideFor(key)) problems.push(`"${key}" is reserved and cannot be a module guide key`);

// operating manual: every task route must exist too
const { OPERATE_ORDER } = await import(pathToFileURL(path.join(src, 'modules', 'guide', 'operateMeta.js')).href);
const opDir = path.join(src, 'modules', 'guide', 'operate');
let opRoutes = 0;
for (const f of fs.readdirSync(opDir).filter((x) => x.endsWith('.json'))) {
  const c = JSON.parse(fs.readFileSync(path.join(opDir, f), 'utf8'));
  if (!OPERATE_ORDER.includes(c.key)) problems.push(`operate/${f}: key "${c.key}" is not listed in operateMeta.js`);
  for (const t of c.tasks || []) {
    if (!t.route) continue;
    opRoutes += 1;
    const clean = t.route.split(/[?#]/)[0];
    if (!clean.startsWith('/')) problems.push(`operate/${f} "${t.title}": "${t.route}" is not an absolute path`);
    else if (!matches(clean)) problems.push(`operate/${f} "${t.title}": "${t.route}" matches no route`);
  }
}

const links = data.allLinks().length;
if (problems.length) {
  console.error(`${problems.length} problem(s):\n- ${problems.join('\n- ')}`);
  process.exit(1);
}
console.log(`guide check OK: ${links} links and ${opRoutes} manual task routes match ${routes.size} routes; ${data.FLOWS.length} flows, ${data.FLOW_STEPS.length} steps, ${data.MODULE_GUIDES.length} module guides.`);
