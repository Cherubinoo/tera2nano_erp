import './tax.css';
import Calendar from './Calendar';
import Dashboard from './Dashboard';
import GstSummary from './GstSummary';
import IncomeTax from './IncomeTax';
import Payments from './Payments';
import PurchaseRegister from './PurchaseRegister';
import ReturnDetail from './ReturnDetail';
import Returns from './Returns';
import SalesRegister from './SalesRegister';
import Tds from './Tds';

export default {
  key: 'gst-tax',
  label: 'GST & Tax',
  basePath: '/tax',
  perm: 'tax.view',
  order: 50,
  nav: [
    { to: '/tax', label: 'Tax Dashboard', end: true, perms: ['tax.view'] },
    { to: '/tax/gst', label: 'GST Summary', perms: ['tax.view'] },
    { to: '/tax/sales-register', label: 'Sales Register', perms: ['tax.view'] },
    { to: '/tax/purchase-register', label: 'Purchase Register', perms: ['tax.view'] },
    { to: '/tax/returns', label: 'Returns', perms: ['tax.view'] },
    { to: '/tax/payments', label: 'GST Payments', perms: ['tax.view'] },
    { to: '/tax/tds', label: 'TDS', perms: ['tax.view'] },
    { to: '/tax/income-tax', label: 'Advance Tax & Income Tax', perms: ['tax.view'] },
    { to: '/tax/calendar', label: 'Tax Calendar', perms: ['tax.view'] },
  ],
  routes: [
    { index: true, element: <Dashboard /> },
    { path: 'gst', element: <GstSummary /> },
    { path: 'sales-register', element: <SalesRegister /> },
    { path: 'purchase-register', element: <PurchaseRegister /> },
    { path: 'returns', element: <Returns /> },
    { path: 'returns/:id', element: <ReturnDetail /> },
    { path: 'payments', element: <Payments /> },
    { path: 'tds', element: <Tds /> },
    { path: 'income-tax', element: <IncomeTax /> },
    { path: 'calendar', element: <Calendar /> },
  ],
  quickCreate: [
    { to: '/tax/payments?new=gst', label: 'Tax payment', perms: ['tax.manage'] },
    { to: '/tax/returns?new=1', label: 'Tax return record', perms: ['tax.manage'] },
  ],
};
