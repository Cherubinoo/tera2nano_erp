import { Link } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart, Donut, HBars, SERIES_COLOURS } from '../../components/charts';
import { useAuth } from '../../context';
import { compact, money, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Loading, PageHeader } from '../../ui';
import { Funnel, Tile } from './common';

export default function Dashboard() {
  const { user } = useAuth();
  const { data: d, error, loading, reload } = useLoad(() => api.get('/crm/dashboard/'), []);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const t = d.tiles;
  const canManage = user.permissions.includes('crm.manage');
  const openPipeline = d.pipeline.reduce((a, p) => a + p.count, 0);
  const trendHasData = d.trend.some((m) => m.won || m.lost);
  const fc = d.forecast_other;
  return (
    <>
      <PageHeader title="CRM" description="Leads, deals and customer activity at a glance."
        actions={canManage && (<>
          <Link className="btn btn-primary" to="/crm/leads?new=1">New lead</Link>
          <Link className="btn" to="/crm/deals?new=1">New deal</Link>
          <Link className="btn" to="/crm/activities?new=1">New activity</Link>
        </>)} />

      <div className="tiles">
        <Tile label="Open pipeline" value={money(t.open_value)} hint={`${t.open_deals} open deal${t.open_deals === 1 ? '' : 's'}`} to="/crm/deals" />
        <Tile label="Weighted forecast" value={money(t.weighted_forecast)} hint="Amount x stage probability" to="/crm/reports" />
        <Tile label="Win rate" value={t.win_rate === null ? '—' : `${num(t.win_rate)}%`} tone={t.win_rate !== null && num(t.win_rate) >= 50 ? 'good' : ''}
          hint={`${t.won_12m} won, ${t.lost_12m} lost in 12 months`} to="/crm/reports" />
        <Tile label="Won in 12 months" value={money(t.won_value_12m)} hint={t.avg_cycle_days === null ? 'No cycle time yet' : `Avg cycle ${num(t.avg_cycle_days)} days`} />
        <Tile label="Open leads" value={t.open_leads} hint={`${t.leads_this_month} new this month`} to="/crm/leads?status=open" />
        <Tile label="My activities" value={d.mine.overdue + d.mine.today} tone={d.mine.overdue > 0 ? 'bad' : ''}
          hint={`${d.mine.overdue} overdue, ${d.mine.today} due today`} to="/crm/activities?mine=1" />
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Sales funnel">
          <h2>Sales funnel</h2>
          <p className="muted">Leads to qualified leads, deals that reached the proposal stage, and deals won. Percentages compare each step with the one above.</p>
          <Funnel steps={d.funnel} />
        </section>
        <section className="card" aria-label="Pipeline by stage">
          <h2>Pipeline by stage</h2>
          <p className="muted">Value of open deals in each stage ({openPipeline} deal{openPipeline === 1 ? '' : 's'}).</p>
          <HBars rows={d.pipeline.map((p) => ({ key: p.key, label: `${p.label} (${p.count})`, value: p.value, colour: p.colour }))}
            format={(v) => money(v)} empty="No open deals yet." />
          <div className="table-wrap"><table className="crm-mini">
            <caption className="sr-only">Pipeline by stage</caption>
            <thead><tr><th>Stage</th><th className="amount">Deals</th><th className="amount">Value</th><th className="amount">Weighted</th></tr></thead>
            <tbody>{d.pipeline.map((p) => (
              <tr key={p.key}><td data-label="Stage">{p.label} <span className="muted">{p.probability}%</span></td><td className="amount" data-label="Deals">{p.count}</td>
                <td className="amount" data-label="Value">{plain(p.value)}</td><td className="amount" data-label="Weighted">{plain(p.weighted)}</td></tr>))}</tbody>
          </table></div>
        </section>
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Lead sources">
          <h2>Lead sources</h2>
          <p className="muted">Where your leads come from.</p>
          {d.sources.length === 0 ? <p className="empty">No leads yet.</p> : (
            <Donut centreLabel="leads" slices={d.sources.map((s, i) => ({ key: s.key, label: s.label, colour: SERIES_COLOURS[i % SERIES_COLOURS.length], value: s.count }))} />
          )}
        </section>
        <section className="card" aria-label="Won and lost">
          <h2>Won and lost deals</h2>
          <p className="muted">Deals closed in each of the last six months.</p>
          {!trendHasData ? <p className="empty">No deal has been closed in the last six months.</p> : (
            <ColumnChart title="Won and lost deals per month" format={(v) => `${num(v)}`}
              series={[{ key: 'won', label: 'Won', colour: 'var(--chart-2)' }, { key: 'lost', label: 'Lost', colour: 'var(--chart-4)' }]}
              data={d.trend.map((m) => ({ label: m.label, values: { won: m.won, lost: m.lost } }))} />
          )}
        </section>
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Top owners">
          <h2>Top owners</h2>
          <p className="muted">Open pipeline plus deals won in the last 12 months.</p>
          <HBars rows={d.owners.map((o) => ({ key: o.key, label: o.label, value: num(o.open_value) + num(o.won_value) }))} format={(v) => money(v)} empty="No deals yet." />
          {d.owners.length > 0 && (
            <div className="table-wrap"><table className="crm-mini">
              <caption className="sr-only">Owners</caption>
              <thead><tr><th>Owner</th><th className="amount">Open</th><th className="amount">Won (12 months)</th></tr></thead>
              <tbody>{d.owners.map((o) => (
                <tr key={o.key}><td data-label="Owner">{o.label}</td><td className="amount" data-label="Open">{o.open_count} · {plain(o.open_value)}</td>
                  <td className="amount" data-label="Won (12 months)">{o.won_count} · {plain(o.won_value)}</td></tr>))}</tbody>
            </table></div>
          )}
        </section>
        <section className="card" aria-label="Forecast by month">
          <h2>Forecast by month</h2>
          <p className="muted">Open deals by expected close month, in full and weighted by probability.</p>
          {openPipeline === 0 ? <p className="empty">No open deals to forecast.</p> : (
            <ColumnChart title="Forecast by expected close month" format={compact}
              series={[{ key: 'value', label: 'Deal value', colour: 'var(--chart-1)' }, { key: 'weighted', label: 'Weighted', colour: 'var(--chart-2)' }]}
              data={d.forecast.map((m) => ({ label: m.label, values: { value: m.value, weighted: m.weighted } }))} />
          )}
          <ul className="crm-plain">
            {num(fc.past_due.count) > 0 && <li><span>Past the expected close date</span><strong>{fc.past_due.count} · {money(fc.past_due.value)}</strong></li>}
            {num(fc.undated.count) > 0 && <li><span>No expected close date</span><strong>{fc.undated.count} · {money(fc.undated.value)}</strong></li>}
            {num(fc.later.count) > 0 && <li><span>Later than six months</span><strong>{fc.later.count} · {money(fc.later.value)}</strong></li>}
          </ul>
        </section>
      </div>
    </>
  );
}
