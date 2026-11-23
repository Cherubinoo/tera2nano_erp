// One place for how each module presents itself on the launcher page (/apps), in the module switcher and in the sidebar header:
// its display name, line icon (a name from icons.jsx), one-line purpose and its position in the process order.
// The user guide reuses this file, so keep the purposes plain and factual.
//
// To add a module: create src/modules/<name>/index.jsx (see modules.js), then add one entry below keyed by that module's `key`.
// A module without an entry still appears (last, before Guide) with its own label, a generic icon and "Open <label>".
// `order` only positions the tile; leave gaps so a new entry can slot in between.
export const MODULE_META = {
  crm: { name: 'CRM', icon: 'crm', order: 10, purpose: 'Leads, deals, accounts and follow-ups' },
  sales: { name: 'Sales', icon: 'sales', order: 20, purpose: 'Quotations, invoices and payments received' },
  purchases: { name: 'Purchases', icon: 'purchases', order: 30, purpose: 'Vendors, purchase orders and bills' },
  accounting: { name: 'Accounting', icon: 'accounting', order: 40, purpose: 'Books, bank, journal and financial reports' },
  'gst-tax': { name: 'GST & Tax', icon: 'tax', order: 50, purpose: 'GST returns, TDS and the tax calendar' },
  projects: { name: 'Projects', icon: 'projects', order: 60, purpose: 'Projects, tasks, timesheets and profitability' },
  hr: { name: 'People (HR)', icon: 'hr', order: 70, purpose: 'Employees, attendance, leave and payroll' },
  partners: { name: 'Partners', icon: 'partners', order: 80, purpose: 'Capital, drawings, loans and profit sharing' },
  approvals: { name: 'Approvals', icon: 'approvals', order: 90, purpose: 'Requests waiting for a decision' },
  compliance: { name: 'Compliance', icon: 'compliance', order: 100, purpose: 'Filings, contracts, notices and renewals' },
  workplace: { name: 'Workplace', icon: 'workplace', order: 110, purpose: 'Meetings, tasks, announcements and assets' },
  reports: { name: 'Reports', icon: 'reports', order: 120, purpose: 'Dashboards and business reports' },
  documents: { name: 'Documents', icon: 'documents', order: 130, purpose: 'Letters, minutes, operations and templates' },
  company: { name: 'Company', icon: 'company', order: 140, purpose: 'Profile, registrations, tax and bank settings' },
  admin: { name: 'Administration', icon: 'admin', order: 150, purpose: 'Users, roles and the change history' },
  guide: { name: 'Guide', icon: 'guide', order: 1000, purpose: 'How each module works, step by step' },
};

const UNLISTED_ORDER = 900;

// Display details for a module key; `fallbackLabel` is the module's own label for keys without an entry.
export function metaFor(key, fallbackLabel) {
  const m = MODULE_META[key];
  const name = m?.name || fallbackLabel || key;
  return { name, icon: m?.icon || 'documents', order: m?.order ?? UNLISTED_ORDER, purpose: m?.purpose || `Open ${name}` };
}
