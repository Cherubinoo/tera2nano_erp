import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { useAuth } from '../../context';
import { money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, fmtDate, useConfirm, useReasonDialog, useToast } from '../../ui';
import { HEAD_COLOUR, StatusBadge, has } from './common';

export const FIGURE_LABEL = {
  outward_taxable: 'Taxable value of outward supplies', b2b_taxable: 'B2B taxable value', b2c_taxable: 'B2C taxable value', credit_debit_notes_taxable: 'Credit and debit notes (net taxable)',
  igst: 'IGST', cgst: 'CGST', sgst: 'SGST / UTGST', cess: 'Cess', invoice_value: 'Total invoice value', itc_igst: 'ITC: IGST', itc_cgst: 'ITC: CGST', itc_sgst: 'ITC: SGST / UTGST',
  itc_cess: 'ITC: cess', itc_reversed: 'ITC reversed or ineligible', net_igst: 'Net IGST (tax less ITC)', net_cgst: 'Net CGST', net_sgst: 'Net SGST / UTGST', net_cess: 'Net cess',
  net_payable: 'Net tax payable (negative = credit)', cash_payable: 'Cash payable after set-off', tax_paid: 'Tax paid in the year', tds_deducted: 'TDS deducted', tds_paid: 'TDS paid to the government',
  book_profit: 'Profit as per books', taxable_income: 'Taxable income', total_tax: 'Total tax', tds_credit: 'TDS credit', advance_tax_paid: 'Advance and self-assessment tax paid', turnover: 'Turnover as per books',
};

function FileDialog({ f, onClose, onSaved }) {
  const notify = useToast();
  const [v, setV] = useState({ filed_date: today() > f.period_end ? today() : f.period_end, arn: f.arn || '', late_fee: f.late_fee, interest: f.interest });
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setV((x) => ({ ...x, [k]: e.target.value }));
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!v.filed_date) { setErr({ message: 'Please correct the highlighted fields.', fieldErrors: { filed_date: 'Enter the date it was filed.' } }); return; }
    setBusy(true);
    setErr(null);
    try { await api.post(`/tax/filings/${f.id}/file/`, v); notify('Marked as filed.'); onSaved(); } catch (x) { setErr(x); }
    setBusy(false);
  };
  const fe = err?.fieldErrors || {};
  return (
    <Dialog title={`Mark ${f.return_label} ${f.period_label} as filed`} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={err} fields={[{ name: 'filed_date' }, { name: 'arn' }, { name: 'late_fee' }, { name: 'interest' }]} />
        <div className="form-grid">
          <Field id="fd-date" label="Filed on" required error={fe.filed_date}><input id="fd-date" type="date" value={v.filed_date} max={today()} onChange={set('filed_date')} /></Field>
          <Field id="fd-arn" label="Acknowledgement / ARN number" hint="Optional" error={fe.arn}><input id="fd-arn" value={v.arn} maxLength={60} onChange={set('arn')} /></Field>
          <Field id="fd-late" label="Late fee paid" error={fe.late_fee}><input id="fd-late" inputMode="decimal" value={v.late_fee} onChange={set('late_fee')} /></Field>
          <Field id="fd-int" label="Interest paid" error={fe.interest}><input id="fd-int" inputMode="decimal" value={v.interest} onChange={set('interest')} /></Field>
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Mark as filed'}</button>
        </div>
      </form>
    </Dialog>
  );
}

