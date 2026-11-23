import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useLoad } from '../../hooks';
import { ErrorBox, Field, Loading, PageHeader, Tabs, fmtDate, useToast } from '../../ui';
import { Avatar, PRIORITIES, PersonSelect, TASK_STATUSES, usePeople, useCan } from './common';
import { PriorityBadge, TaskCharts, TaskDialog, TaskFormDialog, TaskStatusBadge, movesFrom } from './tasks-shared';

const BOARD = TASK_STATUSES.filter(([k]) => k !== 'cancelled');

function Card({ t, onOpen, onMove, canDrag }) {
  const moves = movesFrom(t.status);
  return (
    <li className={`wp-card${t.overdue ? ' overdue' : ''}`} draggable={canDrag} onDragStart={(e) => e.dataTransfer.setData('text/plain', t.id)}>
      <button type="button" className="wp-card-title" onClick={() => onOpen(t.id)}>{t.title}</button>
      <div className="hint">{t.number}</div>
      <div className="wp-card-meta">
        <PriorityBadge task={t} />
        {t.due_date && <span className={t.overdue ? 'bad-text' : 'hint'}>{t.overdue ? 'Overdue ' : 'Due '}{fmtDate(t.due_date)}</span>}
        {t.subtasks_total > 0 && <span className="hint">{t.subtasks_done}/{t.subtasks_total} done</span>}
        <Avatar person={t.assignee} />
      </div>
      {moves.length > 0 && (
        <select aria-label={`Move ${t.title} to`} value="" onChange={(e) => e.target.value && onMove(t, e.target.value)}>
          <option value="">Move to…</option>
          {moves.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      )}
    </li>
  );
}

// Tasks as a Kanban board or a list. `mine` is the "My tasks" page: only what is assigned to the signed-in user.
export default function TasksPage({ mine = false }) {
  const can = useCan();
  const notify = useToast();
  const people = usePeople();
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState('board');
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('open');
  const [priority, setPriority] = useState('');
  const [assignee, setAssignee] = useState('');
  const [overdue, setOverdue] = useState(params.get('overdue') === '1');
  const [creating, setCreating] = useState(params.get('new') === '1');
  const [openId, setOpenId] = useState(params.get('open') || null);
  const manager = can('workplace.manage');

  const query = { mine: mine ? 1 : undefined, q: q || undefined, status: status === 'all' ? undefined : status, priority: priority || undefined,
    assignee: !mine && assignee ? assignee : undefined, overdue: overdue ? 1 : undefined };
  const { data, error, loading, reload } = useLoad(() => api.get('/workplace/tasks/', query), [JSON.stringify(query)]);
  const stats = useLoad(() => api.get('/workplace/tasks/analytics/', { mine: mine ? 1 : undefined }), [mine]);
  const [showCharts, setShowCharts] = useState(false);
  useEffect(() => { if (params.get('new') || params.get('open')) setParams({}, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = () => { reload(); stats.reload(); };
  const rows = data?.results || [];
  const move = async (t, to) => {
    try { await api.post(`/workplace/tasks/${t.id}/status/`, { status: to }); refresh(); } catch (err) { notify(err.message, 'error'); }
  };
  const drop = (to) => (e) => {
    e.preventDefault();
    const t = rows.find((r) => r.id === e.dataTransfer.getData('text/plain'));
    if (t && t.status !== to) move(t, to);
  };
  const columns = status === 'all' || status === 'cancelled' ? TASK_STATUSES : BOARD.filter(([k]) => status === 'open' ? k !== 'done' : k === status);

  return (
    <>
      <PageHeader title={mine ? 'My tasks' : 'Tasks'} description={mine ? 'Everything assigned to you.' : manager ? 'All tasks, with who is doing what.' : 'Tasks you raised or that are assigned to you.'}
        actions={<button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>New task</button>} />

      <section className="card">
        <div className="chips" role="group" aria-label="Filter by status">
          {[['open', 'Open'], ['all', 'All'], ...TASK_STATUSES].map(([k, l]) => (
            <button key={k} type="button" className="chip" aria-pressed={status === k} onClick={() => setStatus(k)}>{l}</button>
          ))}
          <button type="button" className="chip" aria-pressed={overdue} onClick={() => setOverdue(!overdue)}>Overdue only</button>
        </div>
        <form className="toolbar" onSubmit={(e) => e.preventDefault()}>
          <Field id="tk-q" label="Search" className="grow"><input id="tk-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Title, number or description" /></Field>
          <Field id="tk-pr" label="Priority">
            <select id="tk-pr" value={priority} onChange={(e) => setPriority(e.target.value)}><option value="">Any</option>{PRIORITIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          </Field>
          {!mine && manager && <PersonSelect id="tk-as" label="Assigned to" value={assignee} onChange={setAssignee} people={people} none="Anyone" />}
          <Tabs tabs={[{ key: 'board', label: 'Board' }, { key: 'list', label: 'List' }]} value={view} onChange={setView} />
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && rows.length === 0 && <p className="empty">No tasks{q || priority || overdue || assignee ? ' match' : ' here yet'}.</p>}

        {!error && rows.length > 0 && view === 'board' && (
          <div className="wp-board">
            {columns.map(([key, label]) => {
              const col = rows.filter((t) => t.status === key);
              return (
                <section key={key} className="wp-col" aria-label={`${label}, ${col.length} tasks`} onDragOver={(e) => e.preventDefault()} onDrop={drop(key)}>
                  <h3>{label} <span className="hint">{col.length}</span></h3>
                  <ul>{col.map((t) => <Card key={t.id} t={t} onOpen={setOpenId} onMove={move} canDrag />)}</ul>
                  {col.length === 0 && <p className="hint">Drop a task here.</p>}
                </section>
              );
            })}
          </div>
        )}

        {!error && rows.length > 0 && view === 'list' && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Number</th><th>Task</th><th>Assigned to</th><th>Due</th><th>Priority</th><th>Status</th></tr></thead>
              <tbody>
                {rows.map((t) => (
                  <tr key={t.id} className="clickable" onClick={() => setOpenId(t.id)}>
                    <td data-label="Number">{t.number}</td>
                    <td data-label="Task"><button type="button" className="wp-link" onClick={(e) => { e.stopPropagation(); setOpenId(t.id); }}><strong>{t.title}</strong></button>
                      {t.subtasks_total > 0 && <span className="hint"> · {t.subtasks_done}/{t.subtasks_total}</span>}</td>
                    <td data-label="Assigned to">{t.assignee?.name || '—'}</td>
                    <td data-label="Due" className={t.overdue ? 'bad-text' : ''}>{fmtDate(t.due_date)}</td>
                    <td data-label="Priority"><PriorityBadge task={t} /></td>
                    <td data-label="Status"><TaskStatusBadge task={t} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rows.length > 0 && <div className="list-foot"><span>{data.count} task{data.count === 1 ? '' : 's'}</span>{data.overdue > 0 && <span className="bad-text">{data.overdue} overdue</span>}</div>}
      </section>

      <button type="button" className="btn" aria-expanded={showCharts} onClick={() => setShowCharts(!showCharts)}>{showCharts ? 'Hide analytics' : 'Show analytics'}</button>
      {showCharts && stats.data && <div className="wp-gap"><TaskCharts analytics={stats.data} /></div>}

      {creating && <TaskFormDialog people={people} onClose={() => setCreating(false)} onSaved={() => { setCreating(false); refresh(); }} />}
      {openId && <TaskDialog id={openId} people={people} onClose={() => setOpenId(null)} onChanged={refresh} />}
    </>
  );
}

export const MyTasksPage = () => <TasksPage mine />;
