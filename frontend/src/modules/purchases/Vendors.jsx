import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { downloadCsv, money, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, useToast } from '../../ui';
import { usePurchaseOptions } from './common';

export const STATES = [['01', 'Jammu and Kashmir'], ['02', 'Himachal Pradesh'], ['03', 'Punjab'], ['04', 'Chandigarh'], ['05', 'Uttarakhand'], ['06', 'Haryana'],
  ['07', 'Delhi'], ['08', 'Rajasthan'], ['09', 'Uttar Pradesh'], ['10', 'Bihar'], ['11', 'Sikkim'], ['12', 'Arunachal Pradesh'], ['13', 'Nagaland'],
  ['14', 'Manipur'], ['15', 'Mizoram'], ['16', 'Tripura'], ['17', 'Meghalaya'], ['18', 'Assam'], ['19', 'West Bengal'], ['20', 'Jharkhand'],
  ['21', 'Odisha'], ['22', 'Chhattisgarh'], ['23', 'Madhya Pradesh'], ['24', 'Gujarat'], ['26', 'Dadra and Nagar Haveli and Daman and Diu'],
  ['27', 'Maharashtra'], ['29', 'Karnataka'], ['30', 'Goa'], ['31', 'Lakshadweep'], ['32', 'Kerala'], ['33', 'Tamil Nadu'], ['34', 'Puducherry'],
  ['35', 'Andaman and Nicobar Islands'], ['36', 'Telangana'], ['37', 'Andhra Pradesh'], ['38', 'Ladakh'], ['97', 'Other Territory'], ['99', 'Centre Jurisdiction']];

const BLANK = { name: '', contact_person: '', email: '', phone: '', gst_treatment: 'unregistered', gstin: '', pan: '', address_line1: '', address_line2: '', city: '',
  state_code: '', postal_code: '', payment_terms_days: '', tds_section: '', tds_rate: '', opening_balance: '', opening_balance_date: '',
  bank_account_holder: '', bank_name: '', bank_account_number: '', bank_ifsc: '', notes: '' };

