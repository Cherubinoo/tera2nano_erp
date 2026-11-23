import './crm.css';
import Accounts, { AccountDetail } from './Accounts';
import Activities from './Activities';
import Contacts, { ContactDetail } from './Contacts';
import Dashboard from './Dashboard';
import DealDetail from './DealDetail';
import Deals from './Deals';
import LeadDetail from './LeadDetail';
import Leads from './Leads';
import Reports from './Reports';
import Settings from './Settings';

// CRM: leads, contacts and accounts, the deals pipeline, activities, reports. Deals that are won create a sales customer
// and can start a quotation, which already flows into invoices.
export default {
  key: 'crm',
  label: 'CRM',
  basePath: '/crm',
  perm: 'crm.view',
  order: 20,
  nav: [
    { to: '/crm', label: 'CRM Dashboard', end: true, perms: ['crm.view'] },
    { to: '/crm/leads', label: 'Leads', perms: ['crm.view'] },
    { to: '/crm/contacts', label: 'Contacts', perms: ['crm.view'] },
    { to: '/crm/accounts', label: 'Accounts', perms: ['crm.view'] },
    { to: '/crm/deals', label: 'Deals', perms: ['crm.view'] },
    { to: '/crm/activities', label: 'Activities', perms: ['crm.view'] },
    { to: '/crm/reports', label: 'CRM Reports', perms: ['crm.view'] },
    { to: '/crm/settings', label: 'CRM Settings', perms: ['crm.manage'] },
  ],
  routes: [
    { index: true, element: <Dashboard /> },
    { path: 'leads', element: <Leads /> },
    { path: 'leads/:id', element: <LeadDetail /> },
    { path: 'contacts', element: <Contacts /> },
    { path: 'contacts/:id', element: <ContactDetail /> },
    { path: 'accounts', element: <Accounts /> },
    { path: 'accounts/:id', element: <AccountDetail /> },
    { path: 'deals', element: <Deals /> },
    { path: 'deals/:id', element: <DealDetail /> },
    { path: 'activities', element: <Activities /> },
    { path: 'reports', element: <Reports /> },
    { path: 'settings', element: <Settings /> },
  ],
  quickCreate: [
    { to: '/crm/leads?new=1', label: 'Lead', perms: ['crm.manage'] },
    { to: '/crm/deals?new=1', label: 'Deal', perms: ['crm.manage'] },
    { to: '/crm/contacts?new=1', label: 'Contact', perms: ['crm.manage'] },
    { to: '/crm/activities?new=1', label: 'Activity', perms: ['crm.manage'] },
  ],
};
