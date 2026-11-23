import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart } from '../../components/charts';
import { useAuth } from '../../context';
import { compact, money, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Field, FormErrors, Loading, PageHeader, fmtDate, useToast } from '../../ui';
import { ChartTable, FyPicker, StatusBadge, Tile, fyNow, has, pct, useTaxOptions } from './common';
import { FilingTable, NewReturnDialog } from './Returns';

function Items({ id, title, items, onChange, disabled, error }) {
  const set = (i, k, v) => onChange(items.map((x, j) => (j === i ? { ...x, [k]: v } : x)));
  return (
    <fieldset className="subcard" disabled={disabled}>
      <legend>{title}</legend>
      {items.length === 0 && <p className="hint">None added.</p>}
      {items.map((it, i) => (
        <div className="toolbar" key={i}>
          <Field id={`${id}-l-${i}`} label="Description" className="grow"><input id={`${id}-l-${i}`} value={it.label} maxLength={100} onChange={(e) => set(i, 'label', e.target.value)} /></Field>
          <Field id={`${id}-a-${i}`} label="Amount"><input id={`${id}-a-${i}`} inputMode="decimal" className="amount" value={it.amount} onChange={(e) => set(i, 'amount', e.target.value)} /></Field>
          <button type="button" className="btn btn-small" aria-label={`Remove ${it.label || 'item'}`} onClick={() => onChange(items.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      {error && <p className="error-text" role="alert">{error}</p>}
      <button type="button" className="btn btn-small" onClick={() => onChange([...items, { label: '', amount: '' }])}>Add a line</button>
    </fieldset>
  );
}

export default function IncomeTax() {
  const { user } = useAuth();
  const notify = useToast();
  const navigate = useNavigate();
  const canManage = has(user, 'tax.manage');
  const opts = useTaxOptions();
  const [params, setParams] = useSearchParams();
  const fy = params.get('fy') || fyNow();
  const { data, error, loading, reload } = useLoad(() => api.get('/tax/income-tax/', { fy }), [fy]);
  const filings = useLoad(() => api.get('/tax/filings/', { group: 'income_tax', fy }), [fy]);
  const [w, setW] = useState(null);
  const [est, setEst] = useState('');
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [newReturn, setNewReturn] = useState(false);
  useEffect(() => {
    if (!data) return;
    const c = data.computation;
    setW({ profit_override: c.profit_overridden ? c.profit_used : '', addbacks: c.addbacks.map((x) => ({ ...x })), deductions: c.deductions.map((x) => ({ ...x })), rate: c.rate, surcharge_pct: c.surcharge_pct, cess_pct: c.cess_pct, notes: c.notes || '' });
    setEst(data.schedule.estimated_liability ?? '');
  }, [data]);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const c = data.computation;
  const sch = data.schedule;
  const fe = failure?.fieldErrors || {};
  const save = async (body, ok) => {
    setBusy(true); setFailure(null);
    try { await fetchPut(fy, body); notify(ok); reload(); } catch (e) { setFailure(e); }
    setBusy(false);
  };
  const saveWorksheet = (e) => {
    e.preventDefault();
    const nonEmpty = (xs) => xs.filter((x) => x.label.trim() || x.amount !== '');
    save({ profit_override: w.profit_override, addbacks: nonEmpty(w.addbacks), deductions: nonEmpty(w.deductions), rate: w.rate, surcharge_pct: w.surcharge_pct, cess_pct: w.cess_pct, notes: w.notes }, 'Worksheet saved.');
  };
  const saveEstimate = (e) => { e.preventDefault(); save({ estimated_liability: est }, 'Estimate saved.'); };
  const set = (k) => (e) => setW((x) => ({ ...x, [k]: e.target.value }));
  const nextDue = sch.instalments.find((r) => ['overdue', 'due_soon', 'upcoming'].includes(r.status));
  return (
    <>
      <PageHeader title="Advance Tax & Income Tax" description="Estimate the LLP's income tax from the books, schedule the advance tax instalments, and track the ITR and audit filings." />
      <div className="toolbar"><FyPicker value={fy} onChange={(v) => setParams({ fy: v }, { replace: true })} options={opts.data} /></div>
      <FormErrors error={failure} fields={[{ name: 'estimated_liability' }, { name: 'profit_override' }, { name: 'rate' }, { name: 'surcharge_pct' }, { name: 'cess_pct' }, { name: 'addbacks' }, { name: 'deductions' }, { name: 'notes' }]} />
      <div className="tiles">
        <Tile label="Estimated income tax" value={money(c.total_tax)} hint={`On taxable income ${money(c.taxable_income)}`} />
        <Tile label="Advance tax paid" value={money(sch.total_paid)} hint={sch.estimated_liability === null ? 'No estimate set' : `${money(sch.balance)} still to pay of ${money(sch.estimated_liability)}`} tone="good" />
        <Tile label="TDS credit" value={money(c.tds_credit)} hint="From the receivable tracker" to="/tax/tds?tab=receivable" />
        <Tile label="Balance payable" value={money(c.net_payable)} tone={num(c.net_payable) > 0 ? 'bad' : ''} hint={num(c.net_payable) < 0 ? 'Refundable' : 'After TDS and tax paid'} />
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Tax computation">
          <h2>Tax computation</h2>
          <p className="muted">Profit from the books for the year, adjusted by your add-backs and deductions.</p>
          <div className="table-wrap">
            <table className="items-table">
              <tbody>
                <tr><td>Profit as per books</td><td className="amount">{plain(c.book_profit)}</td></tr>
                {c.profit_overridden && <tr><td>Profit used (your figure)</td><td className="amount">{plain(c.profit_used)}</td></tr>}
                <tr><td>Add: disallowed expenses</td><td className="amount">{plain(c.total_addbacks)}</td></tr>
                <tr><td>Less: deductions</td><td className="amount">{plain(c.total_deductions)}</td></tr>
                <tr><th scope="row">Taxable income (nearest ₹10)</th><td className="amount"><strong>{plain(c.taxable_income)}</strong></td></tr>
                <tr><td>Tax at {pct(c.rate)}</td><td className="amount">{plain(c.tax)}</td></tr>
                <tr><td>Surcharge at {pct(c.surcharge_pct)}</td><td className="amount">{plain(c.surcharge)}</td></tr>
                <tr><td>Health and education cess at {pct(c.cess_pct)}</td><td className="amount">{plain(c.cess)}</td></tr>
                <tr><th scope="row">Total tax</th><td className="amount"><strong>{plain(c.total_tax)}</strong></td></tr>
                <tr><td>Less: TDS credit</td><td className="amount">{plain(c.tds_credit)}</td></tr>
                <tr><td>Less: advance tax paid</td><td className="amount">{plain(c.advance_tax_paid)}</td></tr>
                <tr><td>Less: self-assessment tax paid</td><td className="amount">{plain(c.self_assessment_paid)}</td></tr>
                <tr><th scope="row">Balance payable (negative = refund)</th><td className={`amount${num(c.net_payable) > 0 ? ' bad-text' : ''}`}><strong>{plain(c.net_payable)}</strong></td></tr>
              </tbody>
            </table>
          </div>
        </section>

        <section className="card" aria-label="Worksheet inputs">
          <h2>Worksheet inputs</h2>
          {w && (
            <form onSubmit={saveWorksheet} noValidate>
              <div className="form-grid">
                <Field id="iw-profit" label="Profit to use instead of the books" hint="Leave empty to use the books' profit" error={fe.profit_override}><input id="iw-profit" inputMode="decimal" disabled={!canManage} value={w.profit_override} onChange={set('profit_override')} /></Field>
                <Field id="iw-rate" label="Tax rate %" error={fe.rate}><input id="iw-rate" inputMode="decimal" disabled={!canManage} value={w.rate} onChange={set('rate')} /></Field>
                <Field id="iw-sur" label="Surcharge %" hint="12% for income above ₹1 crore" error={fe.surcharge_pct}><input id="iw-sur" inputMode="decimal" disabled={!canManage} value={w.surcharge_pct} onChange={set('surcharge_pct')} /></Field>
                <Field id="iw-cess" label="Cess %" error={fe.cess_pct}><input id="iw-cess" inputMode="decimal" disabled={!canManage} value={w.cess_pct} onChange={set('cess_pct')} /></Field>
              </div>
              <Items id="iw-add" title="Add-backs (expenses not allowed)" items={w.addbacks} disabled={!canManage} error={fe.addbacks} onChange={(addbacks) => setW((x) => ({ ...x, addbacks }))} />
              <Items id="iw-ded" title="Deductions" items={w.deductions} disabled={!canManage} error={fe.deductions} onChange={(deductions) => setW((x) => ({ ...x, deductions }))} />
              <Field id="iw-notes" label="Notes" error={fe.notes}><input id="iw-notes" value={w.notes} maxLength={500} disabled={!canManage} onChange={set('notes')} /></Field>
              {canManage && <div className="actions"><button type="submit" className="btn btn-primary" disabled={busy}>Save worksheet</button></div>}
            </form>
          )}
        </section>
      </div>

      <section className="card" aria-label="Advance tax schedule">
        <h2>Advance tax schedule</h2>
        <p className="muted">15% by 15 June, 45% by 15 September, 75% by 15 December and 100% by 15 March of the year's estimated tax.</p>
        <form className="tax-estimate" onSubmit={saveEstimate} noValidate>
          <Field id="est-liability" label="Estimated tax for the year" error={fe.estimated_liability} hint={`Computed above: ${money(sch.suggested_liability)} after TDS credit`}>
            <input id="est-liability" inputMode="decimal" className="amount" value={est} disabled={!canManage} onChange={(e) => setEst(e.target.value)} />
          </Field>
          {canManage && <><button type="submit" className="btn btn-primary" disabled={busy}>Save estimate</button>
            <button type="button" className="btn" disabled={busy} onClick={() => setEst(sch.suggested_liability)}>Use the computed figure</button></>}
        </form>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Instalment</th><th>Due</th><th className="amount">Cumulative</th><th className="amount">Required</th><th className="amount">Paid</th><th className="amount">Shortfall</th><th>Status</th>{canManage && <th><span className="sr-only">Actions</span></th>}</tr></thead>
            <tbody>
              {sch.instalments.map((r) => (
                <tr key={r.instalment}>
                  <td data-label="Instalment"><strong>{r.instalment}</strong></td><td data-label="Due">{fmtDate(r.due_date)}</td>
                  <td className="amount" data-label="Cumulative">{pct(r.cumulative_pct)}<div className="hint">{plain(r.cumulative_required)}</div></td>
                  <td className="amount" data-label="Required">{plain(r.required)}</td><td className="amount" data-label="Paid">{plain(r.paid)}</td>
                  <td className="amount" data-label="Shortfall">{num(r.shortfall) ? <strong className={r.status === 'overdue' ? 'bad-text' : ''}>{plain(r.shortfall)}</strong> : '—'}</td>
                  <td data-label="Status"><StatusBadge status={r.status} /></td>
                  {canManage && <td className="row-actions">{num(r.shortfall) > 0 && <Link className="btn btn-small" to={`/tax/payments?new=advance_tax&instalment=${r.instalment}&tax=${r.shortfall}`}>Record payment</Link>}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {nextDue && num(nextDue.shortfall) > 0 && <p className="hint">Next: instalment {nextDue.instalment} by {fmtDate(nextDue.due_date)}, {money(nextDue.shortfall)} to pay to be up to date.</p>}
        <ColumnChart title="Advance tax required and paid by instalment" format={compact}
          series={[{ key: 'req', label: 'Cumulative required', colour: 'var(--chart-1)' }, { key: 'paid', label: 'Cumulative paid', colour: 'var(--chart-2)' }]}
          data={sch.instalments.map((r) => ({ label: `By ${new Date(`${r.due_date}T00:00:00`).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}`, values: { req: r.cumulative_required, paid: r.cumulative_paid } }))} />
        <ChartTable title="the instalments" headers={['Instalment', 'Cumulative required', 'Cumulative paid']} rows={sch.instalments.map((r) => [r.instalment, money(r.cumulative_required), money(r.cumulative_paid)])} />
      </section>

      <section className="card" aria-label="Income tax filings">
        <div className="card-head"><h2>ITR and tax audit</h2>{canManage && <button type="button" className="btn" onClick={() => setNewReturn(true)}>New record</button>}</div>
        <p className="muted">ITR-5 is due 31 July after the year ends, or 31 October when the accounts need an audit. The audit report is due 30 September.</p>
        {filings.data && filings.data.results.length === 0 && <p className="empty">No ITR or audit records for {fy}. Add them here or create the year's returns on the Returns page.</p>}
        {filings.data && filings.data.results.length > 0 && <FilingTable rows={filings.data.results} onOpen={(f) => navigate(`/tax/returns/${f.id}`)} />}
      </section>
      {newReturn && opts.data && <NewReturnDialog opts={opts.data} defaults={{ return_type: 'itr5', financial_year: fy }} onClose={() => setNewReturn(false)} onSaved={(f) => navigate(`/tax/returns/${f.id}`)} />}
    </>
  );
}

function fetchPut(fy, body) {
  // api.js has put(path, body) but no query helper; the financial year goes in the path's query string.
  return api.put(`/tax/income-tax/?fy=${encodeURIComponent(fy)}`, body);
}