export function VendorDialog({ vendor, onClose, onSaved }) {
  const notify = useToast();
  const opts = usePurchaseOptions();
  const [v, setV] = useState(() => ({ ...BLANK, ...Object.fromEntries(Object.keys(BLANK).map((k) => [k, vendor?.[k] ?? BLANK[k]])) }));
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setV((x) => ({ ...x, [k]: e.target.value }));
  const posted = vendor?.opening_posted;

  useEffect(() => { // the state follows the GSTIN's first two digits
    if (/^\d{2}/.test(v.gstin) && STATES.some(([c]) => c === v.gstin.slice(0, 2))) setV((x) => ({ ...x, state_code: x.gstin.slice(0, 2) }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [v.gstin]);

  const validate = () => {
    const e = {};
    if (!v.name.trim()) e.name = 'Enter the vendor name.';
    if (v.gst_treatment === 'registered' && !v.gstin.trim()) e.gstin = 'Enter the GSTIN of a registered vendor.';
    if (v.gstin && !/^\d{2}[A-Za-z]{5}\d{4}[A-Za-z][1-9A-Za-z]Z[0-9A-Za-z]$/.test(v.gstin.trim())) e.gstin = 'A GSTIN has 15 characters, such as 29ABCDE1234F1Z5.';
    if (v.pan && !/^[A-Za-z]{5}\d{4}[A-Za-z]$/.test(v.pan.trim())) e.pan = 'A PAN has 10 characters, such as ABCDE1234F.';
    if (v.email && !/^\S+@\S+\.\S+$/.test(v.email)) e.email = 'Enter a valid email address.';
    if (v.tds_section && v.tds_rate === '') e.tds_rate = 'Enter the TDS rate.';
    if (v.tds_rate !== '' && !v.tds_section) e.tds_section = 'Choose the TDS section.';
    if (v.opening_balance !== '' && !(Number(v.opening_balance) >= 0)) e.opening_balance = 'Enter an amount of 0 or more.';
    setErrors(e);
    return !Object.keys(e).length;
  };

  const submit = async (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    setFailure(null);
    if (!validate()) return;
    setBusy(true);
    const body = { ...v, state_code: v.state_code || '', payment_terms_days: v.payment_terms_days === '' ? null : v.payment_terms_days,
      tds_rate: v.tds_rate === '' ? null : v.tds_rate, opening_balance: v.opening_balance === '' ? '0' : v.opening_balance,
      opening_balance_date: v.opening_balance_date || null };
    if (posted) { delete body.opening_balance; delete body.opening_balance_date; }
    try {
      const saved = vendor ? await api.patch(`/purchases/vendors/${vendor.id}/`, body) : await api.post('/purchases/vendors/', body);
      notify(vendor ? 'Vendor saved.' : `Vendor ${saved.code} created.`);
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  const text = (k, label, props = {}) => (
    <Field id={`v-${k}`} label={label} error={errors[k]} required={props.required}><input id={`v-${k}`} value={v[k]} onChange={set(k)} {...props} /></Field>
  );
  return (
    <Dialog title={vendor ? `Edit ${vendor.name}` : 'New vendor'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={Object.keys(BLANK).map((name) => ({ name }))} />
        <div className="form-grid">
          {text('name', 'Vendor name', { required: true, maxLength: 200 })}
          {text('contact_person', 'Contact person', { maxLength: 150 })}
          {text('email', 'Email', { type: 'email' })}
          {text('phone', 'Phone', { maxLength: 30 })}
          <Field id="v-gst_treatment" label="GST registration" error={errors.gst_treatment}>
            <select id="v-gst_treatment" value={v.gst_treatment} onChange={set('gst_treatment')}>
              <option value="registered">Registered (has a GSTIN)</option><option value="unregistered">Unregistered</option><option value="overseas">Overseas</option>
            </select>
          </Field>
          {text('gstin', 'GSTIN', { maxLength: 15, required: v.gst_treatment === 'registered' })}
          {text('pan', 'PAN', { maxLength: 10 })}
          {text('address_line1', 'Address line 1', { maxLength: 200 })}
          {text('address_line2', 'Address line 2', { maxLength: 200 })}
          {text('city', 'City', { maxLength: 100 })}
          <Field id="v-state_code" label="State (place of supply)" error={errors.state_code} hint="Decides CGST/SGST or IGST on purchases.">
            <select id="v-state_code" value={v.state_code} onChange={set('state_code')}>
              <option value="">Not set</option>{STATES.map(([c, n]) => <option key={c} value={c}>{c} · {n}</option>)}
            </select>
          </Field>
          {text('postal_code', 'PIN code', { maxLength: 6, inputMode: 'numeric' })}
          {text('payment_terms_days', 'Payment terms (days)', { type: 'number', min: 0, max: 365 })}
          <Field id="v-tds_section" label="Default TDS section" error={errors.tds_section}>
            <select id="v-tds_section" value={v.tds_section} onChange={(e) => {
              const s = opts.data?.tds_sections.find((t) => t.section === e.target.value);
              setV((x) => ({ ...x, tds_section: e.target.value, tds_rate: s ? s.rate : e.target.value ? x.tds_rate : '' }));
            }}>
              <option value="">No TDS</option>{(opts.data?.tds_sections || []).map((t) => <option key={t.section} value={t.section}>{t.label}</option>)}
            </select>
          </Field>
          {text('tds_rate', 'Default TDS rate (%)', { type: 'number', min: 0, max: 100, step: '0.01' })}
          {text('opening_balance', 'Opening balance we owe', { type: 'number', min: 0, step: '0.01', disabled: posted, placeholder: '0.00' })}
          {text('opening_balance_date', 'Opening balance as on', { type: 'date', disabled: posted })}
        </div>
        {posted && <p className="hint">The opening balance is already in the books and cannot be changed here.</p>}
        <fieldset className="subcard">
          <legend>Bank details</legend>
          <div className="form-grid">
            {text('bank_account_holder', 'Account holder', { maxLength: 150 })}
            {text('bank_name', 'Bank', { maxLength: 100 })}
            {text('bank_account_number', 'Account number', { inputMode: 'numeric', autoComplete: 'off' })}
            {text('bank_ifsc', 'IFSC', { maxLength: 11 })}
          </div>
        </fieldset>
        <Field id="v-notes" label="Notes" wide error={errors.notes}><textarea id="v-notes" rows={2} value={v.notes} onChange={set('notes')} /></Field>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save vendor'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export default function Vendors() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get('q') || '');
  const [filter, setFilter] = useState('active');
  const [creating, setCreating] = useState(params.get('new') === '1');
  const query = filter === 'active' ? { active: 'true' } : filter === 'inactive' ? { active: 'false' } : filter === 'owing' ? { owing: 'true' } : {};
  const term = params.get('q') || '';
  const { data, error, loading, reload } = useLoad(() => api.get('/purchases/vendors/', { ...query, q: term || undefined }), [filter, term]);
  const canManage = user.permissions.includes('purchases.manage');
  const rows = data || [];
  const totals = rows.reduce((a, v) => ({ out: a.out + num(v.outstanding), over: a.over + num(v.overdue) }), { out: 0, over: 0 });
  const exportCsv = () => downloadCsv('vendors.csv', [['Code', 'Name', 'GSTIN', 'Contact', 'Phone', 'Outstanding', 'Overdue', 'Status'],
    ...rows.map((v) => [v.code, v.name, v.gstin, v.contact_person, v.phone, v.outstanding, v.overdue, v.is_active ? 'Active' : 'Inactive'])]);
  return (
    <>
      <PageHeader title="Vendors" description="Who you buy from, and what you owe them."
        actions={<>{rows.length > 0 && <button type="button" className="btn" onClick={exportCsv}>Export CSV</button>}{canManage && <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>New vendor</button>}</>} />
      <section className="card">
        <div className="chips" role="group" aria-label="Filter vendors">
          {[['active', 'Active'], ['owing', 'We owe money'], ['inactive', 'Inactive'], ['all', 'All']].map(([k, l]) => (
            <button key={k} type="button" className="chip" aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}</button>
          ))}
        </div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setParams(q.trim() ? { q: q.trim() } : {}, { replace: true }); }}>
          <Field id="vendor-q" label="Search" className="grow"><input id="vendor-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, code, GSTIN, email or phone" /></Field>
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && rows.length === 0 && <p className="empty">{term ? 'No vendor matches.' : 'No vendors yet. Add the first one.'}</p>}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Code</th><th>Vendor</th><th>GSTIN</th><th>Contact</th><th className="amount">Outstanding</th><th className="amount">Overdue</th><th>Status</th></tr></thead>
              <tbody>
                {rows.map((v) => (
                  <tr key={v.id} className="clickable" onClick={() => navigate(`/purchases/vendors/${v.id}`)}>
                    <td data-label="Code">{v.code}</td>
                    <td data-label="Vendor"><Link to={`/purchases/vendors/${v.id}`} onClick={(e) => e.stopPropagation()}><strong>{v.name}</strong></Link></td>
                    <td data-label="GSTIN">{v.gstin || '—'}</td>
                    <td data-label="Contact">{[v.contact_person, v.phone].filter(Boolean).join(' · ') || '—'}</td>
                    <td className="amount" data-label="Outstanding">{num(v.outstanding) ? <strong>{plain(v.outstanding)}</strong> : '—'}</td>
                    <td className="amount" data-label="Overdue">{num(v.overdue) ? <span className="pur-bad">{plain(v.overdue)}</span> : '—'}</td>
                    <td data-label="Status">{v.is_active ? <Badge tone="good">Active</Badge> : <Badge>Inactive</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rows.length > 0 && <div className="list-foot"><span>{rows.length} vendor{rows.length === 1 ? '' : 's'}</span><span>Outstanding <strong>{money(totals.out)}</strong></span><span>Overdue <strong>{money(totals.over)}</strong></span></div>}
      </section>
      {creating && <VendorDialog onClose={() => setCreating(false)} onSaved={(v) => { setCreating(false); navigate(`/purchases/vendors/${v.id}`); }} />}
    </>
  );
}
