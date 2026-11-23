import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, downloadFile } from '../../api';
import { useAuth } from '../../context';
import { num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Dialog, ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, useReasonDialog, useToast, fmtDate } from '../../ui';
import { blankItem, itemsFromRecord, itemsPayload, ItemsEditor, LinesTable, ProjectSelect, Status, Totals, useVendors, usePurchaseOptions, useWorking, validateItems, VendorSelect } from './common';
import { ApprovalPanel } from '../approvals/ApprovalBadge';
import DocList from './DocList';

const STATUSES = [['', 'All'], ['draft', 'Draft'], ['pending_approval', 'Pending approval'], ['approved', 'Approved'], ['open', 'Open'], ['received', 'Received'], ['closed', 'Closed'], ['cancelled', 'Cancelled']];

export function Orders() {
  const { user } = useAuth();
  return (
    <DocList title="Purchase orders" description="Orders sent to vendors, and what has arrived." url="/purchases/orders/" statuses={STATUSES}
      searchHint="Order number, vendor or reference" emptyText="No purchase orders yet." rowTo={(r) => `/purchases/orders/${r.id}`} totals={[['Total', 'total_amount']]}
      actions={user.permissions.includes('purchases.manage') && <Link className="btn btn-primary" to="/purchases/orders/new">New purchase order</Link>}
      csv={{ file: 'purchase-orders.csv', head: ['Date', 'Number', 'Vendor', 'Status', 'Delivery by', 'Total'], row: (r) => [r.order_date, r.number, r.vendor.name, r.status_label, r.expected_delivery || '', r.grand_total] }}
      columns={[{ h: 'Date', v: (r) => fmtDate(r.order_date) }, { h: 'Number', v: (r) => <strong>{r.number || 'Draft'}</strong> }, { h: 'Vendor', v: (r) => r.vendor.name },
        { h: 'Delivery by', v: (r) => fmtDate(r.expected_delivery) }, { h: 'Status', v: (r) => <Status k={r.status} label={r.status_label} /> }, { h: 'Total', amount: true, v: (r) => plain(r.grand_total) }]} />
  );
}

