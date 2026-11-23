import { Link } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart, Donut, HBars, StackedBar } from '../../components/charts';
import { useAuth } from '../../context';
import { compact, money, num } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Loading, PageHeader, fmtDate } from '../../ui';
import { ChartTable, HEADS, HEAD_COLOUR, KIND_COLOUR, StatusBadge, Tile, has } from './common';

export function EventList({ events, empty = 'Nothing due in this window.' }) {
  if (!events.length) return <p className="muted">{empty}</p>;
  return (
    <ul className="tax-events">
      {events.map((e) => (
        <li key={e.id}>
          <div>
            <Link to={e.link}><strong>{e.title}</strong></Link>
            <div className="hint">{e.kind_label}{e.amount ? ` · ${money(e.amount)} to pay` : ''}</div>
          </div>
          <span>
            {fmtDate(e.due_date)}<br />
            <StatusBadge status={e.overdue ? 'late' : e.status} label={e.overdue ? `${-e.days_left} day${e.days_left === -1 ? '' : 's'} late` : e.days_left === 0 ? 'Due today' : `In ${e.days_left} days`} />
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const { data: d, error, loading, reload } = useLoad(() => api.get('/tax/dashboard/'), []);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const k = d.kpis;
  const r = d.returns;
  const canManage = has(user, 'tax.manage');
  const nextAdv = k.advance_tax_next;
  const outTotal = d.liability_by_head.reduce((a, h) => a + num(h.output), 0);
  const itcTotal = d.liability_by_head.reduce((a, h) => a + num(h.itc), 0);
  const covered = Math.min(outTotal, itcTotal);
  const returnSlices = [['filed', 'Filed', 'var(--chart-2)'], ['prepared', 'Prepared', 'var(--chart-1)'], ['pending', 'Pending', 'var(--chart-3)'], ['late', 'Late', 'var(--chart-4)']]
    .map(([key, label, colour]) => ({ key, label, colour, value: r[key] }));
  return (
    <>
      <PageHeader title="GST & Tax" description={`Financial year ${d.financial_year}: GST, TDS and income tax from the books, returns and challans.`}
        actions={canManage && (<>
          <Link className="btn btn-primary" to="/tax/payments?new=gst">Record tax payment</Link>
          <Link className="btn" to="/tax/returns?new=1">New return record</Link>
        </>)} />

      <div className="tiles">
        <Tile label="GST payable (all periods)" value={money(k.gst_outstanding)} hint={`This month's output tax ${money(k.gst_this_month)}`} to="/tax/gst" tone={num(k.gst_outstanding) > 0 ? 'bad' : ''} />
        <Tile label="Input credit available" value={money(k.itc_available)} hint="Balance in the GST input accounts" to="/tax/purchase-register" tone="good" />
        <Tile label="TDS payable" value={money(k.tds_payable)} hint={`Receivable: ${money(k.tds_receivable_tracker)} tracked`} to="/tax/tds" />
        <Tile label="Tax paid this year" value={money(k.tax_paid_ytd)} hint="GST, TDS and income tax challans" to="/tax/payments" />
        <Tile label="Returns filed" value={`${r.filed} of ${r.total}`} hint={`${r.late} late · ${r.pending + r.prepared} to file`} to="/tax/returns" tone={r.late > 0 ? 'bad' : ''} />
        <Tile label="Next advance tax" value={nextAdv ? money(nextAdv.shortfall || nextAdv.required) : '—'}
          hint={nextAdv ? `Instalment ${nextAdv.instalment}, due ${fmtDate(nextAdv.due_date)}` : 'Set an estimate to see instalments'} to="/tax/income-tax" />
      </div>

      <div className="grid-2">
        <section className="card" aria-label="GST output tax and input credit by month">
          <h2>Output tax and input credit</h2>
          <p className="muted">Last months of this year: tax charged on sales against credit on purchases.</p>
          <ColumnChart title="Output tax and input credit per month" format={compact}
            series={[{ key: 'output', label: 'Output tax', colour: 'var(--chart-1)' }, { key: 'itc', label: 'Input credit', colour: 'var(--chart-2)' }]}
            data={d.trend.map((m) => ({ label: m.label, values: { output: m.output, itc: m.itc } }))} />
          <ChartTable title="monthly tax" headers={['Month', 'Output tax', 'Input credit', 'Net']} rows={d.trend.map((m) => [m.label, money(m.output), money(m.itc), money(m.net)])} />
        </section>
        <section className="card" aria-label="Tax outstanding covered by credit">
          <h2>Liability against credit</h2>
          <p className="muted">Tax still owed on the books, and how much of it your input credit can cover.</p>
          <StackedBar format={money} parts={[{ key: 'cov', label: 'Covered by input credit', value: covered, colour: 'var(--chart-2)' },
            { key: 'cash', label: 'To pay in cash', value: Math.max(outTotal - itcTotal, 0), colour: 'var(--chart-4)' }]} />
          <h3>By head</h3>
          <StackedBar format={money} parts={HEADS.map(([key, label]) => ({ key, label, value: d.liability_by_head.find((h) => h.head === key)?.output || 0, colour: HEAD_COLOUR[key] }))} />
        </section>
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Return filing status">
          <h2>Returns this year</h2>
          <Donut slices={returnSlices} centreLabel="returns" />
          {r.total === 0 && <p className="hint">No return records yet. <Link to="/tax/returns?generate=1">Create the year's returns</Link>.</p>}
        </section>
        <section className="card" aria-label="Tax paid by kind">
          <h2>Tax paid by kind</h2>
          <p className="muted">Challans recorded this financial year.</p>
          <HBars rows={d.paid_by_type.map((p) => ({ key: p.type, label: p.label, value: p.amount, colour: KIND_COLOUR[p.type] }))} format={money} empty="No tax paid yet." />
        </section>
      </div>

      <section className="card" aria-label="Upcoming due dates">
        <div className="card-head"><h2>Coming up</h2><Link to="/tax/calendar">Open the calendar</Link></div>
        <EventList events={d.upcoming} empty="No returns or payments due in the next 90 days." />
      </section>
    </>
  );
}
