import { useState } from 'react';
import { api } from '../../api';
import { useAuth } from '../../context';
import { num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, Tabs, useConfirm, useToast } from '../../ui';

const KIND_LABEL = { open: 'Open', won: 'Won', lost: 'Lost' };

function StageDialog({ stage, onClose, onSaved }) {
  const notify = useToast();
  const [v, setV] = useState({ name: stage?.name || '', probability: stage ? String(stage.probability) : '20', colour: stage?.colour || '#c9a24b' });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const closed = stage && stage.kind !== 'open';
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const submit = async (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    setFailure(null);
    const e = {};
    if (!v.name.trim()) e.name = 'Enter the stage name.';
    if (!closed && !(v.probability !== '' && num(v.probability) >= 0 && num(v.probability) <= 100)) e.probability = 'Enter a percentage from 0 to 100.';
    setErrors(e);
    if (Object.keys(e).length) return;
    setBusy(true);
    const body = { name: v.name.trim(), colour: v.colour, ...(closed ? {} : { probability: v.probability }) };
    try {
      const saved = stage ? await api.patch(`/crm/settings/stages/${stage.id}/`, body) : await api.post('/crm/settings/stages/', { ...body, kind: 'open' });
      notify(stage ? 'Stage saved.' : 'Stage added.');
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title={stage ? `Edit ${stage.name}` : 'New pipeline stage'} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={Object.keys(v).map((name) => ({ name }))} />
        <Field id="st-name" label="Name" required error={errors.name}><input id="st-name" value={v.name} maxLength={60} onChange={set('name')} /></Field>
        <Field id="st-prob" label="Probability (%)" error={errors.probability} hint={closed ? `A ${KIND_LABEL[stage.kind].toLowerCase()} stage is always ${stage.kind === 'won' ? 100 : 0}%.` : 'Used for the weighted forecast.'}>
          <input id="st-prob" type="number" min="0" max="100" value={closed ? stage.probability : v.probability} disabled={closed} onChange={set('probability')} />
        </Field>
        <Field id="st-colour" label="Colour" error={errors.colour}>
          <div className="color-row"><input type="color" aria-label="Colour picker" value={v.colour} onChange={set('colour')} /><input id="st-colour" value={v.colour} maxLength={7} onChange={set('colour')} /></div>
        </Field>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save stage'}</button>
        </div>
      </form>
    </Dialog>
  );
}

function Stages({ canManage }) {
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { data, error, loading, reload } = useLoad(() => api.get('/crm/settings/stages/'), []);
  const [dialog, setDialog] = useState(null);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const rows = data;
  const act = async (fn, ok) => { try { await fn(); if (ok) notify(ok); reload(); } catch (e) { notify(e.message, 'error'); } };
  const shift = (i, dir) => {
    const ids = rows.map((r) => r.id);
    [ids[i], ids[i + dir]] = [ids[i + dir], ids[i]];
    act(() => api.post('/crm/settings/stages/reorder/', { ids }));
  };
  const remove = async (s) => {
    if (!(await confirm({ title: `Delete ${s.name}?`, body: <p>A stage that has deals or deal history cannot be deleted; switch it off instead.</p>, confirmLabel: 'Delete', danger: true }))) return;
    act(() => api.del(`/crm/settings/stages/${s.id}/`), 'Stage deleted.');
  };
  return (
    <>
      <p className="muted">Deals move through these stages left to right on the board. The probability of the stage feeds the weighted forecast. Won and Lost close a deal and always stay last.</p>
      {canManage && <p><button type="button" className="btn btn-primary" onClick={() => setDialog({})}>Add stage</button></p>}
      <div className="table-wrap"><table>
        <thead><tr><th>Stage</th><th>Type</th><th className="amount">Probability</th><th className="amount">Deals</th><th>Status</th>{canManage && <th><span className="sr-only">Actions</span></th>}</tr></thead>
        <tbody>{rows.map((s, i) => (
          <tr key={s.id} className={s.is_active ? '' : 'row-inactive'}>
            <td data-label="Stage"><i className="crm-dot" style={{ background: s.colour }} aria-hidden="true" /> <strong>{s.name}</strong></td>
            <td data-label="Type">{KIND_LABEL[s.kind]}</td><td className="amount" data-label="Probability">{s.probability}%</td><td className="amount" data-label="Deals">{s.deals}</td>
            <td data-label="Status">{s.is_active ? <Badge tone="good">Active</Badge> : <Badge>Switched off</Badge>}</td>
            {canManage && (
              <td className="row-actions">
                {s.kind === 'open' && <button type="button" className="btn btn-small" aria-label={`Move ${s.name} up`} disabled={i === 0 || rows[i - 1].kind !== 'open'} onClick={() => shift(i, -1)}>Up</button>}
                {s.kind === 'open' && <button type="button" className="btn btn-small" aria-label={`Move ${s.name} down`} disabled={i === rows.length - 1 || rows[i + 1].kind !== 'open'} onClick={() => shift(i, 1)}>Down</button>}
                <button type="button" className="btn btn-small" onClick={() => setDialog(s)}>Edit</button>
                {s.kind === 'open' && <button type="button" className="btn btn-small" onClick={() => act(() => api.patch(`/crm/settings/stages/${s.id}/`, { is_active: !s.is_active }), s.is_active ? 'Stage switched off.' : 'Stage switched on.')}>{s.is_active ? 'Switch off' : 'Switch on'}</button>}
                {s.kind === 'open' && <button type="button" className="btn btn-small" onClick={() => remove(s)}>Delete</button>}
              </td>
            )}
          </tr>))}</tbody>
      </table></div>
      {dialog && <StageDialog stage={dialog.id ? dialog : null} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); reload(); }} />}
      {confirmDialog}
    </>
  );
}

