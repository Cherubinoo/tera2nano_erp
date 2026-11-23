import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { HBars, StackedBar } from '../../components/charts';
import { downloadCsv, money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, ErrorBox, Loading, PageHeader, fmtDate } from '../../ui';
import { PeriodBar } from './common';

const REPORTS = [
  ['profit-loss', 'Profit and loss', 'Income less costs and expenses over a period.'],
  ['balance-sheet', 'Balance sheet', 'What the company owns and owes on a date.'],
  ['cash-flow', 'Cash flow statement', 'Where cash came from and where it went.'],
  ['trial-balance', 'Trial balance', 'Every account balance on a date; debits equal credits.'],
];

export function ReportsHub() {
  return (
    <>
      <PageHeader title="Reports" description="Financial statements, always read straight from the books." />
      <div className="tiles">
        {REPORTS.map(([slug, title, text]) => (
          <Link key={slug} className="tile" to={`/accounting/reports/${slug}`}><div className="tile-value" style={{ fontSize: '1.15rem' }}>{title}</div><div className="hint">{text}</div></Link>
        ))}
        <Link className="tile" to="/accounting/accounts"><div className="tile-value" style={{ fontSize: '1.15rem' }}>General ledger</div><div className="hint">Open any account from the chart to see its entries and running balance.</div></Link>
        <Link className="tile" to="/accounting/assets"><div className="tile-value" style={{ fontSize: '1.15rem' }}>Fixed asset register</div><div className="hint">Cost, depreciation and book value of every asset, with depreciation by financial year.</div></Link>
        <Link className="tile" to="/accounting/budgets/report"><div className="tile-value" style={{ fontSize: '1.15rem' }}>Budget vs actual</div><div className="hint">Planned against actual income and spending, by account, project and month.</div></Link>
        <Link className="tile" to="/sales/receivables"><div className="tile-value" style={{ fontSize: '1.15rem' }}>Receivables ageing</div><div className="hint">Who owes you and for how long.</div></Link>
      </div>
    </>
  );
}

function ReportFrame({ title, subtitle, children, onExport, controls }) {
  return (
    <>
      <Link to="/accounting/reports" className="back-link no-print">← Reports</Link>
      <PageHeader title={title} description={subtitle}
        actions={<span className="no-print"><button type="button" className="btn" onClick={onExport}>Export CSV</button> <button type="button" className="btn" onClick={() => window.print()}>Print</button></span>} />
      {controls}
      {children}
    </>
  );
}

const AccountRow = ({ r, strong }) => (
  <tr><td data-label="Account">{r.account_id ? <Link to={`/accounting/accounts/${r.account_id}`}>{r.code ? `${r.code} ` : ''}{r.name}</Link> : r.name}</td><td className="amount" data-label="Amount">{strong ? <strong>{plain(r.amount)}</strong> : plain(r.amount)}</td></tr>
);

function Block({ title, rows, total, totalLabel }) {
  if (!rows.length && !total) return null;
  return (
    <>
      <tr className="group-row"><th colSpan={2} scope="colgroup">{title}</th></tr>
      {rows.map((r, i) => <AccountRow key={r.account_id || `${r.name}${i}`} r={r} />)}
      {totalLabel && <tr><th scope="row">{totalLabel}</th><td className="amount"><strong>{plain(total)}</strong></td></tr>}
    </>
  );
}

const Total = ({ label, value, tone }) => <tr><th scope="row">{label}</th><td className={`amount ${tone || ''}`}><strong>{plain(value)}</strong></td></tr>;