function Settlement({ f, canManage, onChanged }) {
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const { ask, dialog: reasonDialog } = useReasonDialog();
  const { data: s, error, loading, reload } = useLoad(() => api.get(`/tax/filings/${f.id}/setoff/`), [f.id, f.has_setoff]);
  const [busy, setBusy] = useState(false);
  const [fail, setFail] = useState(null);
  if (loading && !s) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const head = (h) => <span style={{ color: HEAD_COLOUR[h] }}>{h.toUpperCase()}</span>;
  const post = async () => {
    if (!(await confirm({ title: 'Set off input credit?', confirmLabel: 'Post set-off', body: <p>This posts a journal entry dated {fmtDate(s.date)}: it debits the GST payable accounts and credits the input credit accounts, in the order the GST law requires. What is left is paid in cash.</p> }))) return;
    setBusy(true); setFail(null);
    try { await api.post(`/tax/filings/${f.id}/setoff/`); notify('Input credit set off.'); onChanged(); reload(); } catch (e) { setFail(e); }
    setBusy(false);
  };
  const undo = async () => {
    const reason = await ask({ title: 'Undo the set-off', label: 'Reason', confirmLabel: 'Undo set-off' });
    if (reason === null) return;
    setBusy(true); setFail(null);
    try { await api.post(`/tax/filings/${f.id}/setoff/undo/`, { reason }); notify('Set-off undone.'); onChanged(); reload(); } catch (e) { setFail(e); }
    setBusy(false);
  };
  const heads = ['cgst', 'sgst', 'igst'];
  const pay = heads.reduce((q, h) => ({ ...q, [h]: num(s.cash_payable[h]) > 0 ? s.cash_payable[h] : '' }), {});
  const payUrl = `/tax/payments?new=gst&period=${f.period_start.slice(0, 7)}${heads.map((h) => (pay[h] ? `&${h}=${pay[h]}` : '')).join('')}`;
  return (
    <section className="card" aria-label="GST settlement">
      <h2>Set-off and payment</h2>
      <p className="muted">From the books as of {fmtDate(s.as_of)}: tax owed in each head, the input credit available, and what remains to pay in cash.</p>
      <FormErrors error={fail} fields={[]} />
      <div className="table-wrap">
        <table>
          <thead><tr><th>Head</th><th className="amount">Tax owed</th><th className="amount">Input credit</th><th className="amount">Set off</th><th className="amount">Cash payable</th></tr></thead>
          <tbody>
            {heads.map((h) => (
              <tr key={h}><td data-label="Head">{head(h)}</td><td className="amount" data-label="Tax owed">{plain(s.output[h])}</td><td className="amount" data-label="Input credit">{plain(s.credit[h] ?? 0)}</td>
                <td className="amount" data-label="Set off">{plain(s.used_output[h] ?? 0)}</td><td className="amount" data-label="Cash payable"><strong>{plain(s.cash_payable[h])}</strong></td></tr>
            ))}
          </tbody>
          <tfoot><tr><th scope="row">Total</th><td className="amount" /><td className="amount" /><td className="amount" /><td className="amount"><strong>{plain(s.cash_total)}</strong></td></tr></tfoot>
        </table>
      </div>
      {s.matrix.length > 0 && (
        <p className="hint">Order: {s.matrix.map((m, i) => <span key={i}>{i ? '; ' : ''}{m.credit.toUpperCase()} credit {plain(m.amount)} against {m.against.toUpperCase()}</span>)}.</p>
      )}
      {s.posted && <p className="notice notice-success">Set-off posted on {fmtDate(s.date)}. Paid since: {money(s.paid_so_far)}. Still to pay: <strong>{money(s.still_to_pay)}</strong>.</p>}
      {canManage && (
        <div className="tax-actions">
          {!s.posted && <button type="button" className="btn btn-primary" disabled={busy || s.matrix.length === 0} onClick={post}>Post input credit set-off</button>}
          {s.posted && <button type="button" className="btn" disabled={busy} onClick={undo}>Undo set-off</button>}
          {num(s.still_to_pay) > 0 && <Link className="btn" to={payUrl}>Record GST payment</Link>}
        </div>
      )}
      {!s.posted && s.matrix.length === 0 && <p className="hint">There is no input credit to set off at this date.</p>}
      {dialog}{reasonDialog}
    </section>
  );
}

