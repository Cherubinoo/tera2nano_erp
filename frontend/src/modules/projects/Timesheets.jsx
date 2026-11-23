import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart } from '../../components/charts';
import { useAuth } from '../../context';
import { downloadCsv, money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, fmtDate, useConfirm, useToast } from '../../ui';
import { useEmployees, useProjectLookup } from './common';

const FIELDS = ['project', 'task', 'date', 'hours', 'billable', 'rate', 'employee_name', 'employee_id', 'notes'].map((name) => ({ name }));

export function TimeDialog({ projectId, projects, record, onClose, onSaved }) {
  const notify = useToast();
  const { user } = useAuth();
  const employees = useEmployees();
  const isEdit = !!record;
  const [v, setV] = useState(() => ({
    project: record?.project.id || projectId || '', task: record?.task?.id || '', date: record?.date || today(), hours: record?.hours || '',
    billable: record ? record.billable : true, rate: record ? record.rate : '', employee_id: record?.employee_id || '',
    employee_name: record ? record.employee_name : (user.full_name || user.username || ''), notes: record?.notes || '',
  }));
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const pid = v.project;
  const tasks = useLoad(() => (pid ? api.get('/projects/tasks/', { project: pid }) : Promise.resolve({ results: [] })), [pid]);
  const team = useLoad(() => (pid ? api.get('/projects/team/', { project: pid }).catch(() => ({ results: [] })) : Promise.resolve({ results: [] })), [pid]);
  const people = [...(team.data?.results || []).map((m) => ({ id: m.employee_id || '', name: m.name, rate: m.hourly_rate })),
    ...employees.filter((e) => !(team.data?.results || []).some((m) => m.employee_id === e.id)).map((e) => ({ id: e.id, name: e.name, rate: '' }))];
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const pick = (e) => {
    const p = people[Number(e.target.value)];
    setV((x) => ({ ...x, employee_id: p ? p.id : '', employee_name: p ? p.name : '', rate: p && num(p.rate) > 0 && !isEdit ? p.rate : x.rate }));
  };
  const amount = v.billable ? num(v.hours) * num(v.rate) : 0;
  const submit = async (ev) => {
    ev.preventDefault(); ev.stopPropagation(); setFailure(null);
    const e = {};
    if (!v.project) e.project = 'Choose the project.';
    if (!v.date) e.date = 'Enter the date.';
    else if (v.date > today()) e.date = 'Time cannot be logged for a future date.';
    if (!(num(v.hours) > 0) || num(v.hours) > 24) e.hours = 'Enter hours between 0 and 24.';
    if (v.rate !== '' && !(Number(v.rate) >= 0)) e.rate = 'Enter a rate of zero or more.';
    if (Object.keys(e).length) { setErrors(e); return; }
    setBusy(true);
    try {
      const body = { date: v.date, hours: v.hours, billable: v.billable, employee_id: v.employee_id || null, employee_name: v.employee_name, notes: v.notes, task: v.task || null };
      if (v.rate !== '') body.rate = v.rate;
      if (!isEdit) body.project = v.project;
      const saved = isEdit ? await api.patch(`/projects/timesheets/${record.id}/`, body) : await api.post('/projects/timesheets/', body);
      notify(isEdit ? 'Time entry updated.' : 'Time logged.');
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  const current = people.findIndex((p) => (p.id && p.id === v.employee_id) || (!p.id && p.name === v.employee_name));
  return (
    <Dialog title={isEdit ? 'Edit time entry' : 'Log time'} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={FIELDS} />
        <div className="form-grid">
          {!projectId && !isEdit && (
            <Field id="ts-project" label="Project" required error={errors.project} wide>
              <select id="ts-project" value={v.project} onChange={set('project')}><option value="">— choose —</option>{(projects || []).map((p) => <option key={p.id} value={p.id}>{p.code} {p.name}</option>)}</select>
            </Field>
          )}
          <Field id="ts-task" label="Task" error={errors.task}>
            <select id="ts-task" value={v.task} onChange={set('task')}><option value="">— none —</option>{(tasks.data?.results || []).map((t) => <option key={t.id} value={t.id}>{t.code} {t.title}</option>)}</select>
          </Field>
          <Field id="ts-date" label="Date" required error={errors.date}><input id="ts-date" type="date" max={today()} value={v.date} onChange={set('date')} /></Field>
          <Field id="ts-hours" label="Hours" required error={errors.hours} hint="For example 1.5"><input id="ts-hours" inputMode="decimal" value={v.hours} onChange={set('hours')} /></Field>
          {people.length > 0 ? (
            <Field id="ts-emp" label="Person" error={errors.employee_name}>
              <select id="ts-emp" value={current >= 0 ? current : ''} onChange={pick}>
                <option value="">{v.employee_name && current < 0 ? `${v.employee_name} (you)` : '— choose —'}</option>
                {people.map((p, i) => <option key={`${p.id}-${p.name}`} value={i}>{p.name}</option>)}
              </select>
            </Field>
          ) : (
            <Field id="ts-emp" label="Person" error={errors.employee_name}><input id="ts-emp" value={v.employee_name} maxLength={150} onChange={set('employee_name')} /></Field>
          )}
          <Field id="ts-rate" label="Rate per hour (INR)" error={errors.rate} hint="Empty uses the team member's or the project's rate."><input id="ts-rate" inputMode="decimal" value={v.rate} onChange={set('rate')} /></Field>
          <div className="field field-check">
            <label className="check"><input type="checkbox" checked={v.billable} onChange={(e) => setV((x) => ({ ...x, billable: e.target.checked }))} /> Billable</label>
            {v.billable && num(v.rate) > 0 && num(v.hours) > 0 && <div className="hint">Billable value {money(amount)}</div>}
          </div>
          <Field id="ts-notes" label="Notes" error={errors.notes} wide><input id="ts-notes" value={v.notes} maxLength={300} onChange={set('notes')} /></Field>
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </Dialog>
  );
}

// Timesheet entries with weekly totals. `projectId` scopes it to one project.
export function TimesheetPanel({ projectId, projects, onChange, initialNew = false }) {
  const { user } = useAuth();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const canManage = user.permissions.includes('projects.manage');
  const [params, setParams] = useSearchParams();
  const [dialog, setDialog] = useState(initialNew ? {} : null);
  const f = { project: projectId || params.get('project') || '', from: params.get('from') || '', to: params.get('to') || '', employee: params.get('employee') || '', billable: params.get('billable') || '' };
  const [who, setWho] = useState(f.employee);
  const { data, error, loading, reload } = useLoad(() => api.get('/projects/timesheets/', {
    project: f.project || undefined, from: f.from || undefined, to: f.to || undefined, employee: f.employee || undefined, billable: f.billable || undefined }),
  [f.project, f.from, f.to, f.employee, f.billable]);
  const setParam = (changes) => {
    const next = { ...(projectId ? {} : { project: f.project }), from: f.from, to: f.to, employee: f.employee, billable: f.billable, ...changes };
    setParams(Object.fromEntries(Object.entries(next).filter(([, v]) => v)), { replace: true });
  };
  const refresh = () => { reload(); onChange && onChange(); };
  const remove = async (e) => {
    if (!(await confirm({ title: 'Delete this time entry?', body: <p>{e.employee_name}, {plain(e.hours)} h on {fmtDate(e.date)}.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/projects/timesheets/${e.id}/`); notify('Time entry deleted.'); refresh(); } catch (err) { notify(err.message, 'error'); }
  };
  const weeks = (data?.weekly || []).slice(0, 8).reverse();
  return (
    <section className="card" aria-label="Timesheets">
      <div className="projects-section-head">
        <h2>Timesheet</h2>
        <div className="projects-actions">
          {canManage && <button type="button" className="btn btn-small btn-primary" onClick={() => setDialog({})}>Log time</button>}
          {data?.results.length > 0 && <button type="button" className="btn btn-small" onClick={() => downloadCsv('timesheets.csv', [['Date', 'Project', 'Task', 'Person', 'Hours', 'Billable', 'Rate', 'Amount', 'Notes'],
            ...data.results.map((e) => [e.date, e.project.name, e.task?.code || '', e.employee_name, e.hours, e.billable ? 'Yes' : 'No', e.rate, e.amount, e.notes])])}>Export CSV</button>}
        </div>
      </div>
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setParam({ employee: who.trim() }); }}>
        {!projectId && (
          <Field id="tsf-project" label="Project">
            <select id="tsf-project" value={f.project} onChange={(e) => setParam({ project: e.target.value })}><option value="">All projects</option>{(projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
          </Field>
        )}
        <Field id="tsf-from" label="From"><input id="tsf-from" type="date" value={f.from} onChange={(e) => setParam({ from: e.target.value })} /></Field>
        <Field id="tsf-to" label="To"><input id="tsf-to" type="date" value={f.to} onChange={(e) => setParam({ to: e.target.value })} /></Field>
        <Field id="tsf-bill" label="Billable">
          <select id="tsf-bill" value={f.billable} onChange={(e) => setParam({ billable: e.target.value })}><option value="">Both</option><option value="true">Billable</option><option value="false">Non-billable</option></select>
        </Field>
        <Field id="tsf-who" label="Person" className="grow"><input id="tsf-who" value={who} onChange={(e) => setWho(e.target.value)} placeholder="Name" /></Field>
        <button type="submit" className="btn">Filter</button>
      </form>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && (
        <div className="balance-strip" aria-label="Totals">
          <div><span>Hours</span><strong>{plain(data.hours)}</strong></div>
          <div><span>Billable hours</span><strong>{plain(data.billable_hours)}</strong></div>
          <div><span>Non-billable</span><strong>{plain(data.non_billable_hours)}</strong></div>
          <div><span>Billable value</span><strong>{money(data.amount)}</strong></div>
        </div>
      )}
      {data && data.results.length === 0 && <p className="empty">No time logged{f.from || f.to || f.employee || f.billable ? ' for this filter' : ' yet'}.</p>}
      {weeks.length > 1 && (
        <div className="grid-2">
          <div><h3>Hours per week</h3>
            <ColumnChart title="Hours per week" format={(v) => plain(v)}
              series={[{ key: 'billable', label: 'Billable', colour: 'var(--chart-2)' }, { key: 'other', label: 'Non-billable', colour: 'var(--chart-grey)' }]}
              data={weeks.map((w) => ({ label: fmtDate(w.week_start).slice(0, 6), values: { billable: w.billable_hours, other: num(w.hours) - num(w.billable_hours) } }))} />
          </div>
          <div><h3>Weekly totals</h3>
            <div className="table-wrap"><table>
              <thead><tr><th>Week of</th><th className="amount">Hours</th><th className="amount">Billable h</th><th className="amount">Value</th></tr></thead>
              <tbody>{(data.weekly || []).slice(0, 8).map((w) => (
                <tr key={w.week_start}><td data-label="Week of">{fmtDate(w.week_start)}</td><td className="amount" data-label="Hours">{plain(w.hours)}</td>
                  <td className="amount" data-label="Billable h">{plain(w.billable_hours)}</td><td className="amount" data-label="Value">{plain(w.amount)}</td></tr>
              ))}</tbody>
            </table></div>
          </div>
        </div>
      )}
      {data && data.results.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Date</th>{!projectId && <th>Project</th>}<th>Person</th><th>Task</th><th className="amount">Hours</th><th>Billable</th><th className="amount">Rate</th><th className="amount">Value</th>{canManage && <th><span className="sr-only">Actions</span></th>}</tr></thead>
            <tbody>
              {data.results.map((e) => (
                <tr key={e.id}>
                  <td data-label="Date">{fmtDate(e.date)}{e.notes && <span className="projects-sub">{e.notes}</span>}</td>
                  {!projectId && <td data-label="Project"><Link to={`/projects/projects/${e.project.id}`}>{e.project.name}</Link></td>}
                  <td data-label="Person">{e.employee_name || '—'}</td>
                  <td data-label="Task">{e.task ? `${e.task.code} ${e.task.title}` : '—'}</td>
                  <td className="amount" data-label="Hours">{plain(e.hours)}</td>
                  <td data-label="Billable">{e.billable ? <Badge tone="good">Billable</Badge> : <Badge>No</Badge>}</td>
                  <td className="amount" data-label="Rate">{plain(e.rate)}</td>
                  <td className="amount" data-label="Value">{plain(e.amount)}</td>
                  {canManage && <td className="row-actions"><button type="button" className="btn btn-small" onClick={() => setDialog(e)}>Edit</button><button type="button" className="btn btn-small" onClick={() => remove(e)}>Delete</button></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {dialog && <TimeDialog projectId={projectId} projects={projects} record={dialog.id ? dialog : null} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); refresh(); }} />}
      {confirmDialog}
    </section>
  );
}

export default function Timesheets() {
  const projects = useProjectLookup();
  const [params] = useSearchParams();
  return (
    <>
      <PageHeader title="Timesheets" description="Hours logged on projects, with weekly totals and what the billable time is worth." />
      <TimesheetPanel projects={projects} initialNew={params.get('new') === '1'} />
    </>
  );
}
