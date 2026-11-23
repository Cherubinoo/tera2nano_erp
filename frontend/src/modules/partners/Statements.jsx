import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, downloadFile } from '../../api';
import { ColumnChart } from '../../components/charts';
import { compact, downloadCsv, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Field, Loading, PageHeader, fmtDate, useToast } from '../../ui';
import { PartnerSelect, fyOf, usePartnerLookups } from './common';

const Amt = ({ v }) => <span className={num(v) < 0 ? 'partners-neg' : ''}>{plain(v)}</span>;

// A partner's capital account statement for a period, with a history chart, CSV, print and (when switched on) PDF.
export function StatementView({ partnerId, range }) {
  const notify = useToast();
  const [busy, setBusy] = useState(false);
  const st = useLoad(() => api.get(`/partners/partners/${partnerId}/statement/`, { from: range.from, to: range.to }), [partnerId, range.from, range.to]);
  const hist = useLoad(() => api.get('/partners/history/', { partner: partnerId, from: range.from, to: range.to }), [partnerId, range.from, range.to]);
  if (st.loading) return <Loading />;
  if (st.error) return <ErrorBox error={st.error} onRetry={st.reload} />;
  const s = st.data;
  const exportCsv = () => downloadCsv(`capital-statement-${s.partner.name.replace(/\s+/g, '-')}-${s.start}-to-${s.end}.csv`, [
    ['Capital account statement', s.partner.name, `${s.start} to ${s.end}`], [],
    ['Particulars', 'Capital account', 'Current account', 'Total'],
    ['Opening balance', s.opening.capital, s.opening.current, s.opening.total],
    ...s.movements.map((m) => [m.label, m.capital, m.current, m.total]),
    ['Closing balance', s.closing.capital, s.closing.current, s.closing.total], [],
    ['Date', 'Number', 'Description', 'Capital account', 'Current account', 'Running total'],
    ...s.rows.map((r) => [r.date, r.number, r.description, r.capital, r.current, r.balance])]);
  const pdf = async () => {
    setBusy(true);
    try {
      const d = await api.post(`/partners/partners/${partnerId}/statement/pdf/`, { from: range.from, to: range.to });
      await downloadFile(`/company/issued-documents/${d.id}/download/`, 'statement.pdf');
      notify(`Statement ${d.reference_number} issued.`);
    } catch (e) { notify(e.message, 'error'); }
    setBusy(false);
  };
  return (
    <div className="partners-statement">
      <div className="partners-print-head"><h2>Capital account statement: {s.partner.name}</h2><p>{fmtDate(s.start)} to {fmtDate(s.end)}</p></div>
      <section className="card" aria-label="Capital account statement">
        <div className="card-head">
          <h2>{s.partner.name}: {fmtDate(s.start)} to {fmtDate(s.end)}</h2>
          <div className="no-print row-actions">
            <button type="button" className="btn btn-small" onClick={exportCsv}>Export CSV</button>
            <button type="button" className="btn btn-small" onClick={() => window.print()}>Print</button>
            {s.pdf_available && <button type="button" className="btn btn-small btn-primary" onClick={pdf} disabled={busy}>{busy ? 'Preparing…' : 'Download PDF'}</button>}
          </div>
        </div>
        <div className="table-wrap"><table>
          <thead><tr><th>Particulars</th><th className="amount">Capital account</th><th className="amount">Current account</th><th className="amount">Total</th></tr></thead>
          <tbody>
            <tr><td data-label="Particulars"><strong>Opening balance</strong></td><td className="amount" data-label="Capital account"><Amt v={s.opening.capital} /></td>
              <td className="amount" data-label="Current account"><Amt v={s.opening.current} /></td><td className="amount" data-label="Total"><Amt v={s.opening.total} /></td></tr>
            {s.movements.map((m) => (
              <tr key={m.key}><td data-label="Particulars">{m.label}</td>
                <td className="amount" data-label="Capital account">{num(m.capital) ? <Amt v={m.capital} /> : '—'}</td>
                <td className="amount" data-label="Current account">{num(m.current) ? <Amt v={m.current} /> : '—'}</td>
                <td className="amount" data-label="Total">{num(m.total) ? <Amt v={m.total} /> : '—'}</td></tr>
            ))}
            <tr className="partners-total-row"><td data-label="Particulars">Closing balance</td><td className="amount" data-label="Capital account"><Amt v={s.closing.capital} /></td>
              <td className="amount" data-label="Current account"><Amt v={s.closing.current} /></td><td className="amount" data-label="Total"><Amt v={s.closing.total} /></td></tr>
          </tbody>
        </table></div>
        {!s.pdf_available && <p className="hint no-print">A printable PDF will be available once the partner statement document type is switched on. Use Print or the CSV for now.</p>}
      </section>

      <section className="card" aria-label="Statement lines">
        <h2>Transactions in the period</h2>
        {s.rows.length === 0 ? <p className="empty">Nothing was posted in this period.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Date</th><th>Number</th><th>Description</th><th className="amount">Capital</th><th className="amount">Current</th><th className="amount">Running total</th></tr></thead>
            <tbody>{s.rows.map((r, i) => (
              <tr key={i}><td data-label="Date">{fmtDate(r.date)}</td><td data-label="Number"><code>{r.number}</code></td><td data-label="Description">{r.description}</td>
                <td className="amount" data-label="Capital">{num(r.capital) ? <Amt v={r.capital} /> : '—'}</td><td className="amount" data-label="Current">{num(r.current) ? <Amt v={r.current} /> : '—'}</td>
                <td className="amount" data-label="Running total"><Amt v={r.balance} /></td></tr>
            ))}</tbody>
          </table></div>
        )}
      </section>

      <section className="card no-print" aria-label="Capital over time">
        <h2>Capital over time</h2>
        {hist.loading ? <Loading /> : hist.error ? <ErrorBox error={hist.error} /> : (
          <ColumnChart title={`${s.partner.name}: capital and current account by month`} format={compact}
            series={[{ key: 'capital', label: 'Capital', colour: 'var(--chart-1)' }, { key: 'current', label: 'Current account', colour: 'var(--chart-2)' }]}
            data={hist.data.months.map((m) => ({ label: m.label, values: { capital: Math.max(num(m.capital), 0), current: Math.max(num(m.current), 0) } }))} />
        )}
      </section>
    </div>
  );
}

