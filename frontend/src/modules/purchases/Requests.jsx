import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { money, num, plain, today } from '../../format';
import { Dialog, Field, FormErrors, useReasonDialog, useToast, fmtDate } from '../../ui';
import { ProjectSelect, Status, useVendors, VendorSelect } from './common';
import DocList from './DocList';

const blank = () => ({ description: '', unit: '', quantity: '1', est_rate: '' });

function RequestDialog({ req, onClose, onSaved }) {
  const notify = useToast();
  const [f, setF] = useState({ title: req?.title || '', request_date: req?.request_date || today(), needed_by: req?.needed_by || '', notes: req?.notes || '', project: req?.project || '' });
  const [items, setItems] = useState(req ? req.items.map((i) => ({ ...blank(), ...i, est_rate: num(i.est_rate) ? i.est_rate : '' })) : [blank()]);
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  const setItem = (n, k, v) => setItems((rows) => rows.map((r, i) => (i === n ? { ...r, [k]: v } : r)));
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    setFailure(null);
    const er = {};
    if (!f.title.trim()) er.title = 'Enter a title.';
    if (!f.request_date) er.request_date = 'Choose the date.';
    if (f.needed_by && f.needed_by < f.request_date) er.needed_by = 'Needed-by cannot be before the request date.';
    const used = items.filter((i) => i.description.trim());
    if (!used.length) er.items = 'Add at least one item.';
    else if (used.some((i) => !(Number(i.quantity) > 0))) er.items = 'Every item needs a quantity above 0.';
    setErrors(er);
    if (Object.keys(er).length) return;
    setBusy(true);
    try {
      const body = { ...f, needed_by: f.needed_by || null, project: f.project || null, items: used };
      const saved = req ? await api.patch(`/purchases/requests/${req.id}/`, body) : await api.post('/purchases/requests/', body);
      notify(req ? 'Request saved.' : `Request ${saved.number} created.`);
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title={req ? `Edit ${req.number}` : 'New purchase request'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={[{ name: 'title' }, { name: 'request_date' }, { name: 'needed_by' }, { name: 'items' }]} />
        <div className="form-grid">
          <Field id="pr-title" label="What is needed" required error={errors.title} wide><input id="pr-title" value={f.title} maxLength={200} onChange={set('title')} /></Field>
          <Field id="pr-date" label="Request date" required error={errors.request_date}><input id="pr-date" type="date" value={f.request_date} onChange={set('request_date')} /></Field>
          <Field id="pr-needed" label="Needed by" error={errors.needed_by}><input id="pr-needed" type="date" value={f.needed_by} onChange={set('needed_by')} /></Field>
          <ProjectSelect id="pr-project" value={f.project} onChange={(v) => setF((x) => ({ ...x, project: v }))} />
        </div>
        <fieldset className="subcard"><legend>Items</legend>
          {errors.items && <p className="error-text" role="alert">{errors.items}</p>}
          <div className="table-wrap"><table className="items-table"><thead><tr><th>Description</th><th className="amount">Qty</th><th>Unit</th><th className="amount">Estimated rate</th><th><span className="sr-only">Remove</span></th></tr></thead>
            <tbody>{items.map((it, n) => (
              <tr key={n}>
                <td data-label="Description"><input aria-label={`Item ${n + 1} description`} value={it.description} maxLength={500} onChange={(e) => setItem(n, 'description', e.target.value)} /></td>
                <td data-label="Qty" className="amount"><input aria-label={`Item ${n + 1} quantity`} inputMode="decimal" value={it.quantity} onChange={(e) => setItem(n, 'quantity', e.target.value)} /></td>
                <td data-label="Unit"><input aria-label={`Item ${n + 1} unit`} value={it.unit} size={5} onChange={(e) => setItem(n, 'unit', e.target.value)} /></td>
                <td data-label="Estimated rate" className="amount"><input aria-label={`Item ${n + 1} estimated rate`} inputMode="decimal" value={it.est_rate} onChange={(e) => setItem(n, 'est_rate', e.target.value)} /></td>
                <td data-label="">{items.length > 1 && <button type="button" className="btn btn-small" onClick={() => setItems((r) => r.filter((_, i) => i !== n))}>Remove</button>}</td>
              </tr>))}</tbody></table></div>
          <button type="button" className="btn btn-small" onClick={() => setItems((r) => [...r, blank()])}>Add item</button>
        </fieldset>
        <Field id="pr-notes" label="Notes" wide><textarea id="pr-notes" rows={2} value={f.notes} onChange={set('notes')} /></Field>
        <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save request'}</button></div>
      </form>
    </Dialog>
  );
}

function ConvertDialog({ req, onClose, onSaved }) {
  const { vendors } = useVendors();
  const [vendor, setVendor] = useState('');
  const [error, setError] = useState('');
  const go = async (e) => {
    e.preventDefault(); e.stopPropagation();
    if (!vendor) { setError('Choose the vendor to order from.'); return; }
    try { onSaved(await api.post(`/purchases/requests/${req.id}/convert/`, { vendor })); } catch (err) { setError(err.message); }
  };
  return (
    <Dialog title={`Turn ${req.number} into a purchase order`} onClose={onClose}>
      <form onSubmit={go} noValidate>
        <VendorSelect id="cv-vendor" value={vendor} onChange={(v) => { setVendor(v); setError(''); }} vendors={vendors} error={error} label="Order from" />
        <p className="hint">A draft order is created with the request's items. Check the rates and GST on it before issuing.</p>
        <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary">Create order</button></div>
      </form>
    </Dialog>
  );
}

export default function Requests() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const [params, setParams] = useSearchParams();
  const [editing, setEditing] = useState(params.get('new') === '1' ? {} : null);
  const [converting, setConverting] = useState(null);
  const [tick, setTick] = useState(0);
  const canManage = user.permissions.includes('purchases.manage');
  const act = async (r, path, msg, body) => { try { await api.post(`/purchases/requests/${r.id}/${path}/`, body); notify(msg); setTick((t) => t + 1); } catch (e) { notify(e.message, 'error'); } };
  const reject = async (r) => { const reason = await ask({ title: 'Reject request', confirmLabel: 'Reject' }); if (reason !== null) act(r, 'reject', 'Request rejected.', { change_reason: reason }); };
  const done = () => { setEditing(null); setConverting(null); setParams({}, { replace: true }); setTick((t) => t + 1); };
  return (
    <>
      <DocList title="Purchase requests" description="Internal requests to buy something. Approve them, collect quotations and turn them into orders." url="/purchases/requests/" reloadKey={tick}
        statuses={[['', 'All'], ['draft', 'Draft'], ['submitted', 'Submitted'], ['approved', 'Approved'], ['rejected', 'Rejected'], ['converted', 'Converted']]}
        searchHint="Number, title or requester" emptyText="No purchase requests yet."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setEditing({})}>New request</button>}
        columns={[{ h: 'Date', v: (r) => fmtDate(r.request_date) }, { h: 'Number', v: (r) => <strong>{r.number}</strong> }, { h: 'Title', v: (r) => r.title }, { h: 'Requested by', v: (r) => r.requested_by_name || '—' },
          { h: 'Needed by', v: (r) => fmtDate(r.needed_by) }, { h: 'Status', v: (r) => <Status k={r.status} label={r.status_label} /> }, { h: 'Estimate', amount: true, v: (r) => plain(r.estimated_total) },
          { h: 'Actions', v: (r) => canManage && (<span className="pur-row-actions">
            {['draft', 'rejected'].includes(r.status) && <><button type="button" className="btn btn-small" onClick={() => setEditing(r)}>Edit</button><button type="button" className="btn btn-small" onClick={() => act(r, 'submit', 'Request submitted.')}>Submit</button></>}
            {['draft', 'rejected'].includes(r.status) && r.quotations === 0 && !r.order && <button type="button" className="btn btn-small" onClick={async () => {
              if (!window.confirm(`Delete purchase request ${r.number}?`)) return;
              try { await api.del(`/purchases/requests/${r.id}/`); notify('Request deleted.'); setTick((t) => t + 1); }
              catch (e) { notify(e.message, 'error'); }
            }}>Delete</button>}
            {r.status === 'submitted' && <><button type="button" className="btn btn-small" onClick={() => act(r, 'approve', 'Request approved.')}>Approve</button><button type="button" className="btn btn-small" onClick={() => reject(r)}>Reject</button></>}
            {['approved', 'draft', 'submitted'].includes(r.status) && <button type="button" className="btn btn-small" onClick={() => navigate(`/purchases/quotations?request=${r.id}`)}>Quotations ({r.quotations})</button>}
            {r.status === 'approved' && <button type="button" className="btn btn-small" onClick={() => setConverting(r)}>Create order</button>}
            {r.order && <button type="button" className="btn btn-small" onClick={() => navigate(`/purchases/orders/${r.order}`)}>View order</button>}</span>) }]} />
      {editing && <RequestDialog req={editing.id ? editing : null} onClose={() => { setEditing(null); setParams({}, { replace: true }); }} onSaved={done} />}
      {converting && <ConvertDialog req={converting} onClose={() => setConverting(null)} onSaved={(r) => { notify('Draft purchase order created.'); navigate(`/purchases/orders/${r.order.id}`); }} />}
      {dialog}
    </>
  );
}