function SimpleList({ canManage, url, noun, usedKey, usedLabel, intro }) {
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { data, error, loading, reload } = useLoad(() => api.get(url), [url]);
  const [name, setName] = useState('');
  const [editing, setEditing] = useState(null);
  const [fieldError, setFieldError] = useState('');
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const act = async (fn, ok) => { try { await fn(); if (ok) notify(ok); reload(); return true; } catch (e) { notify(e.message, 'error'); return false; } };
  const add = async (e) => {
    e.preventDefault();
    if (!name.trim()) { setFieldError(`Enter the ${noun}.`); return; }
    setFieldError('');
    try { await api.post(url, { name: name.trim() }); notify(`${noun[0].toUpperCase()}${noun.slice(1)} added.`); setName(''); reload(); } catch (err) { setFieldError(err.fieldErrors?.name || err.message); }
  };
  const rename = async () => {
    if (!editing.name.trim()) { setFieldError(`Enter the ${noun}.`); return; }
    setFieldError('');
    try { await api.patch(`${url}${editing.id}/`, { name: editing.name.trim() }); notify('Saved.'); setEditing(null); reload(); } catch (err) { setFieldError(err.fieldErrors?.name || err.message); }
  };
  const remove = async (r) => {
    if (!(await confirm({ title: `Delete ${r.name}?`, body: <p>Only one that nothing uses can be deleted; otherwise switch it off.</p>, confirmLabel: 'Delete', danger: true }))) return;
    act(() => api.del(`${url}${r.id}/`), 'Deleted.');
  };
  return (
    <>
      <p className="muted">{intro}</p>
      {canManage && (
        <form className="toolbar" onSubmit={add}>
          <Field id={`new-${noun}`} label={`New ${noun}`} className="grow" error={fieldError && !editing ? fieldError : undefined}><input id={`new-${noun}`} value={name} maxLength={100} onChange={(e) => setName(e.target.value)} /></Field>
          <button type="submit" className="btn btn-primary">Add</button>
        </form>
      )}
      <div className="table-wrap"><table>
        <thead><tr><th>{noun[0].toUpperCase() + noun.slice(1)}</th><th className="amount">{usedLabel}</th><th>Status</th>{canManage && <th><span className="sr-only">Actions</span></th>}</tr></thead>
        <tbody>{data.map((r) => (
          <tr key={r.id} className={r.is_active ? '' : 'row-inactive'}>
            <td data-label={noun}>{editing?.id === r.id ? (
              <Field id={`edit-${r.id}`} label={`Rename ${r.name}`} error={fieldError || undefined}><input id={`edit-${r.id}`} value={editing.name} maxLength={100} onChange={(e) => setEditing({ ...editing, name: e.target.value })} /></Field>
            ) : <strong>{r.name}</strong>}</td>
            <td className="amount" data-label={usedLabel}>{r[usedKey]}</td>
            <td data-label="Status">{r.is_active ? <Badge tone="good">Active</Badge> : <Badge>Switched off</Badge>}</td>
            {canManage && (
              <td className="row-actions">
                {editing?.id === r.id ? (<><button type="button" className="btn btn-small btn-primary" onClick={rename}>Save</button><button type="button" className="btn btn-small" onClick={() => { setEditing(null); setFieldError(''); }}>Cancel</button></>) : (
                  <><button type="button" className="btn btn-small" onClick={() => { setEditing({ id: r.id, name: r.name }); setFieldError(''); }}>Rename</button>
                    <button type="button" className="btn btn-small" onClick={() => act(() => api.patch(`${url}${r.id}/`, { is_active: !r.is_active }), r.is_active ? 'Switched off.' : 'Switched on.')}>{r.is_active ? 'Switch off' : 'Switch on'}</button>
                    <button type="button" className="btn btn-small" onClick={() => remove(r)}>Delete</button></>)}
              </td>
            )}
          </tr>))}</tbody>
      </table></div>
      {confirmDialog}
    </>
  );
}

