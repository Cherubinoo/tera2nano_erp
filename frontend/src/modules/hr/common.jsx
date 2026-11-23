import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, FormErrors, Loading, Notice, SpecForm, useSpecForm } from '../../ui';

export const H = '/hr';

export const useData = (path, params, enabled = true) => useLoad(
  () => (enabled ? api.get(path, params) : Promise.resolve(null)), [path, JSON.stringify(params || {}), enabled]);

export const useCan = () => {
  const { user } = useAuth();
  return (code) => (user.permissions || []).includes(code);
};

// The signed-in user's own employee record (or {linked: false}).
export const useMe = () => useData(`${H}/me/`);

const TONE = {
  active: 'good', on_notice: 'warn', exited: 'neutral', approved: 'good', paid: 'good', posted: 'good', completed: 'good', achieved: 'good',
  present: 'good', wfh: 'info', pending_approval: 'warn', pending: 'warn', self_done: 'warn', locked: 'info', draft: 'neutral', rejected: 'bad',
  cancelled: 'neutral', void: 'neutral', absent: 'bad', missed: 'bad', open: 'info', closed: 'neutral', in_progress: 'info', half_day: 'warn',
  leave: 'info', holiday: 'neutral', not_started: 'neutral',
};
export const StatusBadge = ({ status, label }) => (
  <Badge tone={TONE[status] || 'neutral'}>{label || String(status || '').replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase())}</Badge>
);

export function Tile({ label, value, hint, tone, to }) {
  const inner = (<><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</>);
  return to ? <Link className={`tile ${tone || ''}`} to={to}>{inner}</Link> : <div className={`tile ${tone || ''}`}>{inner}</div>;
}

export const NotLinked = () => (
  <Notice type="info">Your sign-in is not linked to an employee record yet, so there is nothing to show here. Ask HR to link your login to your employee profile.</Notice>
);

// Loading / error / content wrapper for a page that depends on one request.
export function Gate({ state, children }) {
  if (state.loading || (state.data == null && !state.error)) return <Loading />;
  if (state.error) return <ErrorBox error={state.error} onRetry={state.reload} />;
  return children(state.data);
}

export const monthLabel = (iso) => (iso ? new Date(`${iso.slice(0, 7)}-01T00:00:00`).toLocaleDateString('en-IN', { month: 'long', year: 'numeric' }) : '');
export const shortMonth = (iso) => (iso ? new Date(`${iso.slice(0, 7)}-01T00:00:00`).toLocaleDateString('en-IN', { month: 'short', year: '2-digit' }) : '');
export const days = (v) => { const n = Number(v); return Number.isInteger(n) ? String(n) : n.toFixed(1); };
export const thisMonth = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; };
export const options = (rows, label = (r) => r.name) => (rows || []).map((r) => ({ value: r.id, label: label(r) }));
// A required <select> has no blank entry of its own, so give it one: otherwise the first option shows but nothing is chosen.
export const choose = (opts, label = '— choose —') => [{ value: '', label }, ...opts];

// A dialog around a spec-driven form. `onSubmit(payload)` must return a promise; server field errors are shown on the fields.
export function FormDialog({ title, fields: fieldsProp, record: recordProp, defaults: defaultsProp, onSubmit, onClose, submitLabel = 'Save', wide = true, ctx, onlyChanged = false, extra, intro }) {
  // The form's spec and starting values are fixed when the dialog opens, so a re-render of the page behind it never resets what was typed.
  const [{ fields, record, defaults }] = useState({ fields: fieldsProp, record: recordProp, defaults: defaultsProp });
  const f = useSpecForm(fields, record, defaults);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setError(null);
    if (!f.validate()) return;
    setBusy(true);
    try {
      await onSubmit(f.payload(onlyChanged), f.values);
    } catch (err) {
      setError(err);
      f.setErrors(err.fieldErrors || {});
    }
    setBusy(false);
  };
  return (
    <Dialog title={title} onClose={onClose} wide={wide}>
      <form onSubmit={submit} noValidate>
        {intro}
        <FormErrors error={error} fields={fields} />
        <SpecForm fields={fields} values={f.values} errors={f.errors} onChange={f.set} ctx={ctx} idPrefix="hr" />
        {extra && extra(f.values)}
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : submitLabel}</button>
        </div>
      </form>
    </Dialog>
  );
}

export const GENDERS = [{ value: 'female', label: 'Female' }, { value: 'male', label: 'Male' }, { value: 'other', label: 'Other' }, { value: 'undisclosed', label: 'Prefer not to say' }];
export const EMPLOYMENT_TYPES = [{ value: 'full_time', label: 'Full-time' }, { value: 'contract', label: 'Contract' }, { value: 'intern', label: 'Intern' }, { value: 'consultant', label: 'Consultant' }];
export const EMP_STATUSES = [{ value: 'active', label: 'Active' }, { value: 'on_notice', label: 'On notice' }, { value: 'exited', label: 'Exited' }];

// Colours for heat cells and legends (text letters are always shown too, so colour is never the only signal).
export const ATT = {
  P: { label: 'Present', cls: 'hr-c-p' }, A: { label: 'Absent', cls: 'hr-c-a' }, H: { label: 'Half day', cls: 'hr-c-h' },
  L: { label: 'On leave', cls: 'hr-c-l' }, O: { label: 'Holiday', cls: 'hr-c-o' }, W: { label: 'Work from home', cls: 'hr-c-w' },
};

// Month calendar heat table: rows are people, columns are days. `grid` is the API's {days, rows}.
export function HeatTable({ days: cols, rows, onCell }) {
  return (
    <div className="hr-grid" role="region" aria-label="Attendance grid" tabIndex={0}>
      <table className="hr-heat">
        <thead>
          <tr>
            <th scope="col">Employee</th>
            {cols.map((d) => <th key={d.date} scope="col" className={d.off || d.holiday ? 'hr-off' : ''} title={`${d.weekday} ${d.date}`}>{Number(d.date.slice(8))}</th>)}
            <th scope="col" title="Present, work from home and half days">P</th>
            <th scope="col">A</th>
            <th scope="col">L</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.employee.id}>
              <th scope="row"><Link to={`/hr/employees/${r.employee.id}`}>{r.employee.name}</Link></th>
              {cols.map((d) => {
                const c = r.cells[d.date] || '';
                const info = ATT[c];
                return (
                  <td key={d.date} className={`hr-cell ${info ? info.cls : ''}${c === '-' ? ' hr-none' : ''}`} title={`${r.employee.name}, ${d.date}: ${info ? info.label : c === '-' ? 'Not working' : 'Not marked'}`}
                    onClick={onCell ? () => onCell(r.employee, d.date) : undefined}>{c === '-' ? '' : c}</td>
                );
              })}
              <td>{r.summary.present + r.summary.wfh + r.summary.half_day}</td>
              <td>{r.summary.absent}</td>
              <td>{r.summary.leave}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="hr-legend" aria-label="Legend">
        {Object.entries(ATT).map(([k, v]) => <li key={k}><span className={`hr-cell ${v.cls}`}>{k}</span> {v.label}</li>)}
      </ul>
    </div>
  );
}
