import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart, Donut, StackedBar } from '../../components/charts';
import { compact, money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Loading, PageHeader, Tabs } from '../../ui';
import { ChartTable, FyPicker, RangeBar, StatusBadge, Tile, fyBounds, fyNow, pct, useTaxOptions } from './common';

const RATE_COLOURS = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)', 'var(--chart-grey)'];

function Summary() {
  const opts = useTaxOptions();
  const [fy, setFy] = useState(fyNow());
  const { data: s, error, loading, reload } = useLoad(() => api.get('/tax/gst/summary/', { fy }), [fy]);
  const k = s?.kpis;
  return (
    <>
      <div className="toolbar"><FyPicker value={fy} onChange={setFy} options={opts.data} /></div>
      {loading && !s && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {s && (
        <>
          <div className="tiles">
            <Tile label="This month's liability" value={money(k.this_month_liability)} hint={`Net of credit: ${money(k.this_month_net)}`} tone={num(k.this_month_net) > 0 ? 'bad' : ''} />
            <Tile label="Input credit available" value={money(k.itc_available)} tone="good" hint="Balance in the input accounts" />
            <Tile label="Returns filed / pending" value={`${k.returns.filed} / ${k.returns.pending + k.returns.prepared + k.returns.late}`}
              hint={k.returns.late ? `${k.returns.late} late` : k.returns.total ? 'None late' : 'No records yet'} tone={k.returns.late ? 'bad' : ''} to="/tax/returns?group=gst" />
            <Tile label="Tax paid this year" value={money(k.tax_paid_ytd)} hint={`Still owed ${money(k.liability_outstanding)}`} to="/tax/payments?type=gst" />
          </div>
          <div className="grid-2">
            <section className="card" aria-label="Monthly GST trend">
              <h2>Output tax and input credit by month</h2>
              <ColumnChart title="Output tax, input credit per month" format={compact}
                series={[{ key: 'output', label: 'Output tax', colour: 'var(--chart-1)' }, { key: 'itc', label: 'Input credit', colour: 'var(--chart-2)' }]}
                data={s.months.map((m) => ({ label: m.short, values: { output: m.output_total, itc: m.itc_total } }))} />
            </section>
            <section className="card" aria-label="Tax by rate">
              <h2>Tax by rate</h2>
              <p className="muted">Output tax of the year to date, by GST rate.</p>
              <Donut format={money} showTotal={false} centreLabel="rates" slices={s.by_rate.map((r, i) => ({ key: String(r.rate), label: `${pct(r.rate)} GST`, value: r.tax, colour: RATE_COLOURS[i % RATE_COLOURS.length] }))} />
              {s.by_rate.length === 0 && <p className="hint">No taxed sales in this period.</p>}
            </section>
          </div>
          <section className="card" aria-label="Output tax against input credit">
            <h2>Output tax against input credit</h2>
            <p className="muted">The year's output tax: the part your input credit covers and the part that is payable in cash.</p>
            <StackedBar format={money} parts={[
              { key: 'cov', label: 'Covered by input credit', value: Math.min(num(s.totals.output), num(s.totals.itc)), colour: 'var(--chart-2)' },
              { key: 'cash', label: 'Payable in cash', value: Math.max(num(s.totals.output) - num(s.totals.itc), 0), colour: 'var(--chart-4)' },
              { key: 'cf', label: 'Credit carried forward', value: Math.max(num(s.totals.itc) - num(s.totals.output), 0), colour: 'var(--chart-grey)' }]} />
          </section>
          <section className="card" aria-label="Month by month">
            <h2>Month by month</h2>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Month</th><th className="amount">Taxable value</th><th className="amount">CGST</th><th className="amount">SGST</th><th className="amount">IGST</th>
                  <th className="amount">Output tax</th><th className="amount">Input credit</th><th className="amount">Net payable</th><th>GSTR-1</th><th>GSTR-3B</th></tr></thead>
                <tbody>
                  {s.months.map((m) => (
                    <tr key={m.month}>
                      <td data-label="Month"><strong>{m.label}</strong></td>
                      <td className="amount" data-label="Taxable value">{plain(m.taxable)}</td>
                      <td className="amount" data-label="CGST">{plain(m.output.cgst)}</td>
                      <td className="amount" data-label="SGST">{plain(m.output.sgst)}</td>
                      <td className="amount" data-label="IGST">{plain(m.output.igst)}</td>
                      <td className="amount" data-label="Output tax">{plain(m.output_total)}</td>
                      <td className="amount" data-label="Input credit">{plain(m.itc_total)}</td>
                      <td className={`amount${num(m.net) < 0 ? ' good-text' : ''}`} data-label="Net payable"><strong>{plain(m.net)}</strong></td>
                      <td data-label="GSTR-1">{m.gstr1 ? <StatusBadge status={m.gstr1} /> : '—'}</td>
                      <td data-label="GSTR-3B">{m.gstr3b ? <StatusBadge status={m.gstr3b} /> : '—'}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot><tr><th scope="row">Year</th><td className="amount">{plain(s.totals.taxable)}</td><td className="amount">{plain(s.totals.out_cgst)}</td><td className="amount">{plain(s.totals.out_sgst)}</td>
                  <td className="amount">{plain(s.totals.out_igst)}</td><td className="amount"><strong>{plain(s.totals.output)}</strong></td><td className="amount"><strong>{plain(s.totals.itc)}</strong></td>
                  <td className="amount"><strong>{plain(num(s.totals.output) - num(s.totals.itc))}</strong></td><td /><td /></tr></tfoot>
              </table>
            </div>
            <p className="hint">A negative net figure is credit to carry forward. Tax is shown as posted in the books.</p>
            <ChartTable title="the monthly trend" headers={['Month', 'Output tax', 'Input credit']} rows={s.months.map((m) => [m.label, money(m.output_total), money(m.itc_total)])} />
          </section>
        </>
      )}
    </>
  );
}

function Ledger() {
  const [fyStart] = fyBounds(fyNow());
  const [range, setRange] = useState({ from: fyStart, to: today() });
  const { data: l, error, loading, reload } = useLoad(() => api.get('/tax/gst/liability-ledger/', range), [range.from, range.to]);
  return (
    <>
      <RangeBar from={range.from} to={range.to} onApply={setRange} />
      {loading && !l && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {l && (
        <>
          <section className="card" aria-label="Liability ledger">
            <h2>Liability ledger</h2>
            <p className="muted">For each tax head: what was owed at the start, tax raised, credit used, cash paid, and what is owed at the end.</p>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Head</th><th className="amount">Opening</th><th className="amount">Liability</th><th className="amount">ITC utilised</th><th className="amount">Cash paid</th><th className="amount">Closing</th></tr></thead>
                <tbody>
                  {l.heads.map((h) => (
                    <tr key={h.head}>
                      <td data-label="Head"><strong>{h.label}</strong></td>
                      <td className="amount" data-label="Opening">{plain(h.opening)}</td><td className="amount" data-label="Liability">{plain(h.liability)}</td>
                      <td className="amount" data-label="ITC utilised">{plain(h.itc_utilised)}</td><td className="amount" data-label="Cash paid">{plain(h.cash_paid)}</td>
                      <td className="amount" data-label="Closing"><strong>{plain(h.closing)}</strong></td>
                    </tr>
                  ))}
                </tbody>
                <tfoot><tr><th scope="row">Total</th><td className="amount">{plain(l.total.opening)}</td><td className="amount">{plain(l.total.liability)}</td><td className="amount">{plain(l.total.itc_utilised)}</td>
                  <td className="amount">{plain(l.total.cash_paid)}</td><td className="amount"><strong>{plain(l.total.closing)}</strong></td></tr></tfoot>
              </table>
            </div>
          </section>
          <section className="card" aria-label="Input credit ledger">
            <h2>Input credit ledger</h2>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Head</th><th className="amount">Opening</th><th className="amount">Credit earned</th><th className="amount">Credit used</th><th className="amount">Closing</th></tr></thead>
                <tbody>
                  {l.heads.map((h) => (
                    <tr key={h.head}><td data-label="Head"><strong>{h.label}</strong></td><td className="amount" data-label="Opening">{plain(h.itc_opening)}</td>
                      <td className="amount" data-label="Credit earned">{plain(h.itc_accrued)}</td><td className="amount" data-label="Credit used">{plain(h.itc_used)}</td>
                      <td className="amount" data-label="Closing"><strong>{plain(h.itc_closing)}</strong></td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="card" aria-label="Closing liability by month">
            <h2>Month by month</h2>
            <ColumnChart title="Tax liability raised and cash paid per month" format={compact}
              series={[{ key: 'liability', label: 'Liability raised', colour: 'var(--chart-1)' }, { key: 'paid', label: 'Cash paid', colour: 'var(--chart-2)' }]}
              data={l.months.map((m) => ({ label: m.label.split(' ')[0], values: { liability: m.liability, paid: m.cash_paid } }))} />
            <div className="table-wrap">
              <table>
                <thead><tr><th>Month</th><th className="amount">Liability</th><th className="amount">ITC utilised</th><th className="amount">Cash paid</th><th className="amount">Owed at month end</th></tr></thead>
                <tbody>{l.months.map((m) => (
                  <tr key={m.month}><td data-label="Month">{m.label}</td><td className="amount" data-label="Liability">{plain(m.liability)}</td><td className="amount" data-label="ITC utilised">{plain(m.itc_utilised)}</td>
                    <td className="amount" data-label="Cash paid">{plain(m.cash_paid)}</td><td className="amount" data-label="Owed at month end"><strong>{plain(m.closing)}</strong></td></tr>
                ))}</tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </>
  );
}

function Reconciliation() {
  const [range, setRange] = useState({ from: fyBounds(fyNow())[0], to: today() });
  const { data: r, error, loading, reload } = useLoad(() => api.get('/tax/gst/reconciliation/', range), [range.from, range.to]);
  return (
    <>
      <RangeBar from={range.from} to={range.to} onApply={setRange} />
      {loading && !r && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {r && (
        <section className="card" aria-label="Books against filed returns">
          <h2>Books against filed returns</h2>
          <p className="muted">
            Figures from the books and the registers, beside the returns marked as filed for the period. The difference is the filed GSTR-3B figure less the books;
            a difference is shown in red. {r.filed.gstr1} GSTR-1 and {r.filed.gstr3b} GSTR-3B filed in this period.
          </p>
          {r.mismatches > 0 && <p className="notice notice-warn" role="status">{r.mismatches} figure{r.mismatches === 1 ? '' : 's'} differ from the books. Open the return and correct it, or explain it in the notes.</p>}
          <div className="table-wrap">
            <table>
              <thead><tr><th>Figure</th><th className="amount">Books</th><th className="amount">Register</th><th className="amount">GSTR-1 filed</th><th className="amount">GSTR-3B filed</th><th className="amount">Difference</th><th>Result</th></tr></thead>
              <tbody>
                {r.rows.map((x) => (
                  <tr key={x.key} className={x.status === 'mismatch' ? 'tax-diff-row' : ''}>
                    <td data-label="Figure">{x.label}</td>
                    <td className="amount" data-label="Books">{plain(x.books)}</td>
                    <td className="amount" data-label="Register">{plain(x.register)}</td>
                    <td className="amount" data-label="GSTR-1 filed">{x.gstr1 === null ? '—' : plain(x.gstr1)}</td>
                    <td className="amount" data-label="GSTR-3B filed">{x.gstr3b === null ? '—' : plain(x.gstr3b)}</td>
                    <td className={`amount${x.status === 'mismatch' ? ' bad-text' : ''}`} data-label="Difference">
                      {x.difference === null && x.gstr1_difference === null ? '—' : <strong>{plain(x.difference ?? x.gstr1_difference)}</strong>}
                    </td>
                    <td data-label="Result"><StatusBadge status={x.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">
            A "Register" figure that differs from the books usually means a document was voided or a journal posted outside an invoice or bill.
            {' '}<Link to="/tax/returns">Go to returns</Link>
          </p>
        </section>
      )}
    </>
  );
}

export default function GstSummary() {
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') || 'summary';
  return (
    <>
      <PageHeader title="GST Summary" description="Output tax, input credit and what is payable, by month, with the ledger and a check against what you filed." />
      <Tabs tabs={[{ key: 'summary', label: 'Summary' }, { key: 'ledger', label: 'Liability ledger' }, { key: 'reconciliation', label: 'Books vs filed' }]}
        value={tab} onChange={(k) => setParams(k === 'summary' ? {} : { tab: k }, { replace: true })} />
      {tab === 'summary' && <Summary />}
      {tab === 'ledger' && <Ledger />}
      {tab === 'reconciliation' && <Reconciliation />}
    </>
  );
}
