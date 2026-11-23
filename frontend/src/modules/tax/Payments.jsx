import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { useAuth } from '../../context';
import { money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, Tabs, fmtDate, useReasonDialog, useToast } from '../../ui';
import { StatusBadge, fyNow, has, useTaxOptions } from './common';

const TYPE_TABS = [['', 'All'], ['gst', 'GST'], ['tds', 'TDS'], ['advance_tax', 'Advance tax'], ['income_tax', 'Income tax']];

export function PaymentDialog({ opts, defaults = {}, onClose, onSaved }) {
  const notify = useToast();
  const type0 = defaults.tax_type || 'gst';
  const [v, setV] = useState({ tax_type: type0, date: today(), cin: '', bank_account: '', bank_name: '', notes: '', period: defaults.period || '', section: defaults.section || '', quarter: defaults.quarter || '',
    instalment: defaults.instalment || '', ...Object.fromEntries(Object.entries(defaults.heads || {})) });
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const type = opts.payment_types.find((t) => t.value === v.tax_type);
  const set = (k, val) => { setV((x) => ({ ...x, [k]: val })); setErr((e) => (e ? { ...e, fieldErrors: { ...e.fieldErrors, [k]: undefined } } : e)); };
  const total = type.heads.reduce((a, h) => a + num(v[`h_${h.value}`] ?? defaults.heads?.[h.value]), 0);
  const head = (h) => v[`h_${h}`] ?? defaults.heads?.[h] ?? '';
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const errs = {};
    if (!v.date) errs.date = 'Enter the payment date.';
    if (!(total > 0)) errs.heads = 'Enter the amount paid under at least one head.';
    if (Object.keys(errs).length) { setErr({ message: 'Please correct the highlighted fields.', fieldErrors: errs }); return; }
    setBusy(true);
    setErr(null);
    try {
      const heads = Object.fromEntries(type.heads.map((h) => [h.value, head(h.value)]).filter(([, x]) => num(x) > 0));
      const body = { tax_type: v.tax_type, date: v.date, heads, cin: v.cin, bank_name: v.bank_name, notes: v.notes, bank_account: v.bank_account || undefined };
      if (v.tax_type === 'gst') body.period = v.period || undefined;
      if (v.tax_type === 'tds') { body.section = v.section; body.quarter = v.quarter || undefined; }
      if (v.tax_type === 'advance_tax') body.instalment = v.instalment || undefined;
      const p = await api.post('/tax/payments/', body);
      notify(`Payment recorded: ${p.reference}`);
      onSaved(p);
    } catch (x) { setErr(x); }
    setBusy(false);
  };
  const fe = err?.fieldErrors || {};
  return (
    <Dialog title="Record a tax payment" onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={err} fields={[{ name: 'tax_type' }, { name: 'date' }, { name: 'heads' }, { name: 'cin' }, { name: 'period' }, { name: 'section' }, { name: 'quarter' }, { name: 'instalment' }, { name: 'bank_account' }, { name: 'notes' }]} />
        <p className="hint">This posts to the books: the tax payable account is debited and the bank is credited. A mistake is corrected by voiding the payment.</p>
        <div className="form-grid">
          <Field id="tp-type" label="Tax" required error={fe.tax_type}>
            <select id="tp-type" value={v.tax_type} onChange={(e) => set('tax_type', e.target.value)}>{opts.payment_types.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select>
          </Field>
          <Field id="tp-date" label="Payment date" required error={fe.date}><input id="tp-date" type="date" max={today()} value={v.date} onChange={(e) => set('date', e.target.value)} /></Field>
          {v.tax_type === 'gst' && <Field id="tp-period" label="Return month" hint="The month this payment settles" error={fe.period}><input id="tp-period" type="month" value={v.period} onChange={(e) => set('period', e.target.value)} /></Field>}
          {v.tax_type === 'tds' && (<>
            <Field id="tp-section" label="Section" hint="For example 194J; 192 for salary" error={fe.section}>
              <input id="tp-section" list="tp-sections" value={v.section} maxLength={10} onChange={(e) => set('section', e.target.value.toUpperCase())} />
              <datalist id="tp-sections">{opts.sections.map((s) => <option key={s} value={s} />)}</datalist>
            </Field>
            <Field id="tp-quarter" label="Quarter" error={fe.quarter}>
              <select id="tp-quarter" value={v.quarter} onChange={(e) => set('quarter', e.target.value)}><option value="">Not specified</option><option value="1">Q1</option><option value="2">Q2</option><option value="3">Q3</option><option value="4">Q4</option></select>
            </Field>
          </>)}
          {v.tax_type === 'advance_tax' && (
            <Field id="tp-inst" label="Instalment" hint="Chosen from the date when left empty" error={fe.instalment}>
              <select id="tp-inst" value={v.instalment} onChange={(e) => set('instalment', e.target.value)}><option value="">From the date</option>{opts.advance_tax.map((a) => <option key={a.instalment} value={a.instalment}>{a.instalment}: by {fmtDate(a.due_date)} ({num(a.cumulative_pct)}%)</option>)}</select>
            </Field>
          )}
        </div>
        <fieldset className="subcard"><legend>Amounts paid by head</legend>
          <div className="form-grid">
            {type.heads.map((h) => (
              <Field key={h.value} id={`tp-h-${h.value}`} label={h.label}>
                <input id={`tp-h-${h.value}`} inputMode="decimal" className="amount" placeholder="0.00" value={head(h.value)} onChange={(e) => set(`h_${h.value}`, e.target.value)} />
              </Field>
            ))}
          </div>
          {fe.heads && <p className="error-text" role="alert">{fe.heads}</p>}
          <p><strong>Total {money(total)}</strong></p>
        </fieldset>
        <div className="form-grid">
          <Field id="tp-cin" label="CIN / challan reference" hint="Optional" error={fe.cin}><input id="tp-cin" value={v.cin} maxLength={40} onChange={(e) => set('cin', e.target.value)} /></Field>
          <Field id="tp-bank" label="Paid from" hint="Default bank account when empty" error={fe.bank_account}>
            <select id="tp-bank" value={v.bank_account} onChange={(e) => set('bank_account', e.target.value)}><option value="">Default bank account</option>{opts.banks.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}</select>
          </Field>
          <Field id="tp-bankname" label="Bank name on the challan"><input id="tp-bankname" value={v.bank_name} maxLength={100} onChange={(e) => set('bank_name', e.target.value)} /></Field>
          <Field id="tp-notes" label="Notes" wide error={fe.notes}><input id="tp-notes" value={v.notes} maxLength={300} onChange={(e) => set('notes', e.target.value)} /></Field>
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Recording…' : 'Record payment'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export const headsText = (p, opts) => Object.entries(p.heads).map(([k, v]) => `${opts?.payment_types.find((t) => t.value === p.tax_type)?.heads.find((h) => h.value === k)?.label || k} ${plain(v)}`).join(' · ');

export function PaymentTable({ rows, opts, canManage, onVoid, onOpen }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Date</th><th>Reference</th><th>Tax</th><th>Period</th><th>Heads</th><th>Challan</th><th className="amount">Total</th><th>Status</th>{canManage && <th><span className="sr-only">Actions</span></th>}</tr></thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.id} className={p.status === 'void' ? 'row-inactive' : ''}>
              <td data-label="Date">{fmtDate(p.date)}</td>
              <td data-label="Reference"><button type="button" className="tax-link-button" onClick={() => onOpen(p)}>{p.reference}</button></td>
              <td data-label="Tax">{p.tax_label}</td>
              <td data-label="Period">{p.period_label || '—'}{p.section ? ` · ${p.section}` : ''}</td>
              <td data-label="Heads"><span className="hint">{headsText(p, opts)}</span></td>
              <td data-label="Challan">{p.cin || '—'}<div className="hint">{p.bank_name}</div></td>
              <td className="amount" data-label="Total"><strong>{plain(p.total)}</strong></td>
              <td data-label="Status"><StatusBadge status={p.status} />{p.void_reason && <div className="hint">{p.void_reason}</div>}</td>
              {canManage && <td className="row-actions">{p.status === 'recorded' && <button type="button" className="btn btn-small" onClick={() => onVoid(p)}>Void</button>}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Detail({ p, onClose, canManage }) {
  return (
    <Dialog title={`Payment ${p.reference}`} onClose={onClose} wide>
      <dl className="facts">
        <div><dt>Date</dt><dd>{fmtDate(p.date)}</dd></div><div><dt>Tax</dt><dd>{p.tax_label}</dd></div>
        <div><dt>Total</dt><dd>{money(p.total)}</dd></div><div><dt>Challan</dt><dd>{p.cin || '—'}</dd></div>
      </dl>
      <Attachments entityType="tax_payment" entityId={p.id} canManage={canManage} title="Challan receipt" hint="Attach the paid challan or bank confirmation." />
    </Dialog>
  );
}

export default function Payments() {
  const { user } = useAuth();
  const notify = useToast();
  const canManage = has(user, 'tax.manage');
  const opts = useTaxOptions();
  const [params, setParams] = useSearchParams();
  const type = params.get('type') || '';
  const [fy, setFy] = useState('');
  const [q, setQ] = useState('');
  const [dialog, setDialog] = useState(null);
  const [detail, setDetail] = useState(null);
  const { ask, dialog: reasonDialog } = useReasonDialog();
  const { data, error, loading, reload } = useLoad(() => api.get('/tax/payments/', { type, fy, q }), [type, fy, q]);
  useEffect(() => {
    if (params.get('new') && opts.data) {
      const heads = {};
      ['cgst', 'sgst', 'igst', 'tds', 'tax'].forEach((h) => { if (params.get(h)) heads[h] = params.get(h); });
      setDialog({ tax_type: params.get('new'), period: params.get('period') || '', instalment: params.get('instalment') || '', section: params.get('section') || '', heads });
      setParams(params.get('type') ? { type: params.get('type') } : {}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts.data]);
  const voidIt = async (p) => {
    const reason = await ask({ title: `Void payment ${p.reference}`, label: 'Reason', confirmLabel: 'Void payment', message: 'The books are reversed: the tax payable is restored and the bank is credited back.' });
    if (reason === null) return;
    try { await api.post(`/tax/payments/${p.id}/void/`, { reason }); notify('Payment voided.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  return (
    <>
      <PageHeader title="Tax Payments" description="GST, TDS and income tax challans. Each payment posts to the books."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setDialog({ tax_type: type || 'gst' })}>Record payment</button>} />
      <Tabs tabs={TYPE_TABS.map(([key, label]) => ({ key, label }))} value={type} onChange={(k) => setParams(k ? { type: k } : {}, { replace: true })} />
      <section className="card">
        <div className="toolbar">
          <Field id="pay-fy" label="Financial year"><select id="pay-fy" value={fy} onChange={(e) => setFy(e.target.value)}><option value="">All years</option>{(opts.data?.financial_years || [fyNow()]).map((y) => <option key={y}>{y}</option>)}</select></Field>
          <Field id="pay-q" label="Search" className="grow"><input id="pay-q" value={q} placeholder="Reference, challan number or period" onChange={(e) => setQ(e.target.value)} /></Field>
        </div>
        {loading && !data && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {data && data.results.length === 0 && <p className="empty">No payments{type || fy || q ? ' match' : ' recorded yet'}.</p>}
        {data && data.results.length > 0 && <PaymentTable rows={data.results} opts={opts.data} canManage={canManage} onVoid={voidIt} onOpen={setDetail} />}
        {data && <div className="list-foot"><span>{data.count} payment{data.count === 1 ? '' : 's'}</span><span>Total paid <strong>{money(data.total)}</strong></span></div>}
      </section>
      {dialog && opts.data && <PaymentDialog opts={opts.data} defaults={dialog} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); reload(); }} />}
      {detail && <Detail p={detail} canManage={canManage} onClose={() => setDetail(null)} />}
      {reasonDialog}
    </>
  );
}
