import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth, useLookups } from '../../context';
import { money, plain } from '../../format';
import { asList, useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, Tabs, fmtDate, useReasonDialog, useToast } from '../../ui';
import { StatusBadge } from './common';

const BLANK = {
  display_name: '', legal_name: '', customer_type: 'business', gst_treatment: 'unregistered', gstin: '', pan: '', contact_name: '',
  email: '', phone: '', address_line1: '', address_line2: '', city: '', state_code: '', postal_code: '', country: 'India',
  payment_terms_days: '', notes: '',
};

export function CustomerDialog({ record, initial, onClose, onSaved }) {
  const lookups = useLookups();
  const notify = useToast();
  const isEdit = !!record;
  const [v, setV] = useState(() => (record ? Object.fromEntries(Object.keys(BLANK).map((k) => [k, record[k] ?? ''])) : { ...BLANK, ...(initial || {}) }));
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };

  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setError(null);
    if (!v.display_name.trim()) { setErrors({ display_name: 'Enter the customer name.' }); return; }
    setBusy(true);
    try {
      const body = { ...v, gstin: v.gstin.trim().toUpperCase(), pan: v.pan.trim().toUpperCase() };
      const saved = isEdit ? await api.patch(`/sales/customers/${record.id}/`, body) : await api.post('/sales/customers/', body);
      notify(isEdit ? 'Customer updated.' : `Customer added: ${saved.reference_code}`);
      onSaved(saved);
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };

  return (
    <Dialog title={isEdit ? `Edit ${record.display_name}` : 'New customer'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[]} />
        <div className="form-grid">
          <Field id="c-name" label="Customer name" required error={errors.display_name} wide hint="How the customer appears in lists and on the invoice.">
            <input id="c-name" value={v.display_name} maxLength={200} onChange={set('display_name')} />
          </Field>
          <Field id="c-legal" label="Legal name (if different)" error={errors.legal_name} wide hint="Printed on invoices in place of the name above.">
            <input id="c-legal" value={v.legal_name} maxLength={200} onChange={set('legal_name')} />
          </Field>
          <Field id="c-type" label="Type" error={errors.customer_type}>
            <select id="c-type" value={v.customer_type} onChange={set('customer_type')}><option value="business">Business</option><option value="individual">Individual</option></select>
          </Field>
          <Field id="c-gst" label="GST treatment" error={errors.gst_treatment}>
            <select id="c-gst" value={v.gst_treatment} onChange={set('gst_treatment')}>
              <option value="registered">Registered business (has a GSTIN)</option><option value="unregistered">Unregistered business or consumer</option><option value="overseas">Overseas</option>
            </select>
          </Field>
          <Field id="c-gstin" label="GSTIN" error={errors.gstin} required={v.gst_treatment === 'registered'}>
            <input id="c-gstin" value={v.gstin} maxLength={15} autoComplete="off" onChange={set('gstin')} />
          </Field>
          <Field id="c-pan" label="PAN" error={errors.pan}><input id="c-pan" value={v.pan} maxLength={10} onChange={set('pan')} /></Field>
          <Field id="c-contact" label="Contact person" error={errors.contact_name}><input id="c-contact" value={v.contact_name} onChange={set('contact_name')} /></Field>
          <Field id="c-email" label="Email" error={errors.email}><input id="c-email" type="email" value={v.email} onChange={set('email')} /></Field>
          <Field id="c-phone" label="Phone" error={errors.phone}><input id="c-phone" value={v.phone} onChange={set('phone')} /></Field>
          <Field id="c-terms" label="Payment terms (days)" error={errors.payment_terms_days} hint="Invoices fall due this many days after their date. Leave empty to use the company default.">
            <input id="c-terms" type="number" min="0" max="365" value={v.payment_terms_days} onChange={set('payment_terms_days')} />
          </Field>
          <Field id="c-a1" label="Address line 1" error={errors.address_line1} wide><input id="c-a1" value={v.address_line1} onChange={set('address_line1')} /></Field>
          <Field id="c-a2" label="Address line 2" error={errors.address_line2} wide><input id="c-a2" value={v.address_line2} onChange={set('address_line2')} /></Field>
          <Field id="c-city" label="City" error={errors.city}><input id="c-city" value={v.city} onChange={set('city')} /></Field>
          <Field id="c-state" label="State (place of supply)" error={errors.state_code} hint="Decides CGST + SGST or IGST. Taken from the GSTIN when you leave it empty.">
            <select id="c-state" value={v.state_code} onChange={set('state_code')}>
              <option value="">— from GSTIN —</option>
              {lookups.gst_states.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </Field>
          <Field id="c-pin" label="PIN code" error={errors.postal_code}><input id="c-pin" value={v.postal_code} maxLength={10} onChange={set('postal_code')} /></Field>
          <Field id="c-notes" label="Notes" wide error={errors.notes}><textarea id="c-notes" rows={2} value={v.notes} onChange={set('notes')} /></Field>
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export function CustomersPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const [owing, setOwing] = useState(false);
  const [showInactive, setShowInactive] = useState(false);
  const [dialog, setDialog] = useState(params.get('new') === '1');
  const { data, error, loading, reload } = useLoad(
    () => api.get('/sales/customers/', { q: term || undefined, owing: owing ? 'true' : undefined, active: showInactive ? undefined : 'true' }),
    [term, owing, showInactive]);
  const rows = asList(data);
  const canManage = user.permissions.includes('sales.manage');
  useEffect(() => { if (params.get('new')) setParams({}, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const totalOwed = useMemo(() => rows.reduce((a, c) => a + Number(c.outstanding), 0), [rows]);

  return (
    <>
      <PageHeader title="Customers" description="Everyone you invoice, with what they owe."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setDialog(true)}>New customer</button>} />
      <section className="card">
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
          <Field id="cu-q" label="Search" className="grow"><input id="cu-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, GSTIN, code or email" /></Field>
          <button type="submit" className="btn">Search</button>
          <label className="checkbox-inline"><input type="checkbox" checked={owing} onChange={(e) => setOwing(e.target.checked)} /> Only those who owe</label>
          <label className="checkbox-inline"><input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} /> Include switched off</label>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && rows.length === 0 && <p className="empty">{term || owing ? 'No customer matches.' : 'No customers yet. Add the first one.'}</p>}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Customer</th><th>Code</th><th>GSTIN</th><th>Contact</th><th className="amount">Outstanding</th><th className="amount">Advance / credit</th></tr></thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.id} className={`clickable${c.is_active ? '' : ' row-inactive'}`} onClick={() => navigate(`/sales/customers/${c.id}`)}>
                    <td data-label="Customer"><Link to={`/sales/customers/${c.id}`} onClick={(e) => e.stopPropagation()}><strong>{c.display_name}</strong></Link>{!c.is_active && <> <Badge>Switched off</Badge></>}</td>
                    <td data-label="Code"><code>{c.reference_code}</code></td>
                    <td data-label="GSTIN">{c.gstin || '—'}</td>
                    <td data-label="Contact">{c.contact_name || c.email || '—'}</td>
                    <td className="amount" data-label="Outstanding">{Number(c.outstanding) > 0 ? <strong>{money(c.outstanding)}</strong> : '—'}</td>
                    <td className="amount" data-label="Advance / credit">{Number(c.advance) + Number(c.unused_credit) > 0 ? plain(Number(c.advance) + Number(c.unused_credit)) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rows.length > 0 && <div className="list-foot"><span>{rows.length} customer{rows.length === 1 ? '' : 's'}</span><span>Total outstanding <strong>{money(totalOwed)}</strong></span></div>}
      </section>
      {dialog && <CustomerDialog onClose={() => setDialog(false)} onSaved={(c) => { setDialog(false); navigate(`/sales/customers/${c.id}`); }} />}
    </>
  );
}

