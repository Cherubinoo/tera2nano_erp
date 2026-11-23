import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../api';
import { money, num, today } from '../../format';
import { asList, useLoad } from '../../hooks';
import { ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, useToast } from '../../ui';
import { useProjects } from './assetCommon';

const BLANK = {
  name: '', category: '', purchase_date: today(), put_to_use_date: '', cost: '', vendor: '', bill: '', location: '', serial_number: '', project: '', notes: '',
  method: 'slm', useful_life_years: '', rate_percent: '', salvage_percent: '5', half_year_rule: false,
};

// New asset (and edit). The purchase can be posted to the books now, flagged as already there (from a vendor bill or an opening
// balance, so nothing is posted twice), or left for later.
export default function AssetForm() {
  const { id } = useParams();
  const editing = !!id;
  const navigate = useNavigate();
  const notify = useToast();
  const options = useLoad(() => api.get('/accounting/assets/options/'), []);
  const existing = useLoad(() => (editing ? api.get(`/accounting/assets/${id}/`) : Promise.resolve(null)), [id]);
  const vendors = useLoad(() => api.get('/purchases/vendors/lookup/').catch(() => []), []);
  const projects = useProjects();
  const [v, setV] = useState(BLANK);
  const [acquisition, setAcquisition] = useState('post');
  const [paidFrom, setPaidFrom] = useState('payable');
  const [bills, setBills] = useState([]);
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const a = existing.data;
    if (!a) return;
    setV({
      name: a.name, category: a.category.id, purchase_date: a.purchase_date, put_to_use_date: a.put_to_use_date, cost: String(a.cost), vendor: a.vendor?.id || '',
      bill: a.bill?.id || '', location: a.location, serial_number: a.serial_number, project: a.project || '', notes: a.notes, method: a.method,
      useful_life_years: a.useful_life_years ?? '', rate_percent: a.rate_percent ?? '', salvage_percent: String(num(a.salvage_percent)), half_year_rule: a.half_year_rule,
    });
  }, [existing.data]);

  useEffect(() => {
    if (!v.vendor) { setBills([]); return; }
    let live = true;
    api.get('/purchases/bills/', { vendor: v.vendor }).then((r) => live && setBills(asList(r))).catch(() => live && setBills([]));
    return () => { live = false; };
  }, [v.vendor]);

  if (options.loading || existing.loading) return <Loading />;
  if (options.error) return <ErrorBox error={options.error} onRetry={options.reload} />;
  if (existing.error) return <ErrorBox error={existing.error} onRetry={existing.reload} />;
  const o = options.data;
  const a = existing.data;
  if (editing && !a) return <Loading />;
  const termsLocked = editing && (a.has_depreciation || a.status !== 'in_use');
  const moneyLocked = editing && (termsLocked || a.acquisition === 'posted');
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const pickCategory = (e) => {
    const c = o.categories.find((x) => x.id === e.target.value);
    setErrors((x) => ({ ...x, category: undefined }));
    setV((x) => (c && !editing ? {
      ...x, category: c.id, method: c.method, useful_life_years: c.useful_life_years ?? '', rate_percent: c.rate_percent ?? '', salvage_percent: String(num(c.salvage_percent)),
      half_year_rule: c.half_year_rule } : { ...x, category: e.target.value }));
  };
  const cost = num(v.cost);
  const salvage = Math.round(cost * num(v.salvage_percent)) / 100;
  const firstYear = v.method === 'slm' ? (num(v.useful_life_years) > 0 ? (cost - salvage) / num(v.useful_life_years) : 0) : (cost * num(v.rate_percent)) / 100;

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    const errs = {};
    if (!v.name.trim()) errs.name = 'Enter the asset name.';
    if (!v.category) errs.category = 'Choose the category.';
    if (!v.purchase_date) errs.purchase_date = 'Enter the purchase date.';
    if (v.put_to_use_date && v.purchase_date && v.put_to_use_date < v.purchase_date) errs.put_to_use_date = 'It cannot be put to use before it was bought.';
    if (!(cost > 0)) errs.cost = 'Enter the cost.';
    if (v.method === 'slm' && !(num(v.useful_life_years) > 0)) errs.useful_life_years = 'Enter the useful life in years.';
    if (v.method === 'wdv' && !(num(v.rate_percent) > 0)) errs.rate_percent = 'Enter the rate %.';
    if (num(v.salvage_percent) < 0 || num(v.salvage_percent) >= 100) errs.salvage_percent = 'Enter a % from 0 to less than 100.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const body = { ...v, put_to_use_date: v.put_to_use_date || v.purchase_date, useful_life_years: v.useful_life_years === '' ? null : v.useful_life_years,
        rate_percent: v.rate_percent === '' ? null : v.rate_percent, vendor: v.vendor || null, bill: v.bill || null, project: v.project || null };
      if (editing) {
        // Locked fields are not sent, so a locked asset can still have its notes, location and links changed.
        if (moneyLocked) { delete body.category; delete body.purchase_date; delete body.cost; }
        if (termsLocked) ['put_to_use_date', 'method', 'useful_life_years', 'rate_percent', 'salvage_percent', 'half_year_rule'].forEach((k) => delete body[k]);
        await api.patch(`/accounting/assets/${id}/`, body);
        notify('Asset saved.');
        navigate(`/accounting/assets/${id}`);
      } else {
        const saved = await api.post('/accounting/assets/', { ...body, acquisition, paid_from: acquisition === 'post' ? paidFrom : undefined });
        notify(acquisition === 'post' ? `Asset ${saved.code} created and its purchase posted to the books.` : `Asset ${saved.code} created.`);
        navigate(`/accounting/assets/${saved.id}`);
      }
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };

  return (
    <>
      <Link to={editing ? `/accounting/assets/${id}` : '/accounting/assets'} className="back-link">← {editing ? 'Asset' : 'Fixed assets'}</Link>
      <PageHeader title={editing ? `Edit ${a.name}` : 'New fixed asset'} description={editing ? a.code : 'The asset code is numbered automatically when you save.'} />
      {o.categories.length === 0 && <Notice type="warn">There is no asset category yet. <Link to="/accounting/assets?tab=categories">Add one first</Link>: it holds the depreciation terms and the ledger accounts.</Notice>}
      <form className="card" onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[]} />
        {termsLocked && editing && a.status === 'in_use' && <Notice type="info">Depreciation has been posted, so the cost, dates and depreciation terms are locked. Reverse the depreciation runs to change them.</Notice>}
        <h2>Asset</h2>
        <div className="form-grid">
          <Field id="fa-name" label="Name" required error={errors.name}><input id="fa-name" value={v.name} maxLength={200} onChange={set('name')} /></Field>
          <Field id="fa-category" label="Category" required error={errors.category}>
            <select id="fa-category" value={v.category} onChange={pickCategory} disabled={moneyLocked}><option value="">Choose…</option>{o.categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
          </Field>
          <Field id="fa-purchase" label="Purchase date" required error={errors.purchase_date}><input id="fa-purchase" type="date" value={v.purchase_date} onChange={set('purchase_date')} disabled={moneyLocked} /></Field>
          <Field id="fa-use" label="Put to use on" error={errors.put_to_use_date} hint="Depreciation starts here. Leave empty to use the purchase date."><input id="fa-use" type="date" value={v.put_to_use_date} onChange={set('put_to_use_date')} disabled={termsLocked} /></Field>
          <Field id="fa-cost" label="Cost (including GST you cannot claim)" required error={errors.cost}><input id="fa-cost" type="number" min="0" step="0.01" value={v.cost} onChange={set('cost')} disabled={moneyLocked} /></Field>
          <Field id="fa-serial" label="Serial number" error={errors.serial_number}><input id="fa-serial" value={v.serial_number} maxLength={120} onChange={set('serial_number')} /></Field>
          <Field id="fa-location" label="Location" error={errors.location}><input id="fa-location" value={v.location} maxLength={200} onChange={set('location')} /></Field>
          <Field id="fa-project" label="Project (optional)" error={errors.project} hint="Depreciation of this asset is tagged to the project.">
            <select id="fa-project" value={v.project} onChange={set('project')}><option value="">No project</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.code ? `${p.code} ` : ''}{p.name}</option>)}</select>
          </Field>
          <Field id="fa-vendor" label="Vendor (optional)" error={errors.vendor}>
            <select id="fa-vendor" value={v.vendor} onChange={(e) => { set('vendor')(e); setV((x) => ({ ...x, vendor: e.target.value, bill: '' })); }}><option value="">No vendor</option>{asList(vendors.data).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select>
          </Field>
          <Field id="fa-bill" label="Vendor bill (optional)" error={errors.bill} hint="A link only. It does not post anything.">
            <select id="fa-bill" value={v.bill} onChange={set('bill')} disabled={!v.vendor}><option value="">No bill</option>{bills.map((b) => <option key={b.id} value={b.id}>{b.number || b.vendor_bill_no} · {money(b.grand_total)}</option>)}</select>
          </Field>
          <Field id="fa-notes" label="Notes" wide error={errors.notes}><input id="fa-notes" value={v.notes} maxLength={500} onChange={set('notes')} /></Field>
        </div>

        <h2>Depreciation</h2>
        <p className="muted">Copied from the category; change them for this asset if it differs.</p>
        <div className="fa-terms">
          <Field id="fa-method" label="Method" error={errors.method}>
            <select id="fa-method" value={v.method} onChange={set('method')} disabled={termsLocked}><option value="slm">Straight line (Companies Act)</option><option value="wdv">Written-down value (income-tax rate)</option></select>
          </Field>
          {v.method === 'slm'
            ? <Field id="fa-life" label="Useful life (years)" required error={errors.useful_life_years}><input id="fa-life" type="number" min="0" step="0.01" value={v.useful_life_years} onChange={set('useful_life_years')} disabled={termsLocked} /></Field>
            : <Field id="fa-rate" label="Rate (% a year)" required error={errors.rate_percent}><input id="fa-rate" type="number" min="0" max="100" step="0.01" value={v.rate_percent} onChange={set('rate_percent')} disabled={termsLocked} /></Field>}
          <Field id="fa-salv" label="Residual value (% of cost)" error={errors.salvage_percent}><input id="fa-salv" type="number" min="0" max="99.99" step="0.01" value={v.salvage_percent} onChange={set('salvage_percent')} disabled={termsLocked} /></Field>
        </div>
        {v.method === 'wdv' && (
          <label className="checkbox-inline"><input type="checkbox" checked={v.half_year_rule} onChange={set('half_year_rule')} disabled={termsLocked} /> Half-year rule: half the rate in the first year if used for fewer than 180 days</label>
        )}
        {cost > 0 && <p className="hint" role="status">Residual value {money(salvage)}. {v.method === 'slm' ? 'Yearly depreciation' : 'First full year'} about {money(firstYear)}. The detail page shows the month-by-month schedule.</p>}

        {!editing && (
          <>
            <h2>The books</h2>
            <div className="fa-choice" role="radiogroup" aria-label="Purchase in the books">
              <label><input type="radio" name="acq" checked={acquisition === 'post'} onChange={() => setAcquisition('post')} />
                <span><strong>Record the purchase now</strong><p className="hint">Posts Dr Fixed asset, Cr the account below, dated the purchase date.</p></span></label>
              <label><input type="radio" name="acq" checked={acquisition === 'existing'} onChange={() => setAcquisition('existing')} />
                <span><strong>It is already in the books</strong><p className="hint">A vendor bill or an opening balance already debited the asset account. Nothing is posted, so nothing is counted twice.</p></span></label>
              <label><input type="radio" name="acq" checked={acquisition === 'later'} onChange={() => setAcquisition('later')} />
                <span><strong>Record it later</strong><p className="hint">Depreciation is not charged until the purchase is in the books.</p></span></label>
            </div>
            {acquisition === 'post' && (
              <div className="form-grid">
                <Field id="fa-paid" label="Paid from" error={errors.paid_from}>
                  <select id="fa-paid" value={paidFrom} onChange={(e) => setPaidFrom(e.target.value)}>
                    <option value="payable">Accounts payable (pay the vendor later)</option>
                    {o.money_accounts.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                  </select>
                </Field>
              </div>
            )}
          </>
        )}
        <div className="actions"><button type="submit" className="btn btn-primary" disabled={busy || o.categories.length === 0}>{busy ? 'Saving…' : editing ? 'Save changes' : 'Create asset'}</button>
          <Link className="btn" to={editing ? `/accounting/assets/${id}` : '/accounting/assets'}>Cancel</Link></div>
      </form>
    </>
  );
}
