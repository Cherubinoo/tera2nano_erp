import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { Donut } from '../../components/charts';
import { money } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, fmtDate, useConfirm, useToast } from '../../ui';
import { PersonSelect, Tile, usePeople } from './common';

const STATUSES = [['in_stock', 'In stock'], ['in_use', 'In use'], ['in_repair', 'In repair'], ['retired', 'Retired'], ['lost', 'Lost']];
const STATUS_COLOUR = { in_stock: 'var(--chart-1)', in_use: 'var(--chart-2)', in_repair: 'var(--chart-3)', retired: 'var(--chart-grey)', lost: 'var(--chart-4)' };
const STATUS_TONE = { in_stock: 'info', in_use: 'good', in_repair: 'warn', retired: 'neutral', lost: 'bad' };
const WARRANTY = { expired: ['bad', 'Expired'], expiring: ['warn', 'Expiring soon'], ok: ['good', 'In warranty'] };

function AssetDialog({ asset, people, categories, onClose, onSaved }) {
  const notify = useToast();
  const [v, setV] = useState({
    name: asset?.name || '', category: asset?.category || '', serial_number: asset?.serial_number || '', assigned_to: asset?.assigned_to?.id || '',
    location: asset?.location || '', purchase_date: asset?.purchase_date || '', purchase_value: asset?.purchase_value || '',
    status: asset?.status || 'in_stock', warranty_expiry: asset?.warranty_expiry || '', notes: asset?.notes || '',
  });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setFailure(null);
    const errs = {};
    if (!v.name.trim()) errs.name = 'Enter the asset name.';
    if (v.purchase_value !== '' && !(Number(v.purchase_value) >= 0)) errs.purchase_value = 'Enter an amount of zero or more.';
    if (v.purchase_date && v.warranty_expiry && v.warranty_expiry < v.purchase_date) errs.warranty_expiry = 'The warranty cannot expire before the purchase date.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const body = { ...v, name: v.name.trim(), assigned_to: v.assigned_to || null };
      const saved = asset ? await api.patch(`/workplace/assets/${asset.id}/`, body) : await api.post('/workplace/assets/', body);
      notify(asset ? 'Asset saved.' : `Asset added: ${saved.tag}`);
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title={asset ? `Edit ${asset.tag}` : 'Add an asset'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={Object.keys(v).map((name) => ({ name }))} />
        <div className="form-grid">
          <Field id="as-name" label="Name" required wide error={errors.name}><input id="as-name" value={v.name} maxLength={200} onChange={set('name')} /></Field>
          <Field id="as-cat" label="Category" error={errors.category}>
            <input id="as-cat" list="as-cats" value={v.category} maxLength={80} onChange={set('category')} />
            <datalist id="as-cats">{categories.map((c) => <option key={c} value={c} />)}</datalist>
          </Field>
          <Field id="as-serial" label="Serial number" error={errors.serial_number}><input id="as-serial" value={v.serial_number} onChange={set('serial_number')} /></Field>
          <Field id="as-status" label="Status" error={errors.status}><select id="as-status" value={v.status} onChange={set('status')}>{STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
          <PersonSelect id="as-user" label="Assigned to" value={v.assigned_to} onChange={(x) => setV((s) => ({ ...s, assigned_to: x }))} people={people} none="Not assigned" error={errors.assigned_to} />
          <Field id="as-loc" label="Location" error={errors.location}><input id="as-loc" value={v.location} onChange={set('location')} /></Field>
          <Field id="as-pd" label="Purchase date" error={errors.purchase_date}><input id="as-pd" type="date" value={v.purchase_date} onChange={set('purchase_date')} /></Field>
          <Field id="as-pv" label="Purchase value (INR)" error={errors.purchase_value}><input id="as-pv" type="number" min="0" step="0.01" value={v.purchase_value} onChange={set('purchase_value')} /></Field>
          <Field id="as-wy" label="Warranty expiry" error={errors.warranty_expiry}><input id="as-wy" type="date" value={v.warranty_expiry} onChange={set('warranty_expiry')} /></Field>
          <Field id="as-notes" label="Notes" wide error={errors.notes}><textarea id="as-notes" rows={2} value={v.notes} onChange={set('notes')} /></Field>
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Close</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : asset ? 'Save asset' : 'Add asset'}</button>
        </div>
      </form>
      {asset && <Attachments entityType="asset" entityId={asset.id} canManage title="Invoices and warranty papers" />}
    </Dialog>
  );
}