export function OrderForm() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const notify = useToast();
  const opts = usePurchaseOptions();
  const { vendors } = useVendors();
  const existing = useLoad(() => (id ? api.get(`/purchases/orders/${id}/`) : Promise.resolve(null)), [id]);
  const [f, setF] = useState({ vendor: params.get('vendor') || '', order_date: today(), expected_delivery: '', reference: '', delivery_address: '', project: '', terms: '', notes: '' });
  const [items, setItems] = useState([blankItem()]);
  const [errors, setErrors] = useState({});
  const [itemErrors, setItemErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [filled, setFilled] = useState(!id);
  useEffect(() => {
    const o = existing.data;
    if (!o || filled) return;
    setFilled(true);
    setF({ vendor: o.vendor.id, order_date: o.order_date, expected_delivery: o.expected_delivery || '', reference: o.reference || '', delivery_address: o.delivery_address || '',
      project: o.project || '', terms: o.form.terms || '', notes: o.form.notes || '' });
    setItems(itemsFromRecord(o.record.lines));
  }, [existing.data, filled]);
  const working = useWorking({ vendor: f.vendor, items });
  if (existing.loading || opts.loading) return <Loading />;
  if (existing.error) return <ErrorBox error={existing.error} />;
  if (existing.data && !['draft'].includes(existing.data.status)) return <Notice type="warn">Only a draft can be edited. <Link to={`/purchases/orders/${id}`}>Back to the order</Link></Notice>;
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));

  const save = async (intent) => {
    setFailure(null);
    const e = {}, ie = validateItems(items);
    if (!f.vendor) e.vendor = 'Choose the vendor.';
    if (!f.order_date) e.order_date = 'Choose the order date.';
    if (f.expected_delivery && f.expected_delivery < f.order_date) e.expected_delivery = 'Delivery cannot be before the order date.';
    if (ie._all) e.items = ie._all;
    setErrors(e); setItemErrors(ie._all ? {} : ie);
    if (Object.keys(e).length || (!ie._all && Object.keys(ie).length)) return;
    setBusy(true);
    try {
      const body = { vendor: f.vendor, order_date: f.order_date, expected_delivery: f.expected_delivery || null, reference: f.reference, delivery_address: f.delivery_address,
        project: f.project || null, form: { items: itemsPayload(items), terms: f.terms, notes: f.notes } };
      let saved = id ? await api.patch(`/purchases/orders/${id}/`, body) : await api.post('/purchases/orders/', body);
      if (intent === 'issue') {
        try { saved = await api.post(`/purchases/orders/${saved.id}/issue/`); notify(`Purchase order issued: ${saved.number}`); } catch (err) { notify(`Saved as a draft, but not issued: ${err.message}`, 'error'); }
      } else notify('Draft saved.');
      navigate(`/purchases/orders/${saved.id}`);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <>
      <Link to={id ? `/purchases/orders/${id}` : '/purchases/orders'} className="back-link">← Purchase orders</Link>
      <PageHeader title={`${id ? 'Edit' : 'New'} purchase order`} description="GST is split into CGST and SGST or IGST by comparing the vendor's state with the company's. The number is allocated when you issue it." />
      <form className="card" noValidate onSubmit={(e) => e.preventDefault()}>
        <FormErrors error={failure} fields={[{ name: 'vendor' }, { name: 'order_date' }, { name: 'expected_delivery' }, { name: 'items' }]} />
        <div className="form-grid">
          <VendorSelect value={f.vendor} onChange={(v) => setF((x) => ({ ...x, vendor: v }))} vendors={vendors} error={errors.vendor} />
          <Field id="po-date" label="Order date" required error={errors.order_date}><input id="po-date" type="date" value={f.order_date} onChange={set('order_date')} /></Field>
          <Field id="po-delivery" label="Delivery by" error={errors.expected_delivery}><input id="po-delivery" type="date" value={f.expected_delivery} onChange={set('expected_delivery')} /></Field>
          <Field id="po-ref" label="Reference (quotation no.)" error={errors.reference}><input id="po-ref" value={f.reference} maxLength={100} onChange={set('reference')} /></Field>
          <ProjectSelect id="po-project" value={f.project} onChange={(v) => setF((x) => ({ ...x, project: v }))} />
          <Field id="po-address" label="Deliver to" wide error={errors.delivery_address}><input id="po-address" value={f.delivery_address} maxLength={500} onChange={set('delivery_address')} /></Field>
        </div>
        <fieldset className="subcard">
          <legend>Items</legend>
          {errors.items && <p className="error-text" role="alert">{errors.items}</p>}
          <ItemsEditor items={items} setItems={setItems} rates={opts.data.gst_rates} errors={itemErrors} idPrefix="po" />
          <Totals working={working} cur={opts.data.currency} />
        </fieldset>
        <div className="form-grid">
          <Field id="po-terms" label="Terms and conditions" wide><textarea id="po-terms" rows={3} value={f.terms} onChange={set('terms')} /></Field>
          <Field id="po-notes" label="Notes (printed under the totals)" wide><textarea id="po-notes" rows={2} value={f.notes} onChange={set('notes')} /></Field>
        </div>
        <div className="actions">
          <button type="button" className="btn" disabled={busy} onClick={() => save('draft')}>{busy ? 'Saving…' : 'Save as draft'}</button>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => save('issue')}>Save and issue</button>
          <Link className="btn" to={id ? `/purchases/orders/${id}` : '/purchases/orders'}>Cancel</Link>
        </div>
      </form>
    </>
  );
}