export function RangeBar({ range, onApply }) {
  const [f, setF] = useState(range.from);
  const [t, setT] = useState(range.to);
  const fy = fyOf(today());
  return (
    <form className="toolbar no-print" onSubmit={(e) => { e.preventDefault(); onApply({ from: f, to: t }); }}>
      <Field id="st-from" label="From"><input id="st-from" type="date" value={f} onChange={(e) => setF(e.target.value)} /></Field>
      <Field id="st-to" label="To"><input id="st-to" type="date" value={t} onChange={(e) => setT(e.target.value)} /></Field>
      <button type="submit" className="btn">Show</button>
      <button type="button" className="btn" onClick={() => { setF(fy.start); setT(fy.end); onApply({ from: fy.start, to: fy.end }); }}>This financial year</button>
    </form>
  );
}

export default function StatementsPage() {
  const [params, setParams] = useSearchParams();
  const { data: lk, error } = usePartnerLookups();
  const fy = fyOf(today());
  const [range, setRange] = useState({ from: fy.start, to: fy.end });
  const partnerId = params.get('partner') || '';
  if (error) return <ErrorBox error={error} />;
  if (!lk) return <Loading />;
  return (
    <>
      <PageHeader title="Capital account statements" description="Opening balance, what moved in the period, and the closing balance, for each partner." />
      <section className="card no-print">
        <div className="toolbar">
          <PartnerSelect id="st-partner" value={partnerId} onChange={(v) => setParams(v ? { partner: v } : {})} partners={lk.partners} blank="Choose a partner…" />
        </div>
        {!partnerId && <p className="muted">Choose a partner to see the statement.</p>}
        {partnerId && <RangeBar key={`${range.from}${range.to}`} range={range} onApply={setRange} />}
      </section>
      {partnerId && <StatementView partnerId={partnerId} range={range} />}
    </>
  );
}
