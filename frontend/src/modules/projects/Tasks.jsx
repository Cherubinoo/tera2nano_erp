import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { downloadCsv, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, Tabs, fmtDate, useConfirm, useToast } from '../../ui';
import { PRIORITIES, PriorityBadge, TASK_STATUSES, useEmployees, useProjectLookup } from './common';

const STATUS_ORDER = TASK_STATUSES.map(([k]) => k);
const FIELDS = ['project', 'title', 'description', 'assignee_id', 'assignee_name', 'due_date', 'priority', 'status', 'estimated_hours', 'milestone'].map((name) => ({ name }));

export function TaskDialog({ projectId, projects, record, onClose, onSaved, onDelete }) {
  const notify = useToast();
  const employees = useEmployees();
  const isEdit = !!record;
  const [v, setV] = useState(() => ({
    project: record?.project.id || projectId || '', title: record?.title || '', description: record?.description || '', assignee_id: record?.assignee_id || '',
    assignee_name: record?.assignee_name || '', due_date: record?.due_date || '', priority: record?.priority || 'medium', status: record?.status || 'todo',
    estimated_hours: record && num(record.estimated_hours) ? record.estimated_hours : '', milestone: record?.milestone?.id || '',
  }));
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const pid = v.project;
  const milestones = useLoad(() => (pid ? api.get('/projects/milestones/', { project: pid }) : Promise.resolve({ results: [] })), [pid]);
  const team = useLoad(() => (pid ? api.get('/projects/team/', { project: pid }).catch(() => ({ results: [] })) : Promise.resolve({ results: [] })), [pid]);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const people = [...(team.data?.results || []).filter((m) => m.is_active).map((m) => ({ id: m.employee_id || '', name: m.name })),
    ...employees.filter((e) => !(team.data?.results || []).some((m) => m.employee_id === e.id)).map((e) => ({ id: e.id, name: e.name }))];
  const pickPerson = (e) => {
    const idx = e.target.value;
    const person = people[Number(idx)];
    setV((x) => ({ ...x, assignee_id: person ? person.id : '', assignee_name: person ? person.name : '' }));
  };
  const submit = async (ev) => {
    ev.preventDefault(); ev.stopPropagation(); setFailure(null);
    const e = {};
    if (!v.project) e.project = 'Choose the project.';
    if (!v.title.trim()) e.title = 'Enter the task title.';
    if (v.estimated_hours !== '' && !(Number(v.estimated_hours) >= 0)) e.estimated_hours = 'Enter hours of zero or more.';
    if (Object.keys(e).length) { setErrors(e); return; }
    setBusy(true);
    try {
      const body = { title: v.title, description: v.description, assignee_id: v.assignee_id || null, assignee_name: v.assignee_name, due_date: v.due_date || null,
        priority: v.priority, status: v.status, estimated_hours: v.estimated_hours || '0', milestone: v.milestone || null };
      if (!isEdit) body.project = v.project;
      const saved = isEdit ? await api.patch(`/projects/tasks/${record.id}/`, body) : await api.post('/projects/tasks/', body);
      notify(isEdit ? 'Task updated.' : `Task added: ${saved.code}`);
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  const current = people.findIndex((p) => (p.id && p.id === v.assignee_id) || (!p.id && p.name === v.assignee_name));
  return (
    <Dialog title={isEdit ? `Edit ${record.code}` : 'New task'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={FIELDS} />
        <div className="form-grid">
          {!projectId && !isEdit && (
            <Field id="tk-project" label="Project" required error={errors.project} wide>
              <select id="tk-project" value={v.project} onChange={set('project')}><option value="">— choose —</option>{(projects || []).map((p) => <option key={p.id} value={p.id}>{p.code} {p.name}</option>)}</select>
            </Field>
          )}
          <Field id="tk-title" label="Task" required error={errors.title} wide><input id="tk-title" value={v.title} maxLength={200} onChange={set('title')} /></Field>
          <Field id="tk-status" label="Status" error={errors.status}>
            <select id="tk-status" value={v.status} onChange={set('status')}>{TASK_STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          </Field>
          <Field id="tk-priority" label="Priority" error={errors.priority}>
            <select id="tk-priority" value={v.priority} onChange={set('priority')}>{PRIORITIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          </Field>
          {people.length > 0 ? (
            <Field id="tk-assignee" label="Assignee" error={errors.assignee_id}>
              <select id="tk-assignee" value={current >= 0 ? current : ''} onChange={pickPerson}>
                <option value="">{v.assignee_name && current < 0 ? `${v.assignee_name} (typed)` : '— unassigned —'}</option>
                {people.map((p, i) => <option key={`${p.id}-${p.name}`} value={i}>{p.name}</option>)}
              </select>
            </Field>
          ) : (
            <Field id="tk-assignee" label="Assignee" error={errors.assignee_name}><input id="tk-assignee" value={v.assignee_name} maxLength={150} onChange={set('assignee_name')} /></Field>
          )}
          <Field id="tk-due" label="Due date" error={errors.due_date}><input id="tk-due" type="date" value={v.due_date} onChange={set('due_date')} /></Field>
          <Field id="tk-est" label="Estimated hours" error={errors.estimated_hours}><input id="tk-est" inputMode="decimal" value={v.estimated_hours} onChange={set('estimated_hours')} /></Field>
          <Field id="tk-ms" label="Milestone" error={errors.milestone}>
            <select id="tk-ms" value={v.milestone} onChange={set('milestone')}><option value="">— none —</option>{(milestones.data?.results || []).map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</select>
          </Field>
          <Field id="tk-desc" label="Description" error={errors.description} wide><textarea id="tk-desc" rows={3} value={v.description} onChange={set('description')} /></Field>
        </div>
        <div className="dialog-foot-inline">
          {isEdit && onDelete && <button type="button" className="btn btn-danger-outline" onClick={() => onDelete(record)}>Delete</button>}
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save task'}</button>
        </div>
      </form>
    </Dialog>
  );
}

function Card({ t, canManage, showProject, onMove, onEdit, dragging, setDragging }) {
  const i = STATUS_ORDER.indexOf(t.status);
  return (
    <div className={`projects-card${t.overdue ? ' overdue' : ''}${dragging === t.id ? ' dragging' : ''}`} draggable={canManage}
      onDragStart={(e) => { e.dataTransfer.setData('text/plain', t.id); e.dataTransfer.effectAllowed = 'move'; setDragging(t.id); }} onDragEnd={() => setDragging(null)}>
      <button type="button" className="projects-card-title" onClick={() => onEdit(t)}>{t.title}</button>
      <div className="projects-card-meta">
        <span>{t.code}</span>
        {showProject && <Link to={`/projects/projects/${t.project.id}`}>{t.project.name}</Link>}
        {t.assignee_name && <span>{t.assignee_name}</span>}
        {t.due_date && <span className={t.overdue ? 'projects-neg' : ''}>Due {fmtDate(t.due_date)}</span>}
        <span>{plain(t.logged_hours)} / {plain(t.estimated_hours)} h</span>
      </div>
      <div><PriorityBadge value={t.priority} /></div>
      {canManage && (
        <div className="projects-card-move">
          <button type="button" className="btn btn-small" disabled={i <= 0} onClick={() => onMove(t, STATUS_ORDER[i - 1])} aria-label={`Move ${t.title} back to ${TASK_STATUSES[i - 1]?.[1] || ''}`}>←</button>
          <button type="button" className="btn btn-small" disabled={i >= STATUS_ORDER.length - 1} onClick={() => onMove(t, STATUS_ORDER[i + 1])} aria-label={`Move ${t.title} on to ${TASK_STATUSES[i + 1]?.[1] || ''}`}>→</button>
        </div>
      )}
    </div>
  );
}

// Kanban board of tasks with a list view. Drag a card to another column, or use the arrow buttons.
export function TaskBoard({ projectId, projects, onChange, initialNew = false }) {
  const { user } = useAuth();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const canManage = user.permissions.includes('projects.manage');
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState('board');
  const [dialog, setDialog] = useState(initialNew ? {} : null);
  const [dragging, setDragging] = useState(null);
  const [over, setOver] = useState(null);
  const filterProject = projectId || params.get('project') || '';
  const priority = params.get('priority') || '';
  const assignee = params.get('assignee') || '';
  const filter = params.get('status') || '';
  const [who, setWho] = useState(assignee);
  const { data, error, loading, reload } = useLoad(() => api.get('/projects/tasks/', {
    project: filterProject || undefined, priority: priority || undefined, assignee: assignee || undefined,
    status: ['open', 'overdue'].includes(filter) ? filter : undefined }), [filterProject, priority, assignee, filter]);
  const setParam = (changes) => {
    const next = { project: params.get('project') || '', priority, assignee, status: filter, ...changes };
    setParams(Object.fromEntries(Object.entries(next).filter(([, v]) => v)), { replace: true });
  };
  const refresh = () => { reload(); onChange && onChange(); };
  const move = async (t, status) => {
    try { await api.patch(`/projects/tasks/${t.id}/`, { status }); refresh(); } catch (e) { notify(e.message, 'error'); }
  };
  const remove = async (t) => {
    if (!(await confirm({ title: `Delete ${t.code}?`, body: <p>The task is deleted. Hours already logged stay on the project.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/projects/tasks/${t.id}/`); notify('Task deleted.'); refresh(); } catch (e) { notify(e.message, 'error'); }
  };
  const rows = data?.results || [];
  return (
    <section className="card" aria-label="Tasks">
      <div className="projects-section-head">
        <h2>Tasks</h2>
        <div className="projects-actions">
          {canManage && <button type="button" className="btn btn-small btn-primary" onClick={() => setDialog({})}>Add task</button>}
          {rows.length > 0 && <button type="button" className="btn btn-small" onClick={() => downloadCsv('tasks.csv', [['Code', 'Project', 'Task', 'Status', 'Assignee', 'Due', 'Priority', 'Estimated h', 'Logged h'],
            ...rows.map((t) => [t.code, t.project.name, t.title, t.status_label, t.assignee_name, t.due_date || '', t.priority, t.estimated_hours, t.logged_hours])])}>Export CSV</button>}
        </div>
      </div>
      <Tabs tabs={[{ key: 'board', label: 'Board' }, { key: 'list', label: 'List' }]} value={view} onChange={setView} />
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setParam({ assignee: who.trim() }); }}>
        {!projectId && (
          <Field id="tb-project" label="Project">
            <select id="tb-project" value={filterProject} onChange={(e) => setParam({ project: e.target.value })}><option value="">All projects</option>{(projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
          </Field>
        )}
        <Field id="tb-priority" label="Priority">
          <select id="tb-priority" value={priority} onChange={(e) => setParam({ priority: e.target.value })}><option value="">Any</option>{PRIORITIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        </Field>
        <Field id="tb-status" label="Show">
          <select id="tb-status" value={filter} onChange={(e) => setParam({ status: e.target.value })}><option value="">All tasks</option><option value="open">Open tasks</option><option value="overdue">Overdue tasks</option></select>
        </Field>
        <Field id="tb-who" label="Assignee" className="grow"><input id="tb-who" value={who} onChange={(e) => setWho(e.target.value)} placeholder="Name" /></Field>
        <button type="submit" className="btn">Filter</button>
      </form>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && rows.length === 0 && <p className="empty">No tasks{priority || assignee || filter ? ' match' : ' yet'}.</p>}
      {data && rows.length > 0 && view === 'board' && (
        <div className="projects-board">
          {TASK_STATUSES.map(([key, label]) => {
            const col = rows.filter((t) => t.status === key);
            return (
              <div key={key} className={`projects-col${over === key ? ' over' : ''}`} role="group" aria-label={`${label}, ${col.length} tasks`}
                onDragOver={(e) => { if (canManage) { e.preventDefault(); setOver(key); } }} onDragLeave={() => setOver(null)}
                onDrop={(e) => { e.preventDefault(); setOver(null); const id = e.dataTransfer.getData('text/plain'); const t = rows.find((x) => x.id === id); if (t && t.status !== key) move(t, key); }}>
                <div className="projects-col-head"><span>{label}</span><Badge>{col.length}</Badge></div>
                {col.map((t) => <Card key={t.id} t={t} canManage={canManage} showProject={!projectId} onMove={move} onEdit={(x) => canManage && setDialog(x)} dragging={dragging} setDragging={setDragging} />)}
              </div>
            );
          })}
        </div>
      )}
      {data && rows.length > 0 && view === 'list' && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Task</th>{!projectId && <th>Project</th>}<th>Status</th><th>Assignee</th><th>Due</th><th>Priority</th><th className="amount">Est. h</th><th className="amount">Logged h</th>{canManage && <th><span className="sr-only">Actions</span></th>}</tr></thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.id}>
                  <td data-label="Task"><strong>{t.title}</strong><span className="projects-sub">{t.code}{t.milestone ? ` · ${t.milestone.name}` : ''}</span></td>
                  {!projectId && <td data-label="Project"><Link to={`/projects/projects/${t.project.id}`}>{t.project.name}</Link></td>}
                  <td data-label="Status">
                    {canManage ? (
                      <select aria-label={`Status of ${t.title}`} value={t.status} onChange={(e) => move(t, e.target.value)}>{TASK_STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
                    ) : t.status_label}
                  </td>
                  <td data-label="Assignee">{t.assignee_name || '—'}</td>
                  <td data-label="Due" className={t.overdue ? 'projects-neg' : ''}>{fmtDate(t.due_date)}</td>
                  <td data-label="Priority"><PriorityBadge value={t.priority} /></td>
                  <td className="amount" data-label="Est. h">{plain(t.estimated_hours)}</td>
                  <td className="amount" data-label="Logged h">{plain(t.logged_hours)}</td>
                  {canManage && <td className="row-actions"><button type="button" className="btn btn-small" onClick={() => setDialog(t)}>Edit</button><button type="button" className="btn btn-small" onClick={() => remove(t)}>Delete</button></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && rows.length > 0 && <div className="list-foot"><span>{data.count} task{data.count === 1 ? '' : 's'}</span><span>Estimated <strong>{plain(data.estimated_hours)} h</strong></span><span>Logged <strong>{plain(data.logged_hours)} h</strong></span></div>}
      {dialog && (
        <TaskDialog projectId={projectId} projects={projects} record={dialog.id ? dialog : null} onClose={() => setDialog(null)}
          onSaved={() => { setDialog(null); refresh(); }} onDelete={(t) => { setDialog(null); remove(t); }} />
      )}
      {confirmDialog}
    </section>
  );
}

export default function Tasks() {
  const projects = useProjectLookup();
  const [params] = useSearchParams();
  return (
    <>
      <PageHeader title="Tasks" description="Work items across all projects. Drag a card to another column to change its status." />
      <TaskBoard projects={projects} initialNew={params.get('new') === '1'} />
    </>
  );
}
