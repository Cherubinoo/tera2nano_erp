import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import Layout from './Layout';
import Login from './Login';
import Wizard from './Wizard';
import { AuthProvider, LookupsProvider, useAuth } from './context';
import {
  AddressesPage, BankPage, BasicInfoPage, BrandingPage, ContactsPage, DocumentsPage, FinancialYearsPage, HistoryPage,
  LegalPage, LetterheadTemplatesPage, LettersPage, MinutesPage, NumberingPage, OperationsPage, OverviewPage, PartnersPage, PoliciesPage, QuotationsPage, TaxPage,
} from './pages';
import { Loading, ToastProvider } from './ui';

function RequireAuth() {
  const { user, ready } = useAuth();
  const location = useLocation();
  if (!ready) return <Loading />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <LookupsProvider><Outlet /></LookupsProvider>;
}

function AdminOnly({ children }) {
  const { user } = useAuth();
  return user.is_company_admin ? children : <Navigate to="/company/overview" replace />;
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<RequireAuth />}>
            <Route path="/setup" element={<Wizard />} />
            <Route path="/company" element={<Layout />}>
              <Route index element={<Navigate to="overview" replace />} />
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
              <Route path="financial-years" element={<FinancialYearsPage />} />
              <Route path="policies" element={<PoliciesPage />} />
              <Route path="documents" element={<DocumentsPage />} />
              <Route path="history" element={<AdminOnly><HistoryPage /></AdminOnly>} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/company" replace />} />
        </Routes>
      </ToastProvider>
    </AuthProvider>
  );
}