export function CustomerDetailPage() {
  const { id } = useParams();
  const { user } = useAuth();
  const notify = useToast();
  const { ask, dialog: askDialog } = useReasonDialog();
  const [tab, setTab] = useState('transactions');
  const [editing, setEditing] = useState(false);
  const stmt = useLoad(() => api.get(`/sales/customers/${id}/statement/`), [id]);
  const docs = useLoad(() => api.get('/sales/documents/', { customer: id }), [id]);
  const canManage = user.permissions.includes('sales.manage');

  if (stmt.loading) return <Loading />;
  if (stmt.error) return <ErrorBox error={stmt.error} onRetry={stmt.reload} />;
  const c = stmt.data.customer;
  const rows = stmt.data.rows;

  const toggle = async () => {
    const reason = await ask({ title: `${c.is_active ? 'Switch off' : 'Switch on'} ${c.display_name}?`, required: false, label: 'Reason (optional)', confirmLabel: c.is_active ? 'Switch off' : 'Switch on' });
    if (reason === null) return;
    try { await api.patch(`/sales/customers/${id}/`, { is_active: !c.is_active }); notify(c.is_active ? 'Customer switched off.' : 'Customer switched on.'); stmt.reload(); } catch (e) { notify(e.fieldErrors?.is_active || e.message, 'error'); }
  };

  return (
    <>
      <Link to="/sales/customers" className="back-link">← Customers</Link>
      <header className="doc-head">
        <div>
          <h1>{c.display_name} {!c.is_active && <Badge>Switched off</Badge>}</h1>
          <p className="muted"><code>{c.reference_code}</code>{c.gstin ? ` · GSTIN ${c.gstin}` : ''}</p>
        </div>
        {canManage && (
          <div className="page-actions">
            {c.is_active && <Link className="btn btn-primary" to={`/sales/documents/new?kind=invoice&customer=${id}`}>New invoice</Link>}
            {c.is_active && <Link className="btn" to={`/sales/payments?new=1&customer=${id}`}>Record payment</Link>}
            <button type="button" className="btn" onClick={() => setEditing(true)}>Edit</button>
            <button type="button" className="btn" onClick={toggle}>{c.is_active ? 'Switch off' : 'Switch on'}</button>
          </div>
        )}
      </header>

      <div className="tiles">
        <div className={`tile ${Number(c.outstanding) > 0 ? 'bad' : ''}`}><div className="tile-label">Outstanding</div><div className="tile-value">{money(c.outstanding)}</div></div>
        <div className="tile"><div className="tile-label">Received in advance</div><div className="tile-value">{money(c.advance)}</div><div className="hint">Not yet applied to an invoice</div></div>
        <div className="tile"><div className="tile-label">Unused credit notes</div><div className="tile-value">{money(c.unused_credit)}</div></div>
        <div className="tile"><div className="tile-label">Net balance</div><div className="tile-value">{money(c.net_balance)}</div><div className="hint">{Number(c.net_balance) < 0 ? 'The customer is in credit' : 'What the customer owes'}</div></div>
      </div>

      <Tabs tabs={[{ key: 'transactions', label: 'Transactions' }, { key: 'documents', label: 'Invoices and notes' }, { key: 'details', label: 'Details' }]} value={tab} onChange={setTab} />

      {tab === 'transactions' && (
        <section className="card" aria-label="Transactions">
          {rows.length === 0 ? <p className="empty">Nothing yet.</p> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Date</th><th>Type</th><th>Number</th><th className="amount">Debit</th><th className="amount">Credit</th><th className="amount">Balance</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={`${r.kind}-${r.id}`}>
                      <td data-label="Date">{fmtDate(r.date)}</td><td data-label="Type">{r.type}</td>
                      <td data-label="Number">{r.kind === 'payment' ? r.number : <Link to={`/sales/documents/${r.id}`}>{r.number}</Link>}</td>
                      <td className="amount" data-label="Debit">{Number(r.debit) ? plain(r.debit) : ''}</td>
                      <td className="amount" data-label="Credit">{Number(r.credit) ? plain(r.credit) : ''}</td>
                      <td className="amount" data-label="Balance"><strong>{plain(r.balance)}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="hint">A debit is something the customer owes (an invoice or debit note); a credit is money received or a credit note. Balance is what they owe after each line.</p>
        </section>
      )}

      {tab === 'documents' && (
        <section className="card" aria-label="Invoices and notes">
          {docs.loading ? <Loading /> : asList(docs.data?.results).length === 0 ? <p className="empty">No invoices yet.</p> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Date</th><th>Type</th><th>Number</th><th>Status</th><th className="amount">Amount</th><th className="amount">Balance due</th></tr></thead>
                <tbody>
                  {docs.data.results.map((d) => (
                    <tr key={d.id}>
                      <td data-label="Date">{fmtDate(d.issue_date)}</td><td data-label="Type">{d.kind_label}</td>
                      <td data-label="Number"><Link to={`/sales/documents/${d.id}`}>{d.number || 'Draft'}</Link></td>
                      <td data-label="Status"><StatusBadge doc={d} /></td>
                      <td className="amount" data-label="Amount">{plain(d.grand_total)}</td>
                      <td className="amount" data-label="Balance due">{d.balance === null ? '—' : plain(d.balance)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {tab === 'details' && (
        <section className="card" aria-label="Details">
          <dl className="summary">
            <div><dt>Legal name</dt><dd>{c.legal_name || c.display_name}</dd></div>
            <div><dt>Type</dt><dd>{c.customer_type}</dd></div>
            <div><dt>GST treatment</dt><dd>{c.gst_treatment}</dd></div>
            <div><dt>GSTIN</dt><dd>{c.gstin || '—'}</dd></div>
            <div><dt>PAN</dt><dd>{c.pan || '—'}</dd></div>
            <div><dt>Contact</dt><dd>{c.contact_name || '—'}</dd></div>
            <div><dt>Email</dt><dd>{c.email || '—'}</dd></div>
            <div><dt>Phone</dt><dd>{c.phone || '—'}</dd></div>
            <div><dt>Payment terms</dt><dd>{c.payment_terms_days != null ? `${c.payment_terms_days} days` : 'Company default'}</dd></div>
            <div><dt>Address</dt><dd>{c.address_lines.length ? c.address_lines.map((l) => <div key={l}>{l}</div>) : '—'}</dd></div>
            <div><dt>Notes</dt><dd>{c.notes || '—'}</dd></div>
          </dl>
        </section>
      )}
      {editing && <CustomerDialog record={c} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); stmt.reload(); }} />}
      {askDialog}
    </>
  );
}
