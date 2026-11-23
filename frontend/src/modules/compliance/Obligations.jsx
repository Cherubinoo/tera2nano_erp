import { useMemo, useState } from 'react';
import { api } from '../../api';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, useConfirm, useToast } from '../../ui';
import { Pick, opt, useCanManage, useMeta } from './common';

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

function blankRule(type) {
  if (type === 'monthly') return { type, day: 15, lag: 1 };
  if (type === 'event') return { type, days: 30 };
  return { type: 'dates', dates: [{ month: 4, day: 30, fy: 'cur', label: 'FY {fy}' }] };
}

function RuleEditor({ rule, onChange, error }) {
  const set = (patch) => onChange({ ...rule, ...patch });
  const setDate = (i, patch) => set({ dates: rule.dates.map((d, k) => (k === i ? { ...d, ...patch } : d)) });
  return (
    <fieldset className="field field-wide" aria-describedby={error ? 'rule-err' : undefined}>
      <legend>When it falls due</legend>
      <div className="form-grid">
        <Field id="rule-type" label="Rule type">
          <select id="rule-type" value={rule.type} onChange={(e) => onChange(blankRule(e.target.value))}>
            <option value="monthly">Every month</option><option value="dates">Fixed dates in the year</option><option value="event">Days after an event</option>
          </select>
        </Field>
        {rule.type === 'monthly' && <>
          <Field id="rule-day" label="Day of the month (1-31 or last)">
            <input id="rule-day" value={rule.day} onChange={(e) => set({ day: e.target.value === 'last' ? 'last' : e.target.value === '' ? '' : Number(e.target.value) || '' })} />
          </Field>
          <Field id="rule-lag" label="Months after the period" hint="1 = the 20th of next month for last month's return.">
            <input id="rule-lag" type="number" min="0" max="12" value={rule.lag} onChange={(e) => set({ lag: e.target.value === '' ? '' : Number(e.target.value) })} />
          </Field>
        </>}
        {rule.type === 'event' && (
          <Field id="rule-days" label="Days after the event"><input id="rule-days" type="number" min="0" max="365" value={rule.days} onChange={(e) => set({ days: e.target.value === '' ? '' : Number(e.target.value) })} /></Field>
        )}
      </div>
      {rule.type === 'dates' && (
        <div className="stack-gap">
          {rule.dates.map((d, i) => (
            <div className="form-grid compliance-rule-row" key={i}>
              <Field id={`rd-m-${i}`} label="Month"><select id={`rd-m-${i}`} value={d.month} onChange={(e) => setDate(i, { month: Number(e.target.value) })}>{MONTHS.map((m, k) => <option key={m} value={k + 1}>{m}</option>)}</select></Field>
              <Field id={`rd-d-${i}`} label="Day"><input id={`rd-d-${i}`} value={d.day} onChange={(e) => setDate(i, { day: e.target.value === 'last' ? 'last' : e.target.value === '' ? '' : Number(e.target.value) || '' })} /></Field>
              <Field id={`rd-f-${i}`} label="Covers"><select id={`rd-f-${i}`} value={d.fy} onChange={(e) => setDate(i, { fy: e.target.value })}><option value="prev">Previous financial year</option><option value="cur">Current financial year</option></select></Field>
              <Field id={`rd-l-${i}`} label="Label" hint="{fy} = the year, {ay} = assessment year"><input id={`rd-l-${i}`} value={d.label} maxLength={80} onChange={(e) => setDate(i, { label: e.target.value })} /></Field>
              {rule.dates.length > 1 && <button type="button" className="btn btn-small" onClick={() => set({ dates: rule.dates.filter((_, k) => k !== i) })}>Remove date</button>}
            </div>
          ))}
          <button type="button" className="btn btn-small" onClick={() => set({ dates: [...rule.dates, { month: 4, day: 30, fy: 'cur', label: 'FY {fy}' }] })}>Add another date</button>
        </div>
      )}
      {error && <div id="rule-err" className="error-text" role="alert">{error}</div>}
    </fieldset>
  );
}

