import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { useAuth } from '../../context';
import { money, today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, SpecForm, fmtDate, fmtDateTime, useReasonDialog, useSpecForm, useToast } from '../../ui';

export const useCanManage = () => (useAuth().user.permissions || []).includes('compliance.manage');

// Options (categories, roles ...) and the people a task can be given to.
export function useMeta() {
  return useLoad(() => Promise.all([api.get('/compliance/options/'), api.get('/compliance/assignees/')]).then(([o, a]) => ({ ...o, assignees: a })), []);
}

export const STATUS_TONE = { upcoming: 'info', due_soon: 'warn', overdue: 'bad', filed: 'good', not_applicable: 'neutral' };
export const STATUS_LABEL = { upcoming: 'Upcoming', due_soon: 'Due soon', overdue: 'Overdue', filed: 'Filed', not_applicable: 'Not applicable' };
export const STATUS_COLOUR = {
  upcoming: 'var(--chart-1)', due_soon: 'var(--chart-3)', overdue: 'var(--chart-4)', filed: 'var(--chart-2)', not_applicable: 'var(--chart-grey)',
};
export const CATEGORY_COLOUR = {
  mca: 'var(--chart-1)', income_tax: 'var(--chart-2)', gst: 'var(--chart-3)', tds: 'var(--chart-4)', labour: 'var(--chart-5)', local: 'var(--chart-grey)', other: '#64748b',
};
export const PRIORITY_TONE = { critical: 'bad', high: 'warn', normal: 'neutral', low: 'neutral' };

export const StatusBadge = ({ item }) => <Badge tone={STATUS_TONE[item.status_key] || 'neutral'}>{item.status_label || STATUS_LABEL[item.status_key]}</Badge>;
export const ToneBadge = ({ tone, children }) => <Badge tone={{ overdue: 'bad', due_soon: 'warn', upcoming: 'info' }[tone] || 'neutral'}>{children}</Badge>;

export function daysText(n) {
  if (n === null || n === undefined) return '';
  if (n === 0) return 'today';
  if (n === 1) return 'tomorrow';
  if (n === -1) return '1 day overdue';
  return n > 0 ? `in ${n} days` : `${-n} days overdue`;
}

export const Tile = ({ label, value, hint, tone, to }) => {
  const inner = (<><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</>);
  return to ? <Link className={`tile ${tone || ''}`} to={to}>{inner}</Link> : <div className={`tile ${tone || ''}`}>{inner}</div>;
};

export const opt = (rows) => (rows || []).map((r) => ({ value: r.value ?? r.id, label: r.label ?? r.name }));

