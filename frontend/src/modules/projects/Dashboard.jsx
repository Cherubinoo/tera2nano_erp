import { Link } from 'react-router-dom';
import { api } from '../../api';
import { Donut, HBars } from '../../components/charts';
import { useAuth } from '../../context';
import { compact, money, num } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Loading, PageHeader, fmtDate } from '../../ui';
import { MonthlyChart, Tile, Timeline } from './common';

const STATUS_SLICES = [['active', 'Active', 'var(--chart-2)'], ['planned', 'Planned', 'var(--chart-1)'], ['on_hold', 'On hold', 'var(--chart-3)'],
  ['completed', 'Completed', 'var(--chart-grey)'], ['cancelled', 'Cancelled', 'var(--chart-4)']];
const HEALTH_SLICES = [['on_track', 'On track', 'var(--chart-2)'], ['at_risk', 'At risk', 'var(--chart-3)'], ['delayed', 'Delayed', 'var(--chart-4)']];

export default function Dashboard() {
  const { user } = useAuth();
  const { data: d, error, loading, reload } = useLoad(() => api.get('/projects/dashboard/'), []);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const t = d.tiles;
  const canManage = user.permissions.includes('projects.manage');
  return (
    <>
      <PageHeader title="Projects" description="Delivery, deadlines and profitability across every project."
        actions={canManage && (<>
          <Link className="btn btn-primary" to="/projects/projects?new=1">New project</Link>
          <Link className="btn" to="/projects/tasks?new=1">New task</Link>
          <Link className="btn" to="/projects/timesheets?new=1">Log time</Link>
        </>)} />

      <div className="tiles">
        <Tile label="Active projects" value={t.active_projects} hint={`${t.open_projects} open in all`} to="/projects/projects?status=active" />
        <Tile label="Overdue milestones" value={t.overdue_milestones} tone={t.overdue_milestones > 0 ? 'bad' : ''} hint={t.overdue_milestones > 0 ? 'Past the due date' : 'Nothing overdue'} to="/projects/milestones?status=overdue" />
        <Tile label="Open contract value" value={money(t.contract_value)} hint="Planned, active and on hold" to="/projects/profitability" />
        <Tile label="Work in progress" value={money(t.wip)} hint="Earned but not yet billed" to="/projects/profitability" />
        <Tile label="Margin" value={t.margin_percent === null ? '—' : `${num(t.margin_percent)}%`} tone={num(t.profit) < 0 ? 'bad' : num(t.profit) > 0 ? 'good' : ''}
          hint={`Profit ${money(t.profit)} on ${money(t.revenue)} revenue`} to="/projects/profitability" />
        <Tile label="Open tasks" value={t.open_tasks} tone={t.overdue_tasks > 0 ? 'bad' : ''} hint={`${t.overdue_tasks} overdue`} to="/projects/tasks?status=open" />
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Projects by status">
          <h2>Projects by status</h2>
          <p className="muted">Every project, by where it stands.</p>
          <Donut centreLabel="projects" slices={STATUS_SLICES.map(([key, label, colour]) => ({ key, label, colour, value: d.status_counts[key] || 0 }))} />
        </section>
        <section className="card" aria-label="Project health">
          <h2>Health of open projects</h2>
          <p className="muted">Schedule and budget together. See the rules on each project.</p>
          <Donut centreLabel="open projects" slices={HEALTH_SLICES.map(([key, label, colour]) => ({ key, label, colour, value: d.health_counts[key] || 0 }))} />
        </section>
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Revenue and cost per month">
          <h2>Revenue and cost</h2>
          <p className="muted">Last six months, from the books, for entries tagged to a project.</p>
          <MonthlyChart title="Project revenue and cost per month" monthly={d.monthly} />
        </section>
        <section className="card" aria-label="Profit by project">
          <h2>Profit by project</h2>
          <p className="muted">Revenue less cost booked to each project.</p>
          <HBars rows={d.profitability.map((p) => ({ key: p.id, label: p.name, value: p.profit, colour: num(p.profit) < 0 ? 'var(--chart-4)' : 'var(--chart-2)' }))}
            format={(v) => money(v)} empty="Nothing has been booked to a project yet." />
        </section>
      </div>

      <section className="card" aria-label="Timeline">
        <div className="projects-section-head"><h2>Timeline</h2><Link to="/projects/profitability">Portfolio</Link></div>
        <Timeline rows={d.timeline} today={d.today} />
      </section>

      <div className="grid-2">
        <section className="card" aria-label="Overdue milestones">
          <h2>Overdue milestones</h2>
          {d.overdue_milestones.length === 0 ? <p className="empty">No milestone is overdue.</p> : (
            <ul className="projects-list-plain">
              {d.overdue_milestones.map((m) => (
                <li key={m.id}>
                  <span><strong>{m.name}</strong><span className="projects-sub"><Link to={`/projects/projects/${m.project.id}`}>{m.project.name}</Link></span></span>
                  <span className="projects-neg">{m.days_late} day{m.days_late === 1 ? '' : 's'} late · due {fmtDate(m.due_date)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="card" aria-label="Coming up">
          <h2>Due in the next 30 days</h2>
          {d.upcoming_milestones.length === 0 ? <p className="empty">No milestone falls due soon.</p> : (
            <ul className="projects-list-plain">
              {d.upcoming_milestones.map((m) => (
                <li key={m.id}>
                  <span><strong>{m.name}</strong><span className="projects-sub"><Link to={`/projects/projects/${m.project.id}`}>{m.project.name}</Link></span></span>
                  <span>{fmtDate(m.due_date)}{num(m.amount) > 0 && <> · {money(m.amount)}</>}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </>
  );
}
