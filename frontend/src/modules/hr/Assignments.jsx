import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { HBars } from '../../components/charts';
import { dateOrder } from '../../forms';
import { asList } from '../../hooks';
import { ErrorBox, Field, Loading, Notice, PageHeader, fmtDate, useToast } from '../../ui';
import { FormDialog, H, Tile, choose, options, useCan, useData } from './common';

// Assign an employee to a project (or edit one). The project list comes from the projects module; if it is not reachable
// the form says so instead of failing.
export function AssignmentDialog({ record, employee, onClose, onSaved }) {
  const notify = useToast();
  const projects = useData('/projects/lookup/');
  const people = useData(`${H}/lookup/`, undefined, !employee);
  const fields = useMemo(() => [
    ...(employee ? [] : [{ name: 'employee', label: 'Employee', type: 'select', required: true, options: choose(options(asList(people.data), (p) => p.name)) }]),
    { name: 'project_id', label: 'Project', type: 'select', required: true, options: choose(options(asList(projects.data), (p) => `${p.code} ${p.name}`), '— choose a project —') },
    { name: 'role', label: 'Role on the project' },
    { name: 'allocation_percent', label: 'Allocation (%)', type: 'number', min: 1, max: 100, integer: true, required: true, hint: 'Their total across projects can be at most 100% on any day.' },
    { name: 'start_date', label: 'From', type: 'date', required: true },
    { name: 'end_date', label: 'To (leave empty if ongoing)', type: 'date', validate: dateOrder('start_date', 'end_date') },
    { name: 'notes', label: 'Notes', wide: true },
  ], [employee, projects.data, people.data]);
  if (projects.loading || (!employee && people.loading)) return null;
  return (
    <FormDialog title={record ? 'Edit assignment' : 'Assign to a project'} fields={fields} onlyChanged={!!record} submitLabel="Save"
      record={record ? { ...record, employee: record.employee?.id } : undefined} defaults={{ allocation_percent: 100, start_date: new Date().toISOString().slice(0, 10), employee: employee?.id }}
      intro={asList(projects.data).length === 0 ? <Notice type="warn">No projects were found. Create a project first.</Notice> : null}
      onSubmit={async (p) => {
        const body = { ...p, ...(employee ? { employee: employee.id } : {}) };
        if (record) await api.patch(`${H}/assignments/${record.id}/`, body); else await api.post(`${H}/assignments/`, body);
        notify('Assignment saved.'); onSaved();
      }} onClose={onClose} />
  );
}

export default function Assignments() {
  const can = useCan();
  const notify = useToast();
  const [params, setParams] = useSearchParams();
  const project = params.get('project') || '';
  const [dialog, setDialog] = useState(null);
  const [current, setCurrent] = useState(true);
  const rows = useData(`${H}/assignments/`, { project: project || undefined, current: current ? 'true' : undefined });
  const util = useData(`${H}/assignments/utilisation/`);
  const projects = useData('/projects/lookup/');
  const manage = can('hr.manage');
  const remove = async (a) => { try { await api.del(`${H}/assignments/${a.id}/`); notify('Assignment removed.'); rows.reload(); util.reload(); } catch (err) { notify(err.message, 'error'); } };
  const u = util.data;
  return (
    <>
      <PageHeader title="Project assignments" description="Who is working on which project, and how much of their time is committed."
        actions={manage && <button type="button" className="btn btn-primary" onClick={() => setDialog({})}>New assignment</button>} />
      {u && (
        <div className="tiles">
          <Tile label="Average utilisation" value={`${u.average}%`} hint="Of everyone's time, today" />
          <Tile label="Without a project" value={u.bench} hint="People with nothing allocated today" tone={u.bench ? 'warn' : ''} />
          <Tile label="Over-allocated" value={u.over} tone={u.over ? 'bad' : ''} />
        </div>
      )}
      <section className="card" aria-label="Utilisation">
        <h2>Utilisation today</h2>
        <p className="muted">Share of each person's time allocated to projects today.</p>
        {util.loading ? <Loading /> : <HBars rows={asList(u?.rows).map((r) => ({ key: r.employee.id, label: r.employee.name, value: r.allocation, colour: r.allocation > 100 ? 'var(--chart-4)' : r.allocation === 0 ? 'var(--chart-grey)' : 'var(--chart-2)' }))} format={(v) => `${v}%`} empty="No employees yet." />}
      </section>
      <section className="card">
        <form className="toolbar" onSubmit={(e) => e.preventDefault()}>
          <Field id="as-project" label="Project">
            <select id="as-project" value={project} onChange={(e) => setParams(e.target.value ? { project: e.target.value } : {})}>
              <option value="">All projects</option>
              {asList(projects.data).map((p) => <option key={p.id} value={p.id}>{p.code} {p.name}</option>)}
            </select>
          </Field>
          <label className="checkbox-inline"><input type="checkbox" checked={current} onChange={(e) => setCurrent(e.target.checked)} /> Current assignments only</label>
        </form>
        {rows.loading ? <Loading /> : <ErrorBox error={rows.error} onRetry={rows.reload} />}
        {!rows.loading && !rows.error && asList(rows.data).length === 0 && <p className="empty">No assignments match.</p>}
        {asList(rows.data).length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Employee</th><th>Project</th><th>Role</th><th className="num">Allocation</th><th>From</th><th>To</th>{manage && <th />}</tr></thead>
            <tbody>{asList(rows.data).map((a) => (
              <tr key={a.id}>
                <td data-label="Employee"><Link to={`/hr/employees/${a.employee.id}`}>{a.employee.name}</Link></td>
                <td data-label="Project"><Link to={`/projects/projects/${a.project_id}`}>{a.project_code ? `${a.project_code} ` : ''}{a.project_name || 'Project'}</Link></td>
                <td data-label="Role">{a.role || '—'}</td><td className="num" data-label="Allocation">{a.allocation_percent}%</td>
                <td data-label="From">{fmtDate(a.start_date)}</td><td data-label="To">{a.end_date ? fmtDate(a.end_date) : 'Ongoing'}</td>
                {manage && <td className="hr-row-actions"><button type="button" className="btn btn-small" onClick={() => setDialog(a)}>Edit</button><button type="button" className="btn btn-small btn-danger-outline" onClick={() => remove(a)}>Remove</button></td>}
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </section>
      {dialog && <AssignmentDialog record={dialog.id ? dialog : null} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); rows.reload(); util.reload(); }} />}
    </>
  );
}
