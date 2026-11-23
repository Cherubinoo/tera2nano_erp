import { useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { ColumnChart, Donut, HBars } from '../../components/charts';
import { useAuth } from '../../context';
import { compact, downloadCsv, money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, Tabs, fmtDate, fmtDateTime, useConfirm, useReasonDialog, useToast } from '../../ui';
import { BudgetBadge, CATEGORIES, HealthBadge, MonthlyChart, PriorityBadge, Progress, SALES_STATUS_TONE, StatusBadge, Tile, signClass, useEmployees } from './common';
import { ApprovalPanel } from '../approvals/ApprovalBadge';
import { MilestonePanel } from './Milestones';
import { ProjectDialog } from './Projects';
import { TaskBoard } from './Tasks';
import { TimesheetPanel } from './Timesheets';

const TABS = [
  ['overview', 'Overview'], ['work', 'Milestones & Tasks'], ['team', 'Team'], ['timesheets', 'Timesheets'], ['budget', 'Budget vs Actual'],
  ['financials', 'Financials'], ['invoices', 'Invoices'], ['expenses', 'Bills & Expenses'], ['documents', 'Documents'], ['activity', 'Activity'],
];
const TASK_SLICES = [['todo', 'To do', 'var(--chart-grey)'], ['in_progress', 'In progress', 'var(--chart-1)'], ['review', 'In review', 'var(--chart-3)'], ['done', 'Done', 'var(--chart-2)']];

export default function ProjectDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const notify = useToast();
  const { user } = useAuth();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [params, setParams] = useSearchParams();
  const tab = TABS.some(([k]) => k === params.get('tab')) ? params.get('tab') : 'overview';
  const { data: p, error, loading, reload } = useLoad(() => api.get(`/projects/projects/${id}/`), [id]);
  const [editing, setEditing] = useState(false);
  const [statusDialog, setStatusDialog] = useState(false);
  const canManage = user.permissions.includes('projects.manage');
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;

  const remove = async () => {
    if (!(await confirm({ title: `Delete ${p.name}?`, body: <p>Only a project with no time, invoices, bookings or files can be deleted. Otherwise cancel it.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/projects/projects/${id}/`); notify('Project deleted.'); navigate('/projects/projects'); } catch (e) { notify(e.message, 'error'); }
  };

  return (
    <>
      <Link to="/projects/projects" className="back-link">← All projects</Link>
      <header className="doc-head">
        <div>
          <h1>{p.name} <code>{p.code}</code> <StatusBadge status={p.status} /></h1>
          <p className="muted">{p.customer ? <Link to={`/sales/customers/${p.customer.id}`}>{p.customer.name}</Link> : 'Internal project'} · {p.type_label}
            {p.manager_name && <> · Managed by {p.manager_name}</>}</p>
        </div>
        <div className="page-actions">
          {canManage && <button type="button" className="btn" onClick={() => setEditing(true)}>Edit</button>}
          {canManage && <button type="button" className="btn" onClick={() => setStatusDialog(true)}>Change status</button>}
          {canManage && <button type="button" className="btn" onClick={() => setParams({ tab: 'timesheets', log: '1' })}>Log time</button>}
          {canManage && <button type="button" className="btn" onClick={remove}>Delete</button>}
        </div>
      </header>
      {p.status === 'cancelled' && <Notice type="warn">This project is cancelled.</Notice>}
      {p.pending_budget_change && <PendingBudgetChange change={p.pending_budget_change} managed={p.budget_approval_managed} canManage={canManage} onChange={reload} />}
      <Tabs tabs={TABS.map(([key, label]) => ({ key, label }))} value={tab} onChange={(k) => setParams({ tab: k }, { replace: true })} />

      {tab === 'overview' && <Overview p={p} />}
      {tab === 'work' && (<><MilestonePanel projectId={id} onChange={reload} /><TaskBoard projectId={id} onChange={reload} /></>)}
      {tab === 'team' && <Team p={p} canManage={canManage} />}
      {tab === 'timesheets' && <TimesheetPanel projectId={id} onChange={reload} initialNew={params.get('log') === '1'} />}
      {tab === 'budget' && <BudgetVsActual p={p} canManage={canManage} onEdit={() => setEditing(true)} />}
      {tab === 'financials' && <Financials p={p} />}
      {tab === 'invoices' && <Invoices p={p} />}
      {tab === 'expenses' && <Expenses p={p} />}
      {tab === 'documents' && (
        <Attachments entityType="project" entityId={id} canManage={canManage} title="Agreements and documents"
          hint="Contracts, work orders and other agreements. Give an agreement an expiry date to be reminded." />
      )}
      {tab === 'activity' && <Activity p={p} />}

      {editing && <ProjectDialog record={p} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} />}
      {statusDialog && <StatusDialog p={p} onClose={() => setStatusDialog(false)} onSaved={() => { setStatusDialog(false); reload(); }} />}
      {confirmDialog}
    </>
  );
}

function StatusDialog({ p, onClose, onSaved }) {
  const notify = useToast();
  const [status, setStatus] = useState(p.status);
  const [reason, setReason] = useState('');
  const [end, setEnd] = useState(today());
  const [failure, setFailure] = useState(null);
  const [errors, setErrors] = useState({});
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation(); setFailure(null);
    if (status === p.status) { onClose(); return; }
    if (status === 'cancelled' && !reason.trim()) { setErrors({ reason: 'Say why the project is cancelled.' }); return; }
    try {
      await api.post(`/projects/projects/${p.id}/status/`, { status, reason, actual_end_date: status === 'completed' ? end : undefined });
      notify('Status updated.');
      onSaved();
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
  };
  return (
    <Dialog title="Change project status" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={[{ name: 'status' }, { name: 'reason' }, { name: 'actual_end_date' }]} />
        <div className="form-grid">
          <Field id="st-status" label="Status" error={errors.status}>
            <select id="st-status" value={status} onChange={(e) => setStatus(e.target.value)}>
              {[['planned', 'Planned'], ['active', 'Active'], ['on_hold', 'On hold'], ['completed', 'Completed'], ['cancelled', 'Cancelled']].map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </Field>
          {status === 'completed' && <Field id="st-end" label="Actual end date" error={errors.actual_end_date}><input id="st-end" type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>}
          <Field id="st-reason" label={status === 'cancelled' ? 'Reason' : 'Note (optional)'} required={status === 'cancelled'} error={errors.reason} wide>
            <input id="st-reason" value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} />
          </Field>
        </div>
        <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary">Save</button></div>
      </form>
    </Dialog>
  );
}

function Overview({ p }) {
  const fin = useLoad(() => api.get(`/projects/projects/${p.id}/financials/`), [p.id]);
  const tasks = useLoad(() => api.get('/projects/tasks/', { project: p.id }), [p.id]);
  const f = fin.data;
  const counts = Object.fromEntries(TASK_SLICES.map(([k]) => [k, (tasks.data?.results || []).filter((t) => t.status === k).length]));
  const elapsed = (() => {
    if (!p.start_date || !p.planned_end_date) return null;
    const a = new Date(`${p.start_date}T00:00:00`), b = new Date(`${p.planned_end_date}T00:00:00`), n = new Date(`${today()}T00:00:00`);
    return Math.max(0, Math.min(100, Math.round(((n - a) / Math.max(b - a, 86400000)) * 100)));
  })();
  return (
    <>
      <div className="tiles">
        <Tile label="Progress" value={`${p.progress}%`} hint={`${p.milestones_done}/${p.milestones_total} milestones, ${p.tasks_done}/${p.tasks_total} tasks`} />
        <Tile label="Schedule" value={<HealthBadge value={p.schedule_health} />} hint={p.overdue_milestones > 0 ? `${p.overdue_milestones} overdue milestone${p.overdue_milestones === 1 ? '' : 's'}` : elapsed !== null ? `${elapsed}% of the time has passed` : 'No planned end date'} tone={p.schedule_health === 'delayed' ? 'bad' : ''} />
        <Tile label="Budget" value={<BudgetBadge value={p.budget_health} />} hint={p.budget_total > 0 || num(p.budget_total) > 0 ? `${plain(p.cost)} of ${plain(p.budget_total)} used (${p.budget_used_percent === null ? 0 : num(p.budget_used_percent)}%)` : 'Set a budget to track it'} />
        <Tile label="Revenue" value={money(p.revenue)} hint={`Contract value ${money(p.contract_value)}`} />
        <Tile label="Cost" value={money(p.cost)} />
        <Tile label="Profit" value={money(p.profit)} tone={num(p.profit) < 0 ? 'bad' : num(p.profit) > 0 ? 'good' : ''} hint={p.margin_percent === null ? 'No revenue yet' : `${num(p.margin_percent)}% margin`} />
        <Tile label="Receivable" value={f ? money(f.outstanding) : '…'} hint={f ? `${money(f.invoiced)} invoiced, ${money(f.collected)} collected` : ''} />
        <Tile label="Hours" value={plain(p.hours)} hint={`${plain(p.billable_hours)} billable, worth ${money(p.billable_amount)}`} />
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Details">
          <h2>Details</h2>
          <dl className="summary">
            <div><dt>Status</dt><dd><StatusBadge status={p.status} /></dd></div>
            <div><dt>Priority</dt><dd><PriorityBadge value={p.priority} /></dd></div>
            <div><dt>Start</dt><dd>{fmtDate(p.start_date)}</dd></div>
            <div><dt>Planned end</dt><dd>{fmtDate(p.planned_end_date)}</dd></div>
            <div><dt>Actual end</dt><dd>{fmtDate(p.actual_end_date)}</dd></div>
            <div><dt>Billing</dt><dd>{p.billing_label}{p.billing_method === 'time_material' && num(p.hourly_rate) > 0 && <> at {plain(p.hourly_rate)}/h</>}</dd></div>
            <div><dt>Contract value</dt><dd>{money(p.contract_value)}</dd></div>
            <div><dt>Budget</dt><dd>{money(p.budget_total)}</dd></div>
          </dl>
          {p.tags.length > 0 && <div className="projects-tags" style={{ marginTop: '.75rem' }}>{p.tags.map((t) => <span key={t} className="projects-tag">{t}</span>)}</div>}
          {p.description && <p style={{ marginTop: '.75rem', whiteSpace: 'pre-wrap' }}>{p.description}</p>}
          <h3>Progress</h3>
          <Progress value={p.progress} tone={p.schedule_health === 'delayed' ? 'bad' : p.schedule_health === 'at_risk' ? 'warn' : 'good'} />
          {elapsed !== null && <p className="hint">Time elapsed: {elapsed}%. Progress comes from milestones (equal weight) and tasks (weighted by estimated hours).</p>}
        </section>
        <section className="card" aria-label="Tasks by status">
          <h2>Tasks by status</h2>
          {tasks.loading ? <Loading /> : <Donut centreLabel="tasks" slices={TASK_SLICES.map(([key, label, colour]) => ({ key, label, colour, value: counts[key] }))} />}
        </section>
      </div>

      <section className="card" aria-label="Revenue and cost per month">
        <h2>Revenue and cost</h2>
        <p className="muted">Last six months, from entries in the books tagged to this project.</p>
        {fin.error ? <ErrorBox error={fin.error} onRetry={fin.reload} /> : f ? (
          <MonthlyChart title="Project revenue and cost per month" monthly={f.monthly} />
        ) : <Loading />}
      </section>
    </>
  );
}

function Team({ p, canManage }) {
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const employees = useEmployees();
  const { data, error, loading, reload } = useLoad(() => api.get('/projects/team/', { project: p.id }), [p.id]);
  const [dialog, setDialog] = useState(null);
  const remove = async (m) => {
    if (!(await confirm({ title: `Remove ${m.name} from the team?`, body: <p>Time already logged stays.</p>, confirmLabel: 'Remove', danger: true }))) return;
    try { await api.del(`/projects/team/${m.id}/`); notify('Removed from the team.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  return (
    <section className="card" aria-label="Team">
      <div className="projects-section-head"><h2>Team</h2>{canManage && <button type="button" className="btn btn-small btn-primary" onClick={() => setDialog({})}>Add member</button>}</div>
      {p.manager_name && <p className="muted">Project manager: <strong>{p.manager_name}</strong></p>}
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && data.results.length === 0 && <p className="empty">Nobody on the team yet.</p>}
      {data && data.results.length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Name</th><th>Role</th><th className="amount">Allocation</th><th className="amount">Bill rate</th><th className="amount">Hours logged</th><th>Status</th>{canManage && <th><span className="sr-only">Actions</span></th>}</tr></thead>
          <tbody>{data.results.map((m) => (
            <tr key={m.id}>
              <td data-label="Name"><strong>{m.name}</strong></td><td data-label="Role">{m.role || '—'}</td>
              <td className="amount" data-label="Allocation">{m.allocation_percent}%</td><td className="amount" data-label="Bill rate">{num(m.hourly_rate) > 0 ? plain(m.hourly_rate) : '—'}</td>
              <td className="amount" data-label="Hours logged">{plain(m.logged_hours)}</td><td data-label="Status">{m.is_active ? <Badge tone="good">Active</Badge> : <Badge>Left</Badge>}</td>
              {canManage && <td className="row-actions"><button type="button" className="btn btn-small" onClick={() => setDialog(m)}>Edit</button><button type="button" className="btn btn-small" onClick={() => remove(m)}>Remove</button></td>}
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {dialog && <MemberDialog p={p} employees={employees} record={dialog.id ? dialog : null} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); reload(); }} />}
      {confirmDialog}
    </section>
  );
}

function MemberDialog({ p, employees, record, onClose, onSaved }) {
  const notify = useToast();
  const isEdit = !!record;
  const [v, setV] = useState({ name: record?.name || '', employee_id: record?.employee_id || '', role: record?.role || '', allocation_percent: record?.allocation_percent ?? 100,
    hourly_rate: record && num(record.hourly_rate) ? record.hourly_rate : '', is_active: record ? record.is_active : true });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const pick = (e) => {
    const emp = employees.find((x) => x.id === e.target.value);
    setV((x) => ({ ...x, employee_id: emp ? emp.id : '', name: emp ? emp.name : x.name, role: emp && !x.role ? emp.designation || '' : x.role }));
  };
  const submit = async (ev) => {
    ev.preventDefault(); ev.stopPropagation(); setFailure(null);
    const e = {};
    if (!v.name.trim()) e.name = 'Enter the name.';
    if (!(Number(v.allocation_percent) >= 0 && Number(v.allocation_percent) <= 100)) e.allocation_percent = 'Enter a percentage from 0 to 100.';
    if (v.hourly_rate !== '' && !(Number(v.hourly_rate) >= 0)) e.hourly_rate = 'Enter a rate of zero or more.';
    if (Object.keys(e).length) { setErrors(e); return; }
    try {
      const body = { name: v.name, employee_id: v.employee_id || null, role: v.role, allocation_percent: v.allocation_percent, hourly_rate: v.hourly_rate || '0', is_active: v.is_active };
      if (!isEdit) body.project = p.id;
      if (isEdit) await api.patch(`/projects/team/${record.id}/`, body); else await api.post('/projects/team/', body);
      notify(isEdit ? 'Team member updated.' : 'Team member added.');
      onSaved();
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
  };
  return (
    <Dialog title={isEdit ? `Edit ${record.name}` : 'Add team member'} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={['name', 'employee_id', 'role', 'allocation_percent', 'hourly_rate', 'is_active'].map((name) => ({ name }))} />
        <div className="form-grid">
          {employees.length > 0 && !isEdit && (
            <Field id="tm-emp" label="Employee" wide hint="Pick from the employee directory, or type a name below.">
              <select id="tm-emp" value={v.employee_id} onChange={pick}><option value="">— not listed —</option>{employees.map((m) => <option key={m.id} value={m.id}>{m.name}{m.department ? ` (${m.department})` : ''}</option>)}</select>
            </Field>
          )}
          <Field id="tm-name" label="Name" required error={errors.name || errors.employee_id}><input id="tm-name" value={v.name} maxLength={150} onChange={set('name')} /></Field>
          <Field id="tm-role" label="Role" error={errors.role}><input id="tm-role" value={v.role} maxLength={100} onChange={set('role')} /></Field>
          <Field id="tm-alloc" label="Allocation (%)" error={errors.allocation_percent}><input id="tm-alloc" type="number" min="0" max="100" value={v.allocation_percent} onChange={set('allocation_percent')} /></Field>
          <Field id="tm-rate" label="Bill rate per hour (INR)" error={errors.hourly_rate}><input id="tm-rate" inputMode="decimal" value={v.hourly_rate} onChange={set('hourly_rate')} /></Field>
          {isEdit && <div className="field field-check"><label className="check"><input type="checkbox" checked={v.is_active} onChange={(e) => setV((x) => ({ ...x, is_active: e.target.checked }))} /> Still on the project</label></div>}
        </div>
        <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary">Save</button></div>
      </form>
    </Dialog>
  );
}

// A budget change that falls under an approval rule waits here; the project keeps its current budget until it is approved.
function PendingBudgetChange({ change, managed, canManage, onChange }) {
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const act = async (verb, msg, body) => { try { await api.post(`/projects/budget-changes/${change.id}/${verb}/`, body || {}); notify(msg); onChange(); } catch (e) { notify(e.message, 'error'); } };
  const reject = async () => {
    const reason = await ask({ title: 'Reject budget change', confirmLabel: 'Reject' });
    if (reason !== null) act('reject', 'Budget change rejected.', { change_reason: reason });
  };
  return (
    <section className="card" aria-label="Budget change waiting for approval">
      <div className="projects-section-head">
        <h2>Budget change waiting for approval</h2>
        <span className="page-actions">
          <ApprovalPanel kind="budget" sourceId={change.id} waiting canSubmit={canManage} onChange={onChange} />
          {canManage && <button type="button" className="btn btn-small" onClick={() => act('withdraw', 'Budget change withdrawn.')}>Withdraw</button>}
          {canManage && !managed && <><button type="button" className="btn btn-small btn-primary" onClick={() => act('approve', 'Budget change approved.')}>Approve</button>
            <button type="button" className="btn btn-small" onClick={reject}>Reject</button></>}
        </span>
      </div>
      <p>The budget stays at <strong>{money(change.previous_budget)}</strong> until this is approved. Proposed: <strong>{money(change.proposed_total)}</strong>
        {num(change.proposed_budget) > 0 ? '' : ' (the sum of the category lines)'}.</p>
      {change.proposed_lines.length > 0 && <ul>{change.proposed_lines.map((l) => <li key={l.category}>{l.label}: {plain(l.amount)}</li>)}</ul>}
      {change.reason && <p className="hint">{change.reason}</p>}
      {dialog}
    </section>
  );
}

function BudgetVsActual({ p, canManage, onEdit }) {
  const { data: b, error, loading, reload } = useLoad(() => api.get(`/projects/projects/${p.id}/budget/`), [p.id]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const tone = (pct) => (pct === null ? '' : num(pct) > 100 ? 'bad' : num(pct) >= 90 ? 'warn' : '');
  return (
    <>
      <div className="tiles">
        <Tile label="Budget" value={money(b.budget_total)} hint={num(b.allocated) > 0 ? `${money(b.allocated)} allocated, ${money(b.unallocated)} not allocated` : 'No lines allocated'} />
        <Tile label="Actual cost" value={money(b.actual_total)} hint="Booked to the project in the ledger" />
        <Tile label="Remaining" value={money(b.variance)} tone={num(b.variance) < 0 ? 'bad' : ''} hint={b.used_percent === null ? 'No budget set' : `${num(b.used_percent)}% used`} />
        <Tile label="Budget health" value={<BudgetBadge value={b.health} />} hint="On budget up to 90% used" />
      </div>
      <section className="card" aria-label="Budget against actual">
        <div className="projects-section-head"><h2>By category</h2>{canManage && <button type="button" className="btn btn-small" onClick={onEdit}>Edit budget</button>}</div>
        <div className="table-wrap"><table>
          <thead><tr><th>Category</th><th className="amount">Budget</th><th className="amount">Actual</th><th className="amount">Variance</th><th>Used</th></tr></thead>
          <tbody>{b.rows.map((r) => (
            <tr key={r.category}>
              <td data-label="Category"><strong>{r.label}</strong></td><td className="amount" data-label="Budget">{plain(r.budget)}</td><td className="amount" data-label="Actual">{plain(r.actual)}</td>
              <td className={`amount ${signClass(r.variance)}`} data-label="Variance">{plain(r.variance)}</td>
              <td data-label="Used">{r.used_percent === null ? (num(r.actual) > 0 ? <span className="hint">No budget</span> : '—') : (
                <><span className="projects-bva-bar" role="img" aria-label={`${num(r.used_percent)}% used`}><span className={tone(r.used_percent)} style={{ width: `${Math.min(num(r.used_percent), 100)}%` }} /></span>{num(r.used_percent)}%</>)}</td>
            </tr>
          ))}
            <tr><th scope="row">Total</th><td className="amount"><strong>{plain(b.budget_total)}</strong></td><td className="amount"><strong>{plain(b.actual_total)}</strong></td>
              <td className={`amount ${signClass(b.variance)}`}><strong>{plain(b.variance)}</strong></td><td>{b.used_percent === null ? '—' : `${num(b.used_percent)}%`}</td></tr>
          </tbody>
        </table></div>
        <p className="hint">Actual cost is read from the books: expense accounts are matched to categories by name (travel, salaries, subcontract or professional fees, materials or software; direct costs count as materials; the rest is other).</p>
      </section>
      <div className="grid-2">
        <section className="card" aria-label="Budget and actual chart">
          <h2>Budget against actual</h2>
          <ColumnChart title="Budget against actual by category" format={compact}
            series={[{ key: 'budget', label: 'Budget', colour: 'var(--chart-1)' }, { key: 'actual', label: 'Actual', colour: 'var(--chart-3)' }]}
            data={b.rows.map((r) => ({ label: r.label, values: { budget: r.budget, actual: r.actual } }))} />
        </section>
        <section className="card" aria-label="Cost by account">
          <h2>Cost by account</h2>
          <HBars rows={b.cost_rows.map((r) => ({ key: r.account_id, label: `${r.code} ${r.name}`, value: r.amount }))} format={(v) => money(v)} empty="No cost has been booked to this project." />
        </section>
      </div>
    </>
  );
}

function Financials({ p }) {
  const { data: f, error, loading, reload } = useLoad(() => api.get(`/projects/projects/${p.id}/financials/`), [p.id]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const byCat = CATEGORIES.map(([k, l]) => ({ key: k, label: l, rows: f.cost_rows.filter((r) => r.category === k) })).filter((g) => g.rows.length);
  return (
    <>
      <div className="tiles">
        <Tile label="Revenue" value={money(f.revenue)} />
        <Tile label="Cost" value={money(f.cost)} />
        <Tile label="Profit" value={money(f.profit)} tone={num(f.profit) < 0 ? 'bad' : num(f.profit) > 0 ? 'good' : ''} hint={f.margin_percent === null ? 'No revenue yet' : `${num(f.margin_percent)}% margin`} />
        <Tile label="Receivable outstanding" value={money(f.outstanding)} hint={`${money(f.invoiced)} invoiced, ${money(f.collected)} collected`} tone={num(f.outstanding) > 0 ? 'warn' : ''} />
        <Tile label="Work in progress" value={money(f.wip)} hint="Earned, not yet billed" />
      </div>
      <div className="grid-2">
        <section className="card" aria-label="Profit and loss">
          <h2>Profit and loss</h2>
          <div className="table-wrap"><table className="projects-pl">
            <tbody>
              <tr className="group"><th colSpan="2">Revenue</th></tr>
              {f.revenue_rows.length === 0 && <tr><td colSpan="2" className="muted">Nothing booked yet.</td></tr>}
              {f.revenue_rows.map((r) => <tr key={r.account_id}><td>{r.code} {r.name}</td><td className="amount">{plain(r.amount)}</td></tr>)}
              <tr className="total"><th scope="row">Total revenue</th><td className="amount">{plain(f.revenue)}</td></tr>
              <tr className="group"><th colSpan="2">Costs</th></tr>
              {byCat.length === 0 && <tr><td colSpan="2" className="muted">Nothing booked yet.</td></tr>}
              {byCat.map((g) => (
                [<tr key={g.key}><th colSpan="2" scope="colgroup">{g.label}</th></tr>,
                  ...g.rows.map((r) => <tr key={r.account_id}><td>{r.code} {r.name}</td><td className="amount">{plain(r.amount)}</td></tr>)]
              ))}
              <tr className="total"><th scope="row">Total cost</th><td className="amount">{plain(f.cost)}</td></tr>
              <tr className="total"><th scope="row">Profit</th><td className={`amount ${signClass(f.profit)}`}>{plain(f.profit)}</td></tr>
              <tr><th scope="row">Margin</th><td className="amount">{f.margin_percent === null ? '—' : `${num(f.margin_percent)}%`}</td></tr>
            </tbody>
          </table></div>
        </section>
        <section className="card" aria-label="Revenue and cost per month">
          <h2>Month by month</h2>
          <MonthlyChart title="Revenue and cost per month" monthly={f.monthly} />
          <h3>Receivables</h3>
          <dl className="summary">
            <div><dt>Invoiced (incl. tax)</dt><dd>{money(f.invoiced)}</dd></div><div><dt>Collected</dt><dd>{money(f.collected)}</dd></div><div><dt>Outstanding</dt><dd>{money(f.outstanding)}</dd></div>
            <div><dt>Contract value</dt><dd>{money(f.contract_value)}</dd></div>
          </dl>
        </section>
      </div>
    </>
  );
}

function Invoices({ p }) {
  const { user } = useAuth();
  const { data, error, loading, reload } = useLoad(() => api.get(`/projects/projects/${p.id}/invoices/`), [p.id]);
  const canSell = user.permissions.includes('sales.manage');
  return (
    <section className="card" aria-label="Invoices">
      <div className="projects-section-head">
        <h2>Invoices</h2>
        {canSell && p.customer && <Link className="btn btn-small" to={`/sales/documents/new?kind=invoice&customer=${p.customer.id}&project=${p.id}`}>New invoice</Link>}
      </div>
      <p className="muted">Sales documents tagged to this project. To bill a milestone, use "Create invoice" on the Milestones &amp; Tasks tab: it starts a draft already tagged to the project.</p>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && data.results.length === 0 && <p className="empty">No invoices are tagged to this project yet.</p>}
      {data && data.results.length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Number</th><th>Type</th><th>Date</th><th>Due</th><th>Status</th><th className="amount">Amount</th><th className="amount">Balance due</th></tr></thead>
          <tbody>{data.results.map((d) => (
            <tr key={d.id}>
              <td data-label="Number"><Link to={`/sales/documents/${d.id}`}><strong>{d.number || 'Draft'}</strong></Link></td><td data-label="Type">{d.kind_label}</td>
              <td data-label="Date">{fmtDate(d.issue_date)}</td><td data-label="Due">{fmtDate(d.due_date)}</td>
              <td data-label="Status"><Badge tone={SALES_STATUS_TONE[d.status_key] || 'neutral'}>{d.status_label}</Badge></td>
              <td className="amount" data-label="Amount">{plain(d.grand_total)}</td><td className="amount" data-label="Balance due">{d.balance === null ? '—' : plain(d.balance)}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {data && data.results.length > 0 && <div className="list-foot"><span>Invoiced <strong>{money(data.invoiced)}</strong></span><span>Outstanding <strong>{money(data.outstanding)}</strong></span></div>}
    </section>
  );
}

function Expenses({ p }) {
  const { data, error, loading, reload } = useLoad(() => api.get(`/projects/projects/${p.id}/expenses/`), [p.id]);
  return (
    <section className="card" aria-label="Bills and expenses">
      <div className="projects-section-head">
        <h2>Bills &amp; expenses</h2>
        {data?.results.length > 0 && <button type="button" className="btn btn-small" onClick={() => downloadCsv('project-expenses.csv', [['Date', 'Reference', 'Type', 'Account', 'Category', 'Description', 'Amount'],
          ...data.results.map((r) => [r.date, r.reference, r.source_label, r.account, r.category, r.memo, r.amount])])}>Export CSV</button>}
      </div>
      <p className="muted">Costs booked to this project in the books: vendor bills, expenses, salaries and journals that carry the project tag. Choose the project when you record a bill or expense.</p>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && data.results.length === 0 && <p className="empty">No costs are tagged to this project yet.</p>}
      {data && data.results.length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Date</th><th>Entry</th><th>Account</th><th>Category</th><th>Description</th><th className="amount">Amount</th></tr></thead>
          <tbody>{data.results.map((r) => (
            <tr key={r.id}>
              <td data-label="Date">{fmtDate(r.date)}</td>
              <td data-label="Entry"><Link to={`/accounting/journal/${r.entry_id}`}>{r.reference || r.source_label}</Link>{r.reversed && <> <Badge>Reversed</Badge></>}</td>
              <td data-label="Account">{r.account}</td><td data-label="Category" style={{ textTransform: 'capitalize' }}>{r.category}</td><td data-label="Description">{r.memo || '—'}</td>
              <td className={`amount ${signClass(r.amount)}`} data-label="Amount">{plain(r.amount)}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {data && data.results.length > 0 && <div className="list-foot"><span>{data.count} line{data.count === 1 ? '' : 's'}</span><span>Total <strong>{money(data.total)}</strong></span></div>}
    </section>
  );
}

function Activity({ p }) {
  const { data, error, loading, reload } = useLoad(() => api.get(`/projects/projects/${p.id}/activity/`), [p.id]);
  return (
    <section className="card" aria-label="Activity">
      <h2>Activity</h2>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && data.length === 0 && <p className="empty">Nothing recorded yet.</p>}
      {data && data.length > 0 && (
        <ul className="projects-list-plain projects-activity">
          {data.map((e) => (
            <li key={e.id}><span>{e.text}</span><span className="projects-sub">{e.actor} · {fmtDateTime(e.occurred_at)}</span></li>
          ))}
        </ul>
      )}
    </section>
  );
}
