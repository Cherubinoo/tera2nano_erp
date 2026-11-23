import { useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { money, plain } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, fmtDate, useConfirm, useToast } from '../../ui';
import { ActivityDialog, DealStatus, EntityPicker, OwnerSelect, Pager, TimelineList, useLookups } from './common';
import { DealDialog } from './Deals';

export function ContactDialog({ contact, account, lookups, onClose, onSaved }) {
  const notify = useToast();
  const [v, setV] = useState({ name: contact?.name || '', designation: contact?.designation || '', email: contact?.email || '', phone: contact?.phone || '',
    notes: contact?.notes || '', is_primary: !!contact?.is_primary, owner: contact?.owner?.id || '' });
  const [acct, setAcct] = useState({ id: contact?.account?.id || account?.id || '', name: contact?.account?.name || account?.name || '' });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const submit = async (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    setFailure(null);
    const e = {};
    if (!v.name.trim()) e.name = 'Enter the contact name.';
    if (v.email && !/^\S+@\S+\.\S+$/.test(v.email)) e.email = 'Enter a valid email address.';
    setErrors(e);
    if (Object.keys(e).length) return;
    setBusy(true);
    const body = { ...v, name: v.name.trim(), account: acct.id || null, owner: v.owner || null };
    try {
      const saved = contact ? await api.patch(`/crm/contacts/${contact.id}/`, body) : await api.post('/crm/contacts/', body);
      notify(contact ? 'Contact saved.' : 'Contact created.');
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title={contact ? `Edit ${contact.name}` : 'New contact'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={[...Object.keys(v), 'account'].map((name) => ({ name }))} />
        <div className="form-grid">
          <Field id="ct-name" label="Name" required error={errors.name}><input id="ct-name" value={v.name} onChange={set('name')} maxLength={150} /></Field>
          <Field id="ct-designation" label="Designation" error={errors.designation}><input id="ct-designation" value={v.designation} onChange={set('designation')} maxLength={100} /></Field>
          <Field id="ct-email" label="Email" error={errors.email}><input id="ct-email" type="email" value={v.email} onChange={set('email')} /></Field>
          <Field id="ct-phone" label="Phone" error={errors.phone}><input id="ct-phone" value={v.phone} onChange={set('phone')} maxLength={30} /></Field>
          <EntityPicker id="ct-account" label="Account" kind="accounts" value={acct.id} valueLabel={acct.name} onChange={(id, name) => setAcct({ id, name })} error={errors.account} blank="No account" />
          <OwnerSelect id="ct-owner" label="Owner" value={v.owner} onChange={(x) => setV((s) => ({ ...s, owner: x }))} lookups={lookups} error={errors.owner} />
        </div>
        <label className="check"><input type="checkbox" checked={v.is_primary} onChange={(e) => setV((s) => ({ ...s, is_primary: e.target.checked }))} /> Primary contact of the account</label>
        <Field id="ct-notes" label="Notes" wide error={errors.notes}><textarea id="ct-notes" rows={3} value={v.notes} onChange={set('notes')} /></Field>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save contact'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export default function Contacts() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const lookups = useLookups();
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = useState(params.get('new') === '1');
  const q = params.get('q') || '', owner = params.get('owner') || '', page = Number(params.get('page') || 1);
  const [term, setTerm] = useState(q);
  const canManage = user.permissions.includes('crm.manage');
  const { data, error, loading, reload } = useLoad(() => api.get('/crm/contacts/', { q, owner, page, page_size: 50 }), [q, owner, page]);
  const setParam = (c) => setParams(Object.fromEntries(Object.entries({ q, owner, ...c }).filter(([, v]) => v)), { replace: true });
  const rows = data?.results || [];
  return (
    <>
      <PageHeader title="Contacts" description="The people you deal with at your accounts."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>New contact</button>} />
      <section className="card">
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setParam({ q: term.trim(), page: '' }); }}>
          <Field id="ct-q" label="Search" className="grow"><input id="ct-q" value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Name, email, phone, account or designation" /></Field>
          <OwnerSelect id="ct-f-owner" label="Owner" value={owner} onChange={(x) => setParam({ owner: x, page: '' })} lookups={lookups} any extra={[['me', 'Me'], ['none', 'Unassigned']]} />
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {data && rows.length === 0 && <p className="empty">{q || owner ? 'No contact matches.' : 'No contacts yet. Convert a lead or add one.'}</p>}
        {rows.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Name</th><th>Account</th><th>Email</th><th>Phone</th><th>Owner</th></tr></thead>
            <tbody>{rows.map((c) => (
              <tr key={c.id} className="clickable" onClick={() => navigate(`/crm/contacts/${c.id}`)}>
                <td data-label="Name"><Link to={`/crm/contacts/${c.id}`} onClick={(e) => e.stopPropagation()}><strong>{c.name}</strong></Link>
                  <span className="crm-sub">{c.designation}{c.is_primary ? ' · Primary' : ''}</span></td>
                <td data-label="Account">{c.account ? <Link to={`/crm/accounts/${c.account.id}`} onClick={(e) => e.stopPropagation()}>{c.account.name}</Link> : '—'}</td>
                <td data-label="Email">{c.email || '—'}</td><td data-label="Phone">{c.phone || '—'}</td>
                <td data-label="Owner">{c.owner ? c.owner.name : '—'}</td>
              </tr>))}</tbody>
          </table></div>
        )}
        {data && <Pager page={data.page} count={data.count} size={data.page_size} onPage={(p) => setParam({ page: String(p) })} />}
        {rows.length > 0 && <div className="list-foot"><span>{data.count} contact{data.count === 1 ? '' : 's'}</span></div>}
      </section>
      {creating && lookups.ready && <ContactDialog lookups={lookups} onClose={() => setCreating(false)} onSaved={(c) => { setCreating(false); navigate(`/crm/contacts/${c.id}`); }} />}
    </>
  );
}