export default function ReturnDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const notify = useToast();
  const canManage = has(user, 'tax.manage');
  const { confirm, dialog } = useConfirm();
  const { ask, dialog: reasonDialog } = useReasonDialog();
  const { data: f, error, loading, reload } = useLoad(() => api.get(`/tax/filings/${id}/`), [id]);
  const [figs, setFigs] = useState({});
  const [meta, setMeta] = useState({});
  const [filing, setFiling] = useState(false);
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!f) return;
    setFigs(Object.fromEntries(f.figure_keys.map((k) => [k, f.figures[k] ?? ''])));
    setMeta({ arn: f.arn, late_fee: f.late_fee, interest: f.interest, notes: f.notes, due_date: f.due_date, filed_date: f.filed_date || '' });
  }, [f]);
  if (loading && !f) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const locked = f.stored_status === 'filed';
  const run = async (fn, ok) => {
    setBusy(true); setFailure(null);
    try { await fn(); if (ok) notify(ok); reload(); } catch (e) { setFailure(e); }
    setBusy(false);
  };
  const prepare = async () => {
    if (Object.keys(f.figures).length && !(await confirm({ title: 'Prepare again?', confirmLabel: 'Prepare', body: <p>The figures are worked out again from the books and replace any you edited.</p> }))) return;
    run(() => api.post(`/tax/filings/${f.id}/prepare/`), 'Figures prepared from the books.');
  };
  const reopen = async () => {
    const reason = await ask({ title: 'Reopen this return', label: 'Reason', confirmLabel: 'Reopen', message: 'A filed return is locked. Reopening it lets you correct the figures.' });
    if (reason !== null) run(() => api.post(`/tax/filings/${f.id}/reopen/`, { reason }), 'Return reopened.');
  };
  const remove = async () => {
    if (!(await confirm({ title: 'Delete this return record?', danger: true, confirmLabel: 'Delete', body: <p>{f.return_label} for {f.period_label} is removed. Journal entries are not affected.</p> }))) return;
    setBusy(true);
    try { await api.del(`/tax/filings/${f.id}/`); notify('Return record deleted.'); navigate('/tax/returns'); } catch (e) { setFailure(e); setBusy(false); }
  };
  const saveFigures = (e) => {
    e.preventDefault();
    run(() => api.patch(`/tax/filings/${f.id}/`, { figures: Object.fromEntries(Object.entries(figs).filter(([, v]) => v !== '')) }), 'Figures saved.');
  };
  const saveMeta = (e) => {
    e.preventDefault();
    const body = { arn: meta.arn, late_fee: meta.late_fee, interest: meta.interest, notes: meta.notes };
    if (!locked) body.due_date = meta.due_date;
    if (locked && meta.filed_date) body.filed_date = meta.filed_date;
    run(() => api.patch(`/tax/filings/${f.id}/`, body), 'Details saved.');
  };
  const fe = failure?.fieldErrors || {};
  return (
    <>
      <Link to="/tax/returns" className="back-link">← Returns</Link>
      <PageHeader title={`${f.return_label} · ${f.period_label}`} description={`${f.long_label} · reference ${f.reference}`}
        actions={canManage && (<>
          {!locked && <button type="button" className="btn btn-primary" disabled={busy} onClick={prepare}>Prepare from the books</button>}
          {!locked && <button type="button" className="btn" disabled={busy} onClick={() => setFiling(true)}>Mark as filed</button>}
          {locked && <button type="button" className="btn" disabled={busy} onClick={reopen}>Reopen</button>}
          {!locked && !f.has_setoff && <button type="button" className="btn btn-danger-outline" disabled={busy} onClick={remove}>Delete</button>}
        </>)} />
      <FormErrors error={failure} fields={[{ name: 'arn' }, { name: 'late_fee' }, { name: 'interest' }, { name: 'notes' }, { name: 'due_date' }, { name: 'filed_date' }]} />
      <div className="tiles">
        <div className="tile"><div className="tile-label">Status</div><div className="tile-value" style={{ fontSize: '1.2rem' }}><StatusBadge status={f.status} /></div>{f.filed_late && <div className="hint">Filed after the due date</div>}</div>
        <div className="tile"><div className="tile-label">Due date</div><div className="tile-value" style={{ fontSize: '1.2rem' }}>{fmtDate(f.due_date)}</div>
          <div className="hint">{f.stored_status === 'filed' ? `Filed ${fmtDate(f.filed_date)}` : f.overdue ? `${-f.days_left} days late` : `In ${f.days_left} days`}</div></div>
        <div className="tile"><div className="tile-label">Period</div><div className="tile-value" style={{ fontSize: '1.2rem' }}>{fmtDate(f.period_start)} to {fmtDate(f.period_end)}</div><div className="hint">Financial year {f.financial_year}</div></div>
        <div className="tile"><div className="tile-label">Acknowledgement</div><div className="tile-value" style={{ fontSize: '1.1rem', overflowWrap: 'anywhere' }}>{f.arn || '—'}</div>
          <div className="hint">{f.prepared_at ? `Figures prepared ${fmtDate(f.prepared_at.slice(0, 10))}` : 'Figures not prepared yet'}</div></div>
      </div>

      <section className="card" aria-label="Return figures">
        <h2>Figures</h2>
        <p className="muted">{locked ? 'This return is filed, so its figures are locked. Reopen it to correct them.' : 'Worked out from the registers and the books when you prepare the return; every figure can be corrected to match what you file.'}</p>
        <form onSubmit={saveFigures} noValidate>
          <div className="form-grid">
            {f.figure_keys.map((k) => (
              <Field key={k} id={`fig-${k}`} label={FIGURE_LABEL[k] || k}>
                <input id={`fig-${k}`} inputMode="decimal" className="amount" value={figs[k] ?? ''} disabled={locked || !canManage} placeholder="0.00" onChange={(e) => setFigs((x) => ({ ...x, [k]: e.target.value }))} />
              </Field>
            ))}
          </div>
          {fe.figures && <p className="error-text" role="alert">{fe.figures}</p>}
          {!locked && canManage && <div className="actions"><button type="submit" className="btn" disabled={busy}>Save figures</button></div>}
        </form>
      </section>

      {f.return_type === 'gstr3b' && <Settlement f={f} canManage={canManage} onChanged={reload} />}

      <section className="card" aria-label="Filing details">
        <h2>Filing details</h2>
        <form onSubmit={saveMeta} noValidate>
          <div className="form-grid">
            <Field id="fl-arn" label="Acknowledgement / ARN number" error={fe.arn}><input id="fl-arn" value={meta.arn ?? ''} maxLength={60} disabled={!canManage} onChange={(e) => setMeta((x) => ({ ...x, arn: e.target.value }))} /></Field>
            <Field id="fl-due" label="Due date" error={fe.due_date} hint={locked ? 'Reopen to change.' : 'Change it when the date is extended.'}><input id="fl-due" type="date" value={meta.due_date ?? ''} disabled={locked || !canManage} onChange={(e) => setMeta((x) => ({ ...x, due_date: e.target.value }))} /></Field>
            {locked && <Field id="fl-filed" label="Filed on" error={fe.filed_date}><input id="fl-filed" type="date" value={meta.filed_date ?? ''} disabled={!canManage} onChange={(e) => setMeta((x) => ({ ...x, filed_date: e.target.value }))} /></Field>}
            <Field id="fl-late" label="Late fee" error={fe.late_fee}><input id="fl-late" inputMode="decimal" value={meta.late_fee ?? ''} disabled={!canManage} onChange={(e) => setMeta((x) => ({ ...x, late_fee: e.target.value }))} /></Field>
            <Field id="fl-int" label="Interest" error={fe.interest}><input id="fl-int" inputMode="decimal" value={meta.interest ?? ''} disabled={!canManage} onChange={(e) => setMeta((x) => ({ ...x, interest: e.target.value }))} /></Field>
            <Field id="fl-notes" label="Notes" wide error={fe.notes}><input id="fl-notes" value={meta.notes ?? ''} maxLength={500} disabled={!canManage} onChange={(e) => setMeta((x) => ({ ...x, notes: e.target.value }))} /></Field>
          </div>
          {canManage && <div className="actions"><button type="submit" className="btn" disabled={busy}>Save details</button></div>}
        </form>
      </section>

      <Attachments entityType="tax_filing" entityId={f.id} canManage={canManage} title="Acknowledgement and working papers"
        hint="Attach the filing acknowledgement, the return summary or the challan receipt." />
      {filing && <FileDialog f={f} onClose={() => setFiling(false)} onSaved={() => { setFiling(false); reload(); }} />}
      {dialog}{reasonDialog}
    </>
  );
}
