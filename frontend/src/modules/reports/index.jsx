import './reports.css';
import Dashboard from './Dashboard';
import Home from './Home';
import { CashFlowReport, GstReport, PartnerReport, ProjectReport, ReceivablesReport, RevenueReport, SalaryReport, VendorReport } from './Reports';

// Reports and analytics hub. The group needs `reports.view`; each report also needs the permission of the module it reads
// (the sidebar hides what the role cannot open, and the server checks both on every request).
export default {
  key: 'reports',
  label: 'Reports',
  basePath: '/reports',
  perm: 'reports.view',
  order: 100,
  nav: [
    { to: '/reports', label: 'Reports Home', end: true, perms: ['reports.view'] },
    { to: '/reports/dashboard', label: 'Executive Dashboard', perms: ['reports.view'] },
    { to: '/reports/revenue', label: 'Annual Revenue', perms: ['accounting.view'] },
    { to: '/reports/project-profitability', label: 'Project Profitability', perms: ['projects.view'] },
    { to: '/reports/partner-capital', label: 'Partner Capital', perms: ['partners.view'] },
    { to: '/reports/salary', label: 'Salary Report', perms: ['hr.payroll'] },
    { to: '/reports/receivables', label: 'Pending Customer Payments', perms: ['sales.view'] },
    { to: '/reports/vendor-outstanding', label: 'Vendor Outstanding', perms: ['purchases.view'] },
    { to: '/reports/gst', label: 'GST Summary', perms: ['tax.view'] },
    { to: '/reports/cash-flow', label: 'Cash-Flow Analysis', perms: ['accounting.view'] },
  ],
  routes: [
    { index: true, element: <Home /> },
    { path: 'dashboard', element: <Dashboard /> },
    { path: 'revenue', element: <RevenueReport /> },
    { path: 'project-profitability', element: <ProjectReport /> },
    { path: 'partner-capital', element: <PartnerReport /> },
    { path: 'salary', element: <SalaryReport /> },
    { path: 'receivables', element: <ReceivablesReport /> },
    { path: 'vendor-outstanding', element: <VendorReport /> },
    { path: 'gst', element: <GstReport /> },
    { path: 'cash-flow', element: <CashFlowReport /> },
  ],
};
