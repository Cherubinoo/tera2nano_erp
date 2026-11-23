import { Link } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart, Donut, HBars } from '../../components/charts';
import { compact, downloadCsv, money, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Loading, PageHeader, fmtDate } from '../../ui';
import { BudgetBadge, HealthBadge, Progress, StatusBadge, Tile, Timeline, signClass } from './common';

const STATUS_SLICES = [['active', 'Active', 'var(--chart-2)'], ['planned', 'Planned', 'var(--chart-1)'], ['on_hold', 'On hold', 'var(--chart-3)'],
  ['completed', 'Completed', 'var(--chart-grey)'], ['cancelled', 'Cancelled', 'var(--chart-4)']];

export default function Profitability() {
  const portfolio = useLoad(() => api.get('/projects/portfolio/'), []);
  const dash = useLoad(() => api.get('/projects/dashboard/'), []);
  if (portfolio.loading || dash.loading) return <Loading />;
  if (portfolio.error || dash.error) return <ErrorBox error={portfolio.error || dash.error} onRetry={() => { portfolio.reload(); dash.reload(); }} />;
  const p = portfolio.data;
  const d = dash.data;
  const rows = p.results;
  const booked = rows.filter((r) => num(r.revenue) || num(r.cost));
  const byProfit = [...booked].sort((a, b) => num(b.profit) - num(a.profit));
  const live = rows.filter((r) => r.status !== 'cancelled');
  const timeline = live.filter((r) => r.status !== 'completed').map((r) => ({ ...r }));
  return (
    <>
      <PageHeader title="Project profitability" description="What each project has earned and cost, straight from the books."
        actions={rows.length > 0 && <button type="button" className="btn" onClick={() => downloadCsv('project-profitability.csv', [
          ['Code', 'Project', 'Client', 'Status', 'Revenue', 'Cost', 'Profit', 'Margin %', 'Budget', 'Budget used %', 'Progress %', 'WIP'],
          ...rows.map((r) => [r.code, r.name, r.customer?.name || '', r.status_label, r.revenue, r.cost, r.profit, r.margin_percent ?? '', r.budget_total, r.budget_used_percent ?? '', r.progress, r.wip])])}>Export CSV</button>} />

      <div className="tiles">
        <Tile label="Revenue" value={money(p.totals.revenue)} hint="Booked to projects" />
        <Tile label="Cost" value={money(p.totals.cost)} hint="Booked to projects" />
        <Tile label="Profit" value={money(p.totals.profit)} tone={num(p.totals.profit) < 0 ? 'bad' : num(p.totals.profit) > 0 ? 'good' : ''}
          hint={p.totals.margin_percent === null ? 'No revenue yet' : `${num(p.totals.margin_percent)}% margin`} />
        <Tile label="Work in progress" value={money(p.totals.wip)} hint="Earned, not yet billed" />
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Profit by project">
          <h2>Profit by project</h2>
          <HBars rows={byProfit.map((r) => ({ key: r.id, label: r.name, value: r.profit, colour: num(r.profit) < 0 ? 'var(--chart-4)' : 'var(--chart-2)' }))}
            format={(v) => money(v)} empty="Nothing has been booked to a project yet." />
        </section>
        <section className="card" aria-label="Projects by status">
          <h2>Projects by status</h2>
          <Donut centreLabel="projects" slices={STATUS_SLICES.map(([key, label, colour]) => ({ key, label, colour, value: d.status_counts[key] || 0 }))} />
        </section>
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Revenue against cost per project">
          <h2>Revenue and cost by project</h2>
          <ColumnChart title="Revenue and cost by project" format={compact}
            series={[{ key: 'revenue', label: 'Revenue', colour: 'var(--chart-2)' }, { key: 'cost', label: 'Cost', colour: 'var(--chart-4)' }]}
            data={d.revenue_vs_cost.map((r) => ({ label: r.code.split('-').slice(-1)[0], values: { revenue: r.revenue, cost: r.cost } }))} />
          <p className="hint">Columns are labelled with the last part of the project code: {d.revenue_vs_cost.map((r) => `${r.code.split('-').slice(-1)[0]} = ${r.name}`).join('; ') || 'none yet'}.</p>
        </section>
        <section className="card" aria-label="Revenue and cost per month">
          <h2>Month by month</h2>
          <ColumnChart title="Revenue and cost per month" format={compact}
            series={[{ key: 'revenue', label: 'Revenue', colour: 'var(--chart-2)' }, { key: 'cost', label: 'Cost', colour: 'var(--chart-4)' }]}
            data={p.monthly.map((m) => ({ label: m.label, values: { revenue: m.revenue, cost: m.cost } }))} />
        </section>
      </div>

      <section className="card" aria-label="Timeline">
        <h2>Timeline</h2>
        <p className="muted">Bars run from start to planned end; the darker part is the progress. The line is today.</p>
        <Timeline rows={timeline} today={d.today} />
      </section>

      <section className="card" aria-label="Profitability table">
        <h2>All projects</h2>
        {rows.length === 0 ? <p className="empty">No projects yet.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Project</th><th>Status</th><th>Health</th><th>Budget</th><th>Progress</th><th className="amount">Revenue</th><th className="amount">Cost</th><th className="amount">Profit</th><th className="amount">Margin</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td data-label="Project"><Link to={`/projects/projects/${r.id}`}><strong>{r.name}</strong></Link><span className="projects-sub">{r.code} · {r.customer?.name || 'Internal'}</span></td>
                    <td data-label="Status"><StatusBadge status={r.status} /></td>
                    <td data-label="Health">{r.status === 'cancelled' ? '—' : <HealthBadge value={r.health} />}</td>
                    <td data-label="Budget"><BudgetBadge value={r.budget_health} /></td>
                    <td data-label="Progress"><Progress value={r.progress} /></td>
                    <td className="amount" data-label="Revenue">{plain(r.revenue)}</td>
                    <td className="amount" data-label="Cost">{plain(r.cost)}</td>
                    <td className={`amount ${signClass(r.profit)}`} data-label="Profit">{plain(r.profit)}</td>
                    <td className="amount" data-label="Margin">{r.margin_percent === null ? '—' : `${num(r.margin_percent)}%`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card" aria-label="Overdue milestones">
        <h2>Overdue milestones</h2>
        {d.overdue_milestones.length === 0 ? <p className="empty">No milestone is overdue.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Milestone</th><th>Project</th><th>Due</th><th className="amount">Days late</th><th className="amount">To bill</th></tr></thead>
            <tbody>{d.overdue_milestones.map((m) => (
              <tr key={m.id}><td data-label="Milestone"><strong>{m.name}</strong></td><td data-label="Project"><Link to={`/projects/projects/${m.project.id}`}>{m.project.name}</Link></td>
                <td data-label="Due">{fmtDate(m.due_date)}</td><td className="amount projects-neg" data-label="Days late">{m.days_late}</td><td className="amount" data-label="To bill">{num(m.amount) > 0 ? plain(m.amount) : '—'}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </section>
    </>
  );
}
