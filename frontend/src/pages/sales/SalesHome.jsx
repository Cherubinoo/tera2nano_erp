import { Link } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart, Donut, HBars, StackedBar } from '../../components/charts';
import { useAuth } from '../../context';
import { compact, money, num } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Loading, PageHeader, fmtDate } from '../../ui';
import { StatusBadge } from './common';

const AGING_COLOURS = { current: 'var(--chart-2)', d1_15: '#e8b923', d16_30: '#ea7a1a', d31_45: 'var(--chart-4)', d46_plus: '#7f1d1d' };
const STATUS_SLICES = [
  ['paid', 'Paid', 'var(--chart-2)'], ['unpaid', 'Unpaid (not yet due)', 'var(--chart-1)'], ['overdue', 'Overdue', 'var(--chart-4)'],
  ['partial', 'Partially paid', 'var(--chart-3)'], ['draft', 'Draft', 'var(--chart-grey)'],
];

function Tile({ label, value, hint, tone, to }) {
  const inner = (<><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</>);
  return to ? <Link className={`tile ${tone || ''}`} to={to}>{inner}</Link> : <div className={`tile ${tone || ''}`}>{inner}</div>;
}

export default function SalesHome() {
  const { user } = useAuth();
  const dash = useLoad(() => api.get('/sales/dashboard/'), []);
  const recent = useLoad(() => api.get('/sales/documents/', { kind: 'invoice' }), []);
  if (dash.loading) return <Loading />;
  if (dash.error) return <ErrorBox error={dash.error} onRetry={dash.reload} />;
  const d = dash.data;
  const cur = d.currency;
  const r = d.receivables;
  const counts = d.status_counts;
  // Status counts are of receivable documents; "unpaid" includes overdue and part-paid, so the donut shows them apart.
  const slices = STATUS_SLICES.map(([key, label, colour]) => ({
    key, label, colour,
    value: key === 'unpaid' ? Math.max(counts.unpaid - counts.overdue - counts.partial, 0) : counts[key],
  }));
  const canManage = user.permissions.includes('sales.manage');

  return (
    <>
      <PageHeader title="Sales" description="What customers owe you, what you invoiced and what you received."
        actions={canManage && (<>
          <Link className="btn btn-primary" to="/sales/documents/new?kind=invoice">New invoice</Link>
          <Link className="btn" to="/sales/customers?new=1">New customer</Link>
          <Link className="btn" to="/sales/payments?new=1">Record payment</Link>
        </>)} />

      <div className="tiles">
        <Tile label="Total receivables" value={money(r.outstanding, cur)} hint={`${r.customers_owing} customer${r.customers_owing === 1 ? '' : 's'} owe you`} to="/sales/receivables" />
        <Tile label="Overdue" value={money(r.overdue, cur)} tone={num(r.overdue) > 0 ? 'bad' : ''} hint={num(r.overdue) > 0 ? 'Past the due date' : 'Nothing overdue'} to="/sales/documents?status=overdue" />
        <Tile label="Invoiced this month" value={money(d.invoiced_this_month, cur)} hint={`${d.invoices_this_month} invoice${d.invoices_this_month === 1 ? '' : 's'}`} to="/sales/documents" />
        <Tile label="Reminders due" value={d.reminders_due} tone={d.reminders_due > 0 ? 'warn' : ''} hint={d.reminders_due > 0 ? 'Unpaid invoices needing a reminder' : 'No invoice needs a reminder'} to="/sales/reminders" />
        <Tile label="Customers" value={d.customers} hint={num(d.advances) > 0 ? `${money(d.advances, cur)} received in advance` : 'Active customers'} to="/sales/customers" />
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Receivables by age">
          <h2>Receivables by age</h2>
          <p className="muted">How long unpaid invoices have been past their due date.</p>
          <StackedBar parts={d.aging.map((b) => ({ key: b.key, label: b.label, value: b.amount, colour: AGING_COLOURS[b.key] }))}
            format={(v) => money(v, cur)} />
        </section>
        <section className="card" aria-label="Invoice status">
          <h2>Invoice status</h2>
          <p className="muted">Invoices and debit notes, by where they stand.</p>
          <Donut slices={slices} centreLabel="documents" />
        </section>
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Invoiced and received">
          <h2>Invoiced and received</h2>
          <p className="muted">Last six months. Credit notes reduce what was invoiced.</p>
          <ColumnChart title="Invoiced and received per month" format={compact}
            series={[{ key: 'invoiced', label: 'Invoiced', colour: 'var(--chart-1)' }, { key: 'received', label: 'Received', colour: 'var(--chart-2)' }]}
            data={d.monthly.map((m) => ({ label: m.label, values: { invoiced: m.invoiced, received: m.received } }))} />
        </section>
        <section className="card" aria-label="Top customers by balance">
          <h2>Who owes the most</h2>
          <p className="muted">Top customers by outstanding amount.</p>
          <HBars rows={d.top_customers.map((c) => ({ key: c.customer_id, label: c.customer, value: c.outstanding }))}
            format={(v) => money(v, cur)} empty="No customer owes you anything." />
        </section>
      </div>

      <section className="card" aria-label="Recent invoices">
        <div className="card-head"><h2>Recent invoices</h2><Link to="/sales/documents">View all</Link></div>
        {recent.loading ? <Loading /> : recent.data?.results.length === 0 ? <p className="empty">No invoices yet.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Number</th><th>Customer</th><th>Status</th><th className="amount">Amount</th><th className="amount">Balance due</th></tr></thead>
              <tbody>
                {(recent.data?.results || []).slice(0, 6).map((x) => (
                  <tr key={x.id}>
                    <td data-label="Date">{fmtDate(x.issue_date)}</td>
                    <td data-label="Number"><Link to={`/sales/documents/${x.id}`}>{x.number || 'Draft'}</Link></td>
                    <td data-label="Customer">{x.customer.name}</td>
                    <td data-label="Status"><StatusBadge doc={x} /></td>
                    <td className="amount" data-label="Amount">{money(x.grand_total, x.currency)}</td>
                    <td className="amount" data-label="Balance due">{x.balance === null ? '—' : money(x.balance, x.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
