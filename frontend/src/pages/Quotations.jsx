import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useLookups } from '../context';
import { localToday } from '../forms';
import { useLoad } from '../hooks';
import { ErrorBox, Field, FormErrors, Loading, Notice, useConfirm, useToast } from '../ui';
import { Downloads, IssuedList } from './Letters';

// Starting headings for an IT solution quotation. Paste the text under the ones you need; empty ones are not printed.
const SECTION_HEADINGS = ['Scope of work', 'Deliverables', 'Timeline', 'Payment schedule', 'Support and warranty', 'Assumptions and exclusions'];
const blankSection = (heading = '') => ({ heading, body: '' });
const startSections = () => SECTION_HEADINGS.map((h) => blankSection(h));
const DETAIL_LABELS = ['Project', 'Client', 'Project scope', 'Completion period', 'Warranty / defect liability', 'Payment terms', 'Offer validity', 'EMD', 'Performance guarantee'];
const blankDetail = () => ({ label: '', value: '' });
const UNITS = ['Nos', 'Licence', 'User', 'Month', 'Year', 'Hour', 'Man-day', 'Lot', 'Server', 'Domain'];
// Commonly used for IT services; confirm the right code for each item with your tax adviser.
const SAC_CODES = [
  ['998313', 'IT consulting and support'], ['998314', 'IT design and development'], ['998315', 'Hosting and IT infrastructure provisioning'],
  ['998316', 'IT infrastructure and network management'], ['998319', 'Other IT services'], ['997331', 'Licensing of computer software'],
];
const blankItem = () => ({ description: '', hsn_sac: '', quantity: '1', unit: '', rate: '', discount_percent: '', gst_rate: '' });
const inr = (value) => Number(value).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const isBlank = (i) => !i.description.trim() && !String(i.rate).trim();

// Only what the server needs: blank rows are left out and empty optional values are not sent.
const itemsPayload = (items) => items.filter((i) => !isBlank(i)).map((i) => {
  const out = { description: i.description.trim(), quantity: i.quantity, rate: i.rate };
  if (i.hsn_sac.trim()) out.hsn_sac = i.hsn_sac.trim();
  if (i.unit.trim()) out.unit = i.unit.trim();
  if (String(i.discount_percent).trim()) out.discount_percent = i.discount_percent;
  if (i.gst_rate !== '') out.gst_rate = i.gst_rate;
  return out;
});

function ItemEditor({ item, n, many, options, errors, set, remove }) {
  const id = (k) => `qi-${n}-${k}`;
  return (
    <div className="row-editor">
      <div className="form-grid">
        <Field id={id('description')} label={`Item ${n + 1} description`} wide error={errors?.description}>
          <input id={id('description')} value={item.description} maxLength={500} onChange={(e) => set('description', e.target.value)} />
        </Field>
        {options.show_hsn_sac && (
          <Field id={id('hsn')} label="HSN/SAC" error={errors?.hsn_sac}>
            <input id={id('hsn')} value={item.hsn_sac} inputMode="numeric" maxLength={8} list="q-sac" onChange={(e) => set('hsn_sac', e.target.value)} />
          </Field>
        )}
        <Field id={id('qty')} label="Quantity" error={errors?.quantity}>
          <input id={id('qty')} type="number" min="0" step="any" value={item.quantity} onChange={(e) => set('quantity', e.target.value)} />
        </Field>
        <Field id={id('unit')} label="Unit">
          <input id={id('unit')} value={item.unit} maxLength={20} list="q-units" placeholder="Nos, Licence, Month" onChange={(e) => set('unit', e.target.value)} />
        </Field>
        <Field id={id('rate')} label={`Rate (${options.currency})`} error={errors?.rate}>
          <input id={id('rate')} type="number" min="0" step="any" value={item.rate} onChange={(e) => set('rate', e.target.value)} />
        </Field>
        <Field id={id('disc')} label="Discount %" error={errors?.discount_percent}>
          <input id={id('disc')} type="number" min="0" max="100" step="any" value={item.discount_percent} onChange={(e) => set('discount_percent', e.target.value)} />
        </Field>
        {options.gst_applied && (
          <Field id={id('gst')} label="GST %" error={errors?.gst_rate}>
            <select id={id('gst')} value={item.gst_rate} onChange={(e) => set('gst_rate', e.target.value)}>
              <option value="">{options.default_gst_rate ? `Default (${options.default_gst_rate}%)` : 'Choose…'}</option>
              {options.gst_rates.map((r) => <option key={r} value={r}>{Number(r)}%</option>)}
            </select>
          </Field>
        )}
      </div>
      {many && <button type="button" className="btn btn-small" onClick={remove}>Remove item {n + 1}</button>}
    </div>
  );
}

