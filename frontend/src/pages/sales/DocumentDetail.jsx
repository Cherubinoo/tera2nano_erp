import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { money, num, plain } from '../../format';
import { asList, useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, fmtDate, fmtDateTime, useConfirm, useReasonDialog, useToast } from '../../ui';
import { EmailButton, EmailStatusBadge } from '../../components/Email';
import { Downloads } from '../Letters';
import { StatusBadge, projectLabel } from './common';
import { PaymentDialog } from './Payments';
import { ReminderTextDialog } from './Reminders';

function ApplyCreditDialog({ note, onClose, onSaved }) {
  const notify = useToast();
  const open = useLoad(() => api.get(`/sales/customers/${note.customer.id}/open-items/`), [note.customer.id]);
  const [target, setTarget] = useState('');
  const [amount, setAmount] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const owed = asList(open.data?.owed);
  const pick = (id) => {
    setTarget(id);
    const d = owed.find((x) => x.id === id);
    if (d) setAmount(String(Math.min(num(d.balance), num(note.credit_left))));
  };
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    setBusy(true); setError(null);
    try { await api.post('/sales/credits/apply/', { credit_note: note.id, document: target, amount }); notify('Credit applied.'); onSaved(); } catch (err) { setError(err); }
    setBusy(false);
  };
  return (
    <Dialog title={`Apply credit from ${note.number}`} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[{ name: 'amount' }, { name: 'document' }]} />
        <p className="muted">{plain(note.credit_left)} of this credit note is still unused.</p>
        {open.loading && <Loading />}
        {!open.loading && owed.length === 0 && <p className="muted">This customer has no unpaid invoices to apply it to.</p>}
        {owed.length > 0 && (
          <div className="form-grid">
            <Field id="ac-doc" label="Apply to" required>
              <select id="ac-doc" value={target} onChange={(e) => pick(e.target.value)}>
                <option value="">Choose an invoice…</option>
                {owed.map((d) => <option key={d.id} value={d.id}>{d.number} · balance {plain(d.balance)}</option>)}
              </select>
            </Field>
            <Field id="ac-amount" label="Amount" required><input id="ac-amount" type="number" min="0" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
          </div>
        )}
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy || !target || !(num(amount) > 0)}>{busy ? 'Applying…' : 'Apply credit'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export default function DocumentDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { ask, dialog: askDialog } = useReasonDialog();
  const { data: d, error, loading, reload } = useLoad(() => api.get(`/sales/documents/${id}/`), [id]);
  const [paying, setPaying] = useState(false);
  const [applying, setApplying] = useState(false);
  const [viewReminder, setViewReminder] = useState(null);

  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const manage = user.permissions.includes('sales.manage');
  const issue = user.permissions.includes('sales.issue');
  const rec = d.record || {};
  const lines = rec.lines || [];
  const totals = rec.totals;
  const receivable = d.kind === 'invoice' || d.kind === 'debit_note';
  const issued = d.status === 'issued';
  const gst = rec.gst_applied;
  const run = async (fn, ok) => { try { await fn(); if (ok) notify(ok); reload(); } catch (e) { notify(e.fieldErrors?.items || e.message, 'error'); } };

  const doIssue = async () => {
    if (!(await confirm({ title: `Issue this ${d.kind_label.toLowerCase()}?`, body: <p>A number is allocated now and the document is frozen. If it is wrong later you can only void it and make a new one.</p>, confirmLabel: 'Issue' }))) return;
    run(() => api.post(`/sales/documents/${id}/issue/`), 'Issued.');
  };
  const doDelete = async () => {
    if (!(await confirm({ title: 'Delete this draft?', body: <p>The draft is removed. No number has been used.</p>, confirmLabel: 'Delete draft', danger: true }))) return;
    try { await api.del(`/sales/documents/${id}/`); notify('Draft deleted.'); navigate('/sales/documents'); } catch (e) { notify(e.message, 'error'); }
  };
  const doVoid = async () => {
    const reason = await ask({ title: `Void ${d.number}?`, message: 'The document and its number are kept but marked void. The number is never reused.', confirmLabel: 'Void' });
    if (reason === null) return;
    run(() => api.post(`/sales/documents/${id}/void/`, { change_reason: reason }), 'Voided.');
  };
  const clone = async (kind) => {
    try { const n = await api.post(`/sales/documents/${id}/clone/`, kind ? { kind } : {}); notify('Draft created.'); navigate(`/sales/documents/${n.id}/edit`); } catch (e) { notify(e.message, 'error'); }
  };

  return (
    <>
      <Link to={`/sales/documents?kind=${d.kind}`} className="back-link">← {d.kind_label}s</Link>
      <header className="doc-head">
        <div>
          <h1>{d.kind_label} {d.number ? <code>{d.number}</code> : <em>(draft)</em>} <StatusBadge doc={d} /></h1>
          <p className="muted"><Link to={`/sales/customers/${d.customer.id}`}>{d.customer.name}</Link> · {fmtDate(d.issue_date)}
            {d.against && <> · against <Link to={`/sales/documents/${d.against.id}`}>{d.against.number}</Link></>}</p>
        </div>
        <div className="page-actions">
          {d.status === 'draft' && manage && <Link className="btn" to={`/sales/documents/${id}/edit`}>Edit</Link>}
          {d.status === 'draft' && issue && <button type="button" className="btn btn-primary" onClick={doIssue}>Issue</button>}
          {d.status === 'draft' && manage && <button type="button" className="btn" onClick={doDelete}>Delete</button>}
          {d.issued && <Downloads doc={{ id: d.issued.id, reference_number: d.number, has_print_file: d.issued.has_print_file }} onError={(e) => notify(e.message, 'error')} />}
          {issued && d.issued && <EmailButton kind="sales_document" id={d.id} perm="sales.manage" label="Email" title={`Email ${d.kind_label.toLowerCase()} ${d.number}`} />}
          {issued && receivable && manage && num(d.balance) > 0 && <button type="button" className="btn btn-primary" onClick={() => setPaying(true)}>Record payment</button>}
          {issued && d.kind === 'credit_note' && manage && num(d.credit_left) > 0 && <button type="button" className="btn btn-primary" onClick={() => setApplying(true)}>Apply to an invoice</button>}
          {issued && d.kind === 'invoice' && manage && <Link className="btn" to={`/sales/documents/new?kind=credit_note&against=${id}`}>Create credit note</Link>}
          {issued && d.kind === 'invoice' && manage && <Link className="btn" to={`/sales/documents/new?kind=debit_note&against=${id}`}>Create debit note</Link>}
          {issued && d.kind === 'proforma_invoice' && manage && <button type="button" className="btn btn-primary" onClick={() => clone('invoice')}>Convert to invoice</button>}
          {d.status !== 'draft' && ['invoice', 'proforma_invoice'].includes(d.kind) && manage && <button type="button" className="btn" onClick={() => clone()}>Clone</button>}
          {issued && issue && <button type="button" className="btn" onClick={doVoid}>Void</button>}
        </div>
      </header>

      {d.status === 'void' && <Notice type="warn">Voided{d.voided_at ? ` on ${fmtDate(d.voided_at.slice(0, 10))}` : ''}: {d.void_reason}</Notice>}

      <section className="card">
        <dl className="summary">
          <div><dt>Date</dt><dd>{fmtDate(d.issue_date)}</dd></div>
          {receivable && <div><dt>Due date</dt><dd>{fmtDate(d.due_date)}</dd></div>}
          {rec.place_of_supply && <div><dt>Place of supply</dt><dd>{rec.place_of_supply.name} ({rec.place_of_supply.code})</dd></div>}
          {rec.enquiry_ref && <div><dt>Customer reference</dt><dd>{rec.enquiry_ref}</dd></div>}
          {d.project_ref && <div><dt>Project</dt><dd><Link to={`/projects/projects/${d.project_ref.id}`}>{projectLabel(d.project_ref)}</Link></dd></div>}
          {d.reason && <div><dt>Reason</dt><dd>{d.reason}</dd></div>}
          {d.source_quotation && <div><dt>From quotation</dt><dd>{d.source_quotation.number}</dd></div>}
          {d.recurring && <div><dt>Recurring invoice</dt><dd><Link to={`/sales/recurring/${d.recurring.id}`}>{d.recurring.name}</Link> · run of {fmtDate(d.recurring.run_date)}</dd></div>}
        </dl>
        {issued && receivable && (
          <div className="balance-strip">
            <div><span>Total</span><strong>{money(d.grand_total, d.currency)}</strong></div>
            <div><span>Paid or credited</span><strong>{money(d.paid, d.currency)}</strong></div>
            <div className={num(d.balance) > 0 ? 'due' : ''}><span>Balance due</span><strong>{money(d.balance, d.currency)}</strong></div>
          </div>
        )}
        {issued && d.kind === 'credit_note' && (
          <div className="balance-strip">
            <div><span>Credit note total</span><strong>{money(d.grand_total, d.currency)}</strong></div>
            <div><span>Still unused</span><strong>{money(d.credit_left, d.currency)}</strong></div>
          </div>
        )}
      </section>

      <section className="card" aria-label="Items">
        <h2>Items</h2>
        <div className="table-wrap">
          <table className="items-table">
            <thead><tr><th>#</th><th>Description</th><th>HSN/SAC</th><th className="amount">Qty</th><th className="amount">Rate</th><th className="amount">Disc %</th>{gst && <th className="amount">GST %</th>}<th className="amount">Amount</th></tr></thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={i}>
                  <td data-label="#">{i + 1}</td><td data-label="Description">{l.description}</td><td data-label="HSN/SAC">{l.hsn_sac || '—'}</td>
                  <td className="amount" data-label="Qty">{plain(l.quantity)} {l.unit}</td><td className="amount" data-label="Rate">{plain(l.rate)}</td>
                  <td className="amount" data-label="Disc %">{num(l.discount_percent) ? num(l.discount_percent) : '—'}</td>
                  {gst && <td className="amount" data-label="GST %">{num(l.gst_rate)}</td>}
                  <td className="amount" data-label="Amount">{plain(l.taxable)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {totals && (
          <table className="items-table" style={{ marginLeft: 'auto', marginTop: '.75rem' }}>
            <tbody>
              {num(totals.discount) > 0 && <tr><th scope="row">Gross</th><td className="amount">{plain(totals.gross)}</td></tr>}
              {num(totals.discount) > 0 && <tr><th scope="row">Discount</th><td className="amount">-{plain(totals.discount)}</td></tr>}
              {gst && <tr><th scope="row">Taxable amount</th><td className="amount">{plain(totals.taxable)}</td></tr>}
              {gst && totals.tax.map((t) => <tr key={t.label}><th scope="row">{t.label}</th><td className="amount">{plain(t.amount)}</td></tr>)}
              <tr><th scope="row">Total ({d.currency})</th><td className="amount"><strong>{plain(totals.grand_total)}</strong></td></tr>
            </tbody>
          </table>
        )}
        {rec.amount_in_words && <p className="hint">{rec.amount_in_words}</p>}
      </section>

      {receivable && issued && (
        <section className="card" aria-label="Payments and credits">
          <h2>Payments and credits</h2>
          {d.payments.length === 0 && d.credits_received.length === 0 ? <p className="muted">Nothing has been received against this document yet.</p> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Date</th><th>What</th><th>Reference</th><th className="amount">Amount</th></tr></thead>
                <tbody>
                  {d.payments.map((p) => (
                    <tr key={`p${p.id}`} className={p.status === 'void' ? 'row-inactive' : ''}>
                      <td data-label="Date">{fmtDate(p.date)}</td><td data-label="What">Payment · {p.mode} {p.status === 'void' && <Badge>Void</Badge>}</td>
                      <td data-label="Reference"><code>{p.reference_code}</code></td><td className="amount" data-label="Amount">{plain(p.amount)}</td>
                    </tr>
                  ))}
                  {d.credits_received.map((c, i) => (
                    <tr key={`c${i}`}><td data-label="Date">—</td><td data-label="What">Credit note</td><td data-label="Reference"><Link to={`/sales/documents/${c.id}`}>{c.number}</Link></td><td className="amount" data-label="Amount">{plain(c.amount)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {receivable && issued && (
        <section className="card" aria-label="Payment reminders">
          <h2>Payment reminders</h2>
          {d.reminders.length === 0 ? <p className="muted">No reminder has been sent for this invoice yet. Reminders are sent from <Link to="/sales/reminders">Payment reminders</Link>; when email is set up they are emailed to the customer.</p> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Recorded</th><th>Reminder</th><th>Result</th><th>Email</th><th>By</th><th /></tr></thead>
                <tbody>
                  {d.reminders.map((r) => (
                    <tr key={r.id}>
                      <td data-label="Recorded">{fmtDateTime(r.created_at)}</td><td data-label="Reminder">{r.label}</td>
                      <td data-label="Result"><Badge tone={r.status === 'sent' ? 'good' : 'neutral'}>{r.status_label}</Badge></td>
                      <td data-label="Email">{r.email ? <><EmailStatusBadge status={r.email.status} label={r.email.status_label} />{r.email.error && <div className="hint">{r.email.error}</div>}</> : <span className="hint">{r.status === 'sent' ? 'Not emailed' : '—'}</span>}</td>
                      <td data-label="By">{r.by || '—'}</td>
                      <td data-label="Text"><button type="button" className="btn btn-small" onClick={() => setViewReminder(r)}>{r.status === 'sent' ? 'View text' : 'View'}</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {d.kind === 'credit_note' && d.credit_applications.length > 0 && (
        <section className="card" aria-label="Where this credit was used">
          <h2>Where this credit was used</h2>
          <ul>{d.credit_applications.map((c, i) => <li key={i}><Link to={`/sales/documents/${c.id}`}>{c.number}</Link> · {plain(c.amount)}</li>)}</ul>
        </section>
      )}

      {d.notes.length > 0 && (
        <section className="card" aria-label="Credit and debit notes">
          <h2>Credit and debit notes against this invoice</h2>
          <ul>{d.notes.map((n) => <li key={n.id}>{n.kind_label} <Link to={`/sales/documents/${n.id}`}>{n.number || 'Draft'}</Link> · {plain(n.grand_total)} {n.status === 'void' && <Badge>Void</Badge>}</li>)}</ul>
        </section>
      )}

      {paying && <PaymentDialog customerId={d.customer.id} documentId={d.id} onClose={() => setPaying(false)} onSaved={() => { setPaying(false); reload(); }} />}
      {applying && <ApplyCreditDialog note={d} onClose={() => setApplying(false)} onSaved={() => { setApplying(false); reload(); }} />}
      {viewReminder && <ReminderTextDialog reminder={{ ...viewReminder, document: { id: d.id, number: d.number } }} onClose={() => setViewReminder(null)} />}
      {confirmDialog}
      {askDialog}
    </>
  );
}
