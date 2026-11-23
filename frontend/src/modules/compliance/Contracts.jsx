import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { addDays, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, Loading, PageHeader, fmtDate, useConfirm, useReasonDialog, useToast } from '../../ui';
import { Pick, RecordDialog, daysText, opt, useCanManage, useMeta, useSoftList } from './common';

const STATES = [['', 'All'], ['active', 'Active'], ['expiring', 'Expiring soon'], ['expired', 'Expired'], ['draft', 'Draft'], ['terminated', 'Terminated']];
const STATE_TONE = { active: 'good', expiring: 'warn', expired: 'bad', draft: 'neutral', terminated: 'neutral' };
const STATUS_CHOICES = [{ value: 'active', label: 'Active' }, { value: 'draft', label: 'Draft' }];

function ContractDialog({ record, meta, onClose, onSaved }) {
  const notify = useToast();
  const isEdit = !!record;
  const customers = useSoftList('/sales/customers/?active=true', (c) => ({ value: c.id, label: c.display_name }));
  const projects = useSoftList('/projects/lookup/', (p) => ({ value: p.id, label: `${p.code ? `${p.code} ` : ''}${p.name}` }));
  const vendors = useSoftList('/purchases/vendors/lookup/', (p) => ({ value: p.id, label: p.name }));
  const fields = useMemo(() => [
    { name: 'title', label: 'Title', type: 'text', required: true, maxLength: 200, wide: true },
    { name: 'counterparty', label: 'Counterparty', type: 'text', required: true, maxLength: 200 },
    { name: 'contract_type', label: 'Type', type: 'select', required: true, options: opt(meta.contract_types) },
    { name: 'effective_date', label: 'Effective from', type: 'date', required: true },
    { name: 'expiry_date', label: 'Expires on', type: 'date', hint: 'Leave empty for an open-ended contract.', validate: (v, all) => (v && all.effective_date && v < all.effective_date ? 'The expiry cannot be before the start.' : null) },
    { name: 'auto_renew', label: 'Renews automatically', type: 'checkbox', validate: (v, all) => (v && !all.expiry_date ? 'Auto-renewal needs an expiry date.' : null) },
    { name: 'renewal_term_months', label: 'Renewal term (months)', type: 'number', min: 1, max: 120, integer: true, hidden: (v) => !v.auto_renew },
    { name: 'renewal_notice_days', label: 'Alert before expiry (days)', type: 'number', min: 0, max: 365, integer: true },
    { name: 'value', label: 'Value (Rs, optional)', type: 'number', min: 0, step: '0.01' },
    { name: 'status', label: 'Status', type: 'select', required: true, options: STATUS_CHOICES },
    { name: 'owner', label: 'Owner', type: 'select', options: opt(meta.assignees) },
    { name: 'customer_id', label: 'Customer (optional)', type: 'select', options: customers },
    { name: 'vendor_id', label: 'Vendor (optional)', type: 'select', options: vendors },
    { name: 'project_id', label: 'Project (optional)', type: 'select', options: projects },
    { name: 'notes', label: 'Notes', type: 'textarea', wide: true, maxLength: 4000 },
  ], [meta, customers, vendors, projects]);
  const rec = useMemo(() => record && ({ ...record, owner: record.owner?.id || '', customer_id: record.customer?.id || '', vendor_id: record.vendor?.id || '', project_id: record.project?.id || '', value: record.value ?? '' }), [record]);
  const defaults = useMemo(() => ({ contract_type: 'client', effective_date: today(), status: 'active', renewal_term_months: 12, renewal_notice_days: 30 }), []);
  return (
    <RecordDialog title={isEdit ? 'Edit contract' : 'New contract'} fields={fields} record={rec} defaults={defaults} onClose={onClose}
      onSave={async (payload, values) => {
        const body = { ...payload };
        if (!body.auto_renew) delete body.renewal_term_months;
        body.vendor_name = vendors.find((x) => x.value === values.vendor_id)?.label || '';
        body.project_name = projects.find((x) => x.value === values.project_id)?.label || '';
        if (isEdit && record.status === 'terminated') { Object.keys(body).forEach((k) => k !== 'notes' && delete body[k]); }
        const saved = isEdit ? await api.patch(`/compliance/contracts/${record.id}/`, body) : await api.post('/compliance/contracts/', body);
        notify(isEdit ? 'Contract updated.' : `Contract added: ${saved.reference}`);
        onSaved(saved);
      }} />
  );
}