function ReceiveDialog({ order, onClose, onSaved }) {
  const notify = useToast();
  const lines = order.record.lines;
  const [date, setDate] = useState(today());
  const [qty, setQty] = useState({});
  const [notes, setNotes] = useState('');
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const left = (l) => Math.max(num(l.quantity) - num(l.received), 0);
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    const rows = lines.map((l, i) => ({ line: i, quantity: qty[i] })).filter((r) => num(r.quantity) > 0);
    if (!rows.length) { setFailure(new Error('Enter the quantity received for at least one item.')); return; }
    setBusy(true); setFailure(null);
    try { await api.post(`/purchases/orders/${order.id}/receive/`, { date, lines: rows, notes }); notify('Goods received.'); onSaved(); } catch (err) { setFailure(err); }
    setBusy(false);
  };
  return (
    <Dialog title="Receive goods" onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={[]} />
        <div className="form-grid"><Field id="rc-date" label="Received on" required><input id="rc-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
          <Field id="rc-notes" label="Notes"><input id="rc-notes" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Delivery challan no." /></Field></div>
        <div className="table-wrap"><table>
          <thead><tr><th>Item</th><th className="amount">Ordered</th><th className="amount">Received so far</th><th className="amount">Receiving now</th></tr></thead>
          <tbody>{lines.map((l, i) => (
            <tr key={i}><td data-label="Item">{l.description}</td><td className="amount" data-label="Ordered">{num(l.quantity)}</td><td className="amount" data-label="Received so far">{num(l.received)}</td>
              <td className="amount" data-label="Receiving now">{left(l) > 0 ? <input aria-label={`Quantity received for item ${i + 1}`} inputMode="decimal" value={qty[i] ?? ''} placeholder={String(left(l))}
                onChange={(e) => setQty((q) => ({ ...q, [i]: e.target.value }))} /> : 'Complete'}</td></tr>))}</tbody>
        </table></div>
        <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Record receipt'}</button></div>
      </form>
    </Dialog>
  );
}