export function ContactDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const notify = useToast();
  const lookups = useLookups();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [tab, setTab] = useState('overview');
  const [editing, setEditing] = useState(false);
  const [logging, setLogging] = useState(false);
  const [dealing, setDealing] = useState(false);
  const { data: c, error, loading, reload } = useLoad(() => api.get(`/crm/contacts/${id}/`), [id]);
  const tl = useLoad(() => api.get(`/crm/contacts/${id}/timeline/`), [id]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const canManage = user.permissions.includes('crm.manage');
  const refresh = () => { reload(); tl.reload(); };
  const remove = async () => {
    if (!(await confirm({ title: `Delete ${c.name}?`, body: <p>The contact is removed. Deals that name this contact keep working.</p>, confirmLabel: 'Delete contact', danger: true }))) return;
    try { await api.del(`/crm/contacts/${id}/`); notify('Contact deleted.'); navigate('/crm/contacts'); } catch (e) { notify(e.message, 'error'); }
  };
  const fact = (label, value) => <div><dt>{label}</dt><dd>{value || '—'}</dd></div>;
  return (
    <>
      <Link to="/crm/contacts" className="back-link">← Contacts</Link>
      <PageHeader title={c.name} description={[c.designation, c.account?.name].filter(Boolean).join(' at ') || undefined}
        actions={<>
          {c.is_primary && <Badge tone="info">Primary contact</Badge>}
          {canManage && c.account && <button type="button" className="btn btn-primary" onClick={() => setDealing(true)}>New deal</button>}
          {canManage && <button type="button" className="btn" onClick={() => setLogging(true)}>Log activity</button>}
          {canManage && <button type="button" className="btn" onClick={() => setEditing(true)}>Edit</button>}
          {canManage && <button type="button" className="btn btn-danger-outline" onClick={remove}>Delete</button>}
        </>} />
      <div className="tabs" role="tablist">
        {[['overview', 'Overview'], ['deals', `Deals (${c.deals.length})`], ['timeline', 'Timeline']].map(([k, l]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} className={`tab${tab === k ? ' active' : ''}`} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {tab === 'overview' && (
        <section className="card"><dl className="facts">
          {fact('Email', c.email && <a href={`mailto:${c.email}`}>{c.email}</a>)}{fact('Phone', c.phone)}
          {fact('Account', c.account && <Link to={`/crm/accounts/${c.account.id}`}>{c.account.name}</Link>)}
          {fact('Owner', c.owner?.name)}{fact('Added', fmtDate(c.created_at?.slice(0, 10)))}
        </dl>{c.notes && <><h3>Notes</h3><p className="crm-pre">{c.notes}</p></>}</section>
      )}
      {tab === 'deals' && (
        <section className="card">
          {c.deals.length === 0 ? <p className="empty">No deals with this contact.</p> : (
            <div className="table-wrap"><table>
              <thead><tr><th>Deal</th><th>Stage</th><th className="amount">Amount</th><th>Expected close</th></tr></thead>
              <tbody>{c.deals.map((d) => (
                <tr key={d.id}><td data-label="Deal"><Link to={`/crm/deals/${d.id}`}>{d.name}</Link><span className="crm-sub">{d.reference_code}</span></td>
                  <td data-label="Stage"><DealStatus deal={d} /></td><td className="amount" data-label="Amount">{plain(d.amount)}</td><td data-label="Expected close">{fmtDate(d.expected_close_date)}</td></tr>))}</tbody>
            </table></div>
          )}
        </section>
      )}
      {tab === 'timeline' && (
        <section className="card">{tl.loading ? <Loading /> : <TimelineList items={tl.data || []} canManage={canManage} onChanged={refresh} />}</section>
      )}
      {editing && lookups.ready && <ContactDialog contact={c} lookups={lookups} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); refresh(); }} />}
      {logging && lookups.ready && <ActivityDialog links={{ contact: id }} relatedLabel={c.name} lookups={lookups} onClose={() => setLogging(false)} onSaved={() => { setLogging(false); setTab('timeline'); refresh(); }} />}
      {dealing && lookups.ready && <DealDialog account={c.account} contact={{ id: c.id, name: c.name }} lookups={lookups} onClose={() => setDealing(false)} onSaved={(d) => { setDealing(false); navigate(`/crm/deals/${d.id}`); }} />}
      {confirmDialog}
    </>
  );
}
