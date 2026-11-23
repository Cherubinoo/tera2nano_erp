import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, Loading, PageHeader, fmtDate, fmtDateTime, useConfirm, useToast } from '../../ui';
import { RecordDialog, opt, useCanManage, useMeta } from './common';

const STATUS_TONE = { active: 'good', draft: 'neutral', retired: 'neutral' };

function PolicyDialog({ record, meta, onClose, onSaved }) {
  const notify = useToast();
  const isEdit = !!record;
  const fields = useMemo(() => [
    { name: 'name', label: 'Policy name', type: 'text', required: true, maxLength: 200, wide: true },
    { name: 'category', label: 'Category', type: 'text', maxLength: 60, placeholder: 'HR, IT, Finance ...' },
    { name: 'version', label: 'Version', type: 'text', required: true, maxLength: 20, hint: isEdit ? 'Changing the version asks everyone to acknowledge again.' : undefined },
    { name: 'effective_date', label: 'Effective from', type: 'date', required: true },
    { name: 'review_date', label: 'Review on', type: 'date', validate: (v, all) => (v && all.effective_date && v < all.effective_date ? 'The review cannot be before the effective date.' : null) },
    { name: 'status', label: 'Status', type: 'select', required: true, options: [{ value: 'draft', label: 'Draft' }, { value: 'active', label: 'Active' }, { value: 'retired', label: 'Retired' }] },
    { name: 'owner', label: 'Owner', type: 'select', options: opt(meta.assignees) },
    { name: 'requires_acknowledgement', label: 'People must acknowledge this policy', type: 'checkbox', wide: true },
    { name: 'summary', label: 'Summary', type: 'textarea', wide: true, maxLength: 4000 },
  ], [meta, isEdit]);
  const rec = useMemo(() => record && ({ ...record, owner: record.owner?.id || '' }), [record]);
  const defaults = useMemo(() => ({ version: '1.0', effective_date: today(), status: 'draft' }), []);
  return (
    <RecordDialog title={isEdit ? 'Edit policy' : 'New policy'} fields={fields} record={rec} defaults={defaults} wide={false} onClose={onClose}
      onSave={async (payload) => {
        const saved = isEdit ? await api.patch(`/compliance/policies/${record.id}/`, payload) : await api.post('/compliance/policies/', payload);
        notify(isEdit ? 'Policy updated.' : `Policy added: ${saved.reference}`);
        onSaved(saved);
      }} />
  );
}

