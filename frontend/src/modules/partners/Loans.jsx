import { useState } from 'react';
import { api } from '../../api';
import { HBars } from '../../components/charts';
import { useAuth } from '../../context';
import { downloadCsv, money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Field, Loading, PageHeader, fmtDate } from '../../ui';
import { PartnerLink } from './common';
import { TransactionDialog } from './Transactions';

export default function LoansPage() {
  const { user } = useAuth();
  const [asOf, setAsOf] = useState(today());
  const [input, setInput] = useState(asOf);
  const [dialog, setDialog] = useState(null);
  const { data, error, loading, reload } = useLoad(() => api.get('/partners/loans/', { as_of: asOf }), [asOf]);
  const canManage = user.permissions.includes('partners.manage');
  const exportCsv = () => downloadCsv('partner-loans.csv', [['Partner', 'Lent', 'Repaid', 'Outstanding', 'Interest accrued (estimate)', 'As of'],
    ...data.rows.map((r) => [r.partner.name, r.lent, r.repaid, r.outstanding, r.interest_estimate, data.as_of])]);
  return (
    <>
      <PageHeader title="Loans from partners" description="Money partners have lent to the LLP, what has been repaid, and the interest that has built up."
        actions={canManage && (<>
          <button type="button" className="btn btn-primary" onClick={() => setDialog({ kind: 'loan_from_partner' })}>Record loan</button>
          <button type="button" className="btn" onClick={() => setDialog({ kind: 'loan_repayment' })}>Record repayment</button>
        </>)} />
      <section className="card">
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); if (input) setAsOf(input); }}>
          <Field id="ln-asof" label="As of"><input id="ln-asof" type="date" value={input} onChange={(e) => setInput(e.target.value)} /></Field>
          <button type="submit" className="btn">Show</button>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {data && data.rows.length === 0 && <p className="empty">No partner has lent money to the LLP yet.</p>}
        {data && data.rows.length > 0 && (
          <>
            <div className="table-wrap"><table>
              <thead><tr><th>Partner</th><th className="amount">Lent</th><th className="amount">Repaid</th><th className="amount">Outstanding</th><th className="amount">Interest accrued (estimate)</th></tr></thead>
              <tbody>{data.rows.map((r) => (
                <tr key={r.partner.id}><td data-label="Partner"><PartnerLink id={r.partner.id}>{r.partner.name}</PartnerLink></td>
                  <td className="amount" data-label="Lent">{plain(r.lent)}</td><td className="amount" data-label="Repaid">{plain(r.repaid)}</td>
                  <td className="amount" data-label="Outstanding"><strong>{plain(r.outstanding)}</strong></td><td className="amount" data-label="Interest accrued">{plain(r.interest_estimate)}</td></tr>
              ))}</tbody>
            </table></div>
            <div className="list-foot"><span>Outstanding <strong>{money(data.total_outstanding)}</strong></span><span>Interest estimate <strong>{money(data.total_interest_estimate)}</strong></span>
              <button type="button" className="btn btn-small" onClick={exportCsv}>Export CSV</button></div>
            <p className="hint">Simple interest at the rate each loan carries, on a 365-day year. Repayments reduce the oldest loan first. This is an estimate for planning; interest is not posted to the books until you record it.</p>
          </>
        )}
      </section>
      {data && data.rows.some((r) => num(r.outstanding) > 0) && (
        <div className="grid-2">
          <section className="card" aria-label="Outstanding by partner"><h2>Outstanding by partner</h2>
            <HBars rows={data.rows.filter((r) => num(r.outstanding) > 0).map((r) => ({ key: r.partner.id, label: r.partner.name, value: r.outstanding }))} format={(v) => money(v)} /></section>
          <section className="card" aria-label="Open loans"><h2>Open loans</h2>
            <div className="table-wrap"><table>
              <thead><tr><th>Partner</th><th>Lent on</th><th className="amount">Remaining</th><th className="amount">Rate</th></tr></thead>
              <tbody>{data.rows.flatMap((r) => r.open_loans.map((l, i) => (
                <tr key={`${r.partner.id}${i}`}><td data-label="Partner">{r.partner.name}</td><td data-label="Lent on">{fmtDate(l.date)}</td>
                  <td className="amount" data-label="Remaining">{plain(l.remaining)}</td><td className="amount" data-label="Rate">{num(l.rate) ? `${num(l.rate)}%` : 'Interest free'}</td></tr>
              )))}</tbody>
            </table></div></section>
        </div>
      )}
      {dialog && <TransactionDialog preset={dialog} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); reload(); }} />}
    </>
  );
}