export function ProfitLoss() {
  const [range, setRange] = useState(null);
  const { data: p, error, loading, reload } = useLoad(() => api.get('/accounting/reports/profit-loss/', range || undefined), [range?.from, range?.to]);
  if (loading && !p) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const exportCsv = () => downloadCsv('profit-and-loss.csv', [['Section', 'Account', 'Amount'],
    ...p.income.map((r) => ['Income', r.name, r.amount]), ['', 'Total income', p.total_income],
    ...p.direct_costs.map((r) => ['Direct costs', r.name, r.amount]), ['', 'Gross profit', p.gross_profit],
    ...p.expenses.map((r) => ['Expenses', r.name, r.amount]), ['', 'Operating profit', p.operating_profit],
    ...p.other_income.map((r) => ['Other income', r.name, r.amount]), ...p.other_expenses.map((r) => ['Other expenses', r.name, r.amount]), ['', 'Net profit', p.net_profit]]);
  const bars = [
    { key: 'inc', label: 'Income', value: Math.max(num(p.total_income), 0), colour: 'var(--chart-2)' },
    { key: 'cost', label: 'Direct costs', value: num(p.total_direct_costs), colour: 'var(--chart-3)' },
    { key: 'exp', label: 'Expenses', value: num(p.total_expenses) + num(p.total_other_expenses), colour: 'var(--chart-4)' },
    { key: 'net', label: 'Net profit', value: Math.max(num(p.net_profit), 0), colour: 'var(--chart-1)' },
  ];
  return (
    <ReportFrame title="Profit and loss" subtitle={`${fmtDate(p.start)} to ${fmtDate(p.end)}`} onExport={exportCsv} controls={<PeriodBar from={p.start} to={p.end} onApply={setRange} />}>
      <div className="grid-2">
        <section className="card"><div className="table-wrap"><table className="items-table"><thead><tr><th>Account</th><th className="amount">Amount (INR)</th></tr></thead><tbody>
          <Block title="Income" rows={p.income} total={p.total_income} totalLabel="Total income" />
          <Block title="Direct costs" rows={p.direct_costs} total={p.total_direct_costs} totalLabel="Total direct costs" />
          <Total label="Gross profit" value={p.gross_profit} />
          <Block title="Operating expenses" rows={p.expenses} total={p.total_expenses} totalLabel="Total expenses" />
          <Total label="Operating profit" value={p.operating_profit} />
          <Block title="Other income" rows={p.other_income} total={p.total_other_income} />
          <Block title="Other expenses" rows={p.other_expenses} total={p.total_other_expenses} />
          <Total label="Net profit" value={p.net_profit} tone={num(p.net_profit) < 0 ? 'bad-text' : ''} />
        </tbody></table></div></section>
        <section className="card"><h2>At a glance</h2><HBars rows={bars} format={(v) => money(v)} /></section>
      </div>
    </ReportFrame>
  );
}

