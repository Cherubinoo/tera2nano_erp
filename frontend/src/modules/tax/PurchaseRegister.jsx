import { useState } from 'react';
import { api } from '../../api';
import { ColumnChart, HBars, StackedBar } from '../../components/charts';
import { useAuth } from '../../context';
import { compact, money, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, fmtDate, useToast } from '../../ui';
import { ChartTable, DownloadButton, RangeBar, StatusBadge, Tile, has, monthEnd, monthLabel, monthStart } from './common';

function FlagDialog({ row, onClose, onSaved }) {
  const notify = useToast();
  const [reason, setReason] = useState(row.reason || '');
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const save = async (e, eligibility) => {
    e.preventDefault();
    e.stopPropagation();
    setBusy(true);
    setErr(null);
    try {
      await api.post('/tax/gst/purchase-register/flag/', { entry_id: row.entry_id, eligibility, reason });
      notify(eligibility === 'clear' ? 'Decision removed.' : `Marked ${eligibility}.`);
      onSaved();
    } catch (x) { setErr(x); }
    setBusy(false);
  };
  return (
    <Dialog title="Input credit eligibility" onClose={onClose}
      footer={(<>
        <button type="button" className="btn" onClick={onClose}>Cancel</button>
        {row.eligibility === 'ineligible' && <button type="button" className="btn" disabled={busy} onClick={(e) => save(e, 'clear')}>Remove decision</button>}
        {row.eligibility !== 'eligible' && <button type="button" className="btn" disabled={busy} onClick={(e) => save(e, 'eligible')}>Mark eligible</button>}
        <button type="button" className="btn btn-danger" disabled={busy} onClick={(e) => save(e, 'ineligible')}>Mark ineligible</button>
      </>)}>
      <p>{row.supplier} · {row.reference || row.journal_reference} · input credit <strong>{money(row.itc)}</strong></p>
      <FormErrors error={err} fields={[{ name: 'reason' }]} />
      <Field id="flag-reason" label="Reason (needed when ineligible)" error={err?.fieldErrors?.reason} hint="For example blocked under section 17(5), or the supplier has not filed.">
        <input id="flag-reason" value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <p className="hint">An ineligible amount is left out of the prepared GSTR-3B and of the set-off. It stays in the books until you journal it to expense.</p>
    </Dialog>
  );
}

