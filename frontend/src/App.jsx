import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import Launcher from './Launcher';
import Layout from './Layout';
import Login from './Login';
import Wizard from './Wizard';
import { AuthProvider, LookupsProvider, useAuth } from './context';
import {
  AccessPage, AddressesPage, BankPage, BasicInfoPage, BrandingPage, ContactsPage, DocumentsPage, FinancialYearsPage, HistoryPage, HomePage,
  LegalPage, LetterheadTemplatesPage, LettersPage, MinutesPage, NumberingPage, OperationsPage, OverviewPage, PartnersPage, PoliciesPage, QuotationsPage, TaxPage,
} from './pages';
import QuotationDetailPage from './pages/QuotationDetail';
import { CustomerDetailPage, CustomersPage } from './pages/sales/Customers';
import DocumentDetail from './pages/sales/DocumentDetail';
import DocumentForm from './pages/sales/DocumentForm';
import SalesDocumentsPage from './pages/sales/Documents';
import { PaymentDetail, PaymentsPage } from './pages/sales/Payments';
import Receivables from './pages/sales/Receivables';
import RecurringForm from './pages/sales/RecurringForm';
import { RecurringDetail, RecurringList } from './pages/sales/RecurringInvoices';
import Reminders from './pages/sales/Reminders';
import SalesHome from './pages/sales/SalesHome';
import AccountingHome from './pages/accounting/AccountingHome';
import { BankPage as BankCashPage, ReconcilePage } from './pages/accounting/Bank';
import { AccountLedgerPage, ChartPage } from './pages/accounting/ChartOfAccounts';
import { JournalDetailPage, JournalNewPage, JournalPage } from './pages/accounting/Journal';
import { BalanceSheet, CashFlow, ProfitLoss, ReportsHub, TrialBalance } from './pages/accounting/Reports';
import OpeningBalancesPage from './pages/accounting/OpeningBalances';
import VoucherDetail from './pages/accounting/VoucherDetail';
import VouchersPage from './pages/accounting/Vouchers';
import AssetDetail from './pages/accounting/AssetDetail';
import AssetForm from './pages/accounting/AssetForm';
import BudgetReport from './pages/accounting/BudgetReport';
import { BudgetForm, BudgetsPage } from './pages/accounting/Budgets';
import FixedAssetsPage from './pages/accounting/FixedAssets';
import { MODULES } from './modules';
import { Loading, ToastProvider } from './ui';

function RequireAuth() {
  const { user, ready, signedOut } = useAuth();
  const location = useLocation();
  if (!ready) return <Loading />;
  // A deep link is remembered across sign-in; after an explicit sign-out the next person starts at the launcher.
  if (!user) return <Navigate to="/login" replace state={{ from: signedOut ? '/apps' : location.pathname + location.search }} />;
  return <LookupsProvider><Outlet /></LookupsProvider>;
}

