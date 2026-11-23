import { Link } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart, Donut, HBars, SERIES_COLOURS, StackedBar } from '../../components/charts';
import { useLoad } from '../../hooks';
import { ErrorBox, Loading, PageHeader } from '../../ui';
import { STATUS_COLOUR, ageText } from './common';

function Tile({ label, value, hint, tone, to }) {
  const inner = (<><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</>);
  return to ? <Link className={`tile ${tone || ''}`} to={to}>{inner}</Link> : <div className={`tile ${tone || ''}`}>{inner}</div>;
}

export default function Dashboard() {
  const { data: d, error, loading, reload } = useLoad(() => api.get('/approvals/dashboard/'), []);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const t = d.tiles;
  const hours = (v) => `${v} h`;
  const kindSlices = d.by_kind.map((k, i) => ({ key: k.kind, label: k.label, value: k.total, colour: SERIES_COLOURS[i % SERIES_COLOURS.length] }));
  return (
    <>
      <PageHeader title="Approval analytics" description="How quickly requests are decided, and where they wait."
        actions={<Link className="btn" to="/approvals/all">All approvals</Link>} />
      <div className="tiles">
        <Tile label="Pending" value={t.pending} hint={t.pending ? `Average wait ${ageText(t.avg_pending_age_hours)}` : 'Nothing is waiting'} to="/approvals/all?status=open" />
        <Tile label="Average turnaround" value={ageText(t.avg_turnaround_hours)} hint="From request to decision (decided by a person)" />
        <Tile label="Approved" value={t.approved} tone="good" hint={`${t.rejected} rejected, ${t.cancelled} withdrawn`} to="/approvals/all?status=approved" />
        <Tile label="Rejected" value={t.rejected} tone={t.rejected ? 'bad' : ''} to="/approvals/all?status=rejected" />
        <Tile label="Overdue or escalated" value={Math.max(t.overdue, t.escalated)}
          tone={t.overdue || t.escalated ? 'bad' : ''} hint={`${t.escalated} escalated, ${t.overdue} past their deadline`} to="/approvals/all?status=escalated" />
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Requests per month">
          <h2>Requests per month</h2>
          <p className="muted">Sent, approved and rejected over the last six months.</p>
          <ColumnChart title="Approval requests per month" format={(v) => String(Math.round(v))}
            series={[{ key: 'submitted', label: 'Sent', colour: 'var(--chart-1)' }, { key: 'approved', label: 'Approved', colour: 'var(--chart-2)' }, { key: 'rejected', label: 'Rejected', colour: 'var(--chart-4)' }]}
            data={d.monthly.map((m) => ({ label: m.label, values: { submitted: m.submitted, approved: m.approved, rejected: m.rejected } }))} />
        </section>
        <section className="card" aria-label="Requests by type">
          <h2>By type</h2>
          <p className="muted">What people ask approval for.</p>
          {d.by_kind.length ? <Donut slices={kindSlices} centreLabel="requests" /> : <p className="muted">No requests yet.</p>}
        </section>
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Slowest approvers">
          <h2>Slowest approvers</h2>
          <p className="muted">Average hours from a step starting to the approver deciding.</p>
          <HBars rows={d.slowest_approvers.map((a) => ({ key: a.approver, label: `${a.approver} (${a.decisions})`, value: a.avg_hours }))}
            format={hours} colour="var(--chart-3)" empty="No decisions yet." />
        </section>
        <section className="card" aria-label="Pending per approver">
          <h2>Waiting on</h2>
          <p className="muted">Open requests each person can act on.</p>
          <HBars rows={d.pending_per_approver.map((a) => ({ key: a.approver, label: a.approver, value: a.pending }))}
            format={(v) => String(v)} empty="Nothing is waiting." />
        </section>
      </div>

      <section className="card" aria-label="Status per type">
        <h2>Status by type</h2>
        {d.by_kind.length === 0 && <p className="muted">No requests yet.</p>}
        <div className="apr-kind-stacks">
          {d.by_kind.map((k) => (
            <div key={k.kind}>
              <h3>{k.label} <span className="muted">({k.total})</span></h3>
              <StackedBar format={(v) => String(v)} parts={['pending', 'approved', 'rejected', 'cancelled'].map((s) => ({
                key: s, label: s === 'cancelled' ? 'Withdrawn' : s[0].toUpperCase() + s.slice(1), value: k[s], colour: STATUS_COLOUR[s] }))} />
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