export default function PurchaseRegister() {
  const { user } = useAuth();
  const canManage = has(user, 'tax.manage');
  const now = today();
  const [range, setRange] = useState({ from: monthStart(now), to: monthEnd(now) });
  const [filter, setFilter] = useState('');
  const [flag, setFlag] = useState(null);
  const { data: r, error, loading, reload } = useLoad(() => api.get('/tax/gst/purchase-register/', range), [range.from, range.to]);
  const t = r?.totals;
  const rows = r ? r.rows.filter((x) => !filter || x.eligibility === filter) : [];
  return (
    <>
      <PageHeader title="Purchase Register" description="Input GST on bills and expenses, read from the books, with the supplier's GSTIN and whether the credit can be claimed."
        actions={<span className="tax-export no-print"><DownloadButton path="/tax/gst/purchase-register/export/" params={range} name="purchase-register.csv" /></span>} />
      <RangeBar from={range.from} to={range.to} onApply={setRange} />
      {loading && !r && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {r && (
        <>
          <div className="tiles">
            <Tile label="Input credit" value={money(t.itc)} hint={`${t.documents} bill${t.documents === 1 ? '' : 's'}`} />
            <Tile label="Eligible" value={money(t.eligible)} tone="good" hint="Supplier GSTIN known" />
            <Tile label="No GSTIN" value={money(t.unverified)} tone={Number(t.unverified) > 0 ? 'bad' : ''} hint="Credit not yet verified" />
            <Tile label="Ineligible" value={money(t.ineligible)} hint="Marked as not claimable" />
          </div>
          {r.warnings.map((w) => <p key={w} className="notice notice-warn" role="status">{w}</p>)}
          <div className="grid-2">
            <section className="card" aria-label="Credit by eligibility">
              <h2>Credit by eligibility</h2>
              <StackedBar format={money} parts={[{ key: 'e', label: 'Eligible', value: t.eligible, colour: 'var(--chart-2)' }, { key: 'u', label: 'No supplier GSTIN', value: t.unverified, colour: 'var(--chart-3)' },
                { key: 'i', label: 'Ineligible', value: t.ineligible, colour: 'var(--chart-4)' }]} />
              <h3>By tax head</h3>
              <StackedBar format={money} parts={[{ key: 'c', label: 'CGST', value: t.cgst, colour: 'var(--chart-1)' }, { key: 's', label: 'SGST', value: t.sgst, colour: 'var(--chart-2)' }, { key: 'i', label: 'IGST', value: t.igst, colour: 'var(--chart-3)' }]} />
            </section>
            <section className="card" aria-label="Largest suppliers by credit">
              <h2>Largest suppliers by credit</h2>
              <HBars rows={r.by_supplier.map((s) => ({ key: s.supplier, label: s.supplier, value: s.itc }))} format={money} empty="No purchases in this period." />
            </section>
          </div>
          <section className="card" aria-label="Credit by month">
            <h2>Credit by month</h2>
            <ColumnChart title="Input credit per month" format={compact} series={[{ key: 'itc', label: 'Input credit', colour: 'var(--chart-2)' }]}
              data={r.by_month.map((m) => ({ label: monthLabel(m.month).split(' ')[0], values: { itc: m.itc } }))} />
            <ChartTable title="monthly input credit" headers={['Month', 'Bills', 'Input credit']} rows={r.by_month.map((m) => [monthLabel(m.month), m.documents, money(m.itc)])} />
          </section>
          <section className="card">
            <div className="chips" role="group" aria-label="Filter by eligibility">
              {[['', 'All'], ['eligible', 'Eligible'], ['unverified', 'No GSTIN'], ['ineligible', 'Ineligible']].map(([k, l]) => (
                <button key={k} type="button" className="chip" aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}</button>
              ))}
            </div>
            {rows.length === 0 ? <p className="empty">No purchases with input GST {filter ? 'match' : 'in this period'}.</p> : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Date</th><th>Supplier</th><th>GSTIN</th><th>Bill</th><th className="amount">Taxable</th><th className="amount">IGST</th><th className="amount">CGST</th><th className="amount">SGST</th>
                    <th className="amount">Credit</th><th>Eligibility</th>{canManage && <th><span className="sr-only">Actions</span></th>}</tr></thead>
                  <tbody>
                    {rows.map((x) => (
                      <tr key={x.entry_id}>
                        <td data-label="Date">{fmtDate(x.date)}</td>
                        <td data-label="Supplier"><strong>{x.supplier || '—'}</strong>{x.source === 'reversal' && <div className="hint">Reversal</div>}</td>
                        <td data-label="GSTIN">{x.gstin || '—'}</td>
                        <td data-label="Bill">{x.reference || '—'}</td>
                        <td className="amount" data-label="Taxable">{plain(x.taxable)}</td><td className="amount" data-label="IGST">{plain(x.igst)}</td>
                        <td className="amount" data-label="CGST">{plain(x.cgst)}</td><td className="amount" data-label="SGST">{plain(x.sgst)}</td>
                        <td className="amount" data-label="Credit"><strong>{plain(x.itc)}</strong></td>
                        <td data-label="Eligibility"><StatusBadge status={x.eligibility} />{x.reason && <div className="hint">{x.reason}</div>}</td>
                        {canManage && <td className="row-actions"><button type="button" className="btn btn-small" onClick={() => setFlag(x)}>Review</button></td>}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="list-foot"><span>Taxable <strong>{money(t.taxable)}</strong></span><span>Input credit <strong>{money(t.itc)}</strong></span></div>
          </section>
        </>
      )}
      {flag && <FlagDialog row={flag} onClose={() => setFlag(null)} onSaved={() => { setFlag(null); reload(); }} />}
    </>
  );
}
