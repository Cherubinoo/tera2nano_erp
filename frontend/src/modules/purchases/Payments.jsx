import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Dialog, Field, FormErrors, Loading, Notice, PageHeader, useReasonDialog, useToast, fmtDate } from '../../ui';
import { ApprovalActions, ApprovalPanel } from '../approvals/ApprovalBadge';
import { Status, usePurchaseOptions, useVendors, VendorSelect } from './common';
import DocList from './DocList';

function PaymentDialog({ onClose, onSaved, vendorId, billId }) {
  const notify = useToast();
  const opts = usePurchaseOptions();
  const { vendors } = useVendors();
  const [f, setF] = useState({ vendor: vendorId || '', date: today(), amount: '', mode: 'bank', paid_from: '', reference: '', notes: '' });
  const [alloc, setAlloc] = useState({});
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const open = useLoad(() => (f.vendor ? api.get(`/purchases/vendors/${f.vendor}/open-items/`) : Promise.resolve(null)), [f.vendor]);
  const owed = open.data?.owed || [];
  useEffect(() => { // a bill chosen from its page starts fully allocated
    if (billId && owed.length) {
      const it = owed.find((o) => o.key === billId);
      if (it) { setAlloc({ [it.key]: it.balance }); setF((x) => (x.amount ? x : { ...x, amount: it.balance })); }
    }
  }, [billId, open.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  const applied = Object.values(alloc).reduce((a, v) => a + num(v), 0);
  const advance = Math.max(num(f.amount) - applied, 0);
  const auto = () => {
    let left = num(f.amount);
    const next = {};
    owed.forEach((o) => { const use = Math.min(left, num(o.balance)); if (use > 0) { next[o.key] = use.toFixed(2); left -= use; } });
    setAlloc(next);
  };
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    setFailure(null);
    const er = {};
    if (!f.vendor) er.vendor = 'Choose the vendor.';
    if (!(num(f.amount) > 0)) er.amount = 'Enter an amount above 0.';
    if (!f.date) er.date = 'Choose the payment date.';
    if (applied > num(f.amount) + 0.001) er.amount = 'More is applied to bills than the amount paid.';
    for (const o of owed) if (num(alloc[o.key]) > num(o.balance) + 0.001) er.allocations = `${o.label} has only ${plain(o.balance)} left to pay.`;
    setErrors(er);
    if (Object.keys(er).length) return;
    setBusy(true);
    try {
      const allocations = owed.filter((o) => num(alloc[o.key]) > 0).map((o) => ({ bill: o.key, amount: String(alloc[o.key]) }));
      const p = await api.post('/purchases/payments/', { ...f, paid_from: f.paid_from || null, allocations });
      notify(p.status === 'pending_approval' ? 'Payment sent for approval. It is numbered, applied to the bills and posted once approved.' : `Payment ${p.number} recorded.`);
      onSaved(p);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  if (opts.loading) return null;
  return (
    <Dialog title="Record payment to a vendor" onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={[{ name: 'vendor' }, { name: 'amount' }, { name: 'date' }, { name: 'allocations' }, { name: 'mode' }, { name: 'paid_from' }]} />
        <div className="form-grid">
          <VendorSelect id="pay-vendor" value={f.vendor} onChange={(v) => { setF((x) => ({ ...x, vendor: v })); setAlloc({}); }} vendors={vendors} error={errors.vendor} />
          <Field id="pay-amount" label="Amount paid" required error={errors.amount}><input id="pay-amount" type="number" min="0" step="0.01" value={f.amount} onChange={set('amount')} /></Field>
          <Field id="pay-date" label="Payment date" required error={errors.date}><input id="pay-date" type="date" value={f.date} onChange={set('date')} /></Field>
          <Field id="pay-mode" label="Mode" error={errors.mode}><select id="pay-mode" value={f.mode} onChange={set('mode')}>{opts.data.modes.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}</select></Field>
          {f.mode !== 'cash' && <Field id="pay-from" label="Paid from" error={errors.paid_from} hint="Empty uses the default bank account.">
            <select id="pay-from" value={f.paid_from} onChange={set('paid_from')}><option value="">Default bank account</option>{opts.data.bank_accounts.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field>}
          <Field id="pay-ref" label="Reference (UTR, cheque no.)"><input id="pay-ref" value={f.reference} maxLength={100} onChange={set('reference')} /></Field>
        </div>
        {f.vendor && (open.loading ? <Loading /> : (
          <fieldset className="subcard">
            <legend>Apply to bills</legend>
            {errors.allocations && <p className="error-text" role="alert">{errors.allocations}</p>}
            {owed.length === 0 ? <p className="muted">This vendor has nothing outstanding; the whole amount is kept as an advance.</p> : (
              <>
                <button type="button" className="btn btn-small" onClick={auto} disabled={!(num(f.amount) > 0)}>Pay oldest first</button>
                <div className="table-wrap"><table>
                  <thead><tr><th>Bill</th><th>Due</th><th className="amount">Balance</th><th className="amount">Apply</th></tr></thead>
                  <tbody>{owed.map((o) => (
                    <tr key={o.key}><td data-label="Bill">{o.label}</td><td data-label="Due">{fmtDate(o.due_date)}</td><td className="amount" data-label="Balance">{plain(o.balance)}</td>
                      <td className="amount" data-label="Apply"><input aria-label={`Amount applied to ${o.label}`} inputMode="decimal" value={alloc[o.key] ?? ''} onChange={(e) => setAlloc((a) => ({ ...a, [o.key]: e.target.value }))} /></td></tr>))}</tbody>
                </table></div>
              </>
            )}
            <p className="hint">Applied {money(applied)}{advance > 0 ? ` · ${money(advance)} stays as an advance with the vendor` : ''}</p>
          </fieldset>
        ))}
        <Field id="pay-notes" label="Notes" wide><input id="pay-notes" value={f.notes} maxLength={300} onChange={set('notes')} /></Field>
        <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Record payment'}</button></div>
      </form>
    </Dialog>
  );
}

function ApplyDialog({ payment, onClose, onSaved }) {
  const notify = useToast();
  const open = useLoad(() => api.get(`/purchases/vendors/${payment.vendor.id}/open-items/`), [payment.id]);
  const [alloc, setAlloc] = useState({});
  const [failure, setFailure] = useState(null);
  const owed = open.data?.owed || [];
  const total = Object.values(alloc).reduce((a, v) => a + num(v), 0);
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    setFailure(null);
    try {
      await api.post(`/purchases/payments/${payment.id}/apply/`, { allocations: owed.filter((o) => num(alloc[o.key]) > 0).map((o) => ({ bill: o.key, amount: String(alloc[o.key]) })) });
      notify('Advance applied.'); onSaved();
    } catch (err) { setFailure(err); }
  };
  return (
    <Dialog title={`Apply advance from ${payment.number}`} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={[]} />
        <p className="muted">{money(payment.unapplied)} of this payment is not applied to any bill.</p>
        {open.loading ? <Loading /> : owed.length === 0 ? <p className="muted">Nothing is outstanding for this vendor.</p> : (
          <div className="table-wrap"><table><thead><tr><th>Bill</th><th className="amount">Balance</th><th className="amount">Apply</th></tr></thead>
            <tbody>{owed.map((o) => <tr key={o.key}><td data-label="Bill">{o.label}</td><td className="amount" data-label="Balance">{plain(o.balance)}</td>
              <td className="amount" data-label="Apply"><input aria-label={`Amount applied to ${o.label}`} inputMode="decimal" value={alloc[o.key] ?? ''} onChange={(e) => setAlloc((a) => ({ ...a, [o.key]: e.target.value }))} /></td></tr>)}</tbody></table></div>
        )}
        <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary" disabled={!(total > 0)}>Apply</button></div>
      </form>
    </Dialog>
  );
}

export default function Payments() {
  const { user } = useAuth();
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = useState(params.get('new') === '1');
  const [applying, setApplying] = useState(null);
  const [tick, setTick] = useState(0);
  const canManage = user.permissions.includes('purchases.manage');
  const voidIt = async (p) => {
    const reason = await ask({ title: 'Void payment', message: 'The entry in the books is reversed and the bills it settled become unpaid again.', confirmLabel: 'Void payment' });
    if (reason === null) return;
    try {
      const r = await api.post(`/purchases/payments/${p.id}/void/`, { change_reason: reason });
      notify(r.void_requested ? 'Voiding is above an approval limit, so it was sent for approval. The payment stays recorded until then.' : 'Payment voided.');
      setTick((t) => t + 1);
    } catch (e) { notify(e.message, 'error'); }
  };
  const done = () => { setCreating(false); setApplying(null); setParams({}, { replace: true }); setTick((t) => t + 1); };
  return (
    <>
      <DocList title="Vendor payments" description="Money paid to vendors, and which bills it settled." url="/purchases/payments/" reloadKey={tick}
        statuses={[['', 'All'], ['pending_approval', 'Pending approval'], ['recorded', 'Recorded'], ['void_pending', 'Void pending approval'], ['rejected', 'Rejected'], ['void', 'Void']]}
        searchHint="Number, vendor or reference" emptyText="No payments yet." totals={[['Paid', 'total_amount']]} rowTo={(r) => `/purchases/payments/${r.id}`}
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>Record payment</button>}
        csv={{ file: 'vendor-payments.csv', head: ['Date', 'Number', 'Vendor', 'Mode', 'Reference', 'Status', 'Amount', 'Unapplied'], row: (r) => [r.date, r.number, r.vendor.name, r.mode_label, r.reference, r.status, r.amount, r.unapplied] }}
        columns={[{ h: 'Date', v: (r) => fmtDate(r.date) }, { h: 'Number', v: (r) => <strong>{r.number || 'No number yet'}</strong> }, { h: 'Vendor', v: (r) => r.vendor.name }, { h: 'Mode', v: (r) => r.mode_label },
          { h: 'Reference', v: (r) => r.reference || '—' }, { h: 'Status', v: (r) => <Status k={r.void_requested && r.status === 'recorded' ? 'pending_approval' : r.status} label={r.status_label} /> },
          { h: 'Amount', amount: true, v: (r) => plain(r.amount) }, { h: 'Unapplied', amount: true, v: (r) => (num(r.unapplied) ? plain(r.unapplied) : '—') },
          { h: 'Actions', v: (r) => canManage && r.status === 'recorded' && !r.void_requested && (<span className="pur-row-actions" onClick={(e) => e.stopPropagation()}>
            {num(r.unapplied) > 0 && <button type="button" className="btn btn-small" onClick={() => setApplying(r)}>Apply advance</button>}
            <button type="button" className="btn btn-small" onClick={() => voidIt(r)}>Void</button></span>) }]} />
      {creating && <PaymentDialog vendorId={params.get('vendor')} billId={params.get('bill')} onClose={done} onSaved={done} />}
      {applying && <ApplyDialog payment={applying} onClose={() => setApplying(null)} onSaved={done} />}
      {dialog}
    </>
  );
}

// One payment to a vendor. A payment above an approval limit waits here (no number, nothing applied or posted) until it is
// approved; voiding a recorded one above a limit waits too and the payment stays recorded until then.
export function VendorPaymentDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const { data: p, error, loading, reload } = useLoad(() => api.get(`/purchases/payments/${id}/`), [id]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const canManage = user.permissions.includes('purchases.manage');
  const waiting = p.status === 'pending_approval';
  const voidWaiting = p.status === 'recorded' && p.void_requested;
  const voidIt = async () => {
    const reason = await ask({ title: 'Void payment', message: 'The entry in the books is reversed and the bills it settled become unpaid again. Above an approval limit this is sent for approval first.', confirmLabel: 'Void payment' });
    if (reason === null) return;
    try {
      const r = await api.post(`/purchases/payments/${id}/void/`, { change_reason: reason });
      notify(r.void_requested ? 'Sent for approval. The payment stays recorded until then.' : 'Payment voided.');
      reload();
    } catch (e) { notify(e.message, 'error'); }
  };
  const remove = async () => {
    if (!window.confirm('Delete this rejected payment?')) return;
    try { await api.del(`/purchases/payments/${id}/`); navigate('/purchases/payments'); } catch (e) { notify(e.message, 'error'); }
  };
  return (
    <>
      <Link to="/purchases/payments" className="back-link">← Vendor payments</Link>
      <PageHeader title={p.number || 'Payment waiting to be recorded'} description={`${p.vendor.name} · ${fmtDate(p.date)} · ${p.mode_label}`}
        actions={(
          <>
            <Status k={voidWaiting ? 'pending_approval' : p.status} label={p.status_label} />
            <ApprovalPanel kind="payment" sourceId={p.id} waiting={waiting || voidWaiting} canSubmit={canManage} onChange={reload} refreshKey={`${p.status}${p.void_requested}`} />
            {(waiting || voidWaiting) && (
              <ApprovalActions base={`/purchases/payments/${id}`} canAct={canManage} canDecide={canManage} managed={p.approval_managed}
                noun={waiting ? 'payment' : 'void request'} takeBack={waiting ? 'Take back' : 'Withdraw the void request'} onChange={reload} />
            )}
            {canManage && p.status === 'recorded' && !p.void_requested && <button type="button" className="btn" onClick={voidIt}>Void payment</button>}
            {canManage && p.status === 'rejected' && <button type="button" className="btn" onClick={remove}>Delete</button>}
          </>
        )} />
      {waiting && <Notice>This payment is above an approval limit. It is not numbered, applied to any bill or posted to the books until it is approved.</Notice>}
      {voidWaiting && <Notice>Voiding this payment ({p.void_request_reason}) is above an approval limit. The payment stays recorded until it is approved.</Notice>}
      {p.status === 'rejected' && <Notice type="warn">{p.decision_note || 'This payment was not approved.'} Nothing was posted. Record a new payment if it is still due.</Notice>}
      {p.status === 'recorded' && !p.void_requested && p.decision_note && <Notice type="warn">{p.decision_note}</Notice>}
      {p.status === 'void' && <Notice type="warn">Voided{p.void_reason ? `: ${p.void_reason}` : ''}</Notice>}
      <div className="tiles">
        <div className="tile"><div className="tile-label">Amount</div><div className="tile-value">{money(p.amount)}</div></div>
        <div className="tile"><div className="tile-label">Applied to bills</div><div className="tile-value">{waiting || p.status === 'rejected' ? '—' : money(p.applied)}</div></div>
        <div className="tile"><div className="tile-label">Advance with the vendor</div><div className="tile-value">{num(p.unapplied) > 0 ? money(p.unapplied) : '—'}</div></div>
      </div>
      <section className="card">
        <dl className="pur-facts">
          <div><dt>Vendor</dt><dd><Link to={`/purchases/vendors/${p.vendor.id}`}>{p.vendor.name}</Link></dd></div>
          <div><dt>Mode</dt><dd>{p.mode_label}</dd></div>
          <div><dt>Reference</dt><dd>{p.reference || '—'}</dd></div>
          <div><dt>Paid from</dt><dd>{p.paid_from || '—'}</dd></div>
          <div><dt>Notes</dt><dd>{p.notes || '—'}</dd></div>
        </dl>
        {p.allocations.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Bill</th><th className="amount">Applied</th></tr></thead>
            <tbody>{p.allocations.map((a, n) => <tr key={n}><td data-label="Bill">{a.bill ? <Link to={`/purchases/bills/${a.bill}`}>{a.number}</Link> : a.number}</td><td className="amount" data-label="Applied">{plain(a.amount)}</td></tr>)}</tbody>
          </table></div>
        )}
      </section>
      {dialog}
    </>
  );
}
