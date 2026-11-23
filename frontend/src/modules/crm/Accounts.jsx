import { useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { useAuth } from '../../context';
import { money, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, Tabs, fmtDate, useConfirm, useToast } from '../../ui';
import { ActivityDialog, DealStatus, OwnerSelect, Pager, TimelineList, Tile, useLookups } from './common';
import { ContactDialog } from './Contacts';
import { DealDialog } from './Deals';

const BLANK = { name: '', industry: '', website: '', email: '', phone: '', gstin: '', address: '', city: '', state: '', notes: '' };

export function AccountDialog({ account, lookups, onClose, onSaved }) {
  const notify = useToast();
  const [v, setV] = useState(() => ({ ...Object.fromEntries(Object.keys(BLANK).map((k) => [k, account?.[k] ?? ''])), owner: account?.owner?.id || '' }));
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: k === 'gstin' ? e.target.value.toUpperCase() : e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const text = (k, label, props = {}) => (
    <Field id={`ac-${k}`} label={label} error={errors[k]} required={props.required}><input id={`ac-${k}`} value={v[k]} onChange={set(k)} {...props} /></Field>
  );
  const submit = async (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    setFailure(null);
    const e = {};
    if (!v.name.trim()) e.name = 'Enter the account (company) name.';
    if (v.email && !/^\S+@\S+\.\S+$/.test(v.email)) e.email = 'Enter a valid email address.';
    if (v.gstin && !/^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/.test(v.gstin.trim())) e.gstin = 'A GSTIN has 15 characters, such as 29ABCDE1234F1Z5.';
    setErrors(e);
    if (Object.keys(e).length) return;
    setBusy(true);
    try {
      const body = { ...v, name: v.name.trim(), owner: v.owner || null };
      const saved = account ? await api.patch(`/crm/accounts/${account.id}/`, body) : await api.post('/crm/accounts/', body);
      notify(account ? 'Account saved.' : 'Account created.');
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title={account ? `Edit ${account.name}` : 'New account'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={[...Object.keys(BLANK), 'owner'].map((name) => ({ name }))} />
        <div className="form-grid">
          {text('name', 'Account name', { required: true, maxLength: 200 })}
          {text('industry', 'Industry', { maxLength: 100 })}
          {text('website', 'Website', { maxLength: 200, placeholder: 'https://' })}
          {text('email', 'Email', { type: 'email' })}
          {text('phone', 'Phone', { maxLength: 30 })}
          {text('gstin', 'GSTIN', { maxLength: 15 })}
          {text('city', 'City', { maxLength: 100 })}
          {text('state', 'State', { maxLength: 100 })}
          <OwnerSelect id="ac-owner" label="Owner" value={v.owner} onChange={(x) => setV((s) => ({ ...s, owner: x }))} lookups={lookups} error={errors.owner} />
        </div>
        <Field id="ac-address" label="Address" wide error={errors.address}><textarea id="ac-address" rows={2} value={v.address} onChange={set('address')} /></Field>
        <Field id="ac-notes" label="Notes" wide error={errors.notes}><textarea id="ac-notes" rows={2} value={v.notes} onChange={set('notes')} /></Field>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save account'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export default function Accounts() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const lookups = useLookups();
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = useState(params.get('new') === '1');
  const q = params.get('q') || '', owner = params.get('owner') || '', active = params.get('active') || 'true', customer = params.get('customer') || '', page = Number(params.get('page') || 1);
  const [term, setTerm] = useState(q);
  const canManage = user.permissions.includes('crm.manage');
  const { data, error, loading, reload } = useLoad(() => api.get('/crm/accounts/', { q, owner, active, customer, page, page_size: 50 }), [q, owner, active, customer, page]);
  const setParam = (c) => setParams(Object.fromEntries(Object.entries({ q, owner, active, customer, ...c }).filter(([, v]) => v)), { replace: true });
  const rows = data?.results || [];
  return (
    <>
      <PageHeader title="Accounts" description="The companies you sell to. A sales customer is linked once a deal is won."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>New account</button>} />
      <section className="card">
        <div className="chips" role="group" aria-label="Filter accounts">
          {[['true', 'Active'], ['false', 'Switched off']].map(([k, l]) => <button key={k} type="button" className="chip" aria-pressed={active === k} onClick={() => setParam({ active: k, page: '' })}>{l}</button>)}
          {[['', 'Any'], ['yes', 'Customers'], ['no', 'Not yet customers']].map(([k, l]) => <button key={k || 'any'} type="button" className="chip" aria-pressed={customer === k} onClick={() => setParam({ customer: k, page: '' })}>{l}</button>)}
        </div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setParam({ q: term.trim(), page: '' }); }}>
          <Field id="ac-q" label="Search" className="grow"><input id="ac-q" value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Name, industry, city, email or phone" /></Field>
          <OwnerSelect id="ac-f-owner" label="Owner" value={owner} onChange={(x) => setParam({ owner: x, page: '' })} lookups={lookups} any extra={[['me', 'Me'], ['none', 'Unassigned']]} />
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {data && rows.length === 0 && <p className="empty">{q || owner || customer ? 'No account matches.' : 'No accounts yet. Convert a lead or add one.'}</p>}
        {rows.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Account</th><th>Industry</th><th>City</th><th className="amount">Contacts</th><th className="amount">Open deals</th><th className="amount">Open value</th><th>Owner</th></tr></thead>
            <tbody>{rows.map((a) => (
              <tr key={a.id} className="clickable" onClick={() => navigate(`/crm/accounts/${a.id}`)}>
                <td data-label="Account"><Link to={`/crm/accounts/${a.id}`} onClick={(e) => e.stopPropagation()}><strong>{a.name}</strong></Link>
                  <span className="crm-sub">{a.customer_id ? 'Customer' : 'Prospect'}{!a.is_active ? ' · Switched off' : ''}</span></td>
                <td data-label="Industry">{a.industry || '—'}</td><td data-label="City">{a.city || '—'}</td>
                <td className="amount" data-label="Contacts">{a.contacts}</td><td className="amount" data-label="Open deals">{a.open_deals}</td>
                <td className="amount" data-label="Open value">{num(a.open_value) ? plain(a.open_value) : '—'}</td>
                <td data-label="Owner">{a.owner ? a.owner.name : '—'}</td>
              </tr>))}</tbody>
          </table></div>
        )}
        {data && <Pager page={data.page} count={data.count} size={data.page_size} onPage={(p) => setParam({ page: String(p) })} />}
        {rows.length > 0 && <div className="list-foot"><span>{data.count} account{data.count === 1 ? '' : 's'}</span></div>}
      </section>
      {creating && lookups.ready && <AccountDialog lookups={lookups} onClose={() => setCreating(false)} onSaved={(a) => { setCreating(false); navigate(`/crm/accounts/${a.id}`); }} />}
    </>
  );
}

