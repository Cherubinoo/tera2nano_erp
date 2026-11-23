import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { downloadCsv, money, num, plain } from '../../format';
import { asList, useLoad } from '../../hooks';
import { Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, fmtDate, useToast } from '../../ui';
import { CATEGORIES, HealthBadge, PRIORITIES, Progress, StatusBadge, TYPES, signClass, useEmployees } from './common';

const STATUS_CHIPS = [['', 'All'], ['open', 'Open'], ['active', 'Active'], ['planned', 'Planned'], ['on_hold', 'On hold'], ['completed', 'Completed'], ['cancelled', 'Cancelled']];
const HEALTH_CHIPS = [['', 'Any health'], ['on_track', 'On track'], ['at_risk', 'At risk'], ['delayed', 'Delayed']];

export default function Projects() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const status = params.get('status') || '';
  const health = params.get('health') || '';
  const term = params.get('q') || '';
  const [q, setQ] = useState(term);
  const [dialog, setDialog] = useState(params.get('new') === '1');
  const { data, error, loading, reload } = useLoad(
    () => api.get('/projects/projects/', { status: status || undefined, health: health || undefined, q: term || undefined }), [status, health, term]);
  const canManage = user.permissions.includes('projects.manage');
  const setParam = (changes) => {
    const next = { status, health, q: term, ...changes };
    setParams(Object.fromEntries(Object.entries(next).filter(([, v]) => v)), { replace: true });
  };
  const exportCsv = () => downloadCsv('projects.csv', [
    ['Code', 'Name', 'Client', 'Type', 'Status', 'Health', 'Progress %', 'Start', 'Planned end', 'Contract value', 'Budget', 'Revenue', 'Cost', 'Profit'],
    ...data.results.map((p) => [p.code, p.name, p.customer?.name || '', p.type_label, p.status_label, p.health, p.progress, p.start_date || '', p.planned_end_date || '',
      p.contract_value, p.budget_total, p.revenue, p.cost, p.profit]),
  ]);

  return (
    <>
      <PageHeader title="All projects" description="Every project with its progress, health and what it has earned."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setDialog(true)}>New project</button>} />
      <section className="card">
        <div className="chips" role="group" aria-label="Filter by status">
          {STATUS_CHIPS.map(([key, label]) => (
            <button key={key} type="button" className="chip" aria-pressed={status === key} onClick={() => setParam({ status: key })}>{label}</button>
          ))}
        </div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setParam({ q: q.trim() }); }}>
          <Field id="pr-q" label="Search" className="grow"><input id="pr-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, code, client, manager or tag" /></Field>
          <Field id="pr-health" label="Health">
            <select id="pr-health" value={health} onChange={(e) => setParam({ health: e.target.value })}>{HEALTH_CHIPS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          </Field>
          <button type="submit" className="btn">Search</button>
          {data?.results.length > 0 && <button type="button" className="btn" onClick={exportCsv}>Export CSV</button>}
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && data?.results.length === 0 && <p className="empty">No projects{status || term || health ? ' match' : ' yet'}.</p>}
        {data?.results.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Project</th><th>Status</th><th>Health</th><th>Progress</th><th>Dates</th>
                <th className="amount">Contract value</th><th className="amount">Cost</th><th className="amount">Profit</th></tr></thead>
              <tbody>
                {data.results.map((p) => (
                  <tr key={p.id} className="clickable" onClick={() => navigate(`/projects/projects/${p.id}`)}>
                    <td data-label="Project">
                      <Link to={`/projects/projects/${p.id}`} onClick={(e) => e.stopPropagation()}><strong>{p.name}</strong></Link>
                      <span className="projects-sub">{p.code} · {p.customer?.name || 'Internal'}</span>
                    </td>
                    <td data-label="Status"><StatusBadge status={p.status} /></td>
                    <td data-label="Health">{p.status === 'cancelled' ? '—' : <HealthBadge value={p.health} />}</td>
                    <td data-label="Progress"><Progress value={p.progress} /></td>
                    <td data-label="Dates">{fmtDate(p.start_date)}<span className="projects-sub">to {fmtDate(p.planned_end_date)}</span></td>
                    <td className="amount" data-label="Contract value">{plain(p.contract_value)}</td>
                    <td className="amount" data-label="Cost">{plain(p.cost)}</td>
                    <td className={`amount ${signClass(p.profit)}`} data-label="Profit">{plain(p.profit)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data?.results.length > 0 && (
          <div className="list-foot">
            <span>{data.count} project{data.count === 1 ? '' : 's'}</span>
            <span>Contract value <strong>{money(data.totals.contract_value)}</strong></span>
            <span>Revenue <strong>{money(data.totals.revenue)}</strong></span>
            <span>Cost <strong>{money(data.totals.cost)}</strong></span>
          </div>
        )}
      </section>
      {dialog && <ProjectDialog onClose={() => { setDialog(false); if (params.get('new')) setParam({ new: '' }); }}
        onSaved={(p) => { setDialog(false); navigate(`/projects/projects/${p.id}`); }} />}
    </>
  );
}

const BLANK = {
  name: '', description: '', customer: '', project_type: 'it_solution', status: 'planned', priority: 'medium', billing_method: 'fixed',
  manager_id: '', manager_name: '', start_date: '', planned_end_date: '', contract_value: '', budget: '', hourly_rate: '', tags: '',
};

// New or edited project, including the budget lines by category.
export function ProjectDialog({ record, onClose, onSaved }) {
  const notify = useToast();
  const isEdit = !!record;
  const customers = useLoad(() => api.get('/sales/customers/', { active: 'true' }).catch(() => []), []);
  const employees = useEmployees();
  const [v, setV] = useState(() => {
    if (!record) return { ...BLANK };
    const base = Object.fromEntries(Object.keys(BLANK).map((k) => [k, record[k] ?? '']));
    return { ...base, customer: record.customer?.id || '', tags: (record.tags || []).join(', '),
      contract_value: num(record.contract_value) || '', budget: num(record.budget) || '', hourly_rate: num(record.hourly_rate) || '' };
  });
  const [lines, setLines] = useState(() => Object.fromEntries(CATEGORIES.map(([c]) => [c, (record?.budget_lines || []).find((l) => l.category === c)?.amount ?? ''])));
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const internal = v.project_type === 'internal';
  const lineTotal = CATEGORIES.reduce((a, [c]) => a + num(lines[c]), 0);

  const validate = () => {
    const e = {};
    if (!v.name.trim()) e.name = 'Enter the project name.';
    if (!internal && !v.customer) e.customer = 'Choose the client. Only internal projects have none.';
    if (v.start_date && v.planned_end_date && v.planned_end_date < v.start_date) e.planned_end_date = 'The planned end date cannot be before the start date.';
    ['contract_value', 'budget', 'hourly_rate'].forEach((k) => { if (v[k] !== '' && !(num(v[k]) >= 0 && Number.isFinite(Number(v[k])))) e[k] = 'Enter an amount of zero or more.'; });
    if (CATEGORIES.some(([c]) => lines[c] !== '' && !(Number(lines[c]) >= 0))) e.budget_lines = 'Budget lines must be zero or more.';
    else if (num(v.budget) > 0 && lineTotal > num(v.budget)) e.budget_lines = 'The budget lines add up to more than the project budget.';
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const submit = async (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    setFailure(null);
    if (!validate()) return;
    setBusy(true);
    try {
      const body = {
        name: v.name, description: v.description, customer: v.customer || null, project_type: v.project_type, priority: v.priority,
        billing_method: v.billing_method, manager_id: v.manager_id || null, manager_name: v.manager_name, start_date: v.start_date || null,
        planned_end_date: v.planned_end_date || null, contract_value: v.contract_value || '0', budget: v.budget || '0', hourly_rate: v.hourly_rate || '0',
        tags: v.tags.split(',').map((t) => t.trim()).filter(Boolean),
        budget_lines: CATEGORIES.filter(([c]) => num(lines[c]) > 0).map(([c]) => ({ category: c, amount: lines[c] })),
      };
      if (!isEdit) body.status = v.status;
      const saved = isEdit ? await api.patch(`/projects/projects/${record.id}/`, body) : await api.post('/projects/projects/', body);
      notify(isEdit ? (saved.pending_budget_change && saved.pending_budget_change.id !== record.pending_budget_change?.id ? 'Project updated. The budget change is waiting for approval.' : 'Project updated.') : `Project created: ${saved.code}`);
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };

  const pickEmployee = (e) => {
    const emp = employees.find((x) => x.id === e.target.value);
    setV((x) => ({ ...x, manager_id: emp ? emp.id : '', manager_name: emp ? emp.name : x.manager_name }));
  };

  return (
    <Dialog title={isEdit ? `Edit ${record.name}` : 'New project'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={Object.keys(BLANK).map((name) => ({ name })).concat([{ name: 'budget_lines' }])} />
        <div className="form-grid">
          <Field id="pf-name" label="Project name" required error={errors.name} wide><input id="pf-name" value={v.name} maxLength={200} onChange={set('name')} /></Field>
          <Field id="pf-type" label="Project type" error={errors.project_type}>
            <select id="pf-type" value={v.project_type} onChange={set('project_type')}>{TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          </Field>
          <Field id="pf-client" label="Client" required={!internal} error={errors.customer} hint={internal ? 'Optional for internal projects.' : undefined}>
            <select id="pf-client" value={v.customer} onChange={set('customer')}>
              <option value="">{internal ? '— internal (no client) —' : '— choose —'}</option>
              {asList(customers.data).map((c) => <option key={c.id} value={c.id}>{c.display_name}</option>)}
            </select>
          </Field>
          {!isEdit && (
            <Field id="pf-status" label="Status" error={errors.status}>
              <select id="pf-status" value={v.status} onChange={set('status')}><option value="planned">Planned</option><option value="active">Active</option><option value="on_hold">On hold</option></select>
            </Field>
          )}
          <Field id="pf-priority" label="Priority" error={errors.priority}>
            <select id="pf-priority" value={v.priority} onChange={set('priority')}>{PRIORITIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          </Field>
          {employees.length > 0 ? (
            <Field id="pf-mgr" label="Project manager" error={errors.manager_id}>
              <select id="pf-mgr" value={v.manager_id} onChange={pickEmployee}>
                <option value="">{v.manager_name && !v.manager_id ? `${v.manager_name} (typed)` : '— choose —'}</option>
                {employees.map((m) => <option key={m.id} value={m.id}>{m.name}{m.designation ? ` (${m.designation})` : ''}</option>)}
              </select>
            </Field>
          ) : (
            <Field id="pf-mgr" label="Project manager" error={errors.manager_name}><input id="pf-mgr" value={v.manager_name} maxLength={150} onChange={set('manager_name')} /></Field>
          )}
          <Field id="pf-start" label="Start date" error={errors.start_date}><input id="pf-start" type="date" value={v.start_date} onChange={set('start_date')} /></Field>
          <Field id="pf-end" label="Planned end date" error={errors.planned_end_date}><input id="pf-end" type="date" value={v.planned_end_date} min={v.start_date || undefined} onChange={set('planned_end_date')} /></Field>
          <Field id="pf-billing" label="Billing method" error={errors.billing_method}>
            <select id="pf-billing" value={v.billing_method} onChange={set('billing_method')}><option value="fixed">Fixed price</option><option value="time_material">Time and material</option></select>
          </Field>
          <Field id="pf-value" label="Contract value (INR)" error={errors.contract_value}><input id="pf-value" inputMode="decimal" value={v.contract_value} onChange={set('contract_value')} /></Field>
          <Field id="pf-budget" label="Budget (INR)" error={errors.budget} hint="Total cost budget. Leave empty to use the sum of the lines below.">
            <input id="pf-budget" inputMode="decimal" value={v.budget} onChange={set('budget')} />
          </Field>
          {v.billing_method === 'time_material' && (
            <Field id="pf-rate" label="Default hourly rate (INR)" error={errors.hourly_rate} hint="Used for billable time when the person has no rate of their own.">
              <input id="pf-rate" inputMode="decimal" value={v.hourly_rate} onChange={set('hourly_rate')} />
            </Field>
          )}
          <Field id="pf-tags" label="Tags" error={errors.tags} hint="Separate with commas."><input id="pf-tags" value={v.tags} onChange={set('tags')} /></Field>
          <Field id="pf-desc" label="Description" error={errors.description} wide><textarea id="pf-desc" rows={3} value={v.description} onChange={set('description')} /></Field>
        </div>
        <fieldset className="subcard">
          <legend><strong>Budget by category</strong> <span className="hint">Allocated {plain(lineTotal)}{num(v.budget) > 0 && <> of {plain(v.budget)}</>}</span></legend>
          {errors.budget_lines && <div className="error-text" role="alert">{errors.budget_lines}</div>}
          <div className="projects-lines">
            {CATEGORIES.map(([c, label]) => (
              <Field key={c} id={`pf-line-${c}`} label={label}>
                <input id={`pf-line-${c}`} inputMode="decimal" value={lines[c]} onChange={(e) => setLines((x) => ({ ...x, [c]: e.target.value }))} />
              </Field>
            ))}
          </div>
        </fieldset>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : isEdit ? 'Save changes' : 'Create project'}</button>
        </div>
      </form>
    </Dialog>
  );
}
