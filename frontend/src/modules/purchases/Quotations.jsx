import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { money, num, plain, today } from '../../format';
import { asList, useLoad } from '../../hooks';
import { Dialog, Field, FormErrors, Loading, useReasonDialog, useToast, fmtDate } from '../../ui';
import { blankItem, itemsFromRecord, itemsPayload, ItemsEditor, Status, Totals, useVendors, usePurchaseOptions, useWorking, validateItems, VendorSelect } from './common';
import DocList from './DocList';

function QuotationDialog({ quotation, requestId, onClose, onSaved }) {
  const notify = useToast();
  const opts = usePurchaseOptions();
  const { vendors } = useVendors();
  const requests = useLoad(() => api.get('/purchases/requests/'), []);
  const [f, setF] = useState({ vendor: quotation?.vendor.id || '', request: quotation?.request?.id || requestId || '', quote_date: quotation?.quote_date || today(), valid_until: quotation?.valid_until || '',
    vendor_ref: quotation?.vendor_ref || '', delivery_days: quotation?.delivery_days ?? '', payment_terms: quotation?.payment_terms || '', notes: quotation?.notes || '' });
  const [items, setItems] = useState(quotation ? itemsFromRecord(quotation.record.lines) : [blankItem()]);
  const [errors, setErrors] = useState({});
  const [itemErrors, setItemErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const working = useWorking({ vendor: f.vendor, items });
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    setFailure(null);
    const er = {}, ie = validateItems(items);
    if (!f.vendor) er.vendor = 'Choose the vendor.';
    if (!f.quote_date) er.quote_date = 'Choose the quotation date.';
    if (f.valid_until && f.valid_until < f.quote_date) er.valid_until = 'Validity cannot end before the quotation date.';
    if (ie._all) er.items = ie._all;
    setErrors(er); setItemErrors(ie._all ? {} : ie);
    if (Object.keys(er).length || (!ie._all && Object.keys(ie).length)) return;
    setBusy(true);
    try {
      const body = { ...f, request: f.request || null, valid_until: f.valid_until || null, delivery_days: f.delivery_days === '' ? null : f.delivery_days, items: itemsPayload(items) };
      const saved = quotation ? await api.patch(`/purchases/quotations/${quotation.id}/`, body) : await api.post('/purchases/quotations/', body);
      notify(quotation ? 'Quotation saved.' : `Quotation ${saved.number} recorded.`);
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  if (opts.loading) return null;
  return (
    <Dialog title={quotation ? `Edit ${quotation.number}` : 'Record a vendor quotation'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={['vendor', 'quote_date', 'valid_until', 'items', 'delivery_days'].map((name) => ({ name }))} />
        <div className="form-grid">
          <VendorSelect id="q-vendor" value={f.vendor} onChange={(v) => setF((x) => ({ ...x, vendor: v }))} vendors={vendors} error={errors.vendor} />
          <Field id="q-request" label="Purchase request (to compare)" error={errors.request}>
            <select id="q-request" value={f.request} onChange={set('request')}><option value="">None</option>
              {asList(requests.data?.results).map((r) => <option key={r.id} value={r.id}>{r.number} · {r.title}</option>)}</select></Field>
          <Field id="q-date" label="Quotation date" required error={errors.quote_date}><input id="q-date" type="date" value={f.quote_date} onChange={set('quote_date')} /></Field>
          <Field id="q-valid" label="Valid until" error={errors.valid_until}><input id="q-valid" type="date" value={f.valid_until} onChange={set('valid_until')} /></Field>
          <Field id="q-ref" label="Vendor's quotation number"><input id="q-ref" value={f.vendor_ref} maxLength={100} onChange={set('vendor_ref')} /></Field>
          <Field id="q-days" label="Delivery time (days)" error={errors.delivery_days}><input id="q-days" type="number" min="0" value={f.delivery_days} onChange={set('delivery_days')} /></Field>
          <Field id="q-terms" label="Payment terms"><input id="q-terms" value={f.payment_terms} maxLength={200} onChange={set('payment_terms')} /></Field>
        </div>
        <fieldset className="subcard"><legend>Items quoted</legend>
          {errors.items && <p className="error-text" role="alert">{errors.items}</p>}
          <ItemsEditor items={items} setItems={setItems} rates={opts.data.gst_rates} errors={itemErrors} idPrefix="q" />
          <Totals working={working} cur={opts.data.currency} />
        </fieldset>
        <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save quotation'}</button></div>
      </form>
    </Dialog>
  );
}

function Compare({ requestId, tick }) {
  const { data, error, loading } = useLoad(() => api.get('/purchases/quotations/compare/', { request: requestId }), [requestId, tick]);
  if (loading) return <Loading />;
  if (error) return null;
  const qs = data.quotations;
  return (
    <section className="card" aria-label="Quotation comparison">
      <h2>Compare quotations for {data.request.number}: {data.request.title}</h2>
      {qs.length < 2 && <p className="muted">Record at least two quotations to compare them.</p>}
      {qs.length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Vendor</th><th className="amount">Taxable</th><th className="amount">GST</th><th className="amount">Total</th><th className="amount">Above lowest</th><th>Delivery</th><th>Valid until</th><th>Status</th></tr></thead>
          <tbody>{qs.map((q) => (
            <tr key={q.id} className={q.is_lowest ? 'pur-lowest' : ''}>
              <td data-label="Vendor"><strong>{q.vendor.name}</strong>{q.is_lowest && <> <span className="badge badge-good">Lowest</span></>}</td>
              <td className="amount" data-label="Taxable">{plain(q.taxable_total)}</td><td className="amount" data-label="GST">{plain(q.tax_total)}</td>
              <td className="amount" data-label="Total"><strong>{plain(q.grand_total)}</strong></td><td className="amount" data-label="Above lowest">{num(q.difference) ? `+${plain(q.difference)}` : '—'}</td>
              <td data-label="Delivery">{q.delivery_days != null ? `${q.delivery_days} days` : '—'}</td><td data-label="Valid until">{fmtDate(q.valid_until)}</td>
              <td data-label="Status"><Status k={q.status_key} label={q.status_label} /></td>
            </tr>))}</tbody>
        </table></div>
      )}
    </section>
  );
}

export default function Quotations() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const [params, setParams] = useSearchParams();
  const requestId = params.get('request') || '';
  const [editing, setEditing] = useState(null);
  const [tick, setTick] = useState(0);
  const canManage = user.permissions.includes('purchases.manage');
  const act = async (q, path, msg, body, go) => { try { const r = await api.post(`/purchases/quotations/${q.id}/${path}/`, body); notify(msg); if (go) navigate(go(r)); else setTick((t) => t + 1); } catch (e) { notify(e.message, 'error'); } };
  const reject = async (q) => { const reason = await ask({ title: 'Reject quotation', confirmLabel: 'Reject' }); if (reason !== null) act(q, 'reject', 'Quotation rejected.', { change_reason: reason }); };
  return (
    <>
      {requestId && <><Link to="/purchases/requests" className="back-link">← Purchase requests</Link><Compare requestId={requestId} tick={tick} /></>}
      <DocList title="Vendor quotations" description="Prices vendors have quoted. Compare them, accept one and turn it into an order." url="/purchases/quotations/" reloadKey={tick}
        extraParams={requestId ? { request: requestId } : undefined} statuses={[['', 'All'], ['received', 'Received'], ['accepted', 'Accepted'], ['rejected', 'Rejected'], ['expired', 'Expired']]}
        searchHint="Number, vendor or the vendor's reference" emptyText="No quotations recorded yet."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setEditing({})}>Record quotation</button>}
        columns={[{ h: 'Date', v: (q) => fmtDate(q.quote_date) }, { h: 'Number', v: (q) => <strong>{q.number}</strong> }, { h: 'Vendor', v: (q) => q.vendor.name },
          { h: 'Request', v: (q) => q.request?.number || '—' }, { h: 'Valid until', v: (q) => fmtDate(q.valid_until) }, { h: 'Status', v: (q) => <Status k={q.status_key} label={q.status_label} /> },
          { h: 'Total', amount: true, v: (q) => plain(q.grand_total) },
          { h: 'Actions', v: (q) => canManage && (<span className="pur-row-actions">
            {q.status === 'received' && <><button type="button" className="btn btn-small" onClick={() => setEditing(q)}>Edit</button>
              {q.status_key === 'received' && <button type="button" className="btn btn-small" onClick={() => act(q, 'accept', 'Quotation accepted.')}>Accept</button>}
              <button type="button" className="btn btn-small" onClick={() => reject(q)}>Reject</button></>}
            {['received', 'accepted'].includes(q.status) && q.status_key !== 'expired' && !q.order && <button type="button" className="btn btn-small" onClick={() => act(q, 'convert', 'Draft purchase order created.', {}, (r) => `/purchases/orders/${r.order.id}`)}>Create order</button>}
            {q.order && <button type="button" className="btn btn-small" onClick={() => navigate(`/purchases/orders/${q.order}`)}>View order</button>}</span>) }]} />
      {editing && <QuotationDialog quotation={editing.id ? editing : null} requestId={requestId} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); setTick((t) => t + 1); }} />}
      {dialog}
    </>
  );
}
