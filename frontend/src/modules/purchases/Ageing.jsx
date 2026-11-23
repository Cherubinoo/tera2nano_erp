import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { HBars, StackedBar } from '../../components/charts';
import { compact, downloadCsv, money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Field, Loading, PageHeader, fmtDate } from '../../ui';
import { AGEING_COLOURS } from './common';

// What is owed to each vendor and for how long it has been overdue, plus what falls due in the next 30 days.
export default function Ageing() {
  const [asOf, setAsOf] = useState(today());
  const { data, error, loading, reload } = useLoad(() => api.get('/purchases/reports/ageing/', { as_of: asOf || undefined }), [asOf]);
  const cal = useLoad(() => api.get('/purchases/reports/due-calendar/', { as_of: asOf || undefined }), [asOf]);
  const exportCsv = () => downloadCsv(`payables-ageing-${asOf}.csv`, [['Vendor', ...data.buckets.map((b) => b.label), 'Total', 'Advance paid'],
    ...data.rows.map((r) => [r.vendor, ...data.buckets.map((b) => r[b.key]), r.total, r.advance]), ['Total', ...data.buckets.map((b) => b.amount), data.total, '']]);
  return (
    <>
      <PageHeader title="Payables ageing" description="Unpaid bills grouped by how many days past their due date they are."
        actions={data && data.rows.length > 0 && <button type="button" className="btn" onClick={exportCsv}>Export CSV</button>} />
      <section className="card"><div className="toolbar"><Field id="ageing-date" label="As of"><input id="ageing-date" type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></Field></div></section>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && (
        <>
          <div className="grid-2">
            <section className="card" aria-label="Total by age"><h2>Total {money(data.total)}</h2>
              <StackedBar parts={data.buckets.map((b) => ({ key: b.key, label: b.label, value: b.amount, colour: AGEING_COLOURS[b.key] }))} format={(v) => money(v)} /></section>
            <section className="card" aria-label="By vendor"><h2>By vendor</h2>
              <HBars rows={data.rows.slice(0, 8).map((r) => ({ key: r.vendor_id, label: r.vendor, value: r.total }))} format={compact} empty="Nothing is owed to any vendor." /></section>
          </div>
          <section className="card" aria-label="Ageing by vendor">
            {data.rows.length === 0 ? <p className="empty">Nothing is outstanding.</p> : (
              <div className="table-wrap"><table>
                <thead><tr><th>Vendor</th>{data.buckets.map((b) => <th key={b.key} className="amount">{b.label}</th>)}<th className="amount">Total</th><th className="amount">Advance paid</th></tr></thead>
                <tbody>{data.rows.map((r) => (
                  <tr key={r.vendor_id}><td data-label="Vendor"><Link to={`/purchases/vendors/${r.vendor_id}`}>{r.vendor}</Link></td>
                    {data.buckets.map((b) => <td key={b.key} className="amount" data-label={b.label}>{num(r[b.key]) ? plain(r[b.key]) : '—'}</td>)}
                    <td className="amount" data-label="Total"><strong>{plain(r.total)}</strong></td><td className="amount" data-label="Advance paid">{num(r.advance) ? plain(r.advance) : '—'}</td></tr>))}</tbody>
                <tfoot><tr><th scope="row">Total</th>{data.buckets.map((b) => <td key={b.key} className="amount"><strong>{plain(b.amount)}</strong></td>)}<td className="amount"><strong>{plain(data.total)}</strong></td><td /></tr></tfoot>
              </table></div>
            )}
          </section>
        </>
      )}
      {cal.data && (
        <section className="card" aria-label="Payment due calendar">
          <h2>Due in the next 30 days: {money(cal.data.due_total)}</h2>
          {num(cal.data.overdue_total) > 0 && <p className="pur-bad">{money(cal.data.overdue_total)} on {cal.data.overdue_count} bill{cal.data.overdue_count === 1 ? ' is' : 's are'} already overdue.</p>}
          {cal.data.days.length === 0 ? <p className="empty">No bills fall due in this period.</p> : (
            <div className="table-wrap"><table><thead><tr><th>Due date</th><th>Bills</th><th className="amount">Amount</th></tr></thead>
              <tbody>{cal.data.days.map((d) => (
                <tr key={d.date}><td data-label="Due date">{fmtDate(d.date)}</td>
                  <td data-label="Bills">{d.bills.map((b) => <span key={b.id} className="pur-pill"><Link to={`/purchases/bills/${b.id}`}>{b.number}</Link> {b.vendor}</span>)}</td>
                  <td className="amount" data-label="Amount"><strong>{plain(d.amount)}</strong></td></tr>))}</tbody></table></div>
          )}
        </section>
      )}
    </>
  );
}
