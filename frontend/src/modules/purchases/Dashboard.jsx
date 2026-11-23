import { Link } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart, Donut, HBars, SERIES_COLOURS, StackedBar } from '../../components/charts';
import { useAuth } from '../../context';
import { compact, money, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Loading, PageHeader, fmtDate } from '../../ui';
import { AGEING_COLOURS } from './common';

function Tile({ label, value, hint, tone, to }) {
  const inner = (<><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</>);
  return to ? <Link className={`tile ${tone || ''}`} to={to}>{inner}</Link> : <div className={`tile ${tone || ''}`}>{inner}</div>;
}

export default function Dashboard() {
  const { user } = useAuth();
  const dash = useLoad(() => api.get('/purchases/dashboard/'), []);
  const cal = useLoad(() => api.get('/purchases/reports/due-calendar/'), []);
  if (dash.loading) return <Loading />;
  if (dash.error) return <ErrorBox error={dash.error} onRetry={dash.reload} />;
  const d = dash.data;
  const cur = d.currency;
  const p = d.payables;
  const canManage = user.permissions.includes('purchases.manage');
  const slices = d.categories.map((c, i) => ({ key: c.account_id, label: c.category, value: num(c.amount), colour: SERIES_COLOURS[i % SERIES_COLOURS.length] }));
  return (
    <>
      <PageHeader title="Purchases" description="What you owe vendors, what you bought and what is on order."
        actions={canManage && (<><Link className="btn btn-primary" to="/purchases/orders/new">New purchase order</Link><Link className="btn" to="/purchases/bills/new">New bill</Link>
          <Link className="btn" to="/purchases/payments?new=1">Record payment</Link><Link className="btn" to="/purchases/vendors?new=1">New vendor</Link></>)} />
      <div className="tiles">
        <Tile label="Outstanding payables" value={money(p.outstanding, cur)} hint={`${p.vendors_owed} vendor${p.vendors_owed === 1 ? '' : 's'} to pay`} to="/purchases/ageing" />
        <Tile label="Overdue" value={money(p.overdue, cur)} tone={num(p.overdue) > 0 ? 'bad' : ''} hint={num(p.overdue) > 0 ? 'Past the due date' : 'Nothing overdue'} to="/purchases/bills?status=overdue" />
        <Tile label="Purchases this month" value={money(d.this_month.billed, cur)} hint={`${d.this_month.bills} bill${d.this_month.bills === 1 ? '' : 's'}`} to="/purchases/bills" />
        <Tile label="Open purchase orders" value={d.open_orders.count} hint={`${money(d.open_orders.value, cur)} awaiting delivery`} to="/purchases/orders?status=open" />
      </div>
      <div className="grid-2">
        <section className="card" aria-label="Purchases per month"><h2>Purchases per month</h2><p className="muted">Bills recorded and payments made, last six months.</p>
          <ColumnChart title="Billed and paid per month" format={compact}
            series={[{ key: 'billed', label: 'Billed', colour: 'var(--chart-1)' }, { key: 'paid', label: 'Paid', colour: 'var(--chart-2)' }]}
            data={d.monthly.map((m) => ({ label: m.label, values: { billed: m.billed, paid: m.paid } }))} /></section>
        <section className="card" aria-label="Top vendors"><h2>Top vendors</h2><p className="muted">By amount billed in the last six months.</p>
          <HBars rows={d.top_vendors.map((v) => ({ key: v.vendor_id, label: v.vendor, value: v.total }))} format={(v) => money(v, cur)} empty="No bills recorded yet." /></section>
      </div>
      <div className="grid-2">
        <section className="card" aria-label="Payables by age"><h2>Payables by age</h2><p className="muted">Days past the due date.</p>
          <StackedBar parts={d.aging.map((b) => ({ key: b.key, label: b.label, value: b.amount, colour: AGEING_COLOURS[b.key] }))} format={(v) => money(v, cur)} /></section>
        <section className="card" aria-label="Spend by category"><h2>Spend by category</h2><p className="muted">Where the cost went, last six months.</p>
          {slices.length === 0 ? <p className="muted">No bills recorded yet.</p> : <Donut slices={slices} centreLabel="categories" showTotal={false} format={(v) => money(v, cur)} />}</section>
      </div>
      <section className="card" aria-label="Payments due soon">
        <div className="card-head"><h2>Payments due in the next 30 days</h2><Link to="/purchases/ageing">Payables ageing</Link></div>
        {!cal.data ? <Loading /> : cal.data.days.length === 0 ? <p className="empty">No bills fall due in the next 30 days.</p> : (
          <div className="table-wrap"><table><thead><tr><th>Due date</th><th>Bills</th><th className="amount">Amount</th></tr></thead>
            <tbody>{cal.data.days.slice(0, 8).map((x) => (
              <tr key={x.date}><td data-label="Due date">{fmtDate(x.date)}</td>
                <td data-label="Bills">{x.bills.map((b) => <span key={b.id} className="pur-pill"><Link to={`/purchases/bills/${b.id}`}>{b.number}</Link> {b.vendor}</span>)}</td>
                <td className="amount" data-label="Amount"><strong>{plain(x.amount)}</strong></td></tr>))}</tbody></table></div>
        )}
      </section>
    </>
  );
}