export function BalanceSheet() {
  const [asOf, setAsOf] = useState(null);
  const { data: b, error, loading, reload } = useLoad(() => api.get('/accounting/reports/balance-sheet/', asOf ? { as_of: asOf } : undefined), [asOf]);
  if (loading && !b) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const exportCsv = () => downloadCsv('balance-sheet.csv', [['Section', 'Account', 'Amount'],
    ...b.current_assets.map((r) => ['Current assets', r.name, r.amount]), ...b.fixed_assets.map((r) => ['Fixed assets', r.name, r.amount]), ['', 'Total assets', b.total_assets],
    ...b.current_liabilities.map((r) => ['Current liabilities', r.name, r.amount]), ...b.long_term_liabilities.map((r) => ['Long-term liabilities', r.name, r.amount]),
    ...b.equity.map((r) => ['Equity', r.name, r.amount]), ['', 'Total liabilities and equity', b.total_liabilities_and_equity]]);
  return (
    <ReportFrame title="Balance sheet" subtitle={`As of ${fmtDate(b.as_of)}${b.financial_year ? ` · financial year ${b.financial_year}` : ''}`} onExport={exportCsv}
      controls={<PeriodBar single from="" to={b.as_of} onApply={({ to }) => setAsOf(to)} />}>
      <div className="grid-2">
        <section className="card"><div className="table-wrap"><table className="items-table"><thead><tr><th>Assets</th><th className="amount">Amount (INR)</th></tr></thead><tbody>
          <Block title="Current assets" rows={b.current_assets} total={b.total_current_assets} totalLabel="Total current assets" />
          <Block title="Fixed assets" rows={b.fixed_assets} total={b.total_fixed_assets} totalLabel="Total fixed assets" />
          <Total label="Total assets" value={b.total_assets} />
        </tbody></table></div></section>
        <section className="card"><div className="table-wrap"><table className="items-table"><thead><tr><th>Liabilities and equity</th><th className="amount">Amount (INR)</th></tr></thead><tbody>
          <Block title="Current liabilities" rows={b.current_liabilities} total={b.total_current_liabilities} totalLabel="Total current liabilities" />
          <Block title="Long-term liabilities" rows={b.long_term_liabilities} total={b.total_long_term_liabilities} totalLabel="Total long-term liabilities" />
          <Block title="Equity" rows={b.equity} total={b.total_equity} totalLabel="Total equity" />
          <Total label="Total liabilities and equity" value={b.total_liabilities_and_equity} />
        </tbody></table></div></section>
      </div>
      <section className="card">
        <div className="card-head"><h2>Where the value sits</h2>{b.balanced ? <Badge tone="good">Assets equal liabilities plus equity</Badge> : <Badge tone="bad">Does not balance</Badge>}</div>
        <h3>Assets</h3>
        <StackedBar total={num(b.total_assets)} format={(v) => money(v)} parts={[
          { key: 'ca', label: 'Current assets', value: b.total_current_assets, colour: 'var(--chart-2)' }, { key: 'fa', label: 'Fixed assets', value: b.total_fixed_assets, colour: 'var(--chart-1)' }]} />
        <h3 style={{ marginTop: '1.25rem' }}>Liabilities and equity</h3>
        <StackedBar total={num(b.total_liabilities_and_equity)} format={(v) => money(v)} parts={[
          { key: 'cl', label: 'Current liabilities', value: b.total_current_liabilities, colour: 'var(--chart-4)' }, { key: 'll', label: 'Long-term liabilities', value: b.total_long_term_liabilities, colour: 'var(--chart-3)' },
          { key: 'eq', label: 'Equity', value: b.total_equity, colour: 'var(--chart-5)' }]} />
      </section>
    </ReportFrame>
  );
}

