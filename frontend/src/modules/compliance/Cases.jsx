import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, Loading, PageHeader, fmtDate, useConfirm, useToast } from '../../ui';
import { Pick, RecordDialog, daysText, opt, useCanManage, useMeta } from './common';

const STATUSES = [['active', 'Active'], ['', 'All'], ['disposed', 'Disposed'], ['closed', 'Closed']];
const STATUS_TONE = { open: 'info', in_progress: 'warn', disposed: 'good', closed: 'neutral' };
const HEARING_TONE = { past: 'bad', today: 'warn', soon: 'warn', later: 'neutral' };
const HEARING_TEXT = { past: 'Update needed', today: 'Today', soon: 'Soon', later: '' };

function CaseDialog({ record, meta, onClose, onSaved }) {
  const notify = useToast();
  const isEdit = !!record;
  const fields = useMemo(() => [
    { name: 'title', label: 'Matter', type: 'text', required: true, maxLength: 200, wide: true },
    { name: 'case_type', label: 'Kind', type: 'select', required: true, options: opt(meta.case_types) },
    { name: 'party_role', label: 'Our position', type: 'select', required: true, options: opt(meta.party_roles) },
    { name: 'forum', label: 'Court, tribunal or authority', type: 'text', maxLength: 200 },
    { name: 'case_number', label: 'Case or notice number', type: 'text', maxLength: 80 },
    { name: 'counterparty', label: 'Other party', type: 'text', maxLength: 200 },
    { name: 'filed_date', label: 'Filed or received on', type: 'date' },
    { name: 'next_hearing_date', label: 'Next hearing', type: 'date', hint: 'Cleared when the matter is closed.',
      validate: (v, all) => (v && all.filed_date && v < all.filed_date ? 'The hearing cannot be before the filing date.' : null) },
    { name: 'lawyer', label: 'Lawyer', type: 'text', maxLength: 200 },
    { name: 'lawyer_contact', label: 'Lawyer contact', type: 'text', maxLength: 120 },
    { name: 'amount_involved', label: 'Amount involved (Rs)', type: 'number', min: 0, step: '0.01' },
    { name: 'owner', label: 'Owner', type: 'select', options: opt(meta.assignees) },
    { name: 'status', label: 'Status', type: 'select', required: true, options: [{ value: 'open', label: 'Open' }, { value: 'in_progress', label: 'In progress' }, { value: 'disposed', label: 'Disposed' }, { value: 'closed', label: 'Closed' }] },
    { name: 'outcome', label: 'Outcome', type: 'textarea', wide: true, maxLength: 4000, hint: 'Required to dispose of or close a matter.',
      validate: (v, all) => ((all.status === 'disposed' || all.status === 'closed') && !String(v || '').trim() ? 'Record the outcome first.' : null) },
    { name: 'description', label: 'Details', type: 'textarea', wide: true, maxLength: 4000 },
  ], [meta]);
  const rec = useMemo(() => record && ({ ...record, owner: record.owner?.id || '', amount_involved: record.amount_involved ?? '' }), [record]);
  const defaults = useMemo(() => ({ case_type: 'legal_notice', party_role: 'respondent', status: 'open' }), []);
  return (
    <RecordDialog title={isEdit ? 'Edit matter' : 'New notice or case'} fields={fields} record={rec} defaults={defaults} onClose={onClose}
      onSave={async (payload) => {
        const saved = isEdit ? await api.patch(`/compliance/cases/${record.id}/`, payload) : await api.post('/compliance/cases/', payload);
        notify(isEdit ? 'Matter updated.' : `Matter added: ${saved.reference}`);
        onSaved(saved);
      }} />
  );
}

