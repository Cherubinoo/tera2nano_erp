import './guide-style.css';
import Integration from './Integration';
import ModuleGuide from './ModuleGuide';
import Overview from './Overview';
import { Glossary, Roles } from './Reference';
import { MODULE_GUIDES } from './guideData';

// The Guide: the process from end to end, how modules connect, one page per module, roles and a glossary.
// All the text lives in guideData.js (and guideModulesA/B.js); docs/MODULE_GUIDE.md is generated from the same data.
// The launcher tile, icon and purpose come from src/moduleMeta.js ('guide').
const general = [
  { to: '/guide', label: 'Overview', end: true },
  { to: '/guide/integration', label: 'How modules connect' },
  { to: '/guide/roles', label: 'Roles and access' },
  { to: '/guide/glossary', label: 'Glossary' },
];
const modules = MODULE_GUIDES.map((g) => ({ to: `/guide/${g.key}`, label: g.name }));

export default {
  key: 'guide',
  label: 'Guide',
  basePath: '/guide',
  perm: null,
  order: 1000,
  nav: [...general, ...modules],
  groups: [
    { key: 'guide-start', label: 'Start here', items: general.slice(0, 1) },
    { key: 'guide-ref', label: 'Reference', items: general.slice(1) },
    { key: 'guide-modules', label: 'Module guides', items: modules },
  ],
  routes: [
    { index: true, element: <Overview /> },
    { path: 'integration', element: <Integration /> },
    { path: 'roles', element: <Roles /> },
    { path: 'glossary', element: <Glossary /> },
    { path: ':moduleKey', element: <ModuleGuide /> },
  ],
};
