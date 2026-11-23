// Modules plug themselves in. Each folder src/modules/<name>/index.jsx default-exports a definition:
//
//   export default {
//     key: 'purchases', label: 'Purchases',          // the sidebar group
//     basePath: '/purchases', perm: 'purchases.view', // every route lives under basePath; `perm` is optional (null = any signed-in user,
//                                                       for modules with self-service pages; the server still enforces each API)
//     nav: [{ to: '/purchases', label: 'Purchases Dashboard', end: true, perms: ['purchases.view'] }, ...],
//     routes: [{ index: true, element: <Home /> }, { path: 'vendors', element: <Vendors /> }, ...],
//     quickCreate: [{ to: '/purchases/orders/new', label: 'Purchase order', perms: ['purchases.manage'] }],
//   };
//
// App.jsx and Layout.jsx read this list; no shared file needs editing to add a module.
const found = import.meta.glob('./modules/*/index.jsx', { eager: true });

export const MODULES = Object.values(found)
  .map((m) => m.default)
  .filter(Boolean)
  .sort((a, b) => (a.order ?? 100) - (b.order ?? 100) || a.label.localeCompare(b.label));
