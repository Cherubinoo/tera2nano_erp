import { useState } from 'react';
import { Navigate, NavLink, Outlet } from 'react-router-dom';
import { CompanyProvider, useAuth, useCompany } from './context';
import { ErrorBox, Loading, Notice } from './ui';

const NAV = [
  { to: '/company/overview', label: 'Company Overview' },
  { to: '/company/basic', label: 'Basic Company Information' },
  { to: '/company/legal', label: 'Legal and Registration Details' },
  { to: '/company/addresses', label: 'Registered and Business Addresses' },
  { to: '/company/partners', label: 'Partners and Signatories' },
  { to: '/company/contacts', label: 'Contact Directory' },
  { to: '/company/tax', label: 'Tax Configuration' },
  { to: '/company/bank', label: 'Bank Accounts' },
  { to: '/company/branding', label: 'Branding and Letterhead Designer' },
  { to: '/company/letters', label: 'Letters and Documents' },
  { to: '/company/minutes', label: 'Meeting Minutes' },
  { to: '/company/operations', label: 'Operations' },
  { to: '/company/quotations', label: 'Quotations' },
  { to: '/company/letterhead-templates', label: 'Letterhead Templates' },
  { to: '/company/numbering', label: 'Document Numbering' },
  { to: '/company/financial-years', label: 'Financial Year Settings' },
  { to: '/company/policies', label: 'Company Policies and Default Terms' },
  { to: '/company/documents', label: 'Company Documents' },
  { to: '/company/history', label: 'Company Change History', adminOnly: true },
];

function Shell() {
  const { user, logout } = useAuth();
  const { profile, loading, notConfigured, error, reload } = useCompany();
  const [menuOpen, setMenuOpen] = useState(false);

  if (loading) return <Loading label="Loading company…" />;
  if (notConfigured) {
    if (user.is_company_admin) return <Navigate to="/setup" replace />;
    return (
      <div className="page narrow">
        <Notice type="warn">The company has not been set up yet. Ask a company administrator to complete the setup.</Notice>
        <button type="button" className="btn" onClick={logout}>Sign out</button>
      </div>
    );
  }
  if (error) return <div className="page narrow"><ErrorBox error={error} onRetry={reload} /></div>;

  const items = NAV.filter((n) => !n.adminOnly || user.is_company_admin);
  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to content</a>
      <button type="button" className="menu-toggle btn" aria-expanded={menuOpen} aria-controls="sidebar" onClick={() => setMenuOpen((o) => !o)}>Menu</button>
      <nav id="sidebar" className={`sidebar${menuOpen ? ' open' : ''}`} aria-label="Main">
        <div className="brand">
          <strong>{profile.display_name}</strong>
          <span className="muted">ERP</span>
        </div>
        <div className="nav-section">Company Management</div>
        <ul>
          {items.map((n) => (
            <li key={n.to}><NavLink to={n.to} onClick={() => setMenuOpen(false)}>{n.label}</NavLink></li>
          ))}
        </ul>
        <div className="sidebar-foot">
          <div className="who">{user.full_name}<span className="muted"> · {user.role}</span></div>
          {user.is_company_admin && profile.setup_status !== 'completed' && <NavLink to="/setup" className="btn btn-small">Resume setup</NavLink>}
          <button type="button" className="btn btn-small" onClick={logout}>Sign out</button>
        </div>
      </nav>
      <main id="main" className="page" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}

export default function Layout() {
  return (<CompanyProvider><Shell /></CompanyProvider>);
}