export default function AssetsPage() {
  const people = usePeople();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState('');
  const [q, setQ] = useState('');
  const [category, setCategory] = useState('');
  const [dialog, setDialog] = useState(params.get('new') === '1' ? {} : null);
  useEffect(() => { if (params.get('new')) setParams({}, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const { data, error, loading, reload } = useLoad(() => api.get('/workplace/assets/', { status: status || undefined, q: q || undefined, category: category || undefined }), [status, q, category]);
  const all = useLoad(() => api.get('/workplace/assets/'), [data?.count]);
  const rows = data?.results || [];
  const everything = all.data?.results || [];

  const remove = async (a) => {
    if (!(await confirm({ title: `Delete ${a.tag}?`, body: <p>The asset is removed from the register. If it is only out of use, set its status to Retired instead. This is recorded in the change history.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/workplace/assets/${a.id}/`); notify('Asset deleted.'); reload(); } catch (err) { notify(err.message, 'error'); }
  };
  const expiring = everything.filter((a) => a.warranty_state === 'expiring').length;
  const slices = STATUSES.map(([k, l]) => ({ key: k, label: l, colour: STATUS_COLOUR[k], value: everything.filter((a) => a.status === k).length }));

  return (
    <>
      <PageHeader title="Office assets" description="Laptops, furniture and equipment, with who has what."
        actions={<button type="button" className="btn btn-primary" onClick={() => setDialog({})}>Add asset</button>} />
      <div className="tiles">
        <Tile label="Assets" value={all.data?.count ?? '…'} hint="In the register" />
        <Tile label="Purchase value" value={all.data ? money(all.data.total_value) : '…'} hint="All assets together" />
        <Tile label="In use" value={everything.filter((a) => a.status === 'in_use').length} hint="Assigned and working" />
        <Tile label="Warranty ending" value={expiring} tone={expiring ? 'bad' : ''} hint="Within 60 days" />
      </div>
      <div className="grid-2">
        <section className="card" aria-label="Assets by status"><h2>By status</h2><Donut slices={slices} centreLabel="assets" /></section>
        <section className="card" aria-label="Register filters">
          <h2>Find an asset</h2>
          <form className="form-grid" onSubmit={(e) => e.preventDefault()}>
            <Field id="as-q" label="Search" wide><input id="as-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, tag, serial number" /></Field>
            <Field id="as-fs" label="Status"><select id="as-fs" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Any</option>{STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
            <Field id="as-fc" label="Category"><select id="as-fc" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">Any</option>{(all.data?.categories || []).map((c) => <option key={c} value={c}>{c}</option>)}</select></Field>
          </form>
        </section>
      </div>
      <section className="card">
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && rows.length === 0 && <p className="empty">No assets{q || status || category ? ' match' : ' yet'}.</p>}
        {rows.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Tag</th><th>Asset</th><th>Category</th><th>Assigned to</th><th>Status</th><th>Warranty</th><th className="amount">Value</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.id}>
                  <td data-label="Tag">{a.tag}</td>
                  <td data-label="Asset"><strong>{a.name}</strong>{a.serial_number && <div className="hint">S/N {a.serial_number}</div>}</td>
                  <td data-label="Category">{a.category || '—'}</td>
                  <td data-label="Assigned to">{a.assigned_to?.name || '—'}</td>
                  <td data-label="Status"><Badge tone={STATUS_TONE[a.status]}>{a.status_label}</Badge></td>
                  <td data-label="Warranty">{a.warranty_expiry ? <>{fmtDate(a.warranty_expiry)} {WARRANTY[a.warranty_state] && <Badge tone={WARRANTY[a.warranty_state][0]}>{WARRANTY[a.warranty_state][1]}</Badge>}</> : '—'}</td>
                  <td className="amount" data-label="Value">{a.purchase_value ? money(a.purchase_value) : '—'}</td>
                  <td><div className="row-actions">
                    <button type="button" className="btn btn-small" onClick={() => setDialog(a)}>Open</button>
                    <button type="button" className="btn btn-small btn-danger-outline" onClick={() => remove(a)}>Delete</button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
        {data && rows.length > 0 && <div className="list-foot"><span>{data.count} asset{data.count === 1 ? '' : 's'}</span><span>Total value <strong>{money(data.total_value)}</strong></span></div>}
      </section>
      {dialog && <AssetDialog asset={dialog.id ? dialog : null} people={people} categories={all.data?.categories || []} onClose={() => setDialog(null)} onSaved={(a) => { reload(); all.reload(); if (!dialog.id) setDialog(a); }} />}
      {confirmDialog}
    </>
  );
}