export function OrderDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const [receiving, setReceiving] = useState(false);
  const { data: o, error, loading, reload } = useLoad(() => api.get(`/purchases/orders/${id}/`), [id]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const canManage = user.permissions.includes('purchases.manage');
  const act = async (path, msg, body, to) => {
    try { const r = await api.post(`/purchases/orders/${id}/${path}/`, body); notify(msg); if (to) navigate(to(r)); else reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const withReason = async (path, title, msg, required = true) => {
    const reason = await ask({ title, required, confirmLabel: title });
    if (reason === null) return;
    act(path, msg, { change_reason: reason });
  };
  const s = o.status;
  const cur = o.record.currency;
  return (
    <>
      <Link to="/purchases/orders" className="back-link">← Purchase orders</Link>
      <PageHeader title={o.number || 'Draft purchase order'} description={`${o.vendor.name} · ${fmtDate(o.order_date)}`}
        actions={<>
          <Status k={s} label={o.status_label} />
          {s !== 'cancelled' && <ApprovalPanel kind="purchase_order" sourceId={o.id} waiting={s === 'pending_approval'} canSubmit={canManage} onChange={reload} refreshKey={s} />}
          {o.issued && <><button type="button" className="btn" onClick={() => downloadFile(`/purchases/orders/${id}/pdf/?download=true`, 'purchase-order.pdf').catch((e) => notify(e.message, 'error'))}>Download PDF</button>
            {o.issued.has_print_file && <button type="button" className="btn" onClick={() => downloadFile(`/purchases/orders/${id}/pdf/?download=true&variant=print`, 'purchase-order-print.pdf').catch((e) => notify(e.message, 'error'))}>Print copy</button>}</>}
          {canManage && s === 'draft' && <><Link className="btn" to={`/purchases/orders/${id}/edit`}>Edit</Link>
            <button type="button" className="btn" onClick={() => act('submit', 'Sent for approval.')}>Send for approval</button>
            <button type="button" className="btn btn-primary" onClick={() => act('issue', 'Purchase order issued.')}>Issue</button></>}
          {canManage && s === 'pending_approval' && !o.approval_managed && <><button type="button" className="btn btn-primary" onClick={() => act('approve', 'Approved.')}>Approve</button>
            <button type="button" className="btn" onClick={() => withReason('reject', 'Reject order', 'Order sent back to draft.')}>Reject</button></>}
          {canManage && s === 'approved' && <button type="button" className="btn btn-primary" onClick={() => act('issue', 'Purchase order issued.')}>Issue</button>}
          {canManage && ['issued', 'partially_received'].includes(s) && <button type="button" className="btn btn-primary" onClick={() => setReceiving(true)}>Receive goods</button>}
          {canManage && ['issued', 'partially_received', 'received', 'closed'].includes(s) && <button type="button" className="btn" onClick={() => act('bill', 'Draft bill created.', {}, (b) => `/purchases/bills/${b.id}`)}>Convert to bill</button>}
          {canManage && ['issued', 'partially_received', 'received'].includes(s) && <button type="button" className="btn" onClick={() => withReason('close', 'Close order', 'Order closed.', s !== 'received')}>Close</button>}
          {canManage && ['draft', 'pending_approval', 'approved', 'issued'].includes(s) && <button type="button" className="btn" onClick={() => withReason('cancel', 'Cancel order', 'Order cancelled.')}>Cancel order</button>}
          {canManage && s === 'draft' && <button type="button" className="btn" onClick={async () => { if (window.confirm('Delete this draft?')) { try { await api.del(`/purchases/orders/${id}/`); navigate('/purchases/orders'); } catch (e) { notify(e.message, 'error'); } } }}>Delete</button>}
        </>} />
      {s === 'pending_approval' && o.approval_managed && <Notice>Waiting for approval. The approvers decide it in the Approvals module; follow it with the approval badge above.</Notice>}
      {o.decision_note && <Notice type="warn">{o.decision_note}</Notice>}
      {o.cancel_reason && <Notice type="warn">Cancelled: {o.cancel_reason}</Notice>}
      <section className="card">
        <dl className="pur-facts">
          <div><dt>Vendor</dt><dd><Link to={`/purchases/vendors/${o.vendor.id}`}>{o.vendor.name}</Link></dd></div>
          <div><dt>Delivery by</dt><dd>{fmtDate(o.expected_delivery)}</dd></div>
          <div><dt>Reference</dt><dd>{o.reference || '—'}</dd></div>
          <div><dt>Place of supply</dt><dd>{o.record.place_of_supply.name || '—'} ({o.record.supply_type === 'intra_state' ? 'CGST + SGST' : o.record.supply_type === 'inter_state' ? 'IGST' : 'no GST'})</dd></div>
          {o.request && <div><dt>Purchase request</dt><dd>{o.request.number}</dd></div>}
          {o.quotation && <div><dt>Quotation</dt><dd>{o.quotation.number}</dd></div>}
          <div><dt>Deliver to</dt><dd>{o.delivery_address || '—'}</dd></div>
        </dl>
        <LinesTable record={o.record} extra={{ label: 'Received / billed', value: (l) => `${num(l.received)} / ${num(l.billed)}` }} />
        <Totals working={{ record: o.record, tds: null, error: '', pending: false }} cur={cur} />
        {o.form.terms && <><h3>Terms</h3><p className="pre">{o.form.terms}</p></>}
      </section>
      {o.receipts.length > 0 && (
        <section className="card"><h2>Goods received</h2>
          <ul>{o.receipts.map((r) => <li key={r.id}>{fmtDate(r.date)}: {r.lines.map((l) => `item ${l.line + 1} × ${num(l.quantity)}`).join(', ')}{r.notes ? ` (${r.notes})` : ''}</li>)}</ul></section>
      )}
      {o.bills.length > 0 && (
        <section className="card"><h2>Bills against this order</h2>
          <ul>{o.bills.map((b) => <li key={b.id}><Link to={`/purchases/bills/${b.id}`}>{b.number || 'Draft'}</Link> · {b.vendor_bill_no} · {plain(b.grand_total)}</li>)}</ul></section>
      )}
      {receiving && <ReceiveDialog order={o} onClose={() => setReceiving(false)} onSaved={() => { setReceiving(false); reload(); }} />}
      {dialog}
    </>
  );
}