function RenewDialog({ c, onClose, onDone }) {
  const notify = useToast();
  const fields = useMemo(() => [
    { name: 'new_expiry_date', label: 'New expiry date', type: 'date', required: true, min: addDays(c.current_expiry || today(), 1), hint: `Current expiry ${fmtDate(c.current_expiry)}.` },
    { name: 'value', label: 'New value (Rs, optional)', type: 'number', min: 0, step: '0.01' },
    { name: 'note', label: 'Note', type: 'text', maxLength: 300 },
  ], [c]);
  const defaults = useMemo(() => ({ new_expiry_date: addDays(c.current_expiry || today(), 365) }), [c]);
  return (
    <RecordDialog title={`Renew ${c.title}`} fields={fields} defaults={defaults} wide={false} saveLabel="Renew" onClose={onClose}
      onSave={async (payload) => { await api.post(`/compliance/contracts/${c.id}/renew/`, payload); notify('Contract renewed.'); onDone(); }} />
  );
}

function ContractDetail({ id, canManage, meta, onClose, onChanged }) {
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const { ask, dialog: reasonDialog } = useReasonDialog();
  const { data: c, error, loading, reload } = useLoad(() => api.get(`/compliance/contracts/${id}/`), [id]);
  const [editing, setEditing] = useState(false);
  const [renewing, setRenewing] = useState(false);
  const changed = () => { reload(); onChanged(); };
  const terminate = async () => {
    const reason = await ask({ title: 'End this contract', label: 'Why is it ending?', confirmLabel: 'Terminate', message: 'A terminated contract is kept as a record and cannot be renewed.' });
    if (!reason) return;
    try { await api.post(`/compliance/contracts/${id}/terminate/`, { reason }); notify('Contract terminated.'); changed(); } catch (e) { notify(e.message, 'error'); }
  };
  const remove = async () => {
    if (!(await confirm({ title: 'Delete this draft?', confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/compliance/contracts/${id}/`); notify('Draft deleted.'); onChanged(); onClose(); } catch (e) { notify(e.message, 'error'); }
  };
  return (
    <Dialog title={c ? c.title : 'Contract'} onClose={onClose} wide>
      {loading && <Loading />}<ErrorBox error={error} onRetry={reload} />
      {c && (
        <div className="stack-gap">
          <div className="compliance-item-head">
            <Badge tone={STATE_TONE[c.state]}>{c.state_label}</Badge>
            {c.days_left !== null && c.state !== 'terminated' && <span className="muted">{c.days_left >= 0 ? `Expires ${daysText(c.days_left)}` : `Expired ${-c.days_left} days ago`}</span>}
            <span className="muted">Ref {c.reference}</span>
          </div>
          <dl className="summary">
            <div><dt>Counterparty</dt><dd>{c.counterparty}</dd></div><div><dt>Type</dt><dd>{c.contract_type_label}</dd></div>
            <div><dt>Effective from</dt><dd>{fmtDate(c.effective_date)}</dd></div>
            <div><dt>Expires</dt><dd>{c.expiry_date ? fmtDate(c.expiry_date) : 'Open ended'}{c.auto_renewed && <span className="hint"> rolled to {fmtDate(c.current_expiry)} by auto-renewal</span>}</dd></div>
            <div><dt>Auto-renews</dt><dd>{c.auto_renew ? `Yes, every ${c.renewal_term_months} months` : 'No'}</dd></div>
            <div><dt>Alert before expiry</dt><dd>{c.renewal_notice_days} days</dd></div>
            <div><dt>Value</dt><dd>{c.value ? `Rs ${plain(c.value)}` : '—'}</dd></div><div><dt>Owner</dt><dd>{c.owner?.name || '—'}</dd></div>
            <div><dt>Renewed</dt><dd>{c.renewal_count} time{c.renewal_count === 1 ? '' : 's'}</dd></div>
            {c.customer && <div><dt>Customer</dt><dd>{c.customer.name}</dd></div>}
            {c.vendor && <div><dt>Vendor</dt><dd>{c.vendor.name}</dd></div>}
            {c.project && <div><dt>Project</dt><dd>{c.project.name}</dd></div>}
            {c.status === 'terminated' && <div><dt>Terminated</dt><dd>{fmtDate(c.terminated_on)}: {c.termination_reason}</dd></div>}
          </dl>
          {c.notes && <p>{c.notes}</p>}
          {canManage && (
            <div className="actions">
              {c.status !== 'terminated' && <button type="button" className="btn btn-primary" onClick={() => setRenewing(true)}>Renew</button>}
              <button type="button" className="btn" onClick={() => setEditing(true)}>Edit</button>
              {c.status !== 'terminated' && c.status !== 'draft' && <button type="button" className="btn btn-danger-outline" onClick={terminate}>Terminate</button>}
              {c.status === 'draft' && <button type="button" className="btn btn-danger-outline" onClick={remove}>Delete draft</button>}
            </div>
          )}
          <Attachments entityType="contract" entityId={c.id} canManage={canManage} title="Contract documents" hint="The signed agreement, amendments and renewal letters." />
        </div>
      )}
      {editing && <ContractDialog record={c} meta={meta} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); changed(); }} />}
      {renewing && <RenewDialog c={c} onClose={() => setRenewing(false)} onDone={() => { setRenewing(false); changed(); }} />}
      {dialog}{reasonDialog}
    </Dialog>
  );
}

export default function ContractsPage() {
  const canManage = useCanManage();
  const meta = useMeta();
  const [params, setParams] = useSearchParams();
  const [state, setState] = useState('');
  const [type, setType] = useState('');
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const [openId, setOpenId] = useState(params.get('open'));
  const [adding, setAdding] = useState(params.get('new') === '1');
  const { data, error, loading, reload } = useLoad(() => api.get('/compliance/contracts/', { state: state || undefined, type: type || undefined, q: term || undefined }), [state, type, term]);
  const rows = data?.results || [];
  useEffect(() => { if (params.get('open') || params.get('new')) setParams({}, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <PageHeader title="Contracts" description="Agreements with customers, vendors, landlords and others: when they expire, which renew themselves, and the signed documents."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>New contract</button>} />
      <section className="card">
        <div className="chips" role="group" aria-label="Filter by state">
          {STATES.map(([key, label]) => <button key={key} type="button" className="chip" aria-pressed={state === key} onClick={() => setState(key)}>{label}{data && key && ` (${data.totals[key] ?? 0})`}</button>)}
        </div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
          {meta.data && <Pick id="ct-type" label="Type" value={type} onChange={setType} options={opt(meta.data.contract_types)} />}
          <Field id="ct-q" label="Search" className="grow"><input id="ct-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Title, counterparty or reference" /></Field>
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}<ErrorBox error={error} onRetry={reload} />
        {!loading && !error && rows.length === 0 && <p className="empty">No contracts{state || term || type ? ' match' : ' yet'}.</p>}
        {rows.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Contract</th><th>Counterparty</th><th>Type</th><th>Expires</th><th>Renewal</th><th>Owner</th><th>State</th><th className="amount">Value</th></tr></thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="clickable" onClick={() => setOpenId(c.id)}>
                  <td data-label="Contract"><button type="button" className="link-button" onClick={(e) => { e.stopPropagation(); setOpenId(c.id); }}><strong>{c.title}</strong></button><div className="hint">{c.reference}</div></td>
                  <td data-label="Counterparty">{c.counterparty}</td><td data-label="Type">{c.contract_type_label}</td>
                  <td data-label="Expires">{c.current_expiry ? <>{fmtDate(c.current_expiry)}<div className="hint">{c.state === 'terminated' ? '' : daysText(c.days_left)}</div></> : 'Open ended'}</td>
                  <td data-label="Renewal">{c.auto_renew ? 'Auto' : 'Manual'}</td><td data-label="Owner">{c.owner?.name || '—'}</td>
                  <td data-label="State"><Badge tone={STATE_TONE[c.state]}>{c.state_label}</Badge></td>
                  <td className="amount" data-label="Value">{c.value ? plain(c.value) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
        {rows.length > 0 && <div className="list-foot"><span>{rows.length} contract{rows.length === 1 ? '' : 's'}</span></div>}
      </section>
      {openId && meta.data && <ContractDetail id={openId} canManage={canManage} meta={meta.data} onClose={() => setOpenId(null)} onChanged={reload} />}
      {adding && meta.data && <ContractDialog meta={meta.data} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); reload(); }} />}
    </>
  );
}
