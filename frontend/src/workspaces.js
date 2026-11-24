import { metaFor } from './moduleMeta';
import { MODULES } from './modules';

// A "workspace" is one module as the person sees it: the launcher tile, the focused sidebar menu and the breadcrumb all read from here.
// Modules in src/modules/*/index.jsx become workspaces automatically; the older pages (Sales, Accounting, Documents, Company,
// Administration) are described below. Presentation (name, icon, purpose, order) lives in moduleMeta.js.
//
// The current workspace is derived from the URL (see workspaceForPath): no state to keep in sync, so Back, refresh and deep links work.

// `perms` on an item: shown only to users holding one of the codes. `perm` on a workspace: the whole module needs that code.
const STATIC = [
  {
    key: 'sales', label: 'Sales', basePath: '/sales', groups: [{
      key: 'sales', label: 'Sales', items: [
        { to: '/sales', label: 'Sales Dashboard', end: true, perms: ['sales.view'] },
        { to: '/sales/customers', label: 'Customers', perms: ['sales.view'] },
        { to: '/company/quotations', label: 'Quotations' },
        { to: '/sales/documents', label: 'Invoices and Notes', perms: ['sales.view'] },
        { to: '/sales/recurring', label: 'Recurring invoices', perms: ['sales.view'] },
        { to: '/sales/payments', label: 'Payments Received', perms: ['sales.view'] },
        { to: '/sales/reminders', label: 'Reminders', perms: ['sales.view'] },
        { to: '/sales/receivables', label: 'Receivables Ageing', perms: ['sales.view'] },
      ],
    }],
  },
  {
    key: 'accounting', label: 'Accounting', basePath: '/accounting', groups: [{
      key: 'accounting', label: 'Accounting', items: [
        { to: '/accounting', label: 'Accounting Dashboard', end: true, perms: ['accounting.view'] },
        { to: '/accounting/vouchers', label: 'Expenses and Income', perms: ['accounting.view'] },
        { to: '/accounting/bank', label: 'Bank and Cash', perms: ['accounting.view'] },
        { to: '/accounting/bank-reconciliation', label: 'Bank reconciliation', perms: ['accounting.view'] },
        { to: '/accounting/bank-import', label: 'Import statement', perms: ['accounting.manage'] },
        { to: '/accounting/journal', label: 'Journal', perms: ['accounting.view'] },
        { to: '/accounting/assets', label: 'Fixed Assets', perms: ['accounting.view'] },
        { to: '/accounting/budgets', label: 'Budgets', perms: ['accounting.view'] },
        { to: '/accounting/accounts', label: 'Chart of Accounts', perms: ['accounting.view'] },
        { to: '/accounting/opening-balances', label: 'Opening balances', perms: ['accounting.manage'] },
        { to: '/accounting/reports', label: 'Financial Reports', perms: ['accounting.view'] },
      ],
    }],
  },
  {
    key: 'documents', label: 'Documents', groups: [{
      key: 'documents', label: 'Documents', items: [
        { to: '/company/letters', label: 'Letters and Documents' },
        { to: '/company/minutes', label: 'Meeting Minutes' },
        { to: '/company/operations', label: 'Operations' },
        { to: '/company/letterhead-templates', label: 'Letterhead Templates' },
        { to: '/company/documents', label: 'Company Documents' },
      ],
    }],
  },
  {
    key: 'company', label: 'Company', groups: [
      {
        key: 'company', label: 'Company', items: [
          { to: '/company/overview', label: 'Company Overview' },
          { to: '/company/basic', label: 'Basic Information' },
          { to: '/company/legal', label: 'Legal and Registration' },
          { to: '/company/addresses', label: 'Addresses' },
          { to: '/company/partners', label: 'Partners and Signatories' },
          { to: '/company/contacts', label: 'Contact Directory' },
          { to: '/company/branding', label: 'Branding and Letterhead' },
        ],
      },
      {
        key: 'taxsettings', label: 'Tax and Finance Settings', items: [
          { to: '/company/tax', label: 'Tax Configuration' },
          { to: '/company/bank', label: 'Bank Accounts' },
          { to: '/company/financial-years', label: 'Financial Year Settings' },
          { to: '/company/numbering', label: 'Document Numbering' },
          { to: '/company/policies', label: 'Policies and Default Terms' },
        ],
      },
    ],
  },
  {
    key: 'admin', label: 'Administration', groups: [{
      key: 'admin', label: 'Administration', items: [
        { to: '/company/invite', label: 'Invite members', perms: ['users.invite'] },
        { to: '/company/access', label: 'Users and Access', perms: ['users.manage', 'users.view_activity'] },
        { to: '/company/history', label: 'Change History', perms: ['audit.view'] },
        { to: '/company/email', label: 'Email settings', perms: ['email.manage'] },
        { to: '/company/sent-emails', label: 'Sent emails', perms: ['email.manage'] },
      ],
    }],
  },
];

