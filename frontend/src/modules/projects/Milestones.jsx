import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { downloadCsv, money, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, fmtDate, useConfirm, useToast } from '../../ui';
import { MILESTONE_TONE, useProjectLookup } from './common';

const STATUS_CHIPS = [['', 'All'], ['open', 'Open'], ['overdue', 'Overdue'], ['in_progress', 'In progress'], ['pending', 'Pending'], ['done', 'Done']];

export function MilestoneDialog({ projectId, projects, record, onClose, onSaved }) {
  const notify = useToast();
  const isEdit = !!record;
  const [v, setV] = useState(() => ({
    project: record?.project.id || projectId || '', name: record?.name || '', description: record?.description || '', due_date: record?.due_date || '',
    amount: record && Number(record.amount) ? record.amount : '', status: record?.status || 'pending', completed_on: record?.completed_on || '',
  }));
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const submit = async (ev) => {
    ev.preventDefault(); ev.stopPropagation(); setFailure(null);
    const e = {};
    if (!v.project) e.project = 'Choose the project.';
    if (!v.name.trim()) e.name = 'Enter the milestone name.';
    if (!v.due_date) e.due_date = 'Enter the due date.';
    if (v.amount !== '' && !(Number(v.amount) >= 0)) e.amount = 'Enter an amount of zero or more.';
    if (Object.keys(e).length) { setErrors(e); return; }
    setBusy(true);
    try {
      const body = { name: v.name, description: v.description, due_date: v.due_date, amount: v.amount || '0', status: v.status };
      if (v.status === 'done' && v.completed_on) body.completed_on = v.completed_on;
      if (!isEdit) body.project = v.project;
      const saved = isEdit ? await api.patch(`/projects/milestones/${record.id}/`, body) : await api.post('/projects/milestones/', body);
      notify(isEdit ? 'Milestone updated.' : 'Milestone added.');
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title={isEdit ? 'Edit milestone' : 'New milestone'} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={['project', 'name', 'description', 'due_date', 'amount', 'status', 'completed_on'].map((name) => ({ name }))} />
        <div className="form-grid">
          {!projectId && !isEdit && (
            <Field id="ms-project" label="Project" required error={errors.project} wide>
              <select id="ms-project" value={v.project} onChange={set('project')}><option value="">— choose —</option>{(projects || []).map((p) => <option key={p.id} value={p.id}>{p.code} {p.name}</option>)}</select>
            </Field>
          )}
          <Field id="ms-name" label="Milestone" required error={errors.name} wide><input id="ms-name" value={v.name} maxLength={200} onChange={set('name')} /></Field>
          <Field id="ms-due" label="Due date" required error={errors.due_date}><input id="ms-due" type="date" value={v.due_date} onChange={set('due_date')} /></Field>
          <Field id="ms-amount" label="Amount to bill (INR)" error={errors.amount} hint="Optional. Used to raise an invoice when the milestone is reached."><input id="ms-amount" inputMode="decimal" value={v.amount} onChange={set('amount')} /></Field>
          <Field id="ms-status" label="Status" error={errors.status}>
            <select id="ms-status" value={v.status} onChange={set('status')}><option value="pending">Pending</option><option value="in_progress">In progress</option><option value="done">Done</option></select>
          </Field>
          {v.status === 'done' && <Field id="ms-done" label="Completed on" error={errors.completed_on} hint="Today when left empty."><input id="ms-done" type="date" max={today()} value={v.completed_on} onChange={set('completed_on')} /></Field>}
          <Field id="ms-desc" label="Notes" error={errors.description} wide><input id="ms-desc" value={v.description} maxLength={500} onChange={set('description')} /></Field>
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </Dialog>
  );
}

// The milestone table with its actions. `projectId` scopes it to one project; without it the project column is shown.
function MilestoneTable({ rows, projects, projectId, reload, canManage, canInvoice, onEdit }) {
  const navigate = useNavigate();
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const patch = async (m, body, message) => {
    try { await api.patch(`/projects/milestones/${m.id}/`, body); notify(message); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const remove = async (m) => {
    if (!(await confirm({ title: `Remove "${m.name}"?`, body: <p>The milestone is deleted. This is recorded in the project activity.</p>, confirmLabel: 'Remove', danger: true }))) return;
    try { await api.del(`/projects/milestones/${m.id}/`); notify('Milestone removed.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const invoice = async (m) => {
    try {
      const r = await api.post(`/projects/milestones/${m.id}/create-invoice/`);
      notify('Draft invoice created. Review it, then issue it.');
      navigate(`/sales/documents/${r.invoice.id}/edit`);
    } catch (e) { notify(e.message, 'error'); }
  };
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Milestone</th>{!projectId && <th>Project</th>}<th>Due</th><th>Status</th><th className="amount">To bill</th><th>Invoice</th>{canManage && <th><span className="sr-only">Actions</span></th>}</tr></thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.id}>
              <td data-label="Milestone"><strong>{m.name}</strong>{m.description && <span className="projects-sub">{m.description}</span>}</td>
              {!projectId && <td data-label="Project"><Link to={`/projects/projects/${m.project.id}`}>{m.project.name}</Link></td>}
              <td data-label="Due">{fmtDate(m.due_date)}{m.days_late > 0 && <span className="projects-sub projects-neg">{m.days_late} day{m.days_late === 1 ? '' : 's'} late</span>}</td>
              <td data-label="Status"><Badge tone={MILESTONE_TONE[m.status_key]}>{m.status_label}</Badge>{m.completed_on && <span className="projects-sub">on {fmtDate(m.completed_on)}</span>}</td>
              <td className="amount" data-label="To bill">{Number(m.amount) > 0 ? plain(m.amount) : '—'}</td>
              <td data-label="Invoice">{m.invoice ? <Link to={`/sales/documents/${m.invoice.id}`}>{m.invoice.number || 'Draft'}</Link> : '—'}</td>
              {canManage && (
                <td className="row-actions">
                  {m.status !== 'done' && <button type="button" className="btn btn-small" onClick={() => patch(m, { status: 'done' }, 'Marked done.')}>Mark done</button>}
                  {m.status === 'pending' && <button type="button" className="btn btn-small" onClick={() => patch(m, { status: 'in_progress' }, 'Started.')}>Start</button>}
                  {m.status === 'done' && <button type="button" className="btn btn-small" onClick={() => patch(m, { status: 'in_progress' }, 'Reopened.')}>Reopen</button>}
                  {canInvoice && !m.invoice && Number(m.amount) > 0 && <button type="button" className="btn btn-small" onClick={() => invoice(m)}>Create invoice</button>}
                  <button type="button" className="btn btn-small" onClick={() => onEdit(m)}>Edit</button>
                  <button type="button" className="btn btn-small" onClick={() => remove(m)}>Remove</button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {dialog}
    </div>
  );
}

export function MilestonePanel({ projectId, onChange }) {
  const { user } = useAuth();
  const canManage = user.permissions.includes('projects.manage');
  const canInvoice = canManage && user.permissions.includes('sales.manage');
  const { data, error, loading, reload } = useLoad(() => api.get('/projects/milestones/', { project: projectId }), [projectId]);
  const [dialog, setDialog] = useState(null);
  const refresh = () => { reload(); onChange && onChange(); };
  return (
    <section className="card" aria-label="Milestones">
      <div className="projects-section-head"><h2>Milestones</h2>{canManage && <button type="button" className="btn btn-small" onClick={() => setDialog({})}>Add milestone</button>}</div>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && data.results.length === 0 && <p className="empty">No milestones yet. Milestones give the project its progress and its billing points.</p>}
      {data && data.results.length > 0 && <MilestoneTable rows={data.results} projectId={projectId} reload={refresh} canManage={canManage} canInvoice={canInvoice} onEdit={(m) => setDialog(m)} />}
      {dialog && <MilestoneDialog projectId={projectId} record={dialog.id ? dialog : null} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); refresh(); }} />}
    </section>
  );
}

export default function Milestones() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const status = params.get('status') || '';
  const projectId = params.get('project') || '';
  const term = params.get('q') || '';
  const [q, setQ] = useState(term);
  const projects = useProjectLookup();
  const [dialog, setDialog] = useState(null);
  const { data, error, loading, reload } = useLoad(
    () => api.get('/projects/milestones/', { status: status || undefined, project: projectId || undefined, q: term || undefined }), [status, projectId, term]);
  const canManage = user.permissions.includes('projects.manage');
  const canInvoice = canManage && user.permissions.includes('sales.manage');
  const setParam = (changes) => {
    const next = { status, project: projectId, q: term, ...changes };
    setParams(Object.fromEntries(Object.entries(next).filter(([, v]) => v)), { replace: true });
  };
  return (
    <>
      <PageHeader title="Milestones" description="Deliverables and billing points across all projects."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setDialog({})}>New milestone</button>} />
      <section className="card">
        <div className="chips" role="group" aria-label="Filter by status">
          {STATUS_CHIPS.map(([key, label]) => <button key={key} type="button" className="chip" aria-pressed={status === key} onClick={() => setParam({ status: key })}>{label}</button>)}
        </div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setParam({ q: q.trim() }); }}>
          <Field id="ml-q" label="Search" className="grow"><input id="ml-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Milestone or project" /></Field>
          <Field id="ml-project" label="Project">
            <select id="ml-project" value={projectId} onChange={(e) => setParam({ project: e.target.value })}><option value="">All projects</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
          </Field>
          <button type="submit" className="btn">Search</button>
          {data?.results.length > 0 && <button type="button" className="btn" onClick={() => downloadCsv('milestones.csv', [['Project', 'Milestone', 'Due', 'Status', 'Amount', 'Completed'], ...data.results.map((m) => [m.project.name, m.name, m.due_date, m.status_label, m.amount, m.completed_on || ''])])}>Export CSV</button>}
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {data && data.results.length === 0 && <p className="empty">No milestones{status || term || projectId ? ' match' : ' yet'}.</p>}
        {data && data.results.length > 0 && <MilestoneTable rows={data.results} projects={projects} reload={reload} canManage={canManage} canInvoice={canInvoice} onEdit={(m) => setDialog(m)} />}
        {data?.results.length > 0 && (
          <div className="list-foot"><span>{data.count} milestone{data.count === 1 ? '' : 's'}</span><span>Overdue <strong>{data.overdue_count}</strong></span><span>To bill <strong>{money(data.total_amount)}</strong></span></div>
        )}
      </section>
      {dialog && <MilestoneDialog projects={projects} record={dialog.id ? dialog : null} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); reload(); }} />}
    </>
  );
}