function ObligationDialog({ record, meta, onClose, onSaved }) {
  const notify = useToast();
  const isEdit = !!record;
  const [v, setV] = useState(() => ({
    code: record?.code || '', name: record?.name || '', form_no: record?.form_no || '', category: record?.category || 'other', authority: record?.authority || '',
    frequency: record?.frequency || 'annual', due_rule: record?.due_rule?.type ? record.due_rule : blankRule('dates'), due_rule_text: record?.due_rule_text || '',
    penalty_rule: record?.penalty_rule || 'none', penalty_note: record?.penalty_note || '', description: record?.description || '',
    assignee_role: record?.assignee_role || '', default_priority: record?.default_priority || 'normal', reminder_days: (record?.reminder_days || [15, 7, 1]).join(', '),
  }));
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    setError(null);
    const local = {};
    if (!v.name.trim()) local.name = 'Enter the name.';
    if (!isEdit && !v.code.trim()) local.code = 'Enter a short code, such as GSTR-3B.';
    if (Object.keys(local).length) { setErrors(local); return; }
    setBusy(true);
    try {
      const body = { ...v, reminder_days: v.reminder_days };
      if (isEdit) delete body.code;
      const saved = isEdit ? await api.patch(`/compliance/obligations/${record.id}/`, body) : await api.post('/compliance/obligations/', body);
      notify(isEdit ? 'Obligation updated.' : 'Obligation added.');
      onSaved(saved);
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  const names = ['name', 'code', 'form_no', 'category', 'authority', 'frequency', 'due_rule', 'due_rule_text', 'penalty_rule', 'penalty_note', 'description', 'assignee_role', 'default_priority', 'reminder_days'].map((name) => ({ name }));
  return (
    <Dialog title={isEdit ? `Edit ${record.name}` : 'New obligation'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={names} />
        <div className="form-grid">
          <Field id="ob-name" label="Name" required error={errors.name} wide><input id="ob-name" value={v.name} maxLength={200} onChange={set('name')} /></Field>
          <Field id="ob-code" label="Code" required={!isEdit} error={errors.code} hint="A short unique code. Cannot be changed later."><input id="ob-code" value={v.code} maxLength={40} disabled={isEdit} onChange={set('code')} /></Field>
          <Field id="ob-form" label="Form number" error={errors.form_no}><input id="ob-form" value={v.form_no} maxLength={60} onChange={set('form_no')} /></Field>
          <Field id="ob-cat" label="Category" error={errors.category}><select id="ob-cat" value={v.category} onChange={set('category')}>{meta.categories.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</select></Field>
          <Field id="ob-auth" label="Authority" error={errors.authority}><input id="ob-auth" value={v.authority} maxLength={160} onChange={set('authority')} /></Field>
          <Field id="ob-freq" label="Frequency" error={errors.frequency}>
            <select id="ob-freq" value={v.frequency} onChange={(e) => { const f = e.target.value; setV((x) => ({ ...x, frequency: f, due_rule: f === 'event' ? blankRule('event') : x.due_rule.type === 'event' ? blankRule('dates') : x.due_rule })); }}>
              {meta.frequencies.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </Field>
          <RuleEditor rule={v.due_rule} onChange={(r) => { setV((x) => ({ ...x, due_rule: r, frequency: r.type === 'event' ? 'event' : x.frequency === 'event' ? 'annual' : x.frequency })); setErrors((x) => ({ ...x, due_rule: undefined })); }} error={errors.due_rule} />
          <Field id="ob-rtext" label="Due date in words" error={errors.due_rule_text} wide hint="Shown in the catalogue and on each item."><input id="ob-rtext" value={v.due_rule_text} maxLength={200} onChange={set('due_rule_text')} /></Field>
          <Field id="ob-pr" label="Late fee estimate" error={errors.penalty_rule}><select id="ob-pr" value={v.penalty_rule} onChange={set('penalty_rule')}>{meta.penalty_rules.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</select></Field>
          <Field id="ob-pn" label="Penalty note" error={errors.penalty_note} wide><input id="ob-pn" value={v.penalty_note} maxLength={300} onChange={set('penalty_note')} /></Field>
          <Field id="ob-role" label="Default owner (role)" error={errors.assignee_role} hint="New items go to the longest-serving active user with this role."><select id="ob-role" value={v.assignee_role} onChange={set('assignee_role')}><option value="">Nobody</option>{meta.roles.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</select></Field>
          <Field id="ob-prio" label="Default priority" error={errors.default_priority}><select id="ob-prio" value={v.default_priority} onChange={set('default_priority')}>{meta.priorities.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</select></Field>
          <Field id="ob-rem" label="Reminders (days before)" error={errors.reminder_days}><input id="ob-rem" value={v.reminder_days} onChange={set('reminder_days')} /></Field>
          <Field id="ob-desc" label="Description" error={errors.description} wide><textarea id="ob-desc" rows={2} value={v.description} onChange={set('description')} /></Field>
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export default function ObligationsPage() {
  const canManage = useCanManage();
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const meta = useMeta();
  const [category, setCategory] = useState('');
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const [state, setState] = useState('');
  const [edit, setEdit] = useState(null);
  const { data, error, loading, reload } = useLoad(() => api.get('/compliance/obligations/', { category: category || undefined, q: term || undefined, enabled: state || undefined }), [category, term, state]);
  const rows = data?.results || [];
  const toggle = async (o) => {
    try { await api.patch(`/compliance/obligations/${o.id}/`, { enabled: !o.enabled }); notify(`${o.name} switched ${o.enabled ? 'off' : 'on'}. Regenerate the calendar to apply it.`); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const remove = async (o) => {
    if (!(await confirm({ title: `Delete ${o.name}?`, body: <p>Only obligations you added, with no calendar items, can be deleted.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/compliance/obligations/${o.id}/`); notify('Deleted.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const grouped = useMemo(() => rows.reduce((m, o) => { (m[o.category_label] = m[o.category_label] || []).push(o); return m; }, {}), [rows]);

  return (
    <>
      <PageHeader title="Obligations" description="The catalogue of statutory duties for an Indian LLP. Edit dates, owners and reminders, or switch off what does not apply to you."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setEdit({})}>New obligation</button>} />
      <section className="card">
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
          {meta.data && <Pick id="ob-f-cat" label="Category" value={category} onChange={setCategory} options={opt(meta.data.categories)} />}
          <Pick id="ob-f-en" label="Switched" value={state} onChange={setState} options={[{ value: 'true', label: 'On' }, { value: 'false', label: 'Off' }]} />
          <Field id="ob-f-q" label="Search" className="grow"><input id="ob-f-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, form, code or authority" /></Field>
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && rows.length === 0 && <p className="empty">No obligations match.</p>}
        {Object.entries(grouped).map(([cat, list]) => (
          <div key={cat}>
            <h2>{cat}</h2>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Obligation</th><th>Form</th><th>Frequency</th><th>Due</th><th>Owner role</th><th>Penalty</th><th>On</th><th><span className="sr-only">Actions</span></th></tr></thead>
                <tbody>
                  {list.map((o) => (
                    <tr key={o.id} className={o.enabled ? '' : 'row-inactive'}>
                      <td data-label="Obligation"><strong>{o.name}</strong><div className="hint">{o.authority}{o.is_seed ? '' : ' · added by you'}</div></td>
                      <td data-label="Form">{o.form_no || '—'}</td>
                      <td data-label="Frequency">{o.frequency_label}</td>
                      <td data-label="Due">{o.due_rule_text || '—'}</td>
                      <td data-label="Owner role">{o.assignee_role_label || '—'}</td>
                      <td data-label="Penalty">{o.penalty_note || '—'}</td>
                      <td data-label="On">{canManage
                        ? <label className="check"><input type="checkbox" checked={o.enabled} onChange={() => toggle(o)} aria-label={`${o.name} is switched ${o.enabled ? 'on' : 'off'}`} /></label>
                        : <Badge tone={o.enabled ? 'good' : 'neutral'}>{o.enabled ? 'On' : 'Off'}</Badge>}</td>
                      <td className="row-actions">
                        {canManage && <button type="button" className="btn btn-small" onClick={() => setEdit(o)}>Edit</button>}
                        {canManage && !o.is_seed && o.items === 0 && <button type="button" className="btn btn-small" onClick={() => remove(o)}>Delete</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
        <p className="hint">After changing a date rule or switching an obligation on, open Filings and use "Generate calendar": open items follow the new rule, filed items are never changed.</p>
      </section>
      {edit && meta.data && <ObligationDialog record={edit.id ? edit : null} meta={meta.data} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); }} />}
      {dialog}
    </>
  );
}
