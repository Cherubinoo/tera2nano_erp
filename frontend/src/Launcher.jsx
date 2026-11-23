import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { api } from './api';
import { CompanyProvider, useAuth, useCompany } from './context';
import Icon from './icons';
import { Loading, Notice } from './ui';
import { initialsOf, readLast, visibleWorkspaces, workspaceForPath } from './workspaces';

// Small live counts on a few tiles. Each one is optional: a failed or slow call simply shows no badge.
function useBadges(user, workspaces) {
  const [badges, setBadges] = useState({});
  const keys = workspaces.map((w) => w.key).join(',');
  useEffect(() => {
    let alive = true;
    const put = (key, count, noun) => { if (alive && count > 0) setBadges((b) => ({ ...b, [key]: { count, noun } })); };
    const has = (k) => keys.split(',').includes(k);
    try {
      if (has('approvals')) api.get('/approvals/summary/').then((d) => put('approvals', Number(d?.pending_for_me) || 0, 'waiting for you')).catch(() => {});
      if (has('sales') && (user.permissions || []).includes('sales.view')) {
        api.get('/sales/reminders/').then((d) => put('sales', Number(d?.count) || 0, 'reminders due')).catch(() => {});
      }
    } catch { /* badges are optional */ }
    return () => { alive = false; };
  }, [keys, user]);
  return badges;
}

const firstName = (user) => (user.full_name || user.username || '').trim().split(/\s+/)[0];

function Tile({ ws, badge }) {
  const label = `${ws.name}. ${ws.purpose}.${badge ? ` ${badge.count} ${badge.noun}.` : ''}`;
  return (
    <Link to={ws.landing} className="launch-tile" aria-label={label} data-module={ws.key}>
      <span className="launch-ring" aria-hidden="true"><Icon name={ws.icon} size={36} /></span>
      <span className="launch-name">{ws.name}</span>
      <span className="launch-purpose">{ws.purpose}</span>
      {badge && <span className="launch-badge" aria-hidden="true">{badge.count > 99 ? '99+' : badge.count} {badge.noun}</span>}
    </Link>
  );
}

function Launcher() {
  const { user, logout } = useAuth();
  const { profile, loading, notConfigured } = useCompany();
  const gridRef = useRef(null);
  const workspaces = useMemo(() => visibleWorkspaces(user), [user]);
  const badges = useBadges(user, workspaces);

  const last = useMemo(() => {
    const l = readLast();
    const ws = l && workspaces.find((w) => w.key === l.key);
    if (!ws) return null;
    const path = typeof l.path === 'string' && l.path.startsWith('/') && workspaceForPath(l.path.split('?')[0], workspaces)?.key === ws.key ? l.path : ws.landing;
    const item = ws.items.find((i) => i.to === path.split('?')[0]);
    return { ws, path, page: item && item.to !== ws.landing ? item.label : '' };
  }, [workspaces]);

  useEffect(() => { document.title = 'Choose a module'; }, []);

  if (loading) return <div className="launcher"><Loading label="Loading…" /></div>;
  if (notConfigured) {
    if (user.is_company_admin) return <Navigate to="/setup" replace />;
    return (
      <div className="launcher"><div className="launch-main">
        <Notice type="warn">The company has not been set up yet. Ask a company administrator to complete the setup.</Notice>
        <button type="button" className="btn" onClick={logout}>Sign out</button>
      </div></div>
    );
  }

  const company = profile?.display_name || '';
  const today = new Date().toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });

  // Arrow keys move between tiles (and Home/End jump to the first/last); Tab and Enter work as on any links.
  const onKeyDown = (e) => {
    if (!['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
    const tiles = [...gridRef.current.querySelectorAll('a.launch-tile')];
    const i = tiles.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    let next = i;
    if (e.key === 'ArrowRight') next = Math.min(i + 1, tiles.length - 1);
    else if (e.key === 'ArrowLeft') next = Math.max(i - 1, 0);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = tiles.length - 1;
    else {
      // offsetTop/offsetLeft ignore the entrance animation's transform, so rows are stable while tiles fade in
      const at = (t) => ({ top: t.offsetTop, mid: t.offsetLeft + t.offsetWidth / 2 });
      const from = at(tiles[i]);
      const down = e.key === 'ArrowDown';
      const rows = tiles.map((t, n) => ({ n, ...at(t) })).filter((t) => (down ? t.top > from.top + 8 : t.top < from.top - 8));
      if (rows.length) {
        const rowTop = down ? Math.min(...rows.map((t) => t.top)) : Math.max(...rows.map((t) => t.top));
        const inRow = rows.filter((t) => Math.abs(t.top - rowTop) < 8);
        next = inRow.reduce((x, y) => (Math.abs(x.mid - from.mid) <= Math.abs(y.mid - from.mid) ? x : y)).n;
      }
    }
    tiles[next]?.focus();
  };

  return (
    <div className="launcher">
      <header className="launch-top">
        <Link to="/apps" className="launch-brand" aria-label="Home: all modules">
          <span className="nav-org-mark" aria-hidden="true">{initialsOf(company || 'T2N')}</span>
          <span>Tera2Nano ERP</span>
        </Link>
        <div className="launch-top-actions">
          <Link to="/company/home" className="launch-link">Company home</Link>
          <button type="button" className="launch-link" onClick={logout} aria-label="Sign out"><Icon name="logout" size={16} /><span>Sign out</span></button>
        </div>
      </header>
      <main className="launch-main" id="main">
        <h1 className="launch-greeting">Welcome back, {firstName(user)}</h1>
        <p className="launch-sub">{company && <>{company} · </>}{today}</p>

        {last && (
          <section className="launch-continue" aria-label="Continue where you left off">
            <h2 className="launch-title">Continue where you left off</h2>
            <Link to={last.path} className="launch-resume" aria-label={`Continue in ${last.ws.name}${last.page ? `, ${last.page}` : ''}`}>
              <span className="launch-ring launch-ring-sm" aria-hidden="true"><Icon name={last.ws.icon} size={22} /></span>
              <span className="launch-resume-text"><strong>{last.ws.name}</strong>{last.page && <span>{last.page}</span>}</span>
              <Icon name="arrow" size={18} />
            </Link>
          </section>
        )}

        <section aria-label="Modules">
          <h2 className="launch-title">Choose a module</h2>
          <div className="launch-grid" ref={gridRef} onKeyDown={onKeyDown}>
            {workspaces.map((w) => <Tile key={w.key} ws={w} badge={badges[w.key]} />)}
          </div>
          <p className="launch-hint">Use the arrow keys to move between modules and Enter to open one.</p>
        </section>
      </main>
    </div>
  );
}

export default function LauncherPage() {
  return (<CompanyProvider><Launcher /></CompanyProvider>);
}