function Totals({ working, error, pending }) {
  if (error) return <Notice type="warn">{error}</Notice>;
  if (!working) return <p className="hint">The totals appear here as you fill in the customer and items.</p>;
  const t = working.totals;
  const rows = [];
  if (Number(t.discount)) rows.push(['Gross amount', inr(t.gross)], ['Discount', `-${inr(t.discount)}`]);
  if (working.gst_applied) {
    rows.push(['Taxable amount', inr(t.taxable)], ...t.tax.map((x) => [x.label, inr(x.amount)]));
  }
  return (
    <div className="table-wrap" aria-live="polite" aria-busy={pending}>
      <table>
        <caption>
          {working.gst_applied
            ? `${working.supply_type === 'intra_state' ? 'Same state: CGST + SGST/UTGST' : 'Other state: IGST'} · Place of supply ${working.place_of_supply.name} (${working.place_of_supply.code})`
            : 'No GST: it is not switched on under Tax Configuration'}
        </caption>
        <tbody>
          {rows.map(([k, v]) => <tr key={k}><th scope="row">{k}</th><td className="num">{v}</td></tr>)}
          <tr><th scope="row">{working.gst_applied ? 'Grand total' : 'Total'} ({working.currency})</th><td className="num"><strong>{inr(t.grand_total)}</strong></td></tr>
        </tbody>
      </table>
      {working.amount_in_words && <p className="hint">{working.amount_in_words}</p>}
    </div>
  );
}

