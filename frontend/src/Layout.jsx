import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, Navigate, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { api } from './api';
import { CompanyProvider, useAuth, useCompany } from './context';
import Icon from './icons';
import { MODULES } from './modules';
import { MODULE_META } from './moduleMeta';
import { holds, initialsOf, pageCrumbs, visibleWorkspaces, workspaceForPath, writeLast } from './workspaces';
import { ErrorBox, Loading, Notice } from './ui';

// "+ New" shortcuts: each opens the page whose form creates that record.
const QUICK_CREATE = [
  ...MODULES.flatMap((m) => m.quickCreate || []),
  { to: '/sales/documents/new?kind=invoice', label: 'Invoice', perms: ['sales.manage'] },
  { to: '/sales/recurring/new', label: 'Recurring invoice', perms: ['sales.manage'] },
  { to: '/company/quotations', label: 'Quotation' },
  { to: '/sales/customers?new=1', label: 'Customer', perms: ['sales.manage'] },
  { to: '/sales/payments?new=1', label: 'Payment received', perms: ['sales.manage'] },
  { to: '/accounting/vouchers?new=expense', label: 'Expense', perms: ['accounting.manage'] },
  { to: '/accounting/journal/new', label: 'Journal entry', perms: ['accounting.manage'] },
  { to: '/accounting/assets/new', label: 'Fixed asset', perms: ['accounting.manage'] },
  { to: '/company/letters', label: 'Letter' },
  { to: '/company/minutes', label: 'Meeting minutes' },
  { to: '/company/operations', label: 'Operations document' },
  { to: '/company/access', label: 'User', perms: ['users.manage'] },
];

function useOutsideClose(ref, close) {
  useEffect(() => {
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) close(); };
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [ref, close]);
}

function QuickCreate({ user }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useOutsideClose(ref, () => setOpen(false));
  const items = QUICK_CREATE.filter((i) => holds(user, i.perms));
  return (
    <div className="menu-wrap" ref={ref}>
      <button type="button" className="btn btn-primary quick-new" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Icon name="plus" size={16} /><span>New</span>
      </button>
      {open && (
        <ul className="menu menu-right menu-scroll" role="menu" aria-label="Create new">
          {items.map((i) => (
            <li key={i.to + i.label} role="none"><Link role="menuitem" to={i.to} onClick={() => setOpen(false)}>{i.label}</Link></li>
          ))}
        </ul>
      )}
    </div>
  );
}

function UserMenu({ user, logout, profile }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useOutsideClose(ref, () => setOpen(false));
  return (
    <div className="menu-wrap" ref={ref}>
      <button type="button" className="user-chip" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="avatar" aria-hidden="true">{initialsOf(user.full_name || user.username)}</span>
        <span className="user-chip-text"><strong>{user.full_name}</strong><span className="muted">{user.role_label || user.role}</span></span>
      </button>
      {open && (
        <ul className="menu menu-right" role="menu" aria-label="Account">
          <li className="menu-info" role="none"><strong>{profile.display_name}</strong><div className="hint">Signed in as {user.username}</div></li>
          {user.is_company_admin && profile.setup_status !== 'completed' && (
            <li role="none"><Link role="menuitem" to="/setup" onClick={() => setOpen(false)}>Resume company setup</Link></li>
          )}
          <li role="none"><button type="button" role="menuitem" onClick={logout}>Sign out</button></li>
        </ul>
      )}
    </div>
  );
}

