import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart, Donut, HBars } from '../../components/charts';
import { useAuth } from '../../context';
import { asList, useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, fmtDate, fmtDateTime, useConfirm, useToast } from '../../ui';
import { Avatar, PRIORITIES, PRIORITY_TONE, PersonSelect, STATUS_COLOUR, TASK_STATUSES, TASK_TONE, useCan } from './common';

export const TaskStatusBadge = ({ task }) => <Badge tone={task.overdue ? 'bad' : TASK_TONE[task.status] || 'neutral'}>{task.overdue ? 'Overdue' : task.status_label}</Badge>;
export const PriorityBadge = ({ task }) => <Badge tone={PRIORITY_TONE[task.priority]}>{task.priority_label || task.priority}</Badge>;

// Same moves the server allows: a finished task can only be reopened.
const FLOW = {
  todo: ['in_progress', 'blocked', 'done', 'cancelled'], in_progress: ['todo', 'blocked', 'done', 'cancelled'],
  blocked: ['todo', 'in_progress', 'done', 'cancelled'], done: ['todo'], cancelled: ['todo'],
};
export const movesFrom = (status) => TASK_STATUSES.filter(([k]) => (FLOW[status] || []).includes(k));

export function TaskCharts({ analytics }) {
  const slices = TASK_STATUSES.map(([key, label]) => ({ key, label, colour: STATUS_COLOUR[key], value: analytics.by_status[key] || 0 }));
  return (
    <>
      <div className="grid-2">
        <section className="card" aria-label="Tasks by status">
          <h2>Tasks by status</h2>
          <p className="muted">Every task, by where it stands.</p>
          <Donut slices={slices} centreLabel="tasks" />
        </section>
        <section className="card" aria-label="Open tasks by assignee">
          <h2>Open tasks by person</h2>
          <p className="muted">Who is carrying the most work.</p>
          <HBars rows={analytics.by_assignee.map((a) => ({ key: a.user_id || 'none', label: a.name, value: a.open }))} format={(v) => String(v)}
            empty="No open tasks." />
        </section>
      </div>
      <section className="card" aria-label="Tasks completed per week">
        <h2>Completed per week</h2>
        <p className="muted">Tasks marked done in each of the last eight weeks.</p>
        <ColumnChart title="Tasks completed per week" format={(v) => String(Math.round(v))}
          series={[{ key: 'completed', label: 'Completed', colour: 'var(--chart-2)' }]}
          data={analytics.completed_per_week.map((w) => ({ label: w.label, values: { completed: w.completed } }))} />
      </section>
    </>
  );
}

const splitLabels = (s) => [...new Set(s.split(',').map((x) => x.trim()).filter(Boolean))];

function useProjects() {
  const { data } = useLoad(() => api.get('/projects/lookup/').catch(() => []), []);
  return asList(data);
}

// Create a task. A manager can give it to anyone; everyone else creates tasks for themselves.
export function TaskFormDialog({ people, onClose, onSaved, defaults = {} }) {
  const { user } = useAuth();
  const can = useCan();
  const notify = useToast();
  const projects = useProjects();
  const manager = can('workplace.manage');
  const [v, setV] = useState({ title: '', description: '', assignee: user.id, due_date: '', priority: 'medium', labels: '', project: '', ...defaults });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };

  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setFailure(null);
    const errs = {};
    if (!v.title.trim()) errs.title = 'Enter the task title.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const body = { title: v.title.trim(), description: v.description, assignee: manager ? v.assignee : user.id, due_date: v.due_date,
        priority: v.priority, labels: splitLabels(v.labels), project: v.project || null };
      if (defaults.source) Object.assign(body, { source: defaults.source, source_id: defaults.source_id });
      const t = await api.post('/workplace/tasks/', body);
      notify(`Task ${t.number} created.`);
      onSaved(t);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };

  return (
    <Dialog title="New task" onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={[{ name: 'title' }, { name: 'assignee' }, { name: 'due_date' }, { name: 'priority' }, { name: 'labels' }, { name: 'project' }, { name: 'description' }]} />
        <div className="form-grid">
          <Field id="tk-title" label="Title" required wide error={errors.title}><input id="tk-title" value={v.title} maxLength={250} onChange={set('title')} /></Field>
          <Field id="tk-desc" label="Description" wide error={errors.description}><textarea id="tk-desc" rows={3} value={v.description} onChange={set('description')} /></Field>
          {manager
            ? <PersonSelect id="tk-assignee" label="Assigned to" value={v.assignee} onChange={(x) => setV((s) => ({ ...s, assignee: x }))} people={people} none="Unassigned" error={errors.assignee} />
            : <Field id="tk-assignee" label="Assigned to" hint="You can create tasks for yourself."><input id="tk-assignee" value="Me" readOnly /></Field>}
          <Field id="tk-due" label="Due date" error={errors.due_date}><input id="tk-due" type="date" value={v.due_date} onChange={set('due_date')} /></Field>
          <Field id="tk-priority" label="Priority" error={errors.priority}>
            <select id="tk-priority" value={v.priority} onChange={set('priority')}>{PRIORITIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          </Field>
          <Field id="tk-labels" label="Labels" hint="Separate with commas." error={errors.labels}><input id="tk-labels" value={v.labels} onChange={set('labels')} /></Field>
          <Field id="tk-project" label="Project" error={errors.project}>
            <select id="tk-project" value={v.project} onChange={set('project')}>
              <option value="">No project</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.code ? `${p.code} · ` : ''}{p.name}</option>)}
            </select>
          </Field>
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Create task'}</button>
        </div>
      </form>
    </Dialog>
  );
}

