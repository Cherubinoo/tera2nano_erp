import './partners.css';
import PartnerDashboard from './Dashboard';
import LoansPage from './Loans';
import { PartnerDetailPage, PartnersPage } from './Partners';
import ProfitSharingPage from './ProfitSharing';
import StatementsPage from './Statements';
import { TransactionsPage } from './Transactions';

export default {
  key: 'partners',
  label: 'Partners',
  basePath: '/partners',
  perm: 'partners.view',
  order: 35,
  nav: [
    { to: '/partners', label: 'Partner Dashboard', end: true, perms: ['partners.view'] },
    { to: '/partners/accounts', label: 'Partners', perms: ['partners.view'] },
    { to: '/partners/transactions', label: 'Transactions', perms: ['partners.view'] },
    { to: '/partners/profit-sharing', label: 'Profit Sharing', perms: ['partners.view'] },
    { to: '/partners/loans', label: 'Loans', perms: ['partners.view'] },
    { to: '/partners/statements', label: 'Statements', perms: ['partners.view'] },
  ],
  routes: [
    { index: true, element: <PartnerDashboard /> },
    { path: 'accounts', element: <PartnersPage /> },
    { path: 'accounts/:id', element: <PartnerDetailPage /> },
    { path: 'transactions', element: <TransactionsPage /> },
    { path: 'profit-sharing', element: <ProfitSharingPage /> },
    { path: 'loans', element: <LoansPage /> },
    { path: 'statements', element: <StatementsPage /> },
  ],
  quickCreate: [{ to: '/partners/transactions?new=1', label: 'Partner transaction', perms: ['partners.manage'] }],
};
