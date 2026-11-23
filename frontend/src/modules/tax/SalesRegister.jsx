import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart, Donut } from '../../components/charts';
import { compact, money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, ErrorBox, Loading, PageHeader, Tabs, fmtDate } from '../../ui';
import { ChartTable, DownloadButton, RangeBar, Tile, monthEnd, monthLabel, monthStart, pct } from './common';

const RATE_COLOURS = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)', 'var(--chart-grey)'];
const TABS = [['b2b', 'B2B'], ['b2cl', 'B2C large'], ['b2cs', 'B2C small'], ['cdn', 'Credit and debit notes'], ['exp', 'Exports'], ['hsn', 'HSN summary'], ['months', 'By month']];

const DocLink = ({ r }) => <Link to={`/sales/documents/${r.id}`}><strong>{r.number}</strong></Link>;
const Taxes = ({ r }) => (<>
  <td className="amount" data-label="Taxable">{plain(r.taxable)}</td><td className="amount" data-label="IGST">{plain(r.igst)}</td>
  <td className="amount" data-label="CGST">{plain(r.cgst)}</td><td className="amount" data-label="SGST">{plain(r.sgst)}</td></>);
const TaxHead = () => (<><th className="amount">Taxable</th><th className="amount">IGST</th><th className="amount">CGST</th><th className="amount">SGST</th></>);