// Soft cross-module lookups: a failure or an empty answer just gives an empty list.
export function useSoftList(path, map) {
  const [rows, setRows] = useState([]);
  useEffect(() => {
    let alive = true;
    api.get(path).then((d) => alive && setRows((Array.isArray(d) ? d : d?.results || []).map(map))).catch(() => alive && setRows([]));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);
  return rows;
}

// A dialog around a spec-driven form that shows server errors on the fields.
export function RecordDialog({ title, fields, record, defaults, onSave, onClose, saveLabel = 'Save', wide = true, ctx, children, afterFields, onValuesChange }) {
  const form = useSpecForm(fields, record, defaults);
  useEffect(() => { if (onValuesChange) onValuesChange(form.values); }, [form.values]); // eslint-disable-line react-hooks/exhaustive-deps
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setError(null);
    if (!form.validate()) return;
    setBusy(true);
    try { await onSave(form.payload(false), form.values); } catch (err) { setError(err); form.setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title={title} onClose={onClose} wide={wide}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={fields} />
        {children}
        <SpecForm fields={fields} values={form.values} onChange={form.set} errors={form.errors} ctx={ctx} idPrefix="cf" />
        {afterFields && afterFields(form.values)}
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : saveLabel}</button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------------------- mark filed
const FILE_FIELDS = (rule) => [
  { name: 'filed_date', label: 'Filed on', type: 'date', required: true, max: today() },
  { name: 'ack_number', label: 'SRN / ARN / acknowledgement no.', type: 'text', maxLength: 80, hint: 'From the portal after filing.' },
  { name: 'fee_paid', label: 'Filing fee paid (Rs)', type: 'number', min: 0, step: '0.01' },
  { name: 'penalty_paid', label: 'Late fee / penalty paid (Rs)', type: 'number', min: 0, step: '0.01', hint: rule ? 'See the estimate above.' : undefined },
  { name: 'remarks', label: 'Remarks', type: 'textarea', wide: true, maxLength: 2000 },
];

export function FileDialog({ item, onClose, onDone }) {
  const notify = useToast();
  const fields = useMemo(() => FILE_FIELDS(item.penalty_rule !== 'none'), [item.penalty_rule]);
  const defaults = useMemo(() => ({ filed_date: today(), ack_number: item.ack_number, remarks: item.remarks, fee_paid: item.fee_paid ?? '', penalty_paid: item.penalty_paid ?? '' }), [item]);
  const [filed, setFiled] = useState(defaults.filed_date);
  const [est, setEst] = useState(null);
  useEffect(() => {
    if (item.penalty_rule === 'none' || !filed || filed <= item.due_date) { setEst(null); return undefined; }
    let alive = true;
    api.post('/compliance/penalty/estimate/', { item: item.id, filed_date: filed }).then((r) => alive && setEst(r)).catch(() => alive && setEst(null));
    return () => { alive = false; };
  }, [item, filed]);
  return (
    <RecordDialog title={`Mark filed: ${item.title}`} fields={fields} defaults={defaults} wide={false} saveLabel="Mark as filed" onClose={onClose}
      onSave={async (payload) => { const r = await api.post(`/compliance/items/${item.id}/file/`, payload); notify(`Marked as filed. Reference ${r.reference}.`); onDone(r); }}
      onValuesChange={(v) => setFiled(v.filed_date)}>
      <p className="muted">{item.period_label} · due {fmtDate(item.due_date)}. You can attach the acknowledgement once it is saved.</p>
      {est && est.days_late > 0 && (
        <div className="notice notice-warn" role="status">
          Filed {est.days_late} day{est.days_late === 1 ? '' : 's'} after the due date. Estimated late fee: <strong>{money(est.amount)}</strong>
          <div className="hint">{est.formula}. An estimate only; check the portal.</div>
        </div>
      )}
    </RecordDialog>
  );
}

// ---------------------------------------------------------------------------------------------------- new task
export function NewTaskDialog({ meta, onClose, onDone, obligations }) {
  const notify = useToast();
  const events = (obligations || []).filter((o) => o.is_event);
  const [kind, setKind] = useState('task');
  const fields = useMemo(() => (kind === 'task' ? [
    { name: 'title', label: 'Task', type: 'text', required: true, maxLength: 200, wide: true },
    { name: 'due_date', label: 'Due date', type: 'date', required: true },
    { name: 'category', label: 'Category', type: 'select', required: true, options: opt(meta.categories) },
    { name: 'form_no', label: 'Form / reference', type: 'text', maxLength: 60 },
    { name: 'priority', label: 'Priority', type: 'select', required: true, options: opt(meta.priorities) },
    { name: 'assignee', label: 'Assigned to', type: 'select', options: opt(meta.assignees) },
    { name: 'notes', label: 'Notes', type: 'textarea', wide: true, maxLength: 2000 },
  ] : [
    { name: 'obligation', label: 'Filing', type: 'select', required: true, options: events.map((o) => ({ value: o.id, label: `${o.form_no}: ${o.name}` })) },
    { name: 'event_date', label: 'Date of the event', type: 'date', required: true, max: today(), hint: 'The due date is worked out from this (30 days after).' },
    { name: 'assignee', label: 'Assigned to', type: 'select', options: opt(meta.assignees) },
  ]), [kind, meta, events]); // eslint-disable-line react-hooks/exhaustive-deps
  const defaults = useMemo(() => ({ category: 'other', priority: 'normal' }), []);
  return (
    <RecordDialog key={kind} title="New compliance task" fields={fields} defaults={defaults} wide={false} saveLabel="Add task" onClose={onClose}
      onSave={async (payload) => { const r = await api.post('/compliance/items/', payload); notify('Task added.'); onDone(r); }}>
      <div className="chips" role="group" aria-label="Kind of task">
        <button type="button" className="chip" aria-pressed={kind === 'task'} onClick={() => setKind('task')}>One-off task</button>
        <button type="button" className="chip" aria-pressed={kind === 'event'} onClick={() => setKind('event')}>Event filing (Form 3, 4, 5 ...)</button>
      </div>
    </RecordDialog>
  );
}

// ---------------------------------------------------------------------------------------------------- one item
export function ItemDialog({ id, meta, onClose, onChanged }) {
  const canManage = useCanManage();
  const notify = useToast();
  const { data: item, error, loading, reload } = useLoad(() => api.get(`/compliance/items/${id}/`), [id]);
  const { ask, dialog: reasonDialog } = useReasonDialog();
  const [filing, setFiling] = useState(false);
  const [editing, setEditing] = useState(false);
  const changed = () => { reload(); onChanged && onChanged(); };
  const act = async (path, body, ok) => { try { await api.post(`/compliance/items/${id}/${path}/`, body); notify(ok); changed(); } catch (e) { notify(e.message, 'error'); } };
  const na = async () => {
    const reason = await ask({ title: 'Not applicable', label: 'Why does this not apply?', confirmLabel: 'Mark not applicable', message: 'It stays in the calendar as closed, with your reason.' });
    if (reason) act('not-applicable', { reason }, 'Marked not applicable.');
  };
  const reopen = async () => {
    const reason = await ask({ title: 'Reopen', label: 'Why is it being reopened?', confirmLabel: 'Reopen' });
    if (reason) act('reopen', { reason }, 'Reopened.');
  };

  const fields = useMemo(() => [
    { name: 'assignee', label: 'Assigned to', type: 'select', options: opt(meta?.assignees) },
    { name: 'priority', label: 'Priority', type: 'select', required: true, options: opt(meta?.priorities) },
    { name: 'due_date', label: 'Due date', type: 'date', required: true, hint: 'Change this only for an extension granted by the department.' },
    { name: 'reminder_days', label: 'Remind me (days before, comma separated)', type: 'text', maxLength: 40 },
    { name: 'notes', label: 'Notes', type: 'textarea', wide: true, maxLength: 2000 },
  ], [meta]);
  const record = useMemo(() => item && ({ ...item, assignee: item.assignee?.id || '', reminder_days: (item.reminder_days || []).join(', ') }), [item]);

  return (
    <Dialog title={item ? item.title : 'Compliance item'} onClose={onClose} wide>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {item && (
        <div className="stack-gap">
          <div className="compliance-item-head">
            <StatusBadge item={item} />
            {item.status_key !== 'filed' && item.status_key !== 'not_applicable' && <span className="muted">{daysText(item.days_to_due)}</span>}
            <Badge tone={PRIORITY_TONE[item.priority]}>{item.priority} priority</Badge>
            {item.reference && <span className="muted">Ref {item.reference}</span>}
          </div>
          <dl className="summary">
            <div><dt>Period</dt><dd>{item.period_label || '—'}</dd></div>
            <div><dt>Form</dt><dd>{item.form_no || '—'}</dd></div>
            <div><dt>Category</dt><dd>{item.category_label}</dd></div>
            <div><dt>Due date</dt><dd>{fmtDate(item.due_date)}{item.due_date_overridden && <span className="hint"> moved by hand</span>}</dd></div>
            <div><dt>Assigned to</dt><dd>{item.assignee?.name || 'Nobody yet'}</dd></div>
            <div><dt>Remind</dt><dd>{(item.reminder_days || []).map((d) => `${d}d`).join(', ') || '—'} before</dd></div>
            {item.status === 'filed' && <>
              <div><dt>Filed on</dt><dd>{fmtDate(item.filed_date)} {item.on_time ? <Badge tone="good">On time</Badge> : <Badge tone="warn">{item.days_late} days late</Badge>}</dd></div>
              <div><dt>SRN / ARN / ack. no.</dt><dd>{item.ack_number || '—'}</dd></div>
              <div><dt>Fee paid</dt><dd>{item.fee_paid ? money(item.fee_paid) : '—'}</dd></div>
              <div><dt>Late fee / penalty paid</dt><dd>{item.penalty_paid ? money(item.penalty_paid) : '—'}</dd></div>
            </>}
            {item.status === 'not_applicable' && <div><dt>Reason</dt><dd>{item.na_reason}</dd></div>}
          </dl>
          {item.estimated_penalty && item.status !== 'not_applicable' && (
            <div className="notice notice-warn">{item.status === 'filed' ? 'Late by' : 'Overdue by'} {item.days_late} days. Estimated late fee {money(item.estimated_penalty)} (an estimate).</div>
          )}
          {item.obligation && (
            <p className="muted">{item.obligation.name}. {item.obligation.due_rule_text}. {item.obligation.authority && <>Authority: {item.obligation.authority}. </>}{item.obligation.penalty_note}</p>
          )}
          {item.remarks && <p><strong>Remarks:</strong> {item.remarks}</p>}
          {canManage && (
            <div className="actions">
              {item.status === 'open' && <button type="button" className="btn btn-primary" onClick={() => setFiling(true)}>Mark filed</button>}
              {item.status === 'open' && <button type="button" className="btn" onClick={na}>Not applicable</button>}
              {item.status !== 'open' && <button type="button" className="btn" onClick={reopen}>Reopen</button>}
              <button type="button" className="btn" onClick={() => setEditing(true)}>Reassign or change dates</button>
            </div>
          )}
          <Attachments entityType="compliance_filing" entityId={item.id} canManage={canManage} title="Acknowledgement and documents"
            hint="Attach the filing acknowledgement, challan or any working paper." />
          <section className="card" aria-label="History">
            <h2>History</h2>
            {item.history.length === 0 ? <p className="empty">No changes recorded.</p> : (
              <ul className="compliance-history">
                {item.history.map((h, i) => (
                  <li key={i}><strong>{h.action}</strong> {h.changed.length > 0 && <span className="muted">({h.changed.join(', ').replace(/_/g, ' ')})</span>} by {h.who || 'system'} · {fmtDateTime(h.at)}{h.reason && <div className="hint">Reason: {h.reason}</div>}</li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
      {filing && <FileDialog item={item} onClose={() => setFiling(false)} onDone={() => { setFiling(false); changed(); }} />}
      {editing && (
        <RecordDialog title="Reassign or change dates" fields={fields} record={record} wide={false} onClose={() => setEditing(false)}
          onSave={async (payload) => {
            const body = { ...payload, reminder_days: String(payload.reminder_days || '') };
            if (!body.reminder_days) delete body.reminder_days;
            await api.patch(`/compliance/items/${id}/`, body);
            notify('Saved.'); setEditing(false); changed();
          }} />
      )}
      {reasonDialog}
    </Dialog>
  );
}

// Simple labelled select used in toolbars.
export function Pick({ id, label, value, onChange, options, blank = 'All' }) {
  return (
    <Field id={id} label={label}>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{blank}</option>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </Field>
  );
}

export const csvDate = (d) => d || '';
