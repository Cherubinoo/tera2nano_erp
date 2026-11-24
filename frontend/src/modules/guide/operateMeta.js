// The operating manual ("How to operate"): chapter order and the introduction.
// Each chapter is a JSON file in ./operate/<key>.json (shape documented in docs/HOW_TO_OPERATE.md's header note).
// Plain JS with no imports so both the app (operateData.js) and the doc generator (scripts/build-operate-docs.mjs) can read it.

export const OPERATE_ORDER = [
  'getting-started', 'crm', 'sales', 'purchases', 'accounting', 'gst-tax', 'projects', 'hr',
  'partners', 'approvals', 'compliance', 'workplace', 'reports', 'documents', 'company', 'admin',
];

export const OPERATE_INTRO = {
  title: 'How to operate',
  lead: 'Click-by-click instructions for everyday work in the Tera2Nano ERP: what to set up first, how to do each task, what happens afterwards, and the routines to follow every day, month and year.',
  howToUse: [
    'New to the system? Read "Getting started" first: it gives the order in which to set things up and how to find your way around.',
    'Each chapter covers one module. Every task says who can do it, where to find it (the menu path after you open the module from the launcher), the steps, and what the system does by itself afterwards.',
    'Menus only show what your role is allowed to use. If a step mentions a button you cannot see, ask a partner or your administrator.',
    'For how the modules connect and the end-to-end process, open the Overview and "How modules connect" pages of this Guide.',
  ],
};
