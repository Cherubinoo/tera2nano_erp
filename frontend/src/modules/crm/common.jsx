import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { compact, num, today } from '../../format';
import { useDebounced, useLoad } from '../../hooks';
import { Badge, Dialog, Field, FormErrors, fmtDate, fmtDateTime, useToast } from '../../ui';

export const LEAD_TONE = { new: 'info', contacted: 'info', qualified: 'good', nurturing: 'warn', unqualified: 'neutral', converted: 'good' };
export const RATING_TONE = { hot: 'bad', warm: 'warn', cold: 'info' };
export const PRIORITY_TONE = { low: 'neutral', normal: 'info', high: 'bad' };
export const DEAL_TONE = { open: 'info', won: 'good', lost: 'bad' };
const PLURAL = { lead: 'leads', contact: 'contacts', account: 'accounts', deal: 'deals' };

export const relatedPath = (r) => (r ? `/crm/${PLURAL[r.type]}/${r.id}` : '#');
export const initials = (name) => (name || '?').split(/\s+/).slice(0, 2).map((p) => p[0]).join('').toUpperCase();

export const LeadStatus = ({ lead }) => <Badge tone={LEAD_TONE[lead.status] || 'neutral'}>{lead.status_label}</Badge>;
export const RatingBadge = ({ lead }) => (lead.rating ? <Badge tone={RATING_TONE[lead.rating] || 'neutral'}>{lead.rating_label}</Badge> : <span className="muted">—</span>);
export const DealStatus = ({ deal }) => (
  <span className="crm-stage-pill" style={{ '--crm-stage': deal.stage.colour }}>{deal.stage.name}</span>
);

export function Tile({ label, value, hint, tone, to }) {
  const inner = (<><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</>);
  return to ? <Link className={`tile ${tone || ''}`} to={to}>{inner}</Link> : <div className={`tile ${tone || ''}`}>{inner}</div>;
}

export const query = (obj) => {
  const sp = new URLSearchParams();
  Object.entries(obj || {}).forEach(([k, v]) => { if (v !== '' && v != null) sp.set(k, v); });
  const s = sp.toString();
  return s ? `?${s}` : '';
};

export const ownerName = (o) => (o ? o.name : 'Unassigned');

export function useLookups() {
  const { data, error, reload } = useLoad(() => api.get('/crm/lookups/'), []);
  return useMemo(() => ({ ...(data || {}), ready: !!data, error, reload }), [data, error, reload]);
}

