import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, Loading, PageHeader, Tabs, fmtDate, useConfirm, useToast } from '../../ui';
import { Pick, RecordDialog, opt, useCanManage, useMeta } from './common';

const FILING_TYPES = ['partner_change', 'name_change', 'agreement_amendment', 'address_change'];

function Registry() {
  const { data: r, error, loading, reload } = useLoad(() => api.get('/compliance/registry/'), []);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <>
      <section className="card" aria-label="LLP master record">
        <div className="card-head"><h2>{r.legal_name}</h2><Link to="/company/legal">Edit in company profile</Link></div>
        <p className="muted">Read from the company profile. Change these details there, not here.</p>
        <dl className="summary">
          <div><dt>Entity type</dt><dd>{r.entity_type || '—'}</dd></div>
          <div><dt>LLPIN</dt><dd>{r.llpin || '—'}</dd></div>
          <div><dt>Date of incorporation</dt><dd>{fmtDate(r.date_of_incorporation)}</dd></div>
          <div><dt>Incorporation certificate</dt><dd>{r.certificate_reference || '—'}</dd></div>
          <div><dt>PAN</dt><dd>{r.pan || '—'}</dd></div>
          <div><dt>TAN</dt><dd>{r.tan || '—'}</dd></div>
          <div><dt>GSTIN</dt><dd>{r.gstin || '—'}</dd></div>
          <div><dt>Registered office</dt><dd>{r.registered_office || '—'}</dd></div>
          <div><dt>Designated partners</dt><dd>{r.designated_partners}{r.designated_partners < 2 && <span className="hint"> An LLP needs at least two.</span>}</dd></div>
        </dl>
      </section>
      <section className="card" aria-label="Partners">
        <div className="card-head"><h2>Partners</h2><Link to="/company/partners">Manage partners</Link></div>
        {r.partners.length === 0 ? <p className="empty">No partners recorded.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Name</th><th>Role</th><th>DPIN</th><th>Joined</th><th>Ceased</th></tr></thead>
            <tbody>{r.partners.map((p) => (
              <tr key={p.id} className={p.active ? '' : 'row-inactive'}>
                <td data-label="Name"><strong>{p.name}</strong></td>
                <td data-label="Role">{p.designated ? <Badge tone="info">Designated partner</Badge> : p.type}</td>
                <td data-label="DPIN">{p.dpin || '—'}</td><td data-label="Joined">{fmtDate(p.appointed)}</td><td data-label="Ceased">{fmtDate(p.ceased)}</td>
              </tr>))}
            </tbody>
          </table></div>
        )}
      </section>
    </>
  );
}

function EventDialog({ record, meta, onClose, onSaved }) {
  const notify = useToast();
  const isEdit = !!record;
  const fields = useMemo(() => [
    { name: 'event_type', label: 'Kind of event', type: 'select', required: true, options: opt(meta.event_types) },
    { name: 'title', label: 'What happened', type: 'text', required: true, maxLength: 200, wide: true },
    { name: 'event_date', label: 'Date', type: 'date', required: true, max: today() },
    { name: 'form_no', label: 'Form number', type: 'text', maxLength: 60, placeholder: 'LLP Form 4' },
    { name: 'srn', label: 'SRN', type: 'text', maxLength: 60, hint: 'Service request number from the MCA portal.' },
    { name: 'description', label: 'Details', type: 'textarea', wide: true, maxLength: 4000 },
    ...(isEdit ? [] : [{ name: 'create_filing', label: 'Also add the filing due 30 days after this event to the calendar', type: 'checkbox', wide: true,
      hidden: (v) => !FILING_TYPES.includes(v.event_type), hint: 'Form 4 for partners, Form 5 for a name change, Form 3 for the agreement, Form 15 for the office.' }]),
  ], [meta, isEdit]);
  const defaults = useMemo(() => ({ event_type: 'partner_change', event_date: today() }), []);
  return (
    <RecordDialog title={isEdit ? 'Edit legal event' : 'New legal event'} fields={fields} record={record} defaults={defaults} wide={false} onClose={onClose}
      onSave={async (payload) => {
        if (!FILING_TYPES.includes(payload.event_type)) delete payload.create_filing;
        const saved = isEdit ? await api.patch(`/compliance/legal-events/${record.id}/`, payload) : await api.post('/compliance/legal-events/', payload);
        notify(saved.filing ? `Event recorded. Filing due ${fmtDate(saved.filing.due_date)} added to the calendar.` : 'Event saved.');
        onSaved(saved);
      }} />
  );
}