function ProductDialog({ product, onClose, onSaved }) {
  const notify = useToast();
  const [v, setV] = useState({ name: product?.name || '', code: product?.code || '', description: product?.description || '', unit_price: product?.unit_price ?? '', gst_rate: product?.gst_rate ?? '18' });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const submit = async (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    setFailure(null);
    const e = {};
    if (!v.name.trim()) e.name = 'Enter the product or service name.';
    if (v.unit_price === '' || num(v.unit_price) < 0) e.unit_price = 'Enter a price of 0 or more.';
    if (num(v.gst_rate) < 0 || num(v.gst_rate) > 100) e.gst_rate = 'Enter 0 to 100.';
    setErrors(e);
    if (Object.keys(e).length) return;
    setBusy(true);
    try {
      const saved = product ? await api.patch(`/crm/products/${product.id}/`, v) : await api.post('/crm/products/', v);
      notify(product ? 'Product saved.' : 'Product added.');
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title={product ? `Edit ${product.name}` : 'New product or service'} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={Object.keys(v).map((name) => ({ name }))} />
        <div className="form-grid">
          <Field id="pr-name" label="Name" required error={errors.name}><input id="pr-name" value={v.name} maxLength={200} onChange={set('name')} /></Field>
          <Field id="pr-code" label="Code" error={errors.code}><input id="pr-code" value={v.code} maxLength={30} onChange={set('code')} /></Field>
          <Field id="pr-price" label="Unit price (INR)" required error={errors.unit_price}><input id="pr-price" type="number" min="0" step="0.01" value={v.unit_price} onChange={set('unit_price')} /></Field>
          <Field id="pr-gst" label="GST %" error={errors.gst_rate}><input id="pr-gst" type="number" min="0" max="100" step="0.01" value={v.gst_rate} onChange={set('gst_rate')} /></Field>
        </div>
        <Field id="pr-desc" label="Description" wide error={errors.description}><textarea id="pr-desc" rows={2} value={v.description} onChange={set('description')} /></Field>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </Dialog>
  );
}

function Products({ canManage }) {
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { data, error, loading, reload } = useLoad(() => api.get('/crm/products/'), []);
  const [dialog, setDialog] = useState(null);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const act = async (fn, ok) => { try { await fn(); notify(ok); reload(); } catch (e) { notify(e.message, 'error'); } };
  const remove = async (p) => {
    if (!(await confirm({ title: `Delete ${p.name}?`, body: <p>A product that is on a deal cannot be deleted; switch it off instead.</p>, confirmLabel: 'Delete', danger: true }))) return;
    act(() => api.del(`/crm/products/${p.id}/`), 'Product deleted.');
  };
  return (
    <>
      <p className="muted">An optional catalogue of what you sell. Pick from it when adding line items to a deal.</p>
      {canManage && <p><button type="button" className="btn btn-primary" onClick={() => setDialog({})}>Add product or service</button></p>}
      {data.length === 0 ? <p className="empty">No products yet.</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Name</th><th>Code</th><th className="amount">Unit price</th><th className="amount">GST %</th><th>Status</th>{canManage && <th><span className="sr-only">Actions</span></th>}</tr></thead>
          <tbody>{data.map((p) => (
            <tr key={p.id} className={p.is_active ? '' : 'row-inactive'}>
              <td data-label="Name"><strong>{p.name}</strong>{p.description && <span className="crm-sub">{p.description}</span>}</td><td data-label="Code">{p.code || '—'}</td>
              <td className="amount" data-label="Unit price">{plain(p.unit_price)}</td><td className="amount" data-label="GST %">{num(p.gst_rate)}</td>
              <td data-label="Status">{p.is_active ? <Badge tone="good">Active</Badge> : <Badge>Switched off</Badge>}</td>
              {canManage && (
                <td className="row-actions">
                  <button type="button" className="btn btn-small" onClick={() => setDialog(p)}>Edit</button>
                  <button type="button" className="btn btn-small" onClick={() => act(() => api.patch(`/crm/products/${p.id}/`, { is_active: !p.is_active }), p.is_active ? 'Switched off.' : 'Switched on.')}>{p.is_active ? 'Switch off' : 'Switch on'}</button>
                  <button type="button" className="btn btn-small" onClick={() => remove(p)}>Delete</button>
                </td>
              )}
            </tr>))}</tbody>
        </table></div>
      )}
      {dialog && <ProductDialog product={dialog.id ? dialog : null} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); reload(); }} />}
      {confirmDialog}
    </>
  );
}