export function OwnerSelect({ id, label = 'Owner', value, onChange, lookups, error, any, anyLabel = 'Anyone', extra = [], className }) {
  return (
    <Field id={id} label={label} error={error} className={className}>
      <select id={id} value={value ?? ''} onChange={(e) => onChange(e.target.value)}>
        <option value="">{any ? anyLabel : 'Unassigned'}</option>
        {extra.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        {(lookups.owners || []).map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
      </select>
    </Field>
  );
}

export function Pager({ page, count, size, onPage }) {
  const pages = Math.max(1, Math.ceil(count / size));
  if (pages <= 1) return null;
  return (
    <nav className="pager" aria-label="Pages">
      <button type="button" className="btn btn-small" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      <span>Page {page} of {pages}</span>
      <button type="button" className="btn btn-small" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </nav>
  );
}

/** Search box plus a list of matches for picking an account, contact, lead or deal. */
export function EntityPicker({ id, label, kind, value, valueLabel, onChange, error, required, params, hint, blank = 'None' }) {
  const [term, setTerm] = useState('');
  const debounced = useDebounced(term, 250);
  const [options, setOptions] = useState([]);
  const key = JSON.stringify(params || {});
  useEffect(() => {
    let alive = true;
    api.get(`/crm/${kind}/`, { ...(params || {}), q: debounced || undefined, page_size: 30 })
      .then((d) => alive && setOptions((d.results || []).map((r) => ({ id: r.id, name: r.name || r.display_name }))))
      .catch(() => alive && setOptions([]));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, debounced, key]);
  const all = value && !options.some((o) => o.id === value) ? [{ id: value, name: valueLabel || 'Selected' }, ...options] : options;
  return (
    <Field id={id} label={label} error={error} required={required} hint={hint}>
      <input type="search" aria-label={`Search ${label}`} placeholder="Type to search…" value={term} onChange={(e) => setTerm(e.target.value)} />
      <select id={id} value={value || ''} onChange={(e) => { const o = all.find((x) => x.id === e.target.value); onChange(e.target.value, o ? o.name : ''); }}>
        <option value="">{required ? 'Choose…' : blank}</option>
        {all.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
      </select>
    </Field>
  );
}

// ---------------------------------------------------------------------------------------------- charts

/** SVG funnel: each step is a centred bar as wide as its count, with the step-to-step conversion beneath. */
export function Funnel({ steps, format = compact }) {
  const W = 520, rowH = 54, gap = 8, H = steps.length * (rowH + gap);
  const max = Math.max(...steps.map((s) => num(s.count)), 1);
  const colours = ['#3a3d44', '#8a6618', '#a0603a', '#0f766e'];
  const summary = steps.map((s, i) => {
    const prev = i ? num(steps[i - 1].count) : 0;
    return `${s.label}: ${s.count}${s.value != null ? `, worth ${format(s.value)}` : ''}${i && prev ? `, ${Math.round((num(s.count) * 100) / prev)}% of ${steps[i - 1].label}` : ''}`;
  }).join('; ');
  return (
    <figure className="chart crm-funnel">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Sales funnel. ${summary}`} preserveAspectRatio="xMidYMid meet">
        {steps.map((s, i) => {
          const w = Math.max((num(s.count) / max) * (W - 40), 190);
          const next = steps[i + 1];
          const wNext = next ? Math.max((num(next.count) / max) * (W - 40), 190) : w * 0.85;
          const y = i * (rowH + gap), x = (W - w) / 2, xn = (W - wNext) / 2;
          const prev = i ? num(steps[i - 1].count) : 0;
          return (
            <g key={s.key}>
              <polygon points={`${x},${y} ${x + w},${y} ${xn + wNext},${y + rowH} ${xn},${y + rowH}`} fill={colours[i % colours.length]} opacity="0.92">
                <title>{`${s.label}: ${s.count}`}</title>
              </polygon>
              <text x={W / 2} y={y + 23} textAnchor="middle" className="crm-funnel-label">{s.label}</text>
              <text x={W / 2} y={y + 42} textAnchor="middle" className="crm-funnel-count">
                {s.count}{s.value != null && num(s.value) > 0 ? ` · ${format(s.value)}` : ''}{i > 0 && prev > 0 ? ` · ${Math.round((num(s.count) * 100) / prev)}%` : ''}
              </text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

// ---------------------------------------------------------------------------------------------- activities

export function CompleteDialog({ activity, onClose, onDone }) {
  const notify = useToast();
  const [outcome, setOutcome] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    setBusy(true); setError(null);
    try { await api.post(`/crm/activities/${activity.id}/complete/`, { outcome: outcome.trim() }); notify('Activity completed.'); onDone(); } catch (err) { setError(err); }
    setBusy(false);
  };
  return (
    <Dialog title={`Complete: ${activity.subject}`} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[{ name: 'outcome' }]} />
        <Field id="act-outcome" label="Outcome" hint="What happened? Optional, shown in the timeline." error={error?.fieldErrors?.outcome}>
          <textarea id="act-outcome" rows={3} maxLength={500} value={outcome} onChange={(e) => setOutcome(e.target.value)} />
        </Field>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Mark completed'}</button>
        </div>
      </form>
    </Dialog>
  );
}

const RELATED_KINDS = [['lead', 'Lead', 'leads'], ['contact', 'Contact', 'contacts'], ['account', 'Account', 'accounts'], ['deal', 'Deal', 'deals']];

/** Create or edit an activity. `links` ({lead, contact, account, deal} ids) fixes what it is attached to. */
export function ActivityDialog({ record, links, relatedLabel, lookups, onClose, onSaved }) {
  const notify = useToast();
  const [v, setV] = useState({
    type: record?.type || 'task', subject: record?.subject || '', description: record?.description || '', due_date: record?.due_date || (record ? '' : today()),
    due_time: record?.due_time || '', priority: record?.priority || 'normal', owner: record?.owner?.id || '',
  });
  const [kind, setKind] = useState('lead');
  const [target, setTarget] = useState({ id: '', name: '' });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const fixed = !!record || !!links;
  const submit = async (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    setFailure(null);
    const e = {};
    if (!v.subject.trim()) e.subject = 'Enter what the activity is about.';
    if (!fixed && !target.id) e.related_to = 'Choose the lead, contact, account or deal it belongs to.';
    if (v.type === 'task' && !v.due_date) e.due_date = 'Give the task a due date.';
    setErrors(e);
    if (Object.keys(e).length) return;
    setBusy(true);
    const body = { ...v, subject: v.subject.trim(), due_date: v.due_date || null, due_time: v.due_time || null, owner: v.owner || null };
    if (!record) Object.assign(body, links || { [kind]: target.id });
    try {
      const saved = record ? await api.patch(`/crm/activities/${record.id}/`, body) : await api.post('/crm/activities/', body);
      notify(record ? 'Activity saved.' : 'Activity added.');
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title={record ? 'Edit activity' : 'New activity'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={[...Object.keys(v), 'related_to', 'lead', 'contact', 'account', 'deal'].map((name) => ({ name }))} />
        <div className="form-grid">
          <Field id="act-type" label="Type" error={errors.type}>
            <select id="act-type" value={v.type} onChange={set('type')}>{(lookups.activity_types || []).map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select>
          </Field>
          <Field id="act-subject" label="Subject" required error={errors.subject}><input id="act-subject" value={v.subject} maxLength={200} onChange={set('subject')} /></Field>
          <Field id="act-due" label={v.type === 'note' ? 'Date' : 'Due date'} error={errors.due_date} required={v.type === 'task'}><input id="act-due" type="date" value={v.due_date} onChange={set('due_date')} /></Field>
          <Field id="act-time" label="Time" error={errors.due_time}><input id="act-time" type="time" value={v.due_time} onChange={set('due_time')} /></Field>
          <Field id="act-priority" label="Priority" error={errors.priority}>
            <select id="act-priority" value={v.priority} onChange={set('priority')}>{(lookups.priorities || []).map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select>
          </Field>
          <OwnerSelect id="act-owner" label="Owner" value={v.owner} onChange={(x) => setV((s) => ({ ...s, owner: x }))} lookups={lookups} error={errors.owner} />
          {record && <Field id="act-rel" label="Related to"><div>{record.related ? <Link to={relatedPath(record.related)}>{record.related.name}</Link> : '—'}</div></Field>}
          {!record && links && <Field id="act-rel" label="Related to"><div><strong>{relatedLabel || 'This record'}</strong></div></Field>}
          {!fixed && (
            <>
              <Field id="act-kind" label="Related to (type)">
                <select id="act-kind" value={kind} onChange={(e) => { setKind(e.target.value); setTarget({ id: '', name: '' }); }}>
                  {RELATED_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
              </Field>
              <EntityPicker key={kind} id="act-target" label={RELATED_KINDS.find((r) => r[0] === kind)[1]} kind={RELATED_KINDS.find((r) => r[0] === kind)[2]}
                value={target.id} valueLabel={target.name} onChange={(id, name) => { setTarget({ id, name }); setErrors((x) => ({ ...x, related_to: undefined })); }}
                error={errors.related_to} required />
            </>
          )}
        </div>
        <Field id="act-desc" label="Details" wide error={errors.description}><textarea id="act-desc" rows={3} value={v.description} onChange={set('description')} /></Field>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save activity'}</button>
        </div>
      </form>
    </Dialog>
  );
}

/** Activities and change history of one record, newest first. */
export function TimelineList({ items, canManage, onChanged }) {
  const notify = useToast();
  const [completing, setCompleting] = useState(null);
  const [editing, setEditing] = useState(null);
  const lookups = useLookups();
  const reopen = async (a) => {
    try { await api.post(`/crm/activities/${a.id}/reopen/`, {}); onChanged(); } catch (e) { notify(e.message, 'error'); }
  };
  if (!items.length) return <p className="empty">Nothing here yet.</p>;
  return (
    <>
      <ul className="crm-timeline" aria-label="Timeline">
        {items.map((it) => (
          <li key={`${it.kind}-${it.id}`} className={`crm-tl-${it.kind}`}>
            <span className="crm-tl-dot" aria-hidden="true" />
            {it.kind === 'activity' ? (
              <div className="crm-tl-body">
                <div className="crm-tl-head">
                  <Badge tone={it.status === 'completed' ? 'good' : it.bucket === 'overdue' ? 'bad' : 'info'}>{it.type_label}</Badge>
                  <strong>{it.subject}</strong>
                  {it.status === 'open' && it.bucket === 'overdue' && <Badge tone="bad">Overdue</Badge>}
                  {it.status === 'completed' && <span className="muted">completed {fmtDateTime(it.completed_at)}</span>}
                  {it.status === 'open' && it.due_date && <span className="muted">due {fmtDate(it.due_date)}{it.due_time ? ` ${it.due_time}` : ''}</span>}
                </div>
                {it.description && <p className="crm-tl-text">{it.description}</p>}
                {it.outcome && <p className="crm-tl-text"><em>Outcome:</em> {it.outcome}</p>}
                <div className="crm-tl-foot muted">{it.owner ? it.owner.name : 'Unassigned'} · {fmtDateTime(it.at)}
                  {canManage && it.type !== 'note' && (it.status === 'open'
                    ? <button type="button" className="btn btn-small" onClick={() => setCompleting(it)}>Complete</button>
                    : <button type="button" className="btn btn-small" onClick={() => reopen(it)}>Reopen</button>)}
                  {canManage && <button type="button" className="btn btn-small" onClick={() => setEditing(it)}>Edit</button>}
                </div>
              </div>
            ) : (
              <div className="crm-tl-body">
                <div className="crm-tl-head"><strong>{it.title}</strong>{it.detail && <span className="muted">{it.detail}</span>}</div>
                <div className="crm-tl-foot muted">{it.by || 'System'} · {fmtDateTime(it.at)}</div>
              </div>
            )}
          </li>
        ))}
      </ul>
      {completing && <CompleteDialog activity={completing} onClose={() => setCompleting(null)} onDone={() => { setCompleting(null); onChanged(); }} />}
      {editing && <ActivityDialog record={editing} lookups={lookups} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); onChanged(); }} />}
    </>
  );
}

/** Where "Create quotation" takes the person: the company quotation page, filled from the deal through the query string. */
export function quotationUrl(deal) {
  const name = deal.customer?.name || deal.account?.name || '';
  return `/company/quotations${query({
    new: 1, customer: name, subject: deal.name, amount: deal.amount, source_module: 'crm', source_id: deal.id, deal: deal.reference_code,
    customer_id: deal.customer_id || '' })}`;
}

export const dateOrDash = (d) => (d ? fmtDate(d) : '—');