export function QuotationsManager() {
  const lookups = useLookups();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { data: options, error: optionsError, loading, reload } = useLoad(() => api.get('/company/quotations/working/'), []);
  const [customer, setCustomer] = useState({ name: '', address: '', gstin: '', state_code: '' });
  const [meta, setMeta] = useState({ issue_date: localToday(), valid_until: '', enquiry_ref: '', subject: '', intro: '', notes: '', terms: '', include_bank: true, show_signature: false });
  const [items, setItems] = useState([blankItem()]);
  const [sections, setSections] = useState(startSections);
  const [details, setDetails] = useState([blankDetail()]);
  const [nextRef, setNextRef] = useState(undefined); // undefined: loading, null: numbering switched off
  const [errors, setErrors] = useState({});
  const [itemErrors, setItemErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [working, setWorking] = useState(null);
  const [workingError, setWorkingError] = useState('');
  const [pending, setPending] = useState(false);
  const ticket = useRef(0);

  const quotation = () => {
    const q = {
      customer: { name: customer.name.trim(), address: customer.address.split('\n').map((l) => l.trim()).filter(Boolean) },
      items: itemsPayload(items), include_bank: meta.include_bank,
    };
    if (customer.gstin.trim()) q.customer.gstin = customer.gstin.trim().toUpperCase();
    if (customer.state_code) q.customer.state_code = customer.state_code;
    for (const k of ['valid_until', 'enquiry_ref', 'subject', 'intro', 'notes', 'terms']) if (meta[k].trim()) q[k] = meta[k].trim();
    const filled = sections.filter((s) => s.body.trim()).map((s) => ({ heading: s.heading.trim(), body: s.body.trim() }));
    if (filled.length) q.sections = filled;
    const rows = details.filter((d) => d.label.trim() || d.value.trim()).map((d) => ({ label: d.label.trim(), value: d.value.trim() }));
    if (rows.length) q.details = rows;
    q.show_signature = meta.show_signature;
    return q;
  };

  // The ID is assigned by the server when the quotation is issued; this only shows what it will be.
  useEffect(() => {
    if (!meta.issue_date) return;
    let live = true;
    api.get('/company/document-numbering/next/', { document_type: 'quotation', issue_date: meta.issue_date })
      .then((r) => { if (live) setNextRef(r.reference_number); })
      .catch(() => { if (live) setNextRef(undefined); });
    return () => { live = false; };
  }, [meta.issue_date, refreshKey]);

  // Totals are worked out by the server, the same code that prices the quotation when it is issued.
  const ready = customer.name.trim() && itemsPayload(items).length > 0 && itemsPayload(items).every((i) => i.quantity !== '' && i.rate !== '');
  const signature = JSON.stringify([customer, meta.issue_date, meta.valid_until, meta.include_bank, items]);
  useEffect(() => {
    if (!options) return undefined;
    if (!ready) { setWorking(null); setWorkingError(''); return undefined; }
    const mine = ++ticket.current;
    setPending(true);
    const timer = setTimeout(async () => {
      try {
        const result = await api.post('/company/quotations/working/', { quotation: quotation(), issue_date: meta.issue_date });
        if (mine === ticket.current) { setWorking(result); setWorkingError(''); }
      } catch (e) {
        if (mine === ticket.current) { setWorking(null); setWorkingError(e.message); }
      }
      if (mine === ticket.current) setPending(false);
    }, 500);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, options]);

  if (loading && !options) return <Loading />;
  if (optionsError) return <ErrorBox error={optionsError} onRetry={reload} />;
  if (!options) return <Loading />;

  const setC = (k) => (e) => { setCustomer((c) => ({ ...c, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const setM = (k) => (e) => { setMeta((m) => ({ ...m, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const setItem = (n) => (k, v) => { setItems((rows) => rows.map((r, i) => (i === n ? { ...r, [k]: v } : r))); setItemErrors((x) => ({ ...x, [n]: undefined })); };

  const validate = () => {
    const errs = {}, ierrs = {};
    if (!customer.name.trim()) errs.name = 'Enter the customer name.';
    if (!meta.issue_date) errs.issue_date = 'Choose the quotation date.';
    if (details.some((d) => !!d.label.trim() !== !!d.value.trim())) errs.details = 'Each key detail needs both a label and a value.';
    const used = items.map((item, n) => [item, n]).filter(([item]) => !isBlank(item));
    if (!used.length) errs.items = 'Add at least one item.';
    used.forEach(([item, n]) => {
      const e = {};
      if (!item.description.trim()) e.description = 'Describe the item.';
      if (!(Number(item.quantity) > 0)) e.quantity = 'Enter a quantity above 0.';
      if (item.rate === '' || Number(item.rate) < 0) e.rate = 'Enter the rate.';
      if (item.discount_percent !== '' && (Number(item.discount_percent) < 0 || Number(item.discount_percent) > 100)) e.discount_percent = 'Use 0 to 100.';
      if (options.gst_applied && item.gst_rate === '' && !options.default_gst_rate) e.gst_rate = 'Choose a GST rate.';
      if (Object.keys(e).length) ierrs[n] = e;
    });
    setErrors(errs); setItemErrors(ierrs);
    return !Object.keys(errs).length && !Object.keys(ierrs).length;
  };

  const submit = async (e) => {
    e.preventDefault();
    setFailure(null);
    if (!validate()) return;
    const ok = await confirm({
      title: 'Issue this quotation?',
      body: (
        <>
          <p>A unique quotation ID is allocated now and the quotation is saved exactly as shown. An issued quotation cannot be edited; if it is wrong, void it and issue a new one.</p>
          {working && <p>Total: <strong>{working.currency} {inr(working.totals.grand_total)}</strong> for <strong>{customer.name.trim()}</strong>.</p>}
        </>
      ),
      confirmLabel: 'Issue quotation',
    });
    if (!ok) return;
    setBusy(true);
    try {
      const doc = await api.post('/company/issued-documents/', { document_type: 'quotation', issue_date: meta.issue_date, content: { quotation: quotation() } });
      setIssued(doc);
      setRefreshKey((k) => k + 1);
      setCustomer({ name: '', address: '', gstin: '', state_code: '' });
      setMeta((m) => ({ ...m, valid_until: '', enquiry_ref: '', subject: '', intro: '', notes: '', terms: '' }));
      setItems([blankItem()]);
      setSections(startSections());
      setDetails([blankDetail()]);
      notify(`Quotation issued: ${doc.reference_number}`);
    } catch (err) {
      setFailure(err);
      setErrors(err.fieldErrors || {});
    }
    setBusy(false);
  };

  return (
    <>
      <datalist id="q-units">{UNITS.map((u) => <option key={u} value={u} />)}</datalist>
      <datalist id="q-sac">{SAC_CODES.map(([code, label]) => <option key={code} value={code}>{label}</option>)}</datalist>
      <form className="card" onSubmit={submit} noValidate>
        <div className="card-head">
          <div>
            <h2>New IT solution quotation</h2>
            <p className="muted">Enter the customer, the solution items (development, hosting, licences, support) and the scope. Prices and GST are worked out for you; issuing gives a unique quotation ID, a PDF with the letterhead and a plain PDF for your pre-printed paper.</p>
          </div>
        </div>
        <FormErrors error={failure} fields={[]} />
        <div className="notice notice-info" role="status">
          <strong>Quotation ID:</strong>{' '}
          {nextRef === null ? 'numbering for quotations is switched off (Document Numbering).'
            : nextRef ? <><code>{nextRef}</code> <span className="hint">Assigned automatically when you issue; you never type it. It follows the quotation date.</span></>
              : 'assigned automatically when you issue.'}
        </div>
        {!options.gst_applied && <Notice>GST is not switched on for the company (Tax Configuration), so this quotation is printed without tax.</Notice>}

        <fieldset className="subcard">
          <legend>Customer</legend>
          <div className="form-grid">
            <Field id="q-name" label="Customer name" required error={errors.name} wide>
              <input id="q-name" value={customer.name} maxLength={200} onChange={setC('name')} />
            </Field>
            <Field id="q-address" label="Address (one line per row)" wide>
              <textarea id="q-address" rows={3} value={customer.address} onChange={setC('address')} />
            </Field>
            <Field id="q-gstin" label="Customer GSTIN (optional)" error={errors.gstin}>
              <input id="q-gstin" value={customer.gstin} maxLength={15} onChange={setC('gstin')} />
            </Field>
            {options.gst_applied && (
              <Field id="q-state" label="Customer state (place of supply)" hint="Leave empty to use the state in the GSTIN, else the company's default state.">
                <select id="q-state" value={customer.state_code} onChange={setC('state_code')}>
                  <option value="">— automatic —</option>
                  {lookups.gst_states.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                </select>
              </Field>
            )}
          </div>
        </fieldset>

        <div className="form-grid">
          <Field id="q-date" label="Quotation date" required error={errors.issue_date}>
            <input id="q-date" type="date" value={meta.issue_date} onChange={setM('issue_date')} />
          </Field>
          <Field id="q-valid" label="Valid until" error={errors.valid_until}
            hint={options.validity_days ? `Leave empty for ${options.validity_days} days after the date.` : 'Optional.'}>
            <input id="q-valid" type="date" value={meta.valid_until} onChange={setM('valid_until')} />
          </Field>
          <Field id="q-ref" label="Customer's own reference (optional)" error={errors.enquiry_ref} hint="Their RFQ or enquiry number, if they gave one. Our quotation ID is generated for you.">
            <input id="q-ref" value={meta.enquiry_ref} maxLength={100} onChange={setM('enquiry_ref')} />
          </Field>
          <Field id="q-subject" label="Subject" error={errors.subject} wide>
            <input id="q-subject" value={meta.subject} maxLength={200} onChange={setM('subject')} />
          </Field>
          <Field id="q-intro" label="Introduction (optional)" wide hint="Printed above the items. A blank line starts a new paragraph.">
            <textarea id="q-intro" rows={3} value={meta.intro} onChange={setM('intro')} />
          </Field>
        </div>

        <fieldset className="subcard">
          <legend>Items</legend>
          {errors.items && <p className="error-text" role="alert">{errors.items}</p>}
          {items.map((item, n) => (
            <ItemEditor key={n} item={item} n={n} many={items.length > 1} options={options} errors={itemErrors[n]}
              set={setItem(n)} remove={() => setItems((rows) => rows.filter((_, i) => i !== n))} />
          ))}
          <button type="button" className="btn btn-small" onClick={() => setItems((rows) => [...rows, blankItem()])}>Add item</button>
          <Totals working={working} error={workingError} pending={pending} />
        </fieldset>

        <fieldset className="subcard">
          <legend>Key details (optional)</legend>
          <p className="hint">A neat label/value table printed under the totals: project, client, completion period, validity and so on. Rows left empty are ignored.</p>
          {errors.details && <p className="error-text" role="alert">{errors.details}</p>}
          <datalist id="q-detail-labels">{DETAIL_LABELS.map((l) => <option key={l} value={l} />)}</datalist>
          {details.map((d, n) => (
            <div className="row-editor" key={n}>
              <div className="form-grid">
                <Field id={`qd-${n}-label`} label={`Detail ${n + 1} label`}>
                  <input id={`qd-${n}-label`} value={d.label} maxLength={100} list="q-detail-labels"
                    onChange={(e) => setDetails((rows) => rows.map((r, i) => (i === n ? { ...r, label: e.target.value } : r)))} />
                </Field>
                <Field id={`qd-${n}-value`} label="Value">
                  <input id={`qd-${n}-value`} value={d.value} maxLength={1000}
                    onChange={(e) => setDetails((rows) => rows.map((r, i) => (i === n ? { ...r, value: e.target.value } : r)))} />
                </Field>
              </div>
              {details.length > 1 && <button type="button" className="btn btn-small" onClick={() => setDetails((rows) => rows.filter((_, i) => i !== n))}>Remove detail {n + 1}</button>}
            </div>
          ))}
          <button type="button" className="btn btn-small" onClick={() => setDetails((rows) => [...rows, blankDetail()])}>Add detail</button>
        </fieldset>

        <fieldset className="subcard">
          <legend>Scope and conditions (optional)</legend>
          <p className="hint">Paste the text under each heading you need. They print under the totals; headings with no text are left out. Pasted formatting is kept: # headings, **bold**, - and 1. lists, and | tables |.</p>
          {sections.map((s, n) => (
            <div className="row-editor" key={n}>
              <div className="form-grid">
                <Field id={`qs-${n}-heading`} label={`Section ${n + 1} heading`}>
                  <input id={`qs-${n}-heading`} value={s.heading} maxLength={200}
                    onChange={(e) => setSections((rows) => rows.map((r, i) => (i === n ? { ...r, heading: e.target.value } : r)))} />
                </Field>
                <Field id={`qs-${n}-body`} label="Text" wide>
                  <textarea id={`qs-${n}-body`} rows={3} value={s.body}
                    onChange={(e) => setSections((rows) => rows.map((r, i) => (i === n ? { ...r, body: e.target.value } : r)))} />
                </Field>
              </div>
              <button type="button" className="btn btn-small" onClick={() => setSections((rows) => rows.filter((_, i) => i !== n))}>Remove section {n + 1}</button>
            </div>
          ))}
          <button type="button" className="btn btn-small" onClick={() => setSections((rows) => [...rows, blankSection()])}>Add section</button>
        </fieldset>

        <div className="form-grid">
          <Field id="q-notes" label="Notes (optional)" wide hint="Printed under the totals.">
            <textarea id="q-notes" rows={2} value={meta.notes} onChange={setM('notes')} />
          </Field>
          <Field id="q-terms" label="Terms and conditions" wide
            hint={options.default_terms ? 'Formatting is kept. Leave empty to print the company default terms (Company Policies). Type here to replace them for this quotation.' : 'No default terms are set under Company Policies. Optional.'}>
            <textarea id="q-terms" rows={3} value={meta.terms} onChange={setM('terms')} placeholder={options.default_terms} />
          </Field>
          <label className="field field-wide checkbox">
            <input type="checkbox" checked={meta.show_signature} onChange={setM('show_signature')} /> Print the "For (company name) / Authorised Signatory" block (leave off when you stamp the company seal)
          </label>
          {options.bank_will_print && (
            <label className="field field-wide checkbox">
              <input type="checkbox" checked={meta.include_bank} onChange={setM('include_bank')} /> Print the bank details for payment
            </label>
          )}
        </div>
        {options.payment_terms && <p className="hint">Payment terms from Company Policies ("{options.payment_terms}") print under the totals.</p>}
        <div className="actions">
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Issuing…' : 'Issue quotation'}</button>
        </div>
      </form>

      {issued && (
        <Notice type="success">
          <p><strong>Quotation issued.</strong> ID: <code>{issued.reference_number}</code></p>
          <div className="row-actions"><Downloads doc={issued} onError={(e) => notify(e.message, 'error')} /></div>
          <p className="hint">Use "Print PDF" on pre-printed letterhead paper, or "Letterhead PDF" to print or send on plain paper.</p>
        </Notice>
      )}

      <IssuedList refreshKey={refreshKey} types={['quotation']} title="Issued quotations" emptyText="No quotations issued yet."
        showTemplates={false} searchHint="T2N/QUO/26/…" />
      {confirmDialog}
    </>
  );
}
