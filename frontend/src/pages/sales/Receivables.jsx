import { Link } from 'react-router-dom';
import { api } from '../../api';
import { HBars, StackedBar } from '../../components/charts';
import { compact, money, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Loading, PageHeader } from '../../ui';

const COLOURS = { current: 'var(--chart-2)', d1_15: '#e8b923', d16_30: '#ea7a1a', d31_45: 'var(--chart-4)', d46_plus: '#7f1d1d' };

// Who owes what, and for how long. Ageing is counted from each invoice's due date.
export default function Receivables() {
  const { data, error, loading, reload } = useLoad(() => api.get('/sales/reports/receivables/'), []);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const buckets = data.buckets;
  return (
    <>
      <PageHeader title="Receivables ageing" description="Unpaid invoices and debit notes, grouped by how long past their due date they are." />
      <div className="grid-2">
        <section className="card" aria-label="Total by age">
          <h2>Total {money(data.total)}</h2>
          <StackedBar parts={buckets.map((b) => ({ key: b.key, label: b.label, value: b.amount, colour: COLOURS[b.key] }))} format={(v) => money(v)} />
        </section>
        <section className="card" aria-label="By customer">
          <h2>By customer</h2>
          <HBars rows={data.rows.slice(0, 8).map((r) => ({ key: r.customer_id, label: r.customer, value: r.total }))} format={compact} empty="No customer owes you anything." />
        </section>
      </div>
      <section className="card" aria-label="Ageing by customer">
        {data.rows.length === 0 ? <p className="empty">Nothing is outstanding.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Customer</th>{buckets.map((b) => <th key={b.key} className="amount">{b.label}</th>)}<th className="amount">Total</th></tr></thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.customer_id}>
                    <td data-label="Customer"><Link to={`/sales/customers/${r.customer_id}`}>{r.customer}</Link></td>
                    {buckets.map((b) => <td key={b.key} className="amount" data-label={b.label}>{num(r[b.key]) ? plain(r[b.key]) : '—'}</td>)}
                    <td className="amount" data-label="Total"><strong>{plain(r.total)}</strong></td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr><th scope="row">Total</th>{buckets.map((b) => <td key={b.key} className="amount"><strong>{plain(b.amount)}</strong></td>)}<td className="amount"><strong>{plain(data.total)}</strong></td></tr>
              </tfoot>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