// Jump to any page you may open: type part of its name (Ctrl+K from anywhere).
function JumpSearch({ pages }) {
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const input = useRef(null);
  const ref = useRef(null);
  const navigate = useNavigate();
  useOutsideClose(ref, () => setOpen(false));
  useEffect(() => {
    const onKey = (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); input.current?.focus(); setOpen(true); } };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);
  const term = q.trim().toLowerCase();
  const matches = useMemo(() => (term
    ? pages.filter((p) => `${p.group} ${p.label}`.toLowerCase().includes(term)).slice(0, 8)
    : []), [pages, term]);
  const go = (to) => { setQ(''); setOpen(false); navigate(to); };
  return (
    <div className="search" ref={ref} role="search">
      <Icon name="search" size={16} />
      <input ref={input} type="search" value={q} placeholder="Search pages  (Ctrl+K)" aria-label="Go to a page"
        onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)}
        onKeyDown={(e) => { if (e.key === 'Enter' && matches[0]) go(matches[0].to); }} />
      {open && term && (
        <ul className="menu search-results" aria-label="Matching pages">
          {matches.length === 0 && <li className="menu-info hint">No page matches “{q}”.</li>}
          {matches.map((p) => (
            <li key={p.to + p.label}><Link to={p.to} onClick={() => go(p.to)}>{p.label}<span className="hint"> · {p.group}</span></Link></li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Approvals waiting for this person; refreshed every 90 seconds. Silent when the approvals module is unavailable.
function ApprovalsBell() {
  const [count, setCount] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () => api.get('/approvals/summary/').then((d) => { if (alive) setCount(Number(d?.pending_for_me) || 0); }).catch(() => {});
    load();
    const t = setInterval(load, 90000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  return (
    <Link className="icon-link" to="/approvals" aria-label={count ? `${count} approval${count === 1 ? '' : 's'} waiting for you` : 'Approvals'} title="Approvals">
      <Icon name="bell" size={19} />
      {count > 0 && <span className="icon-badge">{count > 99 ? '99+' : count}</span>}
    </Link>
  );
}

// The module's own guide page when the person is inside a module, otherwise the guide's start page.
const hasGuide = (key) => !!key && key !== 'guide' && !!MODULE_META[key];
function HelpLink({ current }) {
  const to = hasGuide(current?.key) ? `/guide/${current.key}` : '/guide';
  const label = hasGuide(current?.key) ? `Guide: how ${current.name} works` : 'Guide: how the system works';
  return (<Link className="icon-link" to={to} aria-label={label} title={label}><Icon name="help" size={19} /></Link>);
}

// True at phone widths, where the sidebar is a slide-over drawer and the icon-only rail does not apply.
function usePhone() {
  const query = '(max-width: 900px)';
  const [phone, setPhone] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setPhone(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return phone;
}


// Compact module picker in the top bar: every module the person may open, one click away from wherever they are.
function ModuleSwitcher({ workspaces, current }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useOutsideClose(ref, () => setOpen(false));
  return (
    <div className="menu-wrap switcher-wrap" ref={ref}>
      <button type="button" className="switcher-btn" aria-haspopup="menu" aria-expanded={open} aria-label="Switch module" title="Switch module" onClick={() => setOpen((o) => !o)}>
        <Icon name="grid" size={19} />
        <span className="switcher-label">{current ? current.name : 'Modules'}</span>
      </button>
      {open && (
        <div className="menu switcher" role="menu" aria-label="Modules">
          <ul className="switcher-grid">
            {workspaces.map((w) => (
              <li key={w.key} role="none">
                <Link role="menuitem" to={w.landing} className={current?.key === w.key ? 'current' : ''} aria-current={current?.key === w.key ? 'page' : undefined} onClick={() => setOpen(false)}>
                  <span className="switcher-ico"><Icon name={w.icon} size={20} /></span>
                  <span>{w.name}</span>
                </Link>
              </li>
            ))}
          </ul>
          <Link role="menuitem" to="/apps" className="switcher-all" onClick={() => setOpen(false)}><Icon name="back" size={16} /> All modules</Link>
        </div>
      )}
    </div>
  );
}

// The sidebar shows one module's menu at a time (derived from the URL); pages that belong to no module get the module list.
// Collapsed to an icon rail it always lists the modules, so it doubles as a switcher.
function Sidebar({ workspaces, current, profile, menuOpen, collapsed: wantCollapsed, closeMenu }) {
  const phone = usePhone();
  const collapsed = wantCollapsed && !phone;
  const mark = initialsOf(profile.display_name);
  if (collapsed) {
    return (
      <nav id="sidebar" className="sidebar collapsed" aria-label="Main">
        <Link to="/apps" className="nav-org" title="All modules" aria-label="All modules" tabIndex={-1}><span className="nav-org-mark" aria-hidden="true">{mark}</span></Link>
        <NavLink to="/company/home" className="nav-top" title="Home" aria-label="Home"><Icon name="home" /></NavLink>
        {workspaces.map((w) => (
          <Link key={w.key} to={w.landing} className={`nav-top${current?.key === w.key ? ' active' : ''}`} title={w.name} aria-label={w.name}><Icon name={w.icon} /></Link>
        ))}
      </nav>
    );
  }
  return (
    <nav id="sidebar" className={`sidebar${menuOpen ? ' open' : ''}`} aria-label="Main">
      {current ? (
        <Link to={current.landing} className="nav-org nav-ws" onClick={closeMenu} tabIndex={-1}>
          <span className="nav-org-mark" aria-hidden="true"><Icon name={current.icon} size={18} /></span>
          <span className="nav-org-text"><strong>{current.name}</strong><span>{profile.display_name}</span></span>
        </Link>
      ) : (
        <Link to="/apps" className="nav-org" onClick={closeMenu} tabIndex={-1}>
          <span className="nav-org-mark" aria-hidden="true">{mark}</span>
          <span className="nav-org-text"><strong>{profile.display_name}</strong><span>ERP</span></span>
        </Link>
      )}
      <Link to="/apps" className="nav-back" onClick={closeMenu}><Icon name="back" size={18} /><span className="nav-label">All modules</span></Link>
      {current ? current.groups.map((g) => (
        <div className="nav-section" key={g.key}>
          {current.groups.length > 1 && <h2 className="nav-heading">{g.label}</h2>}
          <ul className="ws-menu">
            {g.items.map((i) => (
              <li key={i.to + i.label}><NavLink to={i.to} end={i.end} onClick={closeMenu}>{i.label}</NavLink></li>
            ))}
          </ul>
        </div>
      )) : (
        <>
          <NavLink to="/company/home" className="nav-top" onClick={closeMenu}><Icon name="home" /><span className="nav-label">Home</span></NavLink>
          <div className="nav-section">
            <h2 className="nav-heading">Modules</h2>
            <ul className="nav-modules">
              {workspaces.map((w) => (
                <li key={w.key}><Link to={w.landing} onClick={closeMenu}><Icon name={w.icon} /><span className="nav-label">{w.name}</span></Link></li>
              ))}
            </ul>
          </div>
        </>
      )}
      {hasGuide(current?.key) && (
        <Link to={`/guide/${current.key}`} className="nav-guide" onClick={closeMenu}><Icon name="guide" size={16} /><span>How this module works</span></Link>
      )}
    </nav>
  );
}

// "Module / Page" above the page title, so there is always a way back up and a sense of place.
function Crumbs({ current, pathname }) {
  if (!current) return null;
  const crumbs = pageCrumbs(current, pathname);
  return (
    <nav className="crumbs" aria-label="Breadcrumb">
      <ol>
        <li><Link to={current.landing}>{current.name}</Link></li>
        {crumbs.map((c, i) => (
          <li key={c.label + i} aria-current={i === crumbs.length - 1 ? 'page' : undefined}>{c.to ? <Link to={c.to}>{c.label}</Link> : c.label}</li>
        ))}
      </ol>
    </nav>
  );
}

function Shell() {
  const { user, logout } = useAuth();
  const { profile, loading, notConfigured, error, reload } = useCompany();
  const { pathname, search } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem('erp_nav_collapsed') === '1'; } catch { return false; } });
  const setCollapsedSaved = (v) => { setCollapsed(v); try { localStorage.setItem('erp_nav_collapsed', v ? '1' : '0'); } catch { /* not saved */ } };
  const workspaces = useMemo(() => visibleWorkspaces(user), [user]);
  const current = useMemo(() => workspaceForPath(pathname, workspaces), [pathname, workspaces]);
  const currentKey = current?.key;
  useEffect(() => { if (currentKey) writeLast(currentKey, pathname + search); }, [currentKey, pathname, search]);

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

  const pages = [{ to: '/apps', label: 'All modules', group: 'Home' }, { to: '/company/home', label: 'Home', group: 'Dashboard' },
    ...workspaces.flatMap((w) => w.groups.flatMap((g) => g.items.filter((i) => i.to).map((i) => ({ to: i.to, label: i.label, group: w.name }))))];
  const burger = () => {
    if (window.matchMedia('(max-width: 900px)').matches) setMenuOpen((o) => !o);
    else setCollapsedSaved(!collapsed);
  };
  return (
    <div className={`app${collapsed ? ' nav-collapsed' : ''}`}>
      <a className="skip-link" href="#main">Skip to content</a>
      <Sidebar workspaces={workspaces} current={current} profile={profile} menuOpen={menuOpen} collapsed={collapsed} closeMenu={() => setMenuOpen(false)} />
      {menuOpen && <button type="button" className="nav-scrim" aria-label="Close menu" onClick={() => setMenuOpen(false)} />}
      <div className="workspace">
        <header className="topbar">
          <button type="button" className="icon-link menu-toggle" aria-expanded={menuOpen} aria-controls="sidebar" aria-label="Toggle menu" onClick={burger}><Icon name="menu" size={20} /></button>
          <Link to="/apps" className="brand-link" title="Home: all modules" aria-label="Home: all modules"><span className="nav-org-mark" aria-hidden="true">{initialsOf(profile.display_name)}</span></Link>
          <ModuleSwitcher workspaces={workspaces} current={current} />
          <JumpSearch pages={pages} />
          <div className="topbar-actions">
            <QuickCreate user={user} />
            <HelpLink current={current} />
            <ApprovalsBell />
            <UserMenu user={user} logout={logout} profile={profile} />
          </div>
        </header>
        <main id="main" className="page" tabIndex={-1}>
          <Crumbs current={current} pathname={pathname} />
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default function Layout() {
  return (<CompanyProvider><Shell /></CompanyProvider>);
}