function CaseDetail({ id, canManage, meta, onClose, onChanged }) {
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const { data: c, error, loading, reload } = useLoad(() => api.get(`/compliance/cases/${id}/`), [id]);
  const [editing, setEditing] = useState(false);
  const remove = async () => {
    if (!(await confirm({ title: 'Delete this matter?', body: <p>Only matters that are still open can be deleted. This is recorded in the change history.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/compliance/cases/${id}/`); notify('Matter deleted.'); onChanged(); onClose(); } catch (e) { notify(e.message, 'error'); }
  };
  return (
    <Dialog title={c ? c.title : 'Matter'} onClose={onClose} wide>
      {loading && <Loading />}<ErrorBox error={error} onRetry={reload} />
      {c && (
        <div className="stack-gap">
          <div className="compliance-item-head"><Badge tone={STATUS_TONE[c.status]}>{c.status_label}</Badge>{c.hearing_state && <Badge tone={HEARING_TONE[c.hearing_state]}>Hearing {fmtDate(c.next_hearing_date)}</Badge>}<span className="muted">Ref {c.reference}</span></div>
          <dl className="summary">
            <div><dt>Kind</dt><dd>{c.case_type_label}</dd></div><div><dt>Our position</dt><dd>{c.party_role_label}</dd></div>
            <div><dt>Forum</dt><dd>{c.forum || '—'}</dd></div><div><dt>Case or notice no.</dt><dd>{c.case_number || '—'}</dd></div>
            <div><dt>Other party</dt><dd>{c.counterparty || '—'}</dd></div><div><dt>Filed or received</dt><dd>{fmtDate(c.filed_date)}</dd></div>
            <div><dt>Next hearing</dt><dd>{c.next_hearing_date ? `${fmtDate(c.next_hearing_date)} (${daysText((new Date(`${c.next_hearing_date}T00:00:00`) - new Date(`${today()}T00:00:00`)) / 86400000)})` : '—'}</dd></div>
            <div><dt>Lawyer</dt><dd>{c.lawyer || '—'}{c.lawyer_contact && <div className="hint">{c.lawyer_contact}</div>}</dd></div>
            <div><dt>Amount involved</dt><dd>{c.amount_involved ? `Rs ${plain(c.amount_involved)}` : '—'}</dd></div>
            <div><dt>Owner</dt><dd>{c.owner?.name || '—'}</dd></div>
            {c.closed_on && <div><dt>Closed on</dt><dd>{fmtDate(c.closed_on)}</dd></div>}
          </dl>
          {c.outcome && <p><strong>Outcome:</strong> {c.outcome}</p>}
          {c.description && <p>{c.description}</p>}
          {canManage && <div className="actions"><button type="button" className="btn btn-primary" onClick={() => setEditing(true)}>Edit or update</button>{(c.status === 'open' || c.status === 'in_progress') && <button type="button" className="btn btn-danger-outline" onClick={remove}>Delete</button>}</div>}
          <Attachments entityType="legal_case" entityId={c.id} canManage={canManage} title="Notices, orders and pleadings" />
        </div>
      )}
      {editing && <CaseDialog record={c} meta={meta} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); onChanged(); }} />}
      {dialog}
    </Dialog>
  );
}

export default function CasesPage() {
  const canManage = useCanManage();
  const meta = useMeta();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState('active');
  const [type, setType] = useState('');
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const [openId, setOpenId] = useState(params.get('open'));
  const [adding, setAdding] = useState(params.get('new') === '1');
  useEffect(() => { if (params.get('open') || params.get('new')) setParams({}, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const { data, error, loading, reload } = useLoad(() => api.get('/compliance/cases/', { status: status || undefined, type: type || undefined, q: term || undefined }), [status, type, term]);
  const rows = data?.results || [];
  return (
    <>
      <PageHeader title="Notices & Cases" description="Legal notices, court cases and regulatory matters: the forum, the next hearing, the lawyer and the outcome."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>New notice or case</button>} />
      <section className="card">
        <div className="chips" role="group" aria-label="Filter by status">
          {STATUSES.map(([key, label]) => <button key={key} type="button" className="chip" aria-pressed={status === key} onClick={() => setStatus(key)}>{label}</button>)}
        </div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
          {meta.data && <Pick id="cs-type" label="Kind" value={type} onChange={setType} options={opt(meta.data.case_types)} />}
          <Field id="cs-q" label="Search" className="grow"><input id="cs-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Matter, number, forum, other party or lawyer" /></Field>
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}<ErrorBox error={error} onRetry={reload} />
        {!loading && !error && rows.length === 0 && <p className="empty">No matters{term || type ? ' match' : status === 'active' ? ' are open' : ' yet'}.</p>}
        {rows.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Matter</th><th>Forum and number</th><th>Other party</th><th>Next hearing</th><th>Lawyer</th><th>Status</th></tr></thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="clickable" onClick={() => setOpenId(c.id)}>
                  <td data-label="Matter"><button type="button" className="link-button" onClick={(e) => { e.stopPropagation(); setOpenId(c.id); }}><strong>{c.title}</strong></button><div className="hint">{c.case_type_label} · {c.reference}</div></td>
                  <td data-label="Forum and number">{[c.forum, c.case_number].filter(Boolean).join(' · ') || '—'}</td>
                  <td data-label="Other party">{c.counterparty || '—'}</td>
                  <td data-label="Next hearing">{c.next_hearing_date ? <>{fmtDate(c.next_hearing_date)} {c.hearing_state && HEARING_TEXT[c.hearing_state] && <Badge tone={HEARING_TONE[c.hearing_state]}>{HEARING_TEXT[c.hearing_state]}</Badge>}</> : '—'}</td>
                  <td data-label="Lawyer">{c.lawyer || '—'}</td>
                  <td data-label="Status"><Badge tone={STATUS_TONE[c.status]}>{c.status_label}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
        {data && <div className="list-foot"><span>{data.totals.open + data.totals.in_progress} active, {data.totals.disposed + data.totals.closed} closed</span></div>}
      </section>
      {openId && meta.data && <CaseDetail id={openId} canManage={canManage} meta={meta.data} onClose={() => setOpenId(null)} onChanged={reload} />}
      {adding && meta.data && <CaseDialog meta={meta.data} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); reload(); }} />}
    </>
  );
}