// A task opened: status, details, checklist and comments.
export function TaskDialog({ id, people, onClose, onChanged }) {
  const { user } = useAuth();
  const can = useCan();
  const notify = useToast();
  const projects = useProjects();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { data: t, error, loading, reload } = useLoad(() => api.get(`/workplace/tasks/${id}/`), [id]);
  const [edit, setEdit] = useState(null);
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [item, setItem] = useState('');
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);

  const manager = can('workplace.manage');
  const canEdit = t && (manager || t.reporter?.id === user.id);
  const canWork = t && (canEdit || t.assignee?.id === user.id);
  const changed = () => { reload(); onChanged && onChanged(); };
  const run = async (fn, ok) => {
    setFailure(null);
    setBusy(true);
    try { await fn(); if (ok) notify(ok); changed(); } catch (err) { setFailure(err); }
    setBusy(false);
  };
  const startEdit = () => setEdit({ title: t.title, description: t.description, assignee: t.assignee?.id || '', due_date: t.due_date || '',
    priority: t.priority, labels: (t.labels || []).join(', '), project: t.project || '' });
  const save = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!edit.title.trim()) { setErrors({ title: 'Enter the task title.' }); return; }
    setErrors({});
    const body = { title: edit.title.trim(), description: edit.description, due_date: edit.due_date, priority: edit.priority,
      labels: splitLabels(edit.labels), project: edit.project || null };
    if (manager) body.assignee = edit.assignee;
    run(async () => { try { await api.patch(`/workplace/tasks/${id}/`, body); setEdit(null); } catch (err) { setErrors(err.fieldErrors || {}); throw err; } }, 'Task saved.');
  };
  const remove = async () => {
    if (!(await confirm({ title: `Delete ${t.number}?`, body: <p>The task, its checklist and comments are removed. This is recorded in the change history.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/workplace/tasks/${id}/`); notify('Task deleted.'); onChanged && onChanged(); onClose(); } catch (err) { setFailure(err); }
  };
  const set = (k) => (e) => setEdit((x) => ({ ...x, [k]: e.target.value }));

  return (
    <Dialog title={t ? `${t.number}` : 'Task'} onClose={onClose} wide>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {t && (
        <div className="wp-task">
          <FormErrors error={failure} fields={[{ name: 'title' }, { name: 'description' }, { name: 'due_date' }, { name: 'priority' }, { name: 'labels' }, { name: 'assignee' }, { name: 'project' }]} />
          <div className="wp-task-head">
            <div>
              {edit ? null : <h3>{t.title}</h3>}
              <div className="wp-chips"><TaskStatusBadge task={t} /> <PriorityBadge task={t} />{t.labels.map((l) => <Badge key={l} tone="neutral">{l}</Badge>)}</div>
            </div>
            {canWork && movesFrom(t.status).length > 0 && (
              <Field id="tk-move" label="Move to">
                <select id="tk-move" value="" disabled={busy} onChange={(e) => e.target.value && run(() => api.post(`/workplace/tasks/${id}/status/`, { status: e.target.value }), 'Status updated.')}>
                  <option value="">Choose…</option>
                  {movesFrom(t.status).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
              </Field>
            )}
          </div>

          {edit ? (
            <form onSubmit={save} noValidate className="subcard">
              <div className="form-grid">
                <Field id="te-title" label="Title" required wide error={errors.title}><input id="te-title" value={edit.title} maxLength={250} onChange={set('title')} /></Field>
                <Field id="te-desc" label="Description" wide error={errors.description}><textarea id="te-desc" rows={3} value={edit.description} onChange={set('description')} /></Field>
                {manager && <PersonSelect id="te-assignee" label="Assigned to" value={edit.assignee} onChange={(x) => setEdit((s) => ({ ...s, assignee: x }))} people={people} none="Unassigned" error={errors.assignee} />}
                <Field id="te-due" label="Due date" error={errors.due_date}><input id="te-due" type="date" value={edit.due_date} onChange={set('due_date')} /></Field>
                <Field id="te-priority" label="Priority" error={errors.priority}>
                  <select id="te-priority" value={edit.priority} onChange={set('priority')}>{PRIORITIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
                </Field>
                <Field id="te-labels" label="Labels" hint="Separate with commas." error={errors.labels}><input id="te-labels" value={edit.labels} onChange={set('labels')} /></Field>
                <Field id="te-project" label="Project" error={errors.project}>
                  <select id="te-project" value={edit.project} onChange={set('project')}>
                    <option value="">No project</option>
                    {projects.map((p) => <option key={p.id} value={p.id}>{p.code ? `${p.code} · ` : ''}{p.name}</option>)}
                  </select>
                </Field>
              </div>
              <div className="dialog-foot-inline">
                <button type="button" className="btn" onClick={() => setEdit(null)}>Cancel</button>
                <button type="submit" className="btn btn-primary" disabled={busy}>Save task</button>
              </div>
            </form>
          ) : (
            <>
              {t.description && <p className="wp-pre">{t.description}</p>}
              <dl className="facts">
                <div><dt>Assigned to</dt><dd>{t.assignee ? <><Avatar person={t.assignee} /> {t.assignee.name}</> : 'Unassigned'}</dd></div>
                <div><dt>Raised by</dt><dd>{t.reporter?.name || '—'}</dd></div>
                <div><dt>Due</dt><dd className={t.overdue ? 'bad-text' : ''}>{fmtDate(t.due_date)}</dd></div>
                {t.completed_at && <div><dt>Completed</dt><dd>{fmtDateTime(t.completed_at)}</dd></div>}
                <div><dt>Source</dt><dd>{t.source === 'meeting' ? <Link to={`/workplace/meetings/${t.source_id}`}>{t.source_label || 'Meeting'}</Link> : t.source === 'manual' ? 'Added by hand' : t.source_label || t.source}</dd></div>
                {t.project && <div><dt>Project</dt><dd>{projects.find((p) => p.id === t.project)?.name || 'Linked project'}</dd></div>}
              </dl>
              {canEdit && (
                <div className="actions">
                  <button type="button" className="btn btn-small" onClick={startEdit}>Edit task</button>
                  <button type="button" className="btn btn-small btn-danger-outline" onClick={remove}>Delete</button>
                </div>
              )}
            </>
          )}

          <section className="subcard" aria-label="Checklist">
            <h3>Checklist {t.subtasks_total > 0 && <span className="hint">{t.subtasks_done} of {t.subtasks_total} done</span>}</h3>
            {t.subtasks_total > 0 && <progress className="wp-progress" max={t.subtasks_total} value={t.subtasks_done} aria-label="Checklist progress" />}
            <ul className="wp-checks">
              {t.items.map((i) => (
                <li key={i.id}>
                  <label className="check">
                    <input type="checkbox" checked={i.done} disabled={!canWork || busy}
                      onChange={(e) => run(() => api.patch(`/workplace/tasks/${id}/items/${i.id}/`, { done: e.target.checked }))} />
                    <span className={i.done ? 'wp-done' : ''}>{i.text}</span>
                  </label>
                  {canWork && <button type="button" className="icon-btn" aria-label={`Remove ${i.text}`} onClick={() => run(() => api.del(`/workplace/tasks/${id}/items/${i.id}/`))}>×</button>}
                </li>
              ))}
            </ul>
            {canWork && (
              <form className="toolbar" onSubmit={(e) => { e.preventDefault(); e.stopPropagation(); if (item.trim()) run(async () => { await api.post(`/workplace/tasks/${id}/items/`, { text: item.trim() }); setItem(''); }); }}>
                <Field id="tk-item" label="Add a checklist item" className="grow"><input id="tk-item" value={item} maxLength={300} onChange={(e) => setItem(e.target.value)} /></Field>
                <button type="submit" className="btn btn-small" disabled={busy}>Add</button>
              </form>
            )}
          </section>

          <section className="subcard" aria-label="Comments">
            <h3>Comments</h3>
            {t.comments.length === 0 && <p className="muted">No comments yet.</p>}
            <ul className="wp-comments">
              {t.comments.map((c) => (
                <li key={c.id}>
                  <Avatar person={c.author} />
                  <div>
                    <strong>{c.author?.name || 'Someone'}</strong> <span className="hint">{fmtDateTime(c.created_at)}</span>
                    <p className="wp-pre">{c.body}</p>
                    {(manager || c.author?.id === user.id) && <button type="button" className="btn btn-small" onClick={() => run(() => api.del(`/workplace/tasks/${id}/comments/${c.id}/`))}>Remove</button>}
                  </div>
                </li>
              ))}
            </ul>
            <form onSubmit={(e) => { e.preventDefault(); e.stopPropagation(); if (comment.trim()) run(async () => { await api.post(`/workplace/tasks/${id}/comments/`, { body: comment.trim() }); setComment(''); }); }}>
              <Field id="tk-comment" label="Add a comment"><textarea id="tk-comment" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
              <button type="submit" className="btn btn-small" disabled={busy}>Post comment</button>
            </form>
          </section>
        </div>
      )}
      {confirmDialog}
    </Dialog>
  );
}
