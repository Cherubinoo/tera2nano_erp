import { useEffect, useState } from 'react';
import { Link, NavLink } from 'react-router-dom';
import { useAuth } from '../../context';
import Icon from '../../icons';
import { WORKSPACES, holds } from '../../workspaces';
import { MODULE_GUIDES, nameOf } from './guideData';

const pathOf = (to) => to.split(/[?#]/)[0];
const under = (path, prefix) => path === prefix || path.startsWith(`${prefix}/`);

// The permission codes needed to open a screen: those of the most specific menu item that covers its path (any one is enough),
// or the module's own. Undefined means any signed-in user. Used so a guide link the person cannot open is shown as plain text.
export function permsForPath(to) {
  const path = pathOf(to);
  let best = null;
  let bestLen = -1;
  for (const w of WORKSPACES) {
    for (const i of w.groups.flatMap((g) => g.items)) {
      const p = pathOf(i.to);
      if ((i.end ? path === p : under(path, p)) && p.length > bestLen) { best = i.perms || (w.perm ? [w.perm] : undefined); bestLen = p.length; }
    }
  }
  return best || undefined;
}

export function useCanOpen() {
  const { user } = useAuth();
  return (to) => holds(user, permsForPath(to));
}

// A link to a real screen; when the person's role cannot open it, plain text with a note instead of a link that bounces back.
export function ScreenLink({ to, children, className }) {
  const canOpen = useCanOpen();
  if (canOpen(to)) return <Link to={to} className={className}>{children}</Link>;
  return (
    <span className="guide-locked" title="Your role cannot open this screen">{children}<span className="sr-only"> (not available to your role)</span></span>
  );
}

export function useReducedMotion() {
  const query = '(prefers-reduced-motion: reduce)';
  const [reduced, setReduced] = useState(() => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query).matches : false));
  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const mq = window.matchMedia(query);
    const on = () => setReduced(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return reduced;
}

export const GUIDE_PAGES = [
  { to: '/guide', label: 'Overview and process flow', end: true },
  { to: '/guide/operate', label: 'How to operate' },
  { to: '/guide/integration', label: 'How modules connect' },
  { to: '/guide/roles', label: 'Roles and access' },
  { to: '/guide/glossary', label: 'Glossary' },
];

// The left-hand list on every guide page: the four general pages, then one entry per module.
export function GuideList({ current }) {
  return (
    <>
      <nav className="guide-list guide-list-wide" aria-label="Guide pages">
        <h2 className="guide-list-title">Guide</h2>
        <ul>{GUIDE_PAGES.map((p) => <li key={p.to}><NavLink to={p.to} end={p.end}>{p.label}</NavLink></li>)}</ul>
        <h2 className="guide-list-title">Modules</h2>
        <ul>
          {MODULE_GUIDES.map((g) => (
            <li key={g.key}><NavLink to={`/guide/${g.key}`}><Icon name={g.icon} size={16} /><span>{g.name}</span></NavLink></li>
          ))}
        </ul>
      </nav>
      <details className="guide-list guide-list-narrow">
        <summary>All guide pages{current ? `: ${current}` : ''}</summary>
        <ul>
          {GUIDE_PAGES.map((p) => <li key={p.to}><NavLink to={p.to} end={p.end}>{p.label}</NavLink></li>)}
          {MODULE_GUIDES.map((g) => <li key={g.key}><NavLink to={`/guide/${g.key}`}>{g.name}</NavLink></li>)}
        </ul>
      </details>
    </>
  );
}

// A link to another module's guide page, by key.
export function ModuleLink({ k }) {
  return <Link to={`/guide/${k}`}>{nameOf(k)}</Link>;
}

export function Yes({ on, children }) {
  return on ? <span className="guide-yes">{children || 'Yes'}</span> : <span className="guide-no">{children || 'No'}</span>;
}