// Renders `children` only for users holding one of the permission codes (see the Users and Access page).
function RequirePermission({ codes, children }) {
  const { user } = useAuth();
  return codes.some((c) => (user.permissions || []).includes(c)) ? children : <Navigate to="/company/overview" replace />;
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<RequireAuth />}>
            <Route path="/setup" element={<Wizard />} />
            <Route path="/apps" element={<Launcher />} />
            <Route path="/company" element={<Layout />}>
              <Route index element={<Navigate to="home" replace />} />
              <Route path="home" element={<HomePage />} />
              <Route path="overview" element={<OverviewPage />} />
              <Route path="basic" element={<BasicInfoPage />} />
              <Route path="legal" element={<LegalPage />} />
              <Route path="addresses" element={<AddressesPage />} />
              <Route path="partners" element={<PartnersPage />} />
              <Route path="contacts" element={<ContactsPage />} />
              <Route path="tax" element={<TaxPage />} />
              <Route path="bank" element={<BankPage />} />
              <Route path="branding" element={<BrandingPage />} />
              <Route path="numbering" element={<NumberingPage />} />
              <Route path="letterhead-templates" element={<LetterheadTemplatesPage />} />
              <Route path="letters" element={<LettersPage />} />
              <Route path="minutes" element={<MinutesPage />} />
              <Route path="operations" element={<OperationsPage />} />
              <Route path="quotations" element={<QuotationsPage />} />
              <Route path="quotations/:id" element={<QuotationDetailPage />} />
              <Route path="financial-years" element={<FinancialYearsPage />} />
              <Route path="policies" element={<PoliciesPage />} />
              <Route path="documents" element={<DocumentsPage />} />
              <Route path="history" element={<RequirePermission codes={['audit.view']}><HistoryPage /></RequirePermission>} />
              <Route path="access" element={<RequirePermission codes={['users.manage', 'users.view_activity']}><AccessPage /></RequirePermission>} />
            </Route>
            <Route path="/sales" element={<RequirePermission codes={['sales.view']}><Layout /></RequirePermission>}>
            <Route index element={<SalesHome />} />
            <Route path="customers" element={<CustomersPage />} />
            <Route path="customers/:id" element={<CustomerDetailPage />} />
            <Route path="documents" element={<SalesDocumentsPage />} />
            <Route path="documents/new" element={<DocumentForm />} />
            <Route path="documents/:id" element={<DocumentDetail />} />
            <Route path="documents/:id/edit" element={<DocumentForm />} />
            <Route path="payments" element={<PaymentsPage />} />
            <Route path="payments/:id" element={<PaymentDetail />} />
            <Route path="receivables" element={<Receivables />} />
            <Route path="recurring" element={<RecurringList />} />
            <Route path="recurring/new" element={<RecurringForm />} />
            <Route path="recurring/:id" element={<RecurringDetail />} />
            <Route path="recurring/:id/edit" element={<RecurringForm />} />
            <Route path="reminders" element={<Reminders />} />
            </Route>
            {MODULES.map((m) => (
              <Route key={m.key} path={m.basePath} element={m.perm ? <RequirePermission codes={[m.perm]}><Layout /></RequirePermission> : <Layout />}>
                {m.routes.map((r, i) => (r.index ? <Route key={`i${i}`} index element={r.element} /> : <Route key={r.path} path={r.path} element={r.element} />))}
              </Route>
            ))}
            <Route path="/accounting" element={<RequirePermission codes={['accounting.view']}><Layout /></RequirePermission>}>
              <Route index element={<AccountingHome />} />
              <Route path="accounts" element={<ChartPage />} />
              <Route path="accounts/:id" element={<AccountLedgerPage />} />
              <Route path="journal" element={<JournalPage />} />
              <Route path="journal/new" element={<JournalNewPage />} />
              <Route path="journal/:id" element={<JournalDetailPage />} />
              <Route path="opening-balances" element={<RequirePermission codes={['accounting.manage']}><OpeningBalancesPage /></RequirePermission>} />
              <Route path="vouchers" element={<VouchersPage />} />
              <Route path="vouchers/:id" element={<VoucherDetail />} />
              <Route path="bank" element={<BankCashPage />} />
              <Route path="bank/:id/reconcile" element={<ReconcilePage />} />
              <Route path="assets" element={<FixedAssetsPage />} />
              <Route path="assets/new" element={<AssetForm />} />
              <Route path="assets/:id" element={<AssetDetail />} />
              <Route path="assets/:id/edit" element={<AssetForm />} />
              <Route path="budgets" element={<BudgetsPage />} />
              <Route path="budgets/new" element={<BudgetForm />} />
              <Route path="budgets/report" element={<BudgetReport />} />
              <Route path="budgets/:id" element={<BudgetForm />} />
              <Route path="reports" element={<ReportsHub />} />
              <Route path="reports/profit-loss" element={<ProfitLoss />} />
              <Route path="reports/balance-sheet" element={<BalanceSheet />} />
              <Route path="reports/cash-flow" element={<CashFlow />} />
              <Route path="reports/trial-balance" element={<TrialBalance />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/apps" replace />} />
        </Routes>
      </ToastProvider>
    </AuthProvider>
  );
}
