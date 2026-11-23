import './compliance.css';
import Calendar from './Calendar';
import Cases from './Cases';
import Contracts from './Contracts';
import Dashboard from './Dashboard';
import ExpiryWatch from './ExpiryWatch';
import Filings from './Filings';
import LegalEvents from './LegalEvents';
import Obligations from './Obligations';
import Policies from './Policies';

const VIEW = ['compliance.view'];

export default {
  key: 'compliance',
  label: 'Compliance',
  basePath: '/compliance',
  perm: 'compliance.view',
  order: 80,
  nav: [
    { to: '/compliance', label: 'Compliance Dashboard', end: true, perms: VIEW },
    { to: '/compliance/calendar', label: 'Calendar', perms: VIEW },
    { to: '/compliance/filings', label: 'Filings', perms: VIEW },
    { to: '/compliance/obligations', label: 'Obligations', perms: VIEW },
    { to: '/compliance/legal-events', label: 'Legal Events', perms: VIEW },
    { to: '/compliance/contracts', label: 'Contracts', perms: VIEW },
    { to: '/compliance/cases', label: 'Notices & Cases', perms: VIEW },
    { to: '/compliance/policies', label: 'Policies', perms: VIEW },
    { to: '/compliance/expiry-watch', label: 'Expiry Watch', perms: VIEW },
  ],
  routes: [
    { index: true, element: <Dashboard /> },
    { path: 'calendar', element: <Calendar /> },
    { path: 'filings', element: <Filings /> },
    { path: 'obligations', element: <Obligations /> },
    { path: 'legal-events', element: <LegalEvents /> },
    { path: 'contracts', element: <Contracts /> },
    { path: 'cases', element: <Cases /> },
    { path: 'policies', element: <Policies /> },
    { path: 'expiry-watch', element: <ExpiryWatch /> },
  ],
  quickCreate: [
    { to: '/compliance/filings?new=1', label: 'Compliance task', perms: ['compliance.manage'] },
    { to: '/compliance/contracts?new=1', label: 'Contract', perms: ['compliance.manage'] },
  ],
};
