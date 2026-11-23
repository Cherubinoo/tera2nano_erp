import { Link } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart } from '../../components/charts';
import { useLoad } from '../../hooks';
import { Badge, ErrorBox, Loading, PageHeader, fmtDate } from '../../ui';
import { Avatar, Markdown, MeetingStatus, Tile, timeRange, useCan } from './common';
import { TaskCharts, TaskStatusBadge } from './tasks-shared';

export default function WorkplaceHome() {
  const can = useCan();
  const { data: d, error, loading, reload } = useLoad(() => api.get('/workplace/dashboard/'), []);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;

  return (
    <>
      <PageHeader title="Workplace" description="Meetings, tasks, announcements and circulars in one place."
        actions={(
          <>
            <Link className="btn btn-primary" to="/workplace/my-tasks?new=1">New task</Link>
            {can('workplace.manage') && <Link className="btn" to="/workplace/meetings?new=1">Schedule meeting</Link>}
          </>
        )} />

      <div className="tiles">
        <Tile label="Meetings this week" value={d.upcoming_count} hint="In the next 7 days" to="/workplace/meetings" />
        <Tile label="My open tasks" value={d.my_open_count} hint={`${d.my_due_soon} due in the next 7 days`} to="/workplace/my-tasks" />
        <Tile label="Overdue" value={d.overdue_total} tone={d.overdue_total > 0 ? 'bad' : ''}
          hint={d.my_overdue > 0 ? `${d.my_overdue} of them yours` : d.overdue_total ? 'Past their due date' : 'Nothing overdue'}
          to={can('workplace.manage') ? '/workplace/tasks?overdue=1' : '/workplace/my-tasks?overdue=1'} />
        <Tile label="To acknowledge" value={d.pending_acknowledgement_count} tone={d.pending_acknowledgement_count ? 'bad' : ''}
          hint={d.pending_acknowledgement_count ? 'Circulars waiting for you' : 'All acknowledged'} to="/workplace/circulars?pending=1" />
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Upcoming meetings">
          <div className="card-head"><h2>Upcoming meetings</h2><Link to="/workplace/meetings">Calendar</Link></div>
          {d.upcoming_meetings.length === 0 ? <p className="empty">No meetings coming up.</p> : (
            <ul className="wp-list">
              {d.upcoming_meetings.map((m) => (
                <li key={m.id}>
                  <div className="wp-date" aria-hidden="true"><strong>{new Date(`${m.date}T00:00:00`).getDate()}</strong>{new Date(`${m.date}T00:00:00`).toLocaleDateString('en-IN', { month: 'short' })}</div>
                  <div className="wp-list-main">
                    <Link to={`/workplace/meetings/${m.id}`}><strong>{m.title}</strong></Link>
                    <div className="hint">{fmtDate(m.date)} · {timeRange(m)}{m.venue ? ` · ${m.venue}` : ''}</div>
                  </div>
                  <MeetingStatus m={m} />
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="card" aria-label="My tasks">
          <div className="card-head"><h2>My tasks due</h2><Link to="/workplace/my-tasks">My tasks</Link></div>
          {d.my_tasks_due.length === 0 ? <p className="empty">Nothing assigned to you.</p> : (
            <ul className="wp-list">
              {d.my_tasks_due.map((t) => (
                <li key={t.id}>
                  <div className="wp-list-main">
                    <Link to={`/workplace/my-tasks?open=${t.id}`}><strong>{t.title}</strong></Link>
                    <div className={`hint${t.overdue ? ' bad-text' : ''}`}>{t.due_date ? `${t.overdue ? 'Overdue since' : 'Due'} ${fmtDate(t.due_date)}` : 'No due date'}</div>
                  </div>
                  <TaskStatusBadge task={t} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Overdue tasks">
          <div className="card-head"><h2>Overdue tasks</h2></div>
          {d.overdue_tasks.length === 0 ? <p className="empty">Nothing is overdue.</p> : (
            <ul className="wp-list">
              {d.overdue_tasks.map((t) => (
                <li key={t.id}>
                  <div className="wp-list-main">
                    <Link to={`/workplace/${can('workplace.manage') ? 'tasks' : 'my-tasks'}?open=${t.id}`}><strong>{t.title}</strong></Link>
                    <div className="hint bad-text">Due {fmtDate(t.due_date)}</div>
                  </div>
                  <Avatar person={t.assignee} />
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="card" aria-label="Latest announcements">
          <div className="card-head"><h2>Announcements</h2><Link to="/workplace/announcements">All</Link></div>
          {d.announcements.length === 0 ? <p className="empty">No announcements.</p> : d.announcements.slice(0, 3).map((a) => (
            <article key={a.id} className="wp-ann">
              <h3>{a.title} {a.pinned && <Badge tone="info">Pinned</Badge>}</h3>
              <div className="hint">{fmtDate(a.publish_on)}</div>
              <Markdown text={a.body} />
            </article>
          ))}
        </section>
      </div>

      {d.pending_acknowledgements.length > 0 && (
        <section className="card" aria-label="Circulars to acknowledge">
          <div className="card-head"><h2>Circulars waiting for your acknowledgement</h2></div>
          <ul className="wp-list">
            {d.pending_acknowledgements.map((c) => (
              <li key={c.id}>
                <div className="wp-list-main"><Link to={`/workplace/circulars/${c.id}`}><strong>{c.subject}</strong></Link><div className="hint">{c.number} · {fmtDate(c.issue_date)}</div></div>
                <Link className="btn btn-small" to={`/workplace/circulars/${c.id}`}>Read and acknowledge</Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <TaskCharts analytics={d.analytics} />

      {d.meetings_by_month.length > 0 && (
        <section className="card" aria-label="Meetings per month">
          <h2>Meetings per month</h2>
          <p className="muted">Held and still-planned meetings over the last six months.</p>
          <ColumnChart title="Meetings per month" format={(v) => String(Math.round(v))}
            series={[{ key: 'held', label: 'Held', colour: 'var(--chart-2)' }, { key: 'planned', label: 'Planned', colour: 'var(--chart-1)' }]}
            data={d.meetings_by_month.map((m) => ({ label: m.label, values: { held: m.held, planned: m.planned } }))} />
        </section>
      )}
    </>
  );
}
