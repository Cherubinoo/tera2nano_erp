import { useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { money, num, plain, today } from '../../format';
import { asList, useLoad } from '../../hooks';
import { ApprovalActions, ApprovalPanel } from '../../modules/approvals/ApprovalBadge';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, fmtDate, useReasonDialog, useToast } from '../../ui';
import { PAYMENT_MODES, ProjectField, ProjectFilter, projectLabel } from './common';

const cents = (v) => Math.round(num(v) * 100);

// Record money received and apply it to the customer's open invoices (oldest due first, or by hand).
export function PaymentDialog({ customerId = '', documentId = '', onClose, onSaved }) {
  const notify = useToast();
  const customers = useLoad(() => api.get('/sales/customers/', { active: 'true' }), []);
  const banks = useLoad(() => api.get('/company/bank-accounts/', { active: 'true' }), []);
  const [v, setV] = useState({ customer: customerId, date: today(), amount: '', mode: 'bank_transfer', reference: '', deposit_account: '', notes: '', project: '' });
  const [alloc, setAlloc] = useState({}); // document id -> amount text
  const [open, setOpen] = useState([]);
  const [loadingOpen, setLoadingOpen] = useState(false);
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };

  useEffect(() => {
    if (!v.customer) { setOpen([]); setAlloc({}); return undefined; }
    let live = true;
    setLoadingOpen(true);
    api.get(`/sales/customers/${v.customer}/open-items/`).then((r) => {
      if (!live) return;
      setOpen(r.owed);
      setAlloc({});
      const target = r.owed.find((d) => d.id === documentId);
      if (target) setV((x) => (x.amount ? x : { ...x, amount: target.balance })); // never overwrite what was typed
    }).catch(() => {}).finally(() => live && setLoadingOpen(false));
    return () => { live = false; };
  }, [v.customer, documentId]);

  // Opened from one invoice: the amount applied to it follows the amount received (never more than it owes).
  useEffect(() => {
    const target = documentId && open.find((d) => d.id === documentId);
    if (!target) return;
    setAlloc(num(v.amount) > 0 ? { [target.id]: String(Math.min(num(v.amount), num(target.balance))) } : {});
  }, [v.amount, open, documentId]);

  const applied = useMemo(() => Object.values(alloc).reduce((a, x) => a + cents(x), 0), [alloc]);
  const left = cents(v.amount) - applied;
  const autoApply = () => {
    let remaining = cents(v.amount);
    const next = {};
    open.forEach((d) => {
      const use = Math.min(remaining, cents(d.balance));
      if (use > 0) { next[d.id] = (use / 100).toFixed(2); remaining -= use; }
    });
    setAlloc(next);
  };

  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setError(null);
    const errs = {};
    if (!v.customer) errs.customer = 'Choose the customer.';
    if (!(num(v.amount) > 0)) errs.amount = 'Enter the amount received.';
    if (!v.date) errs.date = 'Choose the date.';
    if (left < 0) errs.amount = 'More is applied to invoices than the amount received.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const allocations = Object.entries(alloc).filter(([, a]) => num(a) > 0).map(([document, amount]) => ({ document, amount }));
      const p = await api.post('/sales/payments/', { ...v, allocations, deposit_account: v.deposit_account || undefined, project: v.project || undefined });
      notify(`Payment recorded: ${p.reference_code}`);
      onSaved(p);
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };

  return (
    <Dialog title="Record payment" onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[]} />
        <div className="form-grid">
          <Field id="p-customer" label="Customer" required error={errors.customer}>
            <select id="p-customer" value={v.customer} onChange={set('customer')}>
              <option value="">Choose…</option>
              {asList(customers.data).map((c) => <option key={c.id} value={c.id}>{c.display_name}{num(c.outstanding) > 0 ? ` (owes ${plain(c.outstanding)})` : ''}</option>)}
            </select>
          </Field>
          <Field id="p-amount" label="Amount received" required error={errors.amount}>
            <input id="p-amount" type="number" min="0" step="0.01" value={v.amount} onChange={set('amount')} />
          </Field>
          <Field id="p-date" label="Payment date" required error={errors.date}><input id="p-date" type="date" value={v.date} onChange={set('date')} /></Field>
          <Field id="p-mode" label="Paid by" error={errors.mode}>
            <select id="p-mode" value={v.mode} onChange={set('mode')}>{PAYMENT_MODES.map(([val, label]) => <option key={val} value={val}>{label}</option>)}</select>
          </Field>
          <Field id="p-ref" label="Reference (cheque no., UTR ...)" error={errors.reference}><input id="p-ref" value={v.reference} maxLength={100} onChange={set('reference')} /></Field>
          <Field id="p-bank" label="Deposited to" error={errors.deposit_account}>
            <select id="p-bank" value={v.deposit_account} onChange={set('deposit_account')}>
              <option value="">Not recorded</option>
              {asList(banks.data).map((b) => <option key={b.id} value={b.id}>{b.display_name}</option>)}
            </select>
          </Field>
          <ProjectField id="p-project" value={v.project} error={errors.project} onChange={(val) => { setV((x) => ({ ...x, project: val })); setErrors((x) => ({ ...x, project: undefined })); }} />
          <Field id="p-notes" label="Notes" wide error={errors.notes}><input id="p-notes" value={v.notes} maxLength={300} onChange={set('notes')} /></Field>
        </div>

        {v.customer && (
          <fieldset className="subcard">
            <legend>Apply to invoices</legend>
            {loadingOpen && <Loading />}
            {!loadingOpen && open.length === 0 && <p className="muted">This customer has no unpaid invoices. The amount will be kept as an advance.</p>}
            {open.length > 0 && (
              <>
                <div className="table-wrap">
                  <table className="alloc-table">
                    <thead><tr><th>Invoice</th><th>Due</th><th className="amount">Balance</th><th className="amount">Apply</th></tr></thead>
                    <tbody>
                      {open.map((d) => (
                        <tr key={d.id}>
                          <td data-label="Invoice"><Link to={`/sales/documents/${d.id}`}>{d.number}</Link> {d.status_key === 'overdue' && <Badge tone="bad">Overdue</Badge>}</td>
                          <td data-label="Due">{fmtDate(d.due_date)}</td>
                          <td className="amount" data-label="Balance">{plain(d.balance)}</td>
                          <td className="amount" data-label="Apply">
                            <input type="number" min="0" step="0.01" max={d.balance} value={alloc[d.id] || ''} aria-label={`Amount to apply to ${d.number}`}
                              onChange={(e) => setAlloc((a) => ({ ...a, [d.id]: e.target.value }))} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="toolbar">
                  <button type="button" className="btn btn-small" onClick={autoApply} disabled={!(num(v.amount) > 0)}>Apply to oldest first</button>
                  <button type="button" className="btn btn-small" onClick={() => setAlloc({})}>Clear</button>
                  <span className="hint" role="status">
                    Applied {plain(applied / 100)} of {plain(v.amount || 0)}
                    {left > 0 ? ` · ${plain(left / 100)} will be kept as an advance` : left < 0 ? ' · over-applied' : ''}
                  </span>
                </div>
              </>
            )}
          </fieldset>
        )}
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Record payment'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export function PaymentsPage() {
  const { user } = useAuth();
  const notify = useToast();
  const { ask, dialog: askDialog } = useReasonDialog();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const [status, setStatus] = useState('recorded');
  const [project, setProject] = useState('');
  const [dialog, setDialog] = useState(params.get('new') === '1' ? { customer: params.get('customer') || '' } : null);
  const { data, error, loading, reload } = useLoad(() => api.get('/sales/payments/', { q: term || undefined, status: status || undefined, project: project || undefined }), [term, status, project]);
  const canManage = user.permissions.includes('sales.manage');
  useEffect(() => { if (params.get('new')) setParams({}, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const voidPayment = async (p) => {
    const reason = await ask({ title: `Void payment ${p.reference_code}?`, message: 'The invoices it paid go back to unpaid. The payment record is kept.', confirmLabel: 'Void payment' });
    if (reason === null) return;
    try {
      const r = await api.post(`/sales/payments/${p.id}/void/`, { change_reason: reason });
      notify(r.void_requested ? 'Voiding is above an approval limit, so it was sent for approval. The payment stays recorded until then.' : 'Payment voided.');
      reload();
    } catch (e) { notify(e.message, 'error'); }
  };

  return (
    <>
      <PageHeader title="Payments received" description="Money customers have paid, and which invoices it settled."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setDialog({ customer: '' })}>Record payment</button>} />
      <section className="card">
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
          <Field id="pay-q" label="Search" className="grow"><input id="pay-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Payment number, customer or reference" /></Field>
          <Field id="pay-status" label="Show">
            <select id="pay-status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="recorded">Recorded</option><option value="void_pending">Void pending approval{data?.status_counts?.void_pending ? ` (${data.status_counts.void_pending})` : ''}</option><option value="void">Void</option><option value="">All</option></select>
          </Field>
          <ProjectFilter value={project} onChange={setProject} />
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && data?.results.length === 0 && <p className="empty">No payments recorded yet.</p>}
        {data?.results.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Payment no.</th><th>Customer</th><th>Paid by</th><th>Reference</th><th>Project</th><th className="amount">Amount</th><th className="amount">Not applied</th><th><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {data.results.map((p) => (
                  <tr key={p.id} className={p.status === 'void' ? 'row-inactive' : ''}>
                    <td data-label="Date">{fmtDate(p.date)}</td>
                    <td data-label="Payment no."><Link to={`/sales/payments/${p.id}`}><code>{p.reference_code}</code></Link> {p.status === 'void' && <Badge>Void</Badge>}{p.void_requested && <Badge tone="warn">Void pending approval</Badge>}</td>
                    <td data-label="Customer"><Link to={`/sales/customers/${p.customer.id}`}>{p.customer.name}</Link></td>
                    <td data-label="Paid by">{p.mode_label}</td>
                    <td data-label="Reference">{p.reference || '—'}</td>
                    <td data-label="Project">{p.project_ref ? <Link to={`/projects/projects/${p.project_ref.id}`}>{projectLabel(p.project_ref)}</Link> : '—'}</td>
                    <td className="amount" data-label="Amount"><strong>{money(p.amount)}</strong></td>
                    <td className="amount" data-label="Not applied">{num(p.unapplied) > 0 && p.status === 'recorded' ? plain(p.unapplied) : '—'}</td>
                    <td className="row-actions">{canManage && p.status === 'recorded' && !p.void_requested && <button type="button" className="btn btn-small" onClick={() => voidPayment(p)}>Void</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data?.results.length > 0 && <div className="list-foot"><span>{data.count} payment{data.count === 1 ? '' : 's'}</span><span>Total received <strong>{money(data.total_amount)}</strong></span></div>}
      </section>
      {dialog && <PaymentDialog customerId={dialog.customer} documentId={dialog.document} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); reload(); }} />}
      {askDialog}
    </>
  );
}

// One payment received. Receipts are never held back; voiding one above an approval limit waits for approval and the payment
// stays recorded (and counted in the books) until then.
export function PaymentDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const { data: p, error, loading, reload } = useLoad(() => api.get(`/sales/payments/${id}/`), [id]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const canManage = user.permissions.includes('sales.manage');
  const voidIt = async () => {
    const reason = await ask({ title: `Void payment ${p.reference_code}?`, message: 'The invoices it paid go back to unpaid. The payment record is kept. Above an approval limit this is sent for approval first.', confirmLabel: 'Void payment' });
    if (reason === null) return;
    try {
      const r = await api.post(`/sales/payments/${id}/void/`, { change_reason: reason });
      notify(r.void_requested ? 'Sent for approval. The payment stays recorded until then.' : 'Payment voided.');
      reload();
    } catch (e) { notify(e.message, 'error'); }
  };
  return (
    <>
      <Link to="/sales/payments" className="back-link">← Payments received</Link>
      <PageHeader title={p.reference_code} description={`${p.customer.name} · ${fmtDate(p.date)} · ${p.mode_label}`}
        actions={(
          <>
            <Badge tone={p.status === 'void' ? 'neutral' : p.void_requested ? 'warn' : 'good'}>{p.status_label}</Badge>
            <ApprovalPanel kind="payment" sourceId={p.id} waiting={p.void_requested} canSubmit={canManage} onChange={reload} refreshKey={`${p.status}${p.void_requested}`} />
            {p.void_requested && (
              <ApprovalActions base={`/sales/payments/${id}`} canAct={canManage} canDecide={canManage} managed={p.approval_managed} noun="void request" takeBack="Withdraw the void request" onChange={reload} />
            )}
            {canManage && p.status === 'recorded' && !p.void_requested && <button type="button" className="btn" onClick={voidIt}>Void payment</button>}
          </>
        )} />
      {p.void_requested && <Notice>Voiding this payment ({p.void_request_reason}) is above an approval limit. The payment stays recorded, and the invoices it paid stay paid, until it is approved.</Notice>}
      {p.status === 'recorded' && !p.void_requested && p.decision_note && <Notice type="warn">{p.decision_note}</Notice>}
      {p.status === 'void' && <Notice type="warn">Voided{p.void_reason ? `: ${p.void_reason}` : ''}</Notice>}
      <div className="tiles">
        <div className="tile"><div className="tile-label">Amount received</div><div className="tile-value">{money(p.amount)}</div></div>
        <div className="tile"><div className="tile-label">Applied to invoices</div><div className="tile-value">{money(p.applied)}</div></div>
        <div className="tile"><div className="tile-label">Kept as advance</div><div className="tile-value">{num(p.unapplied) > 0 && p.status === 'recorded' ? money(p.unapplied) : '—'}</div></div>
      </div>
      <section className="card">
        <dl className="pur-facts">
          <div><dt>Customer</dt><dd><Link to={`/sales/customers/${p.customer.id}`}>{p.customer.name}</Link></dd></div>
          <div><dt>Paid by</dt><dd>{p.mode_label}</dd></div>
          <div><dt>Reference</dt><dd>{p.reference || '—'}</dd></div>
          <div><dt>Deposited to</dt><dd>{p.deposit_account || '—'}</dd></div>
          <div><dt>Notes</dt><dd>{p.notes || '—'}</dd></div>
        </dl>
        {p.allocations.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Invoice</th><th className="amount">Applied</th></tr></thead>
              <tbody>{p.allocations.map((a) => <tr key={a.document}><td data-label="Invoice"><Link to={`/sales/documents/${a.document}`}>{a.number}</Link></td><td className="amount" data-label="Applied">{plain(a.amount)}</td></tr>)}</tbody>
            </table>
          </div>
        )}
      </section>
      {dialog}
    </>
  );
}