function PolicyDetail({ id, canManage, meta, onClose, onChanged }) {
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const { data: p, error, loading, reload } = useLoad(() => api.get(`/compliance/policies/${id}/`), [id]);
  const [editing, setEditing] = useState(false);
  const changed = () => { reload(); onChanged(); };
  const acknowledge = async () => { try { await api.post(`/compliance/policies/${id}/acknowledge/`); notify('Thank you. Your acknowledgement is recorded.'); changed(); } catch (e) { notify(e.message, 'error'); } };
  const remove = async () => {
    if (!(await confirm({ title: 'Delete this policy?', body: <p>Active policies must be retired first.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/compliance/policies/${id}/`); notify('Policy deleted.'); onChanged(); onClose(); } catch (e) { notify(e.message, 'error'); }
  };
  return (
    <Dialog title={p ? `${p.name} (v${p.version})` : 'Policy'} onClose={onClose} wide>
      {loading && <Loading />}<ErrorBox error={error} onRetry={reload} />
      {p && (
        <div className="stack-gap">
          <div className="compliance-item-head"><Badge tone={STATUS_TONE[p.status]}>{p.status_label}</Badge>{p.review_due && <Badge tone="warn">Review due</Badge>}<span className="muted">Ref {p.reference}</span></div>
          <dl className="summary">
            <div><dt>Category</dt><dd>{p.category || '—'}</dd></div><div><dt>Version</dt><dd>{p.version}</dd></div>
            <div><dt>Effective from</dt><dd>{fmtDate(p.effective_date)}</dd></div><div><dt>Review on</dt><dd>{fmtDate(p.review_date)}</dd></div>
            <div><dt>Owner</dt><dd>{p.owner?.name || '—'}</dd></div><div><dt>Acknowledgement</dt><dd>{p.requires_acknowledgement ? `${p.acknowledged_count} so far` : 'Not required'}</dd></div>
          </dl>
          {p.summary && <p>{p.summary}</p>}
          <div className="actions">
            {p.requires_acknowledgement && p.status === 'active' && !p.acknowledged_by_me && <button type="button" className="btn btn-primary" onClick={acknowledge}>I have read and accept this policy</button>}
            {p.acknowledged_by_me && <Badge tone="good">You acknowledged version {p.version}</Badge>}
            {canManage && <button type="button" className="btn" onClick={() => setEditing(true)}>Edit</button>}
            {canManage && p.status !== 'active' && <button type="button" className="btn btn-danger-outline" onClick={remove}>Delete</button>}
          </div>
          {p.requires_acknowledgement && p.acknowledgements.length > 0 && (
            <section className="card"><h2>Acknowledged version {p.version}</h2>
              <ul className="compliance-history">{p.acknowledgements.map((a, i) => <li key={i}>{a.user} <span className="muted">{fmtDateTime(a.at)}</span></li>)}</ul>
            </section>
          )}
          <Attachments entityType="compliance_policy" entityId={p.id} canManage={canManage} title="Policy document" hint="Attach the current version of the document." />
        </div>
      )}
      {editing && <PolicyDialog record={p} meta={meta} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); changed(); }} />}
      {dialog}
    </Dialog>
  );
}

export default function PoliciesPage() {
  const canManage = useCanManage();
  const meta = useMeta();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState('');
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const [openId, setOpenId] = useState(params.get('open'));
  const [adding, setAdding] = useState(params.get('new') === '1');
  useEffect(() => { if (params.get('open') || params.get('new')) setParams({}, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const { data, error, loading, reload } = useLoad(() => api.get('/compliance/policies/', { status: status || undefined, q: term || undefined }), [status, term]);
  const rows = data?.results || [];
  return (
    <>
      <PageHeader title="Policies" description="The company's policies: current version, review date and who has acknowledged them."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>New policy</button>} />
      <section className="card">
        <div className="chips" role="group" aria-label="Filter by status">
          {[['', 'All'], ['active', 'Active'], ['draft', 'Draft'], ['retired', 'Retired']].map(([key, label]) => <button key={key} type="button" className="chip" aria-pressed={status === key} onClick={() => setStatus(key)}>{label}</button>)}
        </div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
          <Field id="po-q" label="Search" className="grow"><input id="po-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Policy name, category or reference" /></Field>
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}<ErrorBox error={error} onRetry={reload} />
        {!loading && !error && rows.length === 0 && <p className="empty">No policies{status || term ? ' match' : ' yet'}.</p>}
        {rows.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Policy</th><th>Version</th><th>Effective</th><th>Review</th><th>Acknowledged</th><th>Status</th></tr></thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id} className="clickable" onClick={() => setOpenId(p.id)}>
                  <td data-label="Policy"><button type="button" className="link-button" onClick={(e) => { e.stopPropagation(); setOpenId(p.id); }}><strong>{p.name}</strong></button><div className="hint">{[p.category, p.reference].filter(Boolean).join(' · ')}</div></td>
                  <td data-label="Version">{p.version}</td><td data-label="Effective">{fmtDate(p.effective_date)}</td>
                  <td data-label="Review">{fmtDate(p.review_date)} {p.review_due && <Badge tone="warn">Due</Badge>}</td>
                  <td data-label="Acknowledged">{p.requires_acknowledgement ? (p.acknowledged_by_me ? `${p.acknowledged_count} (including you)` : p.acknowledged_count) : '—'}</td>
                  <td data-label="Status"><Badge tone={STATUS_TONE[p.status]}>{p.status_label}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </section>
      {openId && meta.data && <PolicyDetail id={openId} canManage={canManage} meta={meta.data} onClose={() => setOpenId(null)} onChanged={reload} />}
      {adding && meta.data && <PolicyDialog meta={meta.data} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); reload(); }} />}
    </>
  );
}
