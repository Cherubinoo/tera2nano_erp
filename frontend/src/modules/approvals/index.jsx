import './approvals.css';
import Dashboard from './Dashboard';
import RequestDetail from './RequestDetail';
import Requests from './Requests';
import Rules from './Rules';

// Every signed-in user sees "Pending my approval" and "My requests"; the analytics, the all-approvals list and the rules need
// approvals.view (changing rules needs approvals.manage). The server enforces each of these.
const VIEW = ['approvals.view', 'approvals.manage'];

export default {
  key: 'approvals',
  label: 'Approvals',
  basePath: '/approvals',
  perm: null,
  order: 70,
  nav: [
    { to: '/approvals', label: 'Pending My Approval', end: true },
    { to: '/approvals/mine', label: 'My Requests' },
    { to: '/approvals/all', label: 'All Approvals', perms: VIEW },
    { to: '/approvals/dashboard', label: 'Approval Analytics', perms: VIEW },
    { to: '/approvals/rules', label: 'Approval Rules', perms: VIEW },
  ],
  routes: [
    { index: true, element: <Requests scope="inbox" /> },
    { path: 'mine', element: <Requests scope="mine" /> },
    { path: 'all', element: <Requests scope="all" /> },
    { path: 'dashboard', element: <Dashboard /> },
    { path: 'rules', element: <Rules /> },
    { path: 'requests/:id', element: <RequestDetail /> },
  ],
};