function EventDetail({ id, canManage, meta, onClose, onChanged }) {
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const { data: e, error, loading, reload } = useLoad(() => api.get(`/compliance/legal-events/${id}/`), [id]);
  const [editing, setEditing] = useState(false);
  const remove = async () => {
    if (!(await confirm({ title: 'Delete this event?', body: <p>It is removed from the timeline. This is recorded in the change history.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/compliance/legal-events/${id}/`); notify('Event deleted.'); onChanged(); onClose(); } catch (err) { notify(err.message, 'error'); }
  };
  return (
    <Dialog title={e ? e.title : 'Legal event'} onClose={onClose} wide>
      {loading && <Loading />}<ErrorBox error={error} onRetry={reload} />
      {e && (
        <div className="stack-gap">
          <dl className="summary">
            <div><dt>Kind</dt><dd>{e.event_type_label}</dd></div><div><dt>Date</dt><dd>{fmtDate(e.event_date)}</dd></div>
            <div><dt>Form</dt><dd>{e.form_no || '—'}</dd></div><div><dt>SRN</dt><dd>{e.srn || '—'}</dd></div>
            <div><dt>Reference</dt><dd>{e.reference}</dd></div><div><dt>Source</dt><dd>{e.imported ? 'Imported from company records' : 'Entered by hand'}</dd></div>
          </dl>
          {e.description && <p>{e.description}</p>}
          {canManage && <div className="actions"><button type="button" className="btn" onClick={() => setEditing(true)}>Edit</button><button type="button" className="btn btn-danger-outline" onClick={remove}>Delete</button></div>}
          <Attachments entityType="legal_event" entityId={e.id} canManage={canManage} title="Documents" hint="Certificates, filed forms and acknowledgements." />
        </div>
      )}
      {editing && <EventDialog record={e} meta={meta} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); onChanged(); }} />}
      {dialog}
    </Dialog>
  );
}

export default function LegalEventsPage() {
  const canManage = useCanManage();
  const notify = useToast();
  const meta = useMeta();
  const [tab, setTab] = useState('timeline');
  const [type, setType] = useState('');
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const [openId, setOpenId] = useState(null);
  const [adding, setAdding] = useState(false);
  const { data, error, loading, reload } = useLoad(() => api.get('/compliance/legal-events/', { type: type || undefined, q: term || undefined }), [type, term]);
  const rows = data?.results || [];
  const importHistory = async () => {
    try {
      const r = await api.post('/compliance/legal-events/import-history/');
      notify(r.created ? `${r.created} event${r.created === 1 ? '' : 's'} imported from the company records.` : 'Nothing new to import: the timeline already has everything.');
      reload();
    } catch (e) { notify(e.message, 'error'); }
  };
  return (
    <>
      <PageHeader title="Legal Events" description="The LLP's legal history: incorporation, changes of name, partners and address, agreement amendments and charges."
        actions={canManage && (<><button type="button" className="btn" onClick={importHistory}>Import from company records</button><button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>New event</button></>)} />
      <Tabs tabs={[{ key: 'timeline', label: 'Timeline' }, { key: 'master', label: 'LLP master record' }]} value={tab} onChange={setTab} />
      {tab === 'master' && <Registry />}
      {tab === 'timeline' && (
        <section className="card">
          <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
            {meta.data && <Pick id="le-type" label="Kind" value={type} onChange={setType} options={opt(meta.data.event_types)} />}
            <Field id="le-q" label="Search" className="grow"><input id="le-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Title, form, SRN or details" /></Field>
            <button type="submit" className="btn">Search</button>
          </form>
          {loading && <Loading />}<ErrorBox error={error} onRetry={reload} />
          {!loading && rows.length === 0 && <p className="empty">No events yet.{canManage && ' Use "Import from company records" to start from the profile and partner history.'}</p>}
          <ol className="compliance-timeline">
            {rows.map((e) => (
              <li key={e.id}>
                <span className="compliance-timeline-date">{fmtDate(e.event_date)}</span>
                <div>
                  <button type="button" className="link-button" onClick={() => setOpenId(e.id)}><strong>{e.title}</strong></button> <Badge tone="info">{e.event_type_label}</Badge>
                  <div className="hint">{[e.form_no, e.srn && `SRN ${e.srn}`, e.imported && 'imported'].filter(Boolean).join(' · ')}</div>
                  {e.description && <div className="muted">{e.description}</div>}
                </div>
              </li>
            ))}
          </ol>
          {rows.length > 0 && <div className="list-foot"><span>{rows.length} event{rows.length === 1 ? '' : 's'}</span></div>}
        </section>
      )}
      {openId && meta.data && <EventDetail id={openId} canManage={canManage} meta={meta.data} onClose={() => setOpenId(null)} onChanged={reload} />}
      {adding && meta.data && <EventDialog meta={meta.data} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); reload(); }} />}
    </>
  );
}