function InvoiceTable({ rows, notes = false }) {
  if (!rows.length) return <p className="empty">Nothing in this section for the period.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Date</th><th>{notes ? 'Note' : 'Invoice'}</th>{notes && <th>Type</th>}<th>Customer</th><th>GSTIN</th><th>Place of supply</th><TaxHead /><th className="amount">Value</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td data-label="Date">{fmtDate(r.date)}</td><td data-label={notes ? 'Note' : 'Invoice'}><DocLink r={r} /></td>
              {notes && <td data-label="Type"><Badge tone={r.note_type === 'C' ? 'warn' : 'info'}>{r.note_type === 'C' ? 'Credit' : 'Debit'}</Badge>{r.against && <div className="hint">against {r.against}</div>}</td>}
              <td data-label="Customer">{r.customer}</td><td data-label="GSTIN">{r.gstin || '—'}</td><td data-label="Place of supply">{r.place_of_supply || '—'}</td>
              <Taxes r={r} /><td className="amount" data-label="Value"><strong>{plain(r.invoice_value)}</strong></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function SalesRegister() {
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') || 'b2b';
  const now = today();
  const [range, setRange] = useState({ from: monthStart(now), to: monthEnd(now) });
  const { data: r, error, loading, reload } = useLoad(() => api.get('/tax/gst/sales-register/', range), [range.from, range.to]);
  const csvSection = { b2b: 'b2b', b2cl: 'b2cl', b2cs: 'b2cs', cdn: 'cdnr', exp: 'exp', hsn: 'hsn' }[tab];
  const t = r?.totals;
  return (
    <>
      <PageHeader title="Sales Register" description="Issued invoices and notes arranged like GSTR-1: B2B, B2C, credit and debit notes, exports and HSN summary."
        actions={csvSection && <span className="tax-export no-print">
          <DownloadButton path="/tax/gst/sales-register/export/" params={{ ...range, section: csvSection }} name={`gstr1-${csvSection}.csv`}>Export {TABS.find(([k]) => k === tab)[1]} CSV</DownloadButton>
          {tab === 'cdn' && <DownloadButton path="/tax/gst/sales-register/export/" params={{ ...range, section: 'cdnur' }} name="gstr1-cdnur.csv">Export unregistered notes CSV</DownloadButton>}
        </span>} />
      <RangeBar from={range.from} to={range.to} onApply={setRange} />
      {loading && !r && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {r && (
        <>
          {r.gstin ? <p className="hint">Supplier GSTIN {r.gstin}</p> : <p className="notice notice-info">The company has no GSTIN on its profile; documents are listed without tax.</p>}
          <div className="tiles">
            <Tile label="Taxable value" value={money(t.taxable)} hint={`${t.documents} document${t.documents === 1 ? '' : 's'}, credit notes deducted`} />
            <Tile label="IGST" value={money(t.igst)} />
            <Tile label="CGST + SGST" value={money(num(t.cgst) + num(t.sgst))} hint={`CGST ${plain(t.cgst)} · SGST ${plain(t.sgst)}`} />
            <Tile label="Total tax" value={money(t.tax)} hint={`Invoice value ${money(t.invoice_value)}`} tone="good" />
          </div>
          <div className="grid-2">
            <section className="card" aria-label="Tax by month">
              <h2>Taxable value and tax by month</h2>
              <ColumnChart title="Taxable value and tax per month" format={compact}
                series={[{ key: 'taxable', label: 'Taxable value', colour: 'var(--chart-1)' }, { key: 'tax', label: 'Tax', colour: 'var(--chart-3)' }]}
                data={r.by_month.map((m) => ({ label: monthLabel(m.month).split(' ')[0], values: { taxable: m.taxable, tax: m.tax } }))} />
              {r.by_month.length === 0 && <p className="hint">No documents in this period.</p>}
            </section>
            <section className="card" aria-label="Tax by rate">
              <h2>Tax by rate</h2>
              <Donut format={money} showTotal={false} centreLabel="rates" slices={r.tax_by_rate.filter((x) => num(x.tax) || num(x.taxable)).map((x, i) => ({ key: String(x.rate), label: `${pct(x.rate)} GST`, value: x.tax, colour: RATE_COLOURS[i % RATE_COLOURS.length] }))} />
            </section>
          </div>
          <section className="card">
            <Tabs tabs={TABS.map(([key, label]) => ({ key, label }))} value={tab} onChange={(k) => setParams({ tab: k }, { replace: true })} />
            {tab === 'b2b' && <InvoiceTable rows={r.b2b} />}
            {tab === 'b2cl' && (<><p className="hint">Invoices to unregistered buyers in another state above {money(250000)}.</p><InvoiceTable rows={r.b2cl} /></>)}
            {tab === 'b2cs' && (
              r.b2cs.length === 0 ? <p className="empty">No small B2C supplies for the period.</p> : (
                <div className="table-wrap"><table>
                  <thead><tr><th>Place of supply</th><th className="amount">Rate</th><th className="amount">Documents</th><TaxHead /></tr></thead>
                  <tbody>{r.b2cs.map((x) => (
                    <tr key={`${x.place_code}-${x.rate}`}><td data-label="Place of supply">{x.place_of_supply}</td><td className="amount" data-label="Rate">{pct(x.rate)}</td><td className="amount" data-label="Documents">{x.documents}</td><Taxes r={x} /></tr>
                  ))}</tbody>
                </table></div>
              ))}
            {tab === 'cdn' && (<><h3>Registered buyers</h3><InvoiceTable rows={r.cdnr} notes /><h3>Unregistered buyers (large inter-state)</h3><InvoiceTable rows={r.cdnur} notes /></>)}
            {tab === 'exp' && <InvoiceTable rows={r.exports} />}
            {tab === 'hsn' && (
              r.hsn.length === 0 ? <p className="empty">No lines in this period.</p> : (
                <div className="table-wrap"><table>
                  <thead><tr><th>HSN / SAC</th><th>Description</th><th className="amount">Rate</th><th className="amount">Quantity</th><th className="amount">Value</th><TaxHead /></tr></thead>
                  <tbody>{r.hsn.map((x) => (
                    <tr key={`${x.hsn}-${x.rate}`}><td data-label="HSN / SAC"><strong>{x.hsn}</strong></td><td data-label="Description">{x.description || '—'}</td><td className="amount" data-label="Rate">{pct(x.rate)}</td>
                      <td className="amount" data-label="Quantity">{plain(x.quantity)} {x.uqc}</td><td className="amount" data-label="Value">{plain(x.value)}</td><Taxes r={x} /></tr>
                  ))}</tbody>
                </table></div>
              ))}
            {tab === 'months' && (
              r.by_month.length === 0 ? <p className="empty">No documents in this period.</p> : (
                <div className="table-wrap"><table>
                  <thead><tr><th>Month</th><th className="amount">Documents</th><TaxHead /><th className="amount">Total tax</th></tr></thead>
                  <tbody>{r.by_month.map((m) => (
                    <tr key={m.month}><td data-label="Month">{monthLabel(m.month)}</td><td className="amount" data-label="Documents">{m.documents}</td><Taxes r={m} /><td className="amount" data-label="Total tax"><strong>{plain(m.tax)}</strong></td></tr>
                  ))}</tbody>
                </table></div>
              ))}
            <div className="list-foot">
              <span>Taxable <strong>{money(t.taxable)}</strong></span><span>Tax <strong>{money(t.tax)}</strong></span><span>Invoice value <strong>{money(t.invoice_value)}</strong></span>
            </div>
            <ChartTable title="monthly totals" headers={['Month', 'Taxable value', 'Tax']} rows={r.by_month.map((m) => [monthLabel(m.month), money(m.taxable), money(m.tax)])} />
          </section>
        </>
      )}
    </>
  );
}
