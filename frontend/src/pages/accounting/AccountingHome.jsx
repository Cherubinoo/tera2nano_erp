import { Link } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart, Donut, SERIES_COLOURS } from '../../components/charts';
import { useAuth } from '../../context';
import { compact, money, num } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, ErrorBox, Loading, PageHeader, fmtDate } from '../../ui';
import { SOURCE_LABEL } from './common';

function Tile({ label, value, hint, tone, to }) {
  const inner = (<><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</>);
  return to ? <Link className={`tile ${tone || ''}`} to={to}>{inner}</Link> : <div className={`tile ${tone || ''}`}>{inner}</div>;
}

export default function AccountingHome() {
  const { user } = useAuth();
  const { data: d, error, loading, reload } = useLoad(() => api.get('/accounting/dashboard/'), []);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const cur = d.currency;
  const manage = user.permissions.includes('accounting.manage');
  const slices = d.expense_breakdown.map((r, i) => ({ key: r.name, label: r.name, value: num(r.amount), colour: SERIES_COLOURS[i % SERIES_COLOURS.length] }));

  return (
    <>
      <PageHeader title="Accounting" description={`Your books for the financial year ${d.period.start.slice(0, 4)}–${String(Number(d.period.start.slice(0, 4)) + 1).slice(2)}, up to today.`}
        actions={manage && (<>
          <Link className="btn btn-primary" to="/accounting/vouchers?new=expense">Record expense</Link>
          <Link className="btn" to="/accounting/journal/new">New journal entry</Link>
          <Link className="btn" to="/accounting/bank">Bank and cash</Link>
        </>)} />

      <div className="tiles">
        <Tile label="Cash and bank" value={money(d.cash_total, cur)} hint={`${d.cash.length} account${d.cash.length === 1 ? '' : 's'}`} to="/accounting/bank" />
        <Tile label="Receivable" value={money(d.receivable, cur)} hint="Owed by customers" to="/sales/receivables" />
        <Tile label="Payable" value={money(d.payable, cur)} hint="Owed to vendors" />
        <Tile label="Profit this year" value={money(d.ytd.profit, cur)} tone={num(d.ytd.profit) < 0 ? 'bad' : 'good'} hint={`This month ${money(d.month.profit, cur)}`} to="/accounting/reports/profit-loss" />
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Income and expenses">
          <h2>Income and expenses</h2>
          <p className="muted">Last six months, from the books.</p>
          <ColumnChart title="Income and expenses per month" format={compact}
            series={[{ key: 'income', label: 'Income', colour: 'var(--chart-2)' }, { key: 'expenses', label: 'Expenses', colour: 'var(--chart-3)' }]}
            data={d.monthly.map((m) => ({ label: m.label, values: { income: m.income, expenses: m.expenses } }))} />
        </section>
        <section className="card" aria-label="Where the money went">
          <h2>Where the money went</h2>
          <p className="muted">Expenses this financial year, by account.</p>
          {slices.length === 0 ? <p className="muted">No expenses recorded yet.</p> : <Donut slices={slices} centreLabel="accounts" showTotal={false} format={(v) => money(v, cur)} />}
        </section>
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Cash and bank balances">
          <h2>Cash and bank</h2>
          {d.cash.length === 0 ? <p className="muted">No cash or bank accounts yet.</p> : (
            <table className="items-table"><tbody>
              {d.cash.map((a) => <tr key={a.account_id}><td><Link to={`/accounting/accounts/${a.account_id}`}>{a.name}</Link></td><td className="amount"><strong>{money(a.balance, cur)}</strong></td></tr>)}
            </tbody></table>
          )}
        </section>
        <section className="card" aria-label="This year">
          <h2>This financial year</h2>
          <dl className="summary">
            <div><dt>Income</dt><dd>{money(d.ytd.income, cur)}</dd></div>
            <div><dt>Expenses</dt><dd>{money(d.ytd.expenses, cur)}</dd></div>
            <div><dt>Profit</dt><dd>{money(d.ytd.profit, cur)}</dd></div>
            <div><dt>This month's profit</dt><dd>{money(d.month.profit, cur)}</dd></div>
          </dl>
        </section>
      </div>

      <section className="card" aria-label="Recent entries">
        <div className="card-head"><h2>Recent entries</h2><Link to="/accounting/journal">View journal</Link></div>
        {d.recent.length === 0 ? <p className="empty">Nothing has been posted yet. Issue an invoice, record an expense or add a journal entry.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Date</th><th>Reference</th><th>What</th><th>From</th><th className="amount">Amount</th></tr></thead>
            <tbody>
              {d.recent.map((e) => (
                <tr key={e.id} className={e.status === 'reversed' ? 'row-inactive' : ''}>
                  <td data-label="Date">{fmtDate(e.date)}</td>
                  <td data-label="Reference"><Link to={`/accounting/journal/${e.id}`}><code>{e.reference || '—'}</code></Link></td>
                  <td data-label="What">{e.narration}</td>
                  <td data-label="From"><Badge tone="info">{SOURCE_LABEL[e.source] || e.source}</Badge></td>
                  <td className="amount" data-label="Amount">{money(e.amount, cur)}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </section>
    </>
  );
}
