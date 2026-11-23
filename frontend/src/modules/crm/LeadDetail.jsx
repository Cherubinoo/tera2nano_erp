import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { useAuth } from '../../context';
import { money, num } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, Tabs, fmtDate, fmtDateTime, useConfirm, useToast } from '../../ui';
import { ActivityDialog, LeadStatus, OwnerSelect, RatingBadge, TimelineList, Tile, useLookups } from './common';
import { DuplicateList, LeadDialog } from './Leads';

function ConvertDialog({ lead, lookups, onClose, onDone }) {
  const notify = useToast();
  const [v, setV] = useState({ create_deal: true, deal_name: `${lead.display_name} - ${lead.product_interest || 'opportunity'}`.slice(0, 200), amount: lead.estimated_value || '',
    expected_close_date: '', stage: '', owner: lead.owner?.id || '' });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const openStages = (lookups.stages || []).filter((s) => s.kind === 'open');
  const submit = async (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    setFailure(null);
    if (v.create_deal && v.amount !== '' && !(num(v.amount) >= 0)) { setErrors({ amount: 'Enter an amount of 0 or more.' }); return; }
    setBusy(true);
    try {
      const r = await api.post(`/crm/leads/${lead.id}/convert/`, {
        create_deal: v.create_deal, deal_name: v.deal_name, amount: v.amount === '' ? undefined : v.amount, expected_close_date: v.expected_close_date || undefined,
        stage: v.stage || undefined, owner: v.owner || undefined });
      notify(`${lead.display_name} converted.`);
      onDone(r);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title={`Convert ${lead.display_name}`} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <p className="muted">This creates a contact and an account (company) from the lead. A sales customer is created only when a deal is won.</p>
        <FormErrors error={failure} fields={Object.keys(v).map((name) => ({ name }))} />
        <label className="check"><input type="checkbox" checked={v.create_deal} onChange={(e) => setV((x) => ({ ...x, create_deal: e.target.checked }))} /> Also create a deal</label>
        {v.create_deal && (
          <div className="form-grid">
            <Field id="cv-name" label="Deal name" error={errors.deal_name} wide><input id="cv-name" value={v.deal_name} onChange={set('deal_name')} maxLength={200} /></Field>
            <Field id="cv-amount" label="Amount (INR)" error={errors.amount}><input id="cv-amount" type="number" min="0" step="0.01" value={v.amount} onChange={set('amount')} /></Field>
            <Field id="cv-close" label="Expected close" error={errors.expected_close_date}><input id="cv-close" type="date" value={v.expected_close_date} onChange={set('expected_close_date')} /></Field>
            <Field id="cv-stage" label="Starting stage" error={errors.stage}>
              <select id="cv-stage" value={v.stage} onChange={set('stage')}><option value="">First stage</option>{openStages.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
            </Field>
            <OwnerSelect id="cv-owner" label="Deal owner" value={v.owner} onChange={(x) => setV((s) => ({ ...s, owner: x }))} lookups={lookups} error={errors.owner} />
          </div>
        )}
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Converting…' : 'Convert lead'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export default function LeadDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const notify = useToast();
  const lookups = useLookups();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [tab, setTab] = useState('overview');
  const [editing, setEditing] = useState(false);
  const [converting, setConverting] = useState(false);
  const [logging, setLogging] = useState(false);
  const [converted, setConverted] = useState(null);
  const { data: l, error, loading, reload } = useLoad(() => api.get(`/crm/leads/${id}/`), [id]);
  const tl = useLoad(() => api.get(`/crm/leads/${id}/timeline/`), [id]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const canManage = user.permissions.includes('crm.manage');
  const done = l.status === 'converted';
  const refresh = () => { reload(); tl.reload(); };
  const setStatus = async (status) => {
    try { await api.patch(`/crm/leads/${id}/`, { status }); notify('Status updated.'); refresh(); } catch (e) { notify(e.message, 'error'); }
  };
  const remove = async () => {
    if (!(await confirm({ title: `Delete ${l.display_name}?`, body: <p>The lead and its activities are removed.</p>, confirmLabel: 'Delete lead', danger: true }))) return;
    try { await api.del(`/crm/leads/${id}/`); notify('Lead deleted.'); navigate('/crm/leads'); } catch (e) { notify(e.message, 'error'); }
  };
  const fact = (label, value) => <div><dt>{label}</dt><dd>{value || '—'}</dd></div>;
  const tabs = [['overview', 'Overview'], ['timeline', 'Timeline'], ['documents', 'Documents']];
  const statuses = (lookups.lead_statuses || []).filter((s) => s.value !== 'converted');
  return (
    <>
      <Link to="/crm/leads" className="back-link">← Leads</Link>
      <PageHeader title={l.display_name} description={`${l.reference_code}${l.contact_name && l.company_name ? ` · ${l.contact_name}` : ''}`}
        actions={<>
          <LeadStatus lead={l} /><RatingBadge lead={l} />
          {canManage && !done && <button type="button" className="btn btn-primary" onClick={() => setConverting(true)}>Convert</button>}
          {canManage && <button type="button" className="btn" onClick={() => setLogging(true)}>Log activity</button>}
          {canManage && !done && <button type="button" className="btn" onClick={() => setEditing(true)}>Edit</button>}
          {canManage && !done && (
            <select aria-label="Change status" value={l.status} onChange={(e) => setStatus(e.target.value)}>
              {statuses.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          )}
          {canManage && !done && <button type="button" className="btn btn-danger-outline" onClick={remove}>Delete</button>}
        </>} />

      {done && l.converted && (
        <div className="notice notice-success" role="status">
          Converted {fmtDateTime(l.converted_at)}:{' '}
          {l.converted.account && <Link to={`/crm/accounts/${l.converted.account.id}`}>account {l.converted.account.name}</Link>}
          {l.converted.contact && <>, <Link to={`/crm/contacts/${l.converted.contact.id}`}>contact {l.converted.contact.name}</Link></>}
          {l.converted.deal && <>, <Link to={`/crm/deals/${l.converted.deal.id}`}>deal {l.converted.deal.name}</Link></>}.
        </div>
      )}
      {converted && (
        <div className="notice notice-success" role="status">
          Created <Link to={`/crm/accounts/${converted.account.id}`}>account {converted.account.name}</Link>, <Link to={`/crm/contacts/${converted.contact.id}`}>contact {converted.contact.name}</Link>
          {converted.deal && <>, and <Link to={`/crm/deals/${converted.deal.id}`}>deal {converted.deal.reference_code}</Link></>}.
        </div>
      )}
      {l.duplicates?.length > 0 && !done && (
        <div className="notice notice-warn" role="status"><div><strong>Possible duplicates</strong><DuplicateList duplicates={l.duplicates} /></div></div>
      )}

      <div className="tiles">
        <Tile label="Lead score" value={`${l.score} / 100`} hint={`Suggested rating: ${l.suggested_rating}`} tone={l.score >= 60 ? 'good' : ''} />
        <Tile label="Estimated value" value={num(l.estimated_value) ? money(l.estimated_value) : '—'} />
        <Tile label="Source" value={l.source_label} />
        <Tile label="Owner" value={l.owner ? l.owner.name : 'Unassigned'} />
      </div>

      <Tabs tabs={tabs.map(([key, label]) => ({ key, label }))} value={tab} onChange={setTab} />
      {tab === 'overview' && (
        <div className="grid-2">
          <section className="card" aria-label="Lead details">
            <h2>Details</h2>
            <dl className="facts">
              {fact('Company', l.company_name)}{fact('Contact person', l.contact_name)}{fact('Email', l.email && <a href={`mailto:${l.email}`}>{l.email}</a>)}
              {fact('Phone', l.phone)}{fact('Industry', l.industry)}{fact('Interested in', l.product_interest)}
              {fact('City', l.city)}{fact('State', l.state)}{fact('Created', fmtDate(l.created_at?.slice(0, 10)))}
            </dl>
            {l.notes && <><h3>Notes</h3><p className="crm-pre">{l.notes}</p></>}
          </section>
          <section className="card" aria-label="How the score is worked out">
            <h2>Score</h2>
            <p className="muted">Out of 100: source, estimated value, completed calls and meetings, and contact details.</p>
            <table className="crm-mini">
              <caption className="sr-only">Score breakdown</caption>
              <tbody>
                {(l.score_breakdown || []).map((p) => <tr key={p.label}><td data-label="Factor">{p.label}</td><td className="amount" data-label="Points">+{p.points}</td></tr>)}
                <tr className="total"><th>Total</th><td className="amount"><strong>{l.score}</strong></td></tr>
              </tbody>
            </table>
          </section>
        </div>
      )}
      {tab === 'timeline' && (
        <section className="card" aria-label="Timeline">
          <div className="crm-head"><h2>Timeline</h2>{canManage && <button type="button" className="btn btn-small btn-primary" onClick={() => setLogging(true)}>Add activity</button>}</div>
          {tl.loading ? <Loading /> : tl.error ? <ErrorBox error={tl.error} onRetry={tl.reload} /> : <TimelineList items={tl.data || []} canManage={canManage} onChanged={refresh} />}
        </section>
      )}
      {tab === 'documents' && (
        <section className="card"><Attachments entityType="crm_lead" entityId={id} canManage={canManage} title="Lead documents" hint="Enquiries, brochures sent, requirement notes." /></section>
      )}

      {editing && lookups.ready && <LeadDialog lead={l} lookups={lookups} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); refresh(); }} />}
      {converting && lookups.ready && <ConvertDialog lead={l} lookups={lookups} onClose={() => setConverting(false)} onDone={(r) => { setConverting(false); setConverted(r); refresh(); }} />}
      {logging && lookups.ready && <ActivityDialog links={{ lead: id }} relatedLabel={l.display_name} lookups={lookups} onClose={() => setLogging(false)} onSaved={() => { setLogging(false); setTab('timeline'); refresh(); }} />}
      {confirmDialog}
    </>
  );
}
