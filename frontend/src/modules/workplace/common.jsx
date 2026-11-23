import { Link } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { asList, useLoad } from '../../hooks';
import { Badge, Field } from '../../ui';

export const MEETING_TYPES = [['partners', 'Partners'], ['board', 'Board'], ['team', 'Team'], ['client', 'Client'], ['vendor', 'Vendor']];
export const MEETING_TONE = { scheduled: 'info', held: 'good', cancelled: 'neutral', rescheduled: 'warn' };
export const TASK_STATUSES = [['todo', 'To do'], ['in_progress', 'In progress'], ['blocked', 'Blocked'], ['done', 'Done'], ['cancelled', 'Cancelled']];
export const TASK_TONE = { todo: 'neutral', in_progress: 'info', blocked: 'warn', done: 'good', cancelled: 'neutral' };
export const STATUS_COLOUR = { todo: 'var(--chart-grey)', in_progress: 'var(--chart-1)', blocked: 'var(--chart-3)', done: 'var(--chart-2)', cancelled: 'var(--chart-5)' };
export const PRIORITIES = [['low', 'Low'], ['medium', 'Medium'], ['high', 'High'], ['urgent', 'Urgent']];
export const PRIORITY_TONE = { low: 'neutral', medium: 'info', high: 'warn', urgent: 'bad' };
export const ROLE_OPTIONS = [
  ['managing_partner', 'Managing Partner'], ['partner', 'Partner'], ['finance_manager', 'Finance Manager'],
  ['hr_manager', 'HR Manager'], ['project_manager', 'Project Manager'], ['employee', 'Employee'], ['admin', 'Admin'],
];

export const useCan = () => {
  const { user } = useAuth();
  return (code) => (user.permissions || []).includes(code);
};

// Active people to pick from (name and role only; open to every signed-in user). A failure just gives an empty list.
export function usePeople() {
  const { data } = useLoad(() => api.get('/workplace/people/').catch(() => []), []);
  return asList(data);
}

export function Tile({ label, value, hint, tone, to }) {
  const inner = (<><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</>);
  return to ? <Link className={`tile ${tone || ''}`} to={to}>{inner}</Link> : <div className={`tile ${tone || ''}`}>{inner}</div>;
}

export function PersonSelect({ id, label, value, onChange, people, none = 'Nobody', error, required, hint }) {
  return (
    <Field id={id} label={label} error={error} required={required} hint={hint}>
      <select id={id} value={value || ''} onChange={(e) => onChange(e.target.value)}>
        <option value="">{none}</option>
        {people.map((p) => <option key={p.id} value={p.id}>{p.name}{p.role_label ? ` (${p.role_label})` : ''}</option>)}
      </select>
    </Field>
  );
}

export const initials = (name) => (name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
export const Avatar = ({ person }) => (person ? <span className="wp-avatar" title={person.name} aria-label={person.name}>{initials(person.name)}</span> : null);

export const timeRange = (m) => (m.start_time ? `${m.start_time}${m.end_time ? `–${m.end_time}` : ''}` : 'All day');

export const AudienceText = ({ row }) => (row.audience === 'all' ? 'Everyone' : (row.role_labels || row.roles).join(', '));

// Audience picker: everyone, or selected roles.
export function AudienceFields({ prefix, audience, roles, onChange, error }) {
  const toggle = (r) => onChange({ roles: roles.includes(r) ? roles.filter((x) => x !== r) : [...roles, r] });
  return (
    <>
      <Field id={`${prefix}-aud`} label="Audience" error={error.audience}>
        <select id={`${prefix}-aud`} value={audience} onChange={(e) => onChange({ audience: e.target.value })}>
          <option value="all">Everyone</option>
          <option value="roles">Selected roles</option>
        </select>
      </Field>
      {audience === 'roles' && (
        <Field id={`${prefix}-roles`} label="Roles" required error={error.roles} wide>
          <div className="checkgroup-items" id={`${prefix}-roles`} role="group" aria-label="Roles">
            {ROLE_OPTIONS.map(([key, label]) => (
              <label key={key} className="check"><input type="checkbox" checked={roles.includes(key)} onChange={() => toggle(key)} /> {label}</label>
            ))}
          </div>
        </Field>
      )}
    </>
  );
}

// ---------------------------------------------------------------- a small, safe Markdown renderer (no raw HTML)
function inline(text, key) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g;
  let last = 0;
  let m;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith('**')) out.push(<strong key={`${key}-${i}`}>{t.slice(2, -2)}</strong>);
    else if (t.startsWith('`')) out.push(<code key={`${key}-${i}`}>{t.slice(1, -1)}</code>);
    else if (t.startsWith('[')) {
      const [, label, url] = /\[([^\]]+)\]\(([^)]+)\)/.exec(t);
      out.push(<a key={`${key}-${i}`} href={url} target="_blank" rel="noopener noreferrer">{label}</a>);
    } else out.push(<em key={`${key}-${i}`}>{t.slice(1, -1)}</em>);
    last = m.index + t.length;
    i += 1;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text }) {
  const lines = (text || '').replace(/\r/g, '').split('\n');
  const blocks = [];
  let para = [];
  let list = null;
  const flushPara = () => { if (para.length) { blocks.push({ type: 'p', text: para.join(' ') }); para = []; } };
  const flushList = () => { if (list) { blocks.push(list); list = null; } };
  lines.forEach((raw) => {
    const line = raw.trimEnd();
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    const ul = /^\s*[-*]\s+(.*)$/.exec(line);
    const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (!line.trim()) { flushPara(); flushList(); } else if (h) { flushPara(); flushList(); blocks.push({ type: 'h', level: h[1].length, text: h[2] }); } else if (ul || ol) {
      flushPara();
      const kind = ul ? 'ul' : 'ol';
      if (!list || list.type !== kind) { flushList(); list = { type: kind, items: [] }; }
      list.items.push((ul || ol)[1]);
    } else { flushList(); para.push(line.trim()); }
  });
  flushPara(); flushList();
  return (
    <div className="wp-md">
      {blocks.map((b, i) => {
        if (b.type === 'h') { const H = `h${Math.min(b.level + 2, 5)}`; return <H key={i}>{inline(b.text, i)}</H>; }
        if (b.type === 'ul') return <ul key={i}>{b.items.map((t, j) => <li key={j}>{inline(t, `${i}-${j}`)}</li>)}</ul>;
        if (b.type === 'ol') return <ol key={i}>{b.items.map((t, j) => <li key={j}>{inline(t, `${i}-${j}`)}</li>)}</ol>;
        return <p key={i}>{inline(b.text, i)}</p>;
      })}
    </div>
  );
}

export const MeetingStatus = ({ m }) => <Badge tone={MEETING_TONE[m.status] || 'neutral'}>{m.status_label}</Badge>;

// ---------------------------------------------------------------- local-date helpers (dates are YYYY-MM-DD strings)
export const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
export const parseYmd = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
export const addDaysTo = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
export const mondayOf = (d) => addDaysTo(d, -((d.getDay() + 6) % 7));

export { asList };