export function AccountDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const notify = useToast();
  const lookups = useLookups();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [tab, setTab] = useState('overview');
  const [dialog, setDialog] = useState(null);
  const { data: a, error, loading, reload } = useLoad(() => api.get(`/crm/accounts/${id}/`), [id]);
  const tl = useLoad(() => api.get(`/crm/accounts/${id}/timeline/`), [id]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const canManage = user.permissions.includes('crm.manage');
  const refresh = () => { reload(); tl.reload(); };
  const toggleActive = async () => {
    try { await api.patch(`/crm/accounts/${id}/`, { is_active: !a.is_active }); notify(a.is_active ? 'Account switched off.' : 'Account switched on.'); refresh(); } catch (e) { notify(e.message, 'error'); }
  };
  const remove = async () => {
    if (!(await confirm({ title: `Delete ${a.name}?`, body: <p>Only an account with no deals and no customer link can be deleted. Otherwise switch it off.</p>, confirmLabel: 'Delete account', danger: true }))) return;
    try { await api.del(`/crm/accounts/${id}/`); notify('Account deleted.'); navigate('/crm/accounts'); } catch (e) { notify(e.message, 'error'); }
  };
  const fact = (label, value) => <div><dt>{label}</dt><dd>{value || '—'}</dd></div>;
  const tabs = [['overview', 'Overview'], ['contacts', `Contacts (${a.contacts.length})`], ['deals', `Deals (${a.deals.length})`], ['activities', 'Activities'], ['quotations', `Quotations (${a.quotations.length})`], ['documents', 'Documents']];
  const wonValue = a.deals.filter((d) => d.status === 'won').reduce((s, d) => s + num(d.amount), 0);
  return (
    <>
      <Link to="/crm/accounts" className="back-link">← Accounts</Link>
      <PageHeader title={a.name} description={[a.industry, a.city].filter(Boolean).join(' · ') || undefined}
        actions={<>
          {!a.is_active && <Badge>Switched off</Badge>}
          {canManage && a.is_active && <button type="button" className="btn btn-primary" onClick={() => setDialog('deal')}>New deal</button>}
          {canManage && <button type="button" className="btn" onClick={() => setDialog('contact')}>New contact</button>}
          {canManage && <button type="button" className="btn" onClick={() => setDialog('activity')}>Log activity</button>}
          {canManage && <button type="button" className="btn" onClick={() => setDialog('edit')}>Edit</button>}
          {canManage && <button type="button" className="btn" onClick={toggleActive}>{a.is_active ? 'Switch off' : 'Switch on'}</button>}
          {canManage && <button type="button" className="btn btn-danger-outline" onClick={remove}>Delete</button>}
        </>} />
      <div className="tiles">
        <Tile label="Open deals" value={a.open_deals} hint={money(a.open_value)} />
        <Tile label="Won deals" value={a.won_deals} hint={money(wonValue)} tone={a.won_deals ? 'good' : ''} />
        <Tile label="Contacts" value={a.contacts.length} />
        <Tile label="Sales customer" value={a.customer ? a.customer.reference_code : 'Not yet'} hint={a.customer ? a.customer.name : 'Created when a deal is won'} to={a.customer ? `/sales/customers/${a.customer.id}` : undefined} />
      </div>
      <Tabs tabs={tabs.map(([key, label]) => ({ key, label }))} value={tab} onChange={setTab} />
      {tab === 'overview' && (
        <section className="card"><dl className="facts">
          {fact('Email', a.email && <a href={`mailto:${a.email}`}>{a.email}</a>)}{fact('Phone', a.phone)}{fact('Website', a.website)}
          {fact('GSTIN', a.gstin)}{fact('Address', a.address)}{fact('State', a.state)}{fact('Owner', a.owner?.name)}{fact('Created', fmtDate(a.created_at?.slice(0, 10)))}
        </dl>{a.notes && <><h3>Notes</h3><p className="crm-pre">{a.notes}</p></>}</section>
      )}
      {tab === 'contacts' && (
        <section className="card">
          {a.contacts.length === 0 ? <p className="empty">No contacts yet.</p> : (
            <div className="table-wrap"><table>
              <thead><tr><th>Name</th><th>Email</th><th>Phone</th></tr></thead>
              <tbody>{a.contacts.map((c) => (
                <tr key={c.id}><td data-label="Name"><Link to={`/crm/contacts/${c.id}`}>{c.name}</Link>{c.is_primary && <> <Badge tone="info">Primary</Badge></>}<span className="crm-sub">{c.designation}</span></td>
                  <td data-label="Email">{c.email || '—'}</td><td data-label="Phone">{c.phone || '—'}</td></tr>))}</tbody>
            </table></div>
          )}
        </section>
      )}
      {tab === 'deals' && (
        <section className="card">
          {a.deals.length === 0 ? <p className="empty">No deals yet.</p> : (
            <div className="table-wrap"><table>
              <thead><tr><th>Deal</th><th>Stage</th><th className="amount">Amount</th><th>Expected close</th><th>Owner</th></tr></thead>
              <tbody>{a.deals.map((d) => (
                <tr key={d.id}><td data-label="Deal"><Link to={`/crm/deals/${d.id}`}>{d.name}</Link><span className="crm-sub">{d.reference_code}</span></td>
                  <td data-label="Stage"><DealStatus deal={d} /></td><td className="amount" data-label="Amount">{plain(d.amount)}</td>
                  <td data-label="Expected close">{fmtDate(d.expected_close_date)}</td><td data-label="Owner">{d.owner?.name || '—'}</td></tr>))}</tbody>
            </table></div>
          )}
        </section>
      )}
      {tab === 'activities' && (
        <section className="card">{tl.loading ? <Loading /> : <TimelineList items={tl.data || []} canManage={canManage} onChanged={refresh} />}</section>
      )}
      {tab === 'quotations' && (
        <section className="card">
          {a.quotations.length === 0 ? <p className="empty">No quotation has been issued to this account yet.</p> : (
            <div className="table-wrap"><table>
              <thead><tr><th>Number</th><th>Date</th><th>Title</th><th>Status</th><th className="amount">Total</th></tr></thead>
              <tbody>{a.quotations.map((q) => (
                <tr key={q.id}><td data-label="Number">{q.number}</td><td data-label="Date">{fmtDate(q.issue_date)}</td><td data-label="Title">{q.title || '—'}</td>
                  <td data-label="Status">{q.status}</td><td className="amount" data-label="Total">{q.total === null ? '—' : plain(q.total)}</td></tr>))}</tbody>
            </table></div>
          )}
        </section>
      )}
      {tab === 'documents' && <section className="card"><Attachments entityType="crm_account" entityId={id} canManage={canManage} title="Account documents" hint="Agreements, NDAs, registration documents." /></section>}

      {dialog === 'edit' && <AccountDialog account={a} lookups={lookups} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); refresh(); }} />}
      {dialog === 'contact' && lookups.ready && <ContactDialog account={a} lookups={lookups} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); setTab('contacts'); refresh(); }} />}
      {dialog === 'deal' && lookups.ready && <DealDialog account={a} lookups={lookups} onClose={() => setDialog(null)} onSaved={(d) => { setDialog(null); navigate(`/crm/deals/${d.id}`); }} />}
      {dialog === 'activity' && lookups.ready && <ActivityDialog links={{ account: id }} relatedLabel={a.name} lookups={lookups} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); setTab('activities'); refresh(); }} />}
      {confirmDialog}
    </>
  );
}