export function CashFlow() {
  const [range, setRange] = useState(null);
  const { data: c, error, loading, reload } = useLoad(() => api.get('/accounting/reports/cash-flow/', range || undefined), [range?.from, range?.to]);
  if (loading && !c) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const exportCsv = () => downloadCsv('cash-flow.csv', [['Section', 'Item', 'Amount'], ['Operating', 'Net profit', c.net_profit], ...c.operating.map((r) => ['Operating', r.name, r.amount]),
    ['', 'Net cash from operating activities', c.net_operating], ...c.investing.map((r) => ['Investing', r.name, r.amount]), ['', 'Net cash from investing activities', c.net_investing],
    ...c.financing.map((r) => ['Financing', r.name, r.amount]), ['', 'Net cash from financing activities', c.net_financing], ['', 'Net change in cash', c.net_change],
    ['', 'Cash at start', c.opening_cash], ['', 'Cash at end', c.closing_cash]]);
  const bars = [
    { key: 'op', label: 'Operating', value: Math.abs(num(c.net_operating)), colour: num(c.net_operating) < 0 ? 'var(--chart-4)' : 'var(--chart-2)' },
    { key: 'in', label: 'Investing', value: Math.abs(num(c.net_investing)), colour: num(c.net_investing) < 0 ? 'var(--chart-4)' : 'var(--chart-2)' },
    { key: 'fi', label: 'Financing', value: Math.abs(num(c.net_financing)), colour: num(c.net_financing) < 0 ? 'var(--chart-4)' : 'var(--chart-2)' },
  ];
  return (
    <ReportFrame title="Cash flow statement" subtitle={`${fmtDate(c.start)} to ${fmtDate(c.end)} · indirect method`} onExport={exportCsv} controls={<PeriodBar from={c.start} to={c.end} onApply={setRange} />}>
      <div className="grid-2">
        <section className="card"><div className="table-wrap"><table className="items-table"><thead><tr><th>Item</th><th className="amount">Amount (INR)</th></tr></thead><tbody>
          <tr className="group-row"><th colSpan={2} scope="colgroup">Operating activities</th></tr>
          <tr><td>Net profit</td><td className="amount">{plain(c.net_profit)}</td></tr>
          {c.operating.map((r) => <AccountRow key={r.account_id} r={r} />)}
          <Total label="Net cash from operating activities" value={c.net_operating} />
          {c.investing.length > 0 && <><tr className="group-row"><th colSpan={2} scope="colgroup">Investing activities</th></tr>{c.investing.map((r) => <AccountRow key={r.account_id} r={r} />)}<Total label="Net cash from investing activities" value={c.net_investing} /></>}
          {c.financing.length > 0 && <><tr className="group-row"><th colSpan={2} scope="colgroup">Financing activities</th></tr>{c.financing.map((r) => <AccountRow key={r.account_id} r={r} />)}<Total label="Net cash from financing activities" value={c.net_financing} /></>}
          <tr className="group-row"><th colSpan={2} scope="colgroup">Cash</th></tr>
          <Total label="Net change in cash" value={c.net_change} />
          <tr><td>Cash and bank at the start</td><td className="amount">{plain(c.opening_cash)}</td></tr>
          <Total label="Cash and bank at the end" value={c.closing_cash} />
        </tbody></table></div>
        {c.ties ? <p className="hint">The closing figure equals the real cash and bank balance in the books.</p> : <p className="error-text" role="alert">This does not agree with the cash and bank balance. Please tell your accountant.</p>}</section>
        <section className="card"><h2>Size of each activity</h2><p className="muted">Green brought cash in; red used it.</p><HBars rows={bars} format={(v) => money(v)} /></section>
      </div>
    </ReportFrame>
  );
}

export function TrialBalance() {
  const [asOf, setAsOf] = useState(null);
  const { data: t, error, loading, reload } = useLoad(() => api.get('/accounting/reports/trial-balance/', asOf ? { as_of: asOf } : undefined), [asOf]);
  if (loading && !t) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const exportCsv = () => downloadCsv('trial-balance.csv', [['Code', 'Account', 'Debit', 'Credit'], ...t.rows.map((r) => [r.code, r.name, r.debit, r.credit]), ['', 'Total', t.total_debit, t.total_credit]]);
  return (
    <ReportFrame title="Trial balance" subtitle={`As of ${fmtDate(t.as_of)}`} onExport={exportCsv} controls={<PeriodBar single from="" to={t.as_of || today()} onApply={({ to }) => setAsOf(to)} />}>
      <section className="card">
        <div className="table-wrap"><table>
          <thead><tr><th>Code</th><th>Account</th><th className="amount">Debit</th><th className="amount">Credit</th></tr></thead>
          <tbody>
            {t.rows.map((r) => <tr key={r.account_id}><td data-label="Code"><code>{r.code}</code></td><td data-label="Account"><Link to={`/accounting/accounts/${r.account_id}`}>{r.name}</Link></td>
              <td className="amount" data-label="Debit">{num(r.debit) ? plain(r.debit) : ''}</td><td className="amount" data-label="Credit">{num(r.credit) ? plain(r.credit) : ''}</td></tr>)}
          </tbody>
          <tfoot><tr><th colSpan={2} scope="row">Total {t.balanced ? <Badge tone="good">Balanced</Badge> : <Badge tone="bad">Does not balance</Badge>}</th><td className="amount"><strong>{plain(t.total_debit)}</strong></td><td className="amount"><strong>{plain(t.total_credit)}</strong></td></tr></tfoot>
        </table></div>
      </section>
    </ReportFrame>
  );
}