export default function Settings() {
  const { user } = useAuth();
  const [tab, setTab] = useState('stages');
  const canManage = user.permissions.includes('crm.manage');
  return (
    <>
      <PageHeader title="CRM Settings" description="Pipeline stages, lead sources, lost reasons and the product catalogue." />
      {!canManage && <Notice type="info">You can view these lists. A partner or administrator can change them.</Notice>}
      <Tabs tabs={[{ key: 'stages', label: 'Pipeline stages' }, { key: 'sources', label: 'Lead sources' }, { key: 'reasons', label: 'Lost reasons' }, { key: 'products', label: 'Products' }]} value={tab} onChange={setTab} />
      <section className="card">
        {tab === 'stages' && <Stages canManage={canManage} />}
        {tab === 'sources' && <SimpleList key="sources" canManage={canManage} url="/crm/settings/sources/" noun="source" usedKey="leads" usedLabel="Leads" intro="Where leads come from. A source that leads use can be switched off but not deleted." />}
        {tab === 'reasons' && <SimpleList key="reasons" canManage={canManage} url="/crm/settings/lost-reasons/" noun="reason" usedKey="deals" usedLabel="Deals" intro="The reasons offered when a deal is marked lost." />}
        {tab === 'products' && <Products canManage={canManage} />}
      </section>
    </>
  );
}
