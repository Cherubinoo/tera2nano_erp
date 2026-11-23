import './purchases.css';
import Ageing from './Ageing';
import { BillDetail, BillForm, Bills } from './Bills';
import Dashboard from './Dashboard';
import { OrderDetail, OrderForm, Orders } from './Orders';
import Payments, { VendorPaymentDetail } from './Payments';
import Quotations from './Quotations';
import Requests from './Requests';
import VendorDetail from './VendorDetail';
import Vendors from './Vendors';

export default {
  key: 'purchases',
  label: 'Purchases',
  basePath: '/purchases',
  perm: 'purchases.view',
  order: 40,
  nav: [
    { to: '/purchases', label: 'Purchases Dashboard', end: true, perms: ['purchases.view'] },
    { to: '/purchases/vendors', label: 'Vendors', perms: ['purchases.view'] },
    { to: '/purchases/requests', label: 'Purchase Requests', perms: ['purchases.view'] },
    { to: '/purchases/quotations', label: 'Quotations', perms: ['purchases.view'] },
    { to: '/purchases/orders', label: 'Purchase Orders', perms: ['purchases.view'] },
    { to: '/purchases/bills', label: 'Bills', perms: ['purchases.view'] },
    { to: '/purchases/payments', label: 'Payments', perms: ['purchases.view'] },
    { to: '/purchases/ageing', label: 'Payables Ageing', perms: ['purchases.view'] },
  ],
  routes: [
    { index: true, element: <Dashboard /> },
    { path: 'vendors', element: <Vendors /> },
    { path: 'vendors/:id', element: <VendorDetail /> },
    { path: 'requests', element: <Requests /> },
    { path: 'quotations', element: <Quotations /> },
    { path: 'orders', element: <Orders /> },
    { path: 'orders/new', element: <OrderForm /> },
    { path: 'orders/:id', element: <OrderDetail /> },
    { path: 'orders/:id/edit', element: <OrderForm /> },
    { path: 'bills', element: <Bills /> },
    { path: 'bills/new', element: <BillForm /> },
    { path: 'bills/:id', element: <BillDetail /> },
    { path: 'bills/:id/edit', element: <BillForm /> },
    { path: 'payments', element: <Payments /> },
    { path: 'payments/:id', element: <VendorPaymentDetail /> },
    { path: 'ageing', element: <Ageing /> },
  ],
  quickCreate: [
    { to: '/purchases/orders/new', label: 'Purchase order', perms: ['purchases.manage'] },
    { to: '/purchases/bills/new', label: 'Vendor bill', perms: ['purchases.manage'] },
    { to: '/purchases/payments?new=1', label: 'Vendor payment', perms: ['purchases.manage'] },
    { to: '/purchases/vendors?new=1', label: 'Vendor', perms: ['purchases.manage'] },
  ],
};