const FROM_MODULES = MODULES.map((m) => ({
  key: m.key, label: m.label, basePath: m.basePath, perm: m.perm,
  // A module may define `groups: [{ key, label, items }]` for a sidebar with several headed sections (the Guide does); otherwise `nav` is one group.
  groups: m.groups || [{ key: m.key, label: m.label, items: m.nav }],
  // Optional `crumbLabel(segment)` names the last breadcrumb for pages that are not menu items (return null for the default).
  crumbLabel: m.crumbLabel,
}));

export const WORKSPACES = [...STATIC, ...FROM_MODULES]
  .map((w) => ({ ...w, ...metaFor(w.key, w.label) }))
  .sort((a, b) => a.order - b.order || a.name.localeCompare(b.name));

export const holds = (user, perms) => !perms || perms.some((c) => (user.permissions || []).includes(c));
export const initialsOf = (text) => (text || '?').split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase();

// The workspaces this user may open, each with only the menu items the user may see; `landing` is the first of them
// (the module's dashboard for most people).
export function visibleWorkspaces(user) {
  return WORKSPACES
    .filter((w) => !w.perm || holds(user, [w.perm]))
    .map((w) => {
      const groups = w.groups.map((g) => ({ ...g, items: g.items.filter((i) => holds(user, i.perms)) })).filter((g) => g.items.length);
      const items = groups.flatMap((g) => g.items);
      return { ...w, groups, items, landing: items[0]?.to };
    })
    .filter((w) => w.items.length);
}

const under = (path, prefix) => path === prefix || path.startsWith(`${prefix}/`);
const itemPath = (to) => to.split('?')[0];

// The most specific menu item (or the module's base path) that the URL falls under.
function matchLength(ws, pathname) {
  let best = -1;
  for (const i of ws.items || []) {
    const to = itemPath(i.to);
    if (i.end ? pathname === to : under(pathname, to)) best = Math.max(best, to.length);
  }
  if (ws.basePath && under(pathname, ws.basePath)) best = Math.max(best, ws.basePath.length);
  return best;
}

// Which module the current URL belongs to, or null for pages that belong to none (company home, launcher, search).
export function workspaceForPath(pathname, list) {
  let found = null;
  let bestLen = -1;
  for (const w of list) {
    const len = matchLength(w, pathname);
    if (len > bestLen) { bestLen = len; found = w; }
  }
  return found;
}

// Breadcrumb for a URL inside a workspace: [{label, to?}] after the module name.
export function pageCrumbs(ws, pathname) {
  let item = null;
  let len = -1;
  for (const i of ws.items) {
    const to = itemPath(i.to);
    if ((i.end ? pathname === to : under(pathname, to)) && to.length > len) { item = i; len = to.length; }
  }
  if (!item) return [];
  const rest = pathname.slice(len).split('/').filter(Boolean);
  const crumbs = [{ label: item.label, to: rest.length ? item.to : undefined }];
  if (rest.length) {
    const last = rest[rest.length - 1];
    const plain = last.replace(/-/g, ' ');
    const named = ws.crumbLabel ? ws.crumbLabel(last) : null;
    const fallback = last === 'new' ? 'New' : last === 'edit' ? 'Edit' : /^[0-9a-f-]{8,}$|^\d+$/i.test(last) ? 'Details' : plain.charAt(0).toUpperCase() + plain.slice(1);
    crumbs.push({ label: named || fallback });
  }
  return crumbs;
}

// The last module and page used, remembered in this browser so the launcher can offer "Continue where you left off".
const LAST_KEY = 'erp_last_module';
export function readLast() {
  try {
    const v = JSON.parse(localStorage.getItem(LAST_KEY) || 'null');
    return v && typeof v.key === 'string' ? v : null;
  } catch { return null; }
}
export function writeLast(key, path) {
  try { localStorage.setItem(LAST_KEY, JSON.stringify({ key, path })); } catch { /* not saved */ }
}
