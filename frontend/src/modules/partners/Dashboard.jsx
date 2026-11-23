import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart, Donut, HBars, SERIES_COLOURS } from '../../components/charts';
import { useAuth } from '../../context';
import { compact, money, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Loading, Notice, PageHeader, fmtDate } from '../../ui';
import { PartnerLink, StatusBadge } from './common';
import { TransactionDialog } from './Transactions';

function Tile({ label, value, hint, tone, to }) {
  const inner = (<><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</>);
  return to ? <Link className={`tile ${tone || ''}`} to={to}>{inner}</Link> : <div className={`tile ${tone || ''}`}>{inner}</div>;
}

export default function PartnerDashboard() {
  const { user } = useAuth();
  const dash = useLoad(() => api.get('/partners/dashboard/'), []);
  const [dialog, setDialog] = useState(false);
  const hist = useLoad(() => api.get('/partners/history/'), []);
  if (dash.loading) return <Loading />;
  if (dash.error) return <ErrorBox error={dash.error} onRetry={dash.reload} />;
  const d = dash.data;
  const canManage = user.permissions.includes('partners.manage');
  const slices = d.ratio.shares.map((s, i) => ({ key: s.partner, label: s.name, value: s.percent, colour: SERIES_COLOURS[i % SERIES_COLOURS.length] }));
  const pct = (v) => `${num(v)}%`;
  return (
    <>
      <PageHeader title="Partners" description={`Capital, drawings, loans and profit sharing for the financial year ${fmtDate(d.period.start)} to ${fmtDate(d.period.end)}.`}
        actions={canManage && (<>
          <button type="button" className="btn btn-primary" onClick={() => setDialog(true)}>Record transaction</button>
          <Link className="btn" to="/partners/profit-sharing">Profit sharing</Link>
        </>)} />

      {num(d.pending_approvals) > 0 && (
        <Notice type="warn">
          <span>{d.pending_approvals} withdrawal{d.pending_approvals === 1 ? ' is' : 's are'} waiting for approval ({money(d.pending_amount)}).</span>
          <Link className="btn btn-small" to="/partners/transactions?status=pending_approval">Review</Link>
        </Notice>
      )}
      {!d.books_agree.agree && <Notice type="error">The partner records and the ledger do not agree on capital, current accounts or loans. Check the journal for manual entries to those accounts.</Notice>}

      <div className="tiles">
        <Tile label="Total capital" value={money(d.total_capital)} hint={`Current accounts ${money(d.total_current)}`} to="/partners/accounts" />
        <Tile label="Partners" value={d.partner_count} hint="Active partners" to="/partners/accounts" />
        <Tile label="Loans from partners" value={money(d.loans_outstanding)} hint="Outstanding" to="/partners/loans" />
        <Tile label="Drawings this year" value={money(d.drawings_this_fy)} to="/partners/transactions?kind=drawing" />
        <Tile label="Profit share allocated" value={money(d.profit_share_ytd)} hint="This financial year" tone={num(d.profit_share_ytd) < 0 ? 'bad' : ''} to="/partners/profit-sharing" />
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Profit-sharing ratio">
          <h2>Profit-sharing ratio</h2>
          <p className="muted">{d.ratio.effective_from ? `In force from ${fmtDate(d.ratio.effective_from)}.` : 'No ratio has been set yet.'}</p>
          {slices.length ? <Donut slices={slices} centreLabel="partners" format={pct} showTotal={false} />
            : <p className="empty">Set the ratio on the <Link to="/partners/profit-sharing">Profit Sharing</Link> page.</p>}
        </section>
        <section className="card" aria-label="Capital by partner">
          <h2>Capital by partner</h2>
          <p className="muted">Capital account balance today.</p>
          <HBars rows={d.capital_by_partner.map((p) => ({ key: p.partner, label: p.name, value: p.capital }))} format={(v) => money(v)} empty="No capital has been introduced yet." />
        </section>
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Drawings by month">
          <h2>Drawings this year</h2>
          <p className="muted">Month by month, posted drawings only.</p>
          <ColumnChart title="Drawings per month" format={compact} series={[{ key: 'amount', label: 'Drawings', colour: 'var(--chart-4)' }]}
            data={d.drawings_by_month.map((m) => ({ label: m.label, values: { amount: m.amount } }))} />
        </section>
        <section className="card" aria-label="Capital over time">
          <h2>Capital over time</h2>
          <p className="muted">Month-end balances this financial year.</p>
          {hist.loading ? <Loading /> : hist.error ? <ErrorBox error={hist.error} onRetry={hist.reload} /> : (
            <ColumnChart title="Capital and current accounts by month" format={compact}
              series={[{ key: 'capital', label: 'Capital', colour: 'var(--chart-1)' }, { key: 'current', label: 'Current accounts', colour: 'var(--chart-2)' }]}
              data={hist.data.months.map((m) => ({ label: m.label, values: { capital: Math.max(num(m.capital), 0), current: Math.max(num(m.current), 0) } }))} />
          )}
        </section>
      </div>

      <section className="card" aria-label="Contribution against profit share">
        <h2>Contribution against profit share</h2>
        <p className="muted">Each partner&apos;s share of the capital put in, against their share of the profit.</p>
        {d.comparison.length === 0 ? <p className="empty">No partners yet.</p> : (
          <>
            <ColumnChart title="Contribution and profit share percent" format={(v) => `${num(v)}%`}
              series={[{ key: 'c', label: 'Share of capital', colour: 'var(--chart-1)' }, { key: 'p', label: 'Share of profit', colour: 'var(--chart-3)' }]}
              data={d.comparison.map((r) => ({ label: r.name.split(' ')[0], values: { c: r.contribution, p: r.profit_share } }))} />
            <div className="table-wrap"><table>
              <thead><tr><th>Partner</th><th className="amount">Share of capital</th><th className="amount">Share of profit</th><th className="amount">Difference</th></tr></thead>
              <tbody>{d.comparison.map((r) => (
                <tr key={r.name}><td data-label="Partner">{r.name}</td><td className="amount" data-label="Share of capital">{plain(r.contribution)}%</td>
                  <td className="amount" data-label="Share of profit">{plain(r.profit_share)}%</td>
                  <td className="amount" data-label="Difference">{plain(num(r.profit_share) - num(r.contribution))}%</td></tr>
              ))}</tbody>
            </table></div>
          </>
        )}
      </section>

      <section className="card" aria-label="Recent transactions">
        <div className="card-head"><h2>Recent transactions</h2><Link to="/partners/transactions">View all</Link></div>
        {d.recent.length === 0 ? <p className="empty">No transactions yet.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Date</th><th>Number</th><th>Partner</th><th>Type</th><th>Status</th><th className="amount">Amount</th></tr></thead>
            <tbody>{d.recent.map((t) => (
              <tr key={t.id}><td data-label="Date">{fmtDate(t.date)}</td><td data-label="Number"><code>{t.number}</code></td>
                <td data-label="Partner"><PartnerLink id={t.partner.id}>{t.partner.name}</PartnerLink></td><td data-label="Type">{t.kind_label}</td>
                <td data-label="Status"><StatusBadge status={t.status} label={t.status_label} /></td><td className="amount" data-label="Amount">{plain(t.amount)}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </section>
      {dialog && <TransactionDialog onClose={() => setDialog(false)} onSaved={() => { setDialog(false); dash.reload(); hist.reload(); }} />}
    </>
  );
}
