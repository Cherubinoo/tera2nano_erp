import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { today } from '../../format';
import { asList, useLoad } from '../../hooks';
import { ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, fmtDate, useToast } from '../../ui';
import { ItemEditor, SAC_CODES, Totals, UNITS, blankItem, isBlank, itemsPayload } from '../Quotations';
import { CustomerDialog } from './Customers';
import { ProjectField } from './common';

export const FREQUENCIES = [
  ['weekly', 'Weekly'], ['monthly', 'Monthly'], ['quarterly', 'Quarterly'], ['half_yearly', 'Half-yearly'], ['yearly', 'Yearly'],
  ['custom', 'Custom (every N days or months)'],
];

// New or edited recurring-invoice profile: who is billed, what, how often, and whether each invoice is issued or left as a draft.
export default function RecurringForm() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const notify = useToast();
  const existing = useLoad(() => (id ? api.get(`/sales/recurring-invoices/${id}/`) : Promise.resolve(null)), [id]);
  const options = useLoad(() => api.get('/sales/documents/options/', { kind: 'invoice' }), []);
  const customers = useLoad(() => api.get('/sales/customers/', { active: 'true' }), []);

  const [name, setName] = useState('');
  const [customerId, setCustomerId] = useState('');
  const [mode, setMode] = useState('draft');
  const [frequency, setFrequency] = useState('monthly');
  const [unit, setUnit] = useState('days');
  const [count, setCount] = useState('');
  const [start, setStart] = useState(today());
  const [end, setEnd] = useState('');
  const [max, setMax] = useState('');
  const [dueDays, setDueDays] = useState('');
  const [project, setProject] = useState('');
  const [items, setItems] = useState([blankItem()]);
  const [meta, setMeta] = useState({ subject: '', enquiry_ref: '', include_bank: true, show_signature: false });
  const [applyTerms, setApplyTerms] = useState(false);
  const [notes, setNotes] = useState('');
  const [terms, setTerms] = useState('');
  const [errors, setErrors] = useState({});
  const [itemErrors, setItemErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [newCustomer, setNewCustomer] = useState(false);
  const [working, setWorking] = useState(null);
  const [workingError, setWorkingError] = useState('');
  const [pending, setPending] = useState(false);
  const [dates, setDates] = useState([]);
  const ticket = useRef(0);
  const filled = useRef(false);

  useEffect(() => {
    const p = existing.data;
    if (!id || !p || filled.current) return;
    filled.current = true;
    setName(p.name); setCustomerId(p.customer.id); setMode(p.mode); setFrequency(p.frequency);
    setUnit(p.interval_unit || 'days'); setCount(p.interval_count ?? ''); setStart(p.start_date); setEnd(p.end_date || '');
    setMax(p.max_occurrences ?? ''); setDueDays(p.due_days ?? ''); setProject(p.project || '');
    const f = p.form || {};
    setItems((f.items || []).map((i) => ({ ...blankItem(), ...i, gst_rate: i.gst_rate ?? '', discount_percent: i.discount_percent ?? '', hsn_sac: i.hsn_sac || '', unit: i.unit || '' })));
    setMeta({ subject: f.subject || '', enquiry_ref: f.enquiry_ref || '', include_bank: f.include_bank !== false, show_signature: !!f.show_signature });
    setApplyTerms(p.apply_terms); setNotes(p.notes || ''); setTerms(p.terms || '');
  }, [existing.data, id]);

  const formBody = () => {
    const f = { items: itemsPayload(items), include_bank: meta.include_bank, show_signature: meta.show_signature };
    for (const k of ['subject', 'enquiry_ref']) if (meta[k].trim()) f[k] = meta[k].trim();
    return f;
  };

  // Totals and GST for one invoice, from the server, as the form changes.
  const used = itemsPayload(items);
  const ready = customerId && used.length > 0 && used.every((i) => i.quantity !== '' && i.rate !== '');
  const signature = JSON.stringify([customerId, start, items]);
  useEffect(() => {
    if (!options.data) return undefined;
    if (!ready) { setWorking(null); setWorkingError(''); return undefined; }
    const mine = ++ticket.current;
    setPending(true);
    const timer = setTimeout(async () => {
      try {
        const r = await api.post('/sales/documents/working/', { kind: 'invoice', customer: customerId, issue_date: start || today(), form: formBody() });
        if (mine === ticket.current) { setWorking(r.record); setWorkingError(''); }
      } catch (e) { if (mine === ticket.current) { setWorking(null); setWorkingError(e.message); } }
      if (mine === ticket.current) setPending(false);
    }, 500);
    return () => clearTimeout(timer);
  }, [signature, options.data]); // eslint-disable-line react-hooks/exhaustive-deps

  // The next run dates, so a profile starting on the 31st can be seen to move to month ends.
  const scheduleKey = JSON.stringify([start, frequency, unit, count, end, max]);
  useEffect(() => {
    const customOk = frequency !== 'custom' || (Number(count) > 0 && unit);
    if (!start || !customOk) { setDates([]); return undefined; }
    const timer = setTimeout(() => {
      api.post('/sales/recurring-invoices/preview/', {
        start_date: start, frequency, interval_unit: frequency === 'custom' ? unit : undefined,
        interval_count: frequency === 'custom' ? count : undefined, end_date: end || undefined, max_occurrences: max || undefined,
      }).then((r) => setDates(r.dates)).catch(() => setDates([]));
    }, 300);
    return () => clearTimeout(timer);
  }, [scheduleKey]); // eslint-disable-line react-hooks/exhaustive-deps

  if (existing.loading || options.loading || customers.loading) return <Loading />;
  if (existing.error) return <ErrorBox error={existing.error} onRetry={existing.reload} />;
  if (options.error) return <ErrorBox error={options.error} onRetry={options.reload} />;
  const p = existing.data;
  if (p && !['active', 'paused'].includes(p.status)) {
    return <Notice type="warn">A {p.status_label.toLowerCase()} profile cannot be edited. <Link to={`/sales/recurring/${id}`}>Back to the profile</Link></Notice>;
  }
  const o = options.data;
  const list = asList(customers.data);
  const customer = list.find((c) => c.id === customerId) || (p && p.customer.id === customerId ? { id: customerId, display_name: p.customer.name, address_lines: [] } : null);
  const canIssue = user.permissions.includes('sales.issue');
  const locked = !!p && p.occurrences_done > 0; // the start date anchors the schedule once invoices exist
  const setM = (k) => (e) => setMeta((m) => ({ ...m, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }));
  const clear = (k) => setErrors((x) => ({ ...x, [k]: undefined }));
  const setItem = (n) => (k, v) => { setItems((rows) => rows.map((r, i) => (i === n ? { ...r, [k]: v } : r))); setItemErrors((x) => ({ ...x, [n]: undefined })); clear('items'); };

  const validate = () => {
    const errs = {}, ierrs = {};
    if (!name.trim()) errs.name = 'Give the profile a name.';
    if (!customerId) errs.customer = 'Choose the customer.';
    if (!start) errs.start_date = 'Choose the first invoice date.';
    if (end && start && end < start) errs.end_date = 'The end date cannot be before the start date.';
    if (max !== '' && !(Number(max) >= 1)) errs.max_occurrences = 'Use 1 or more, or leave empty.';
    if (dueDays !== '' && !(Number(dueDays) >= 0)) errs.due_days = 'Use 0 or more days, or leave empty.';
    if (frequency === 'custom' && !(Number(count) >= 1)) errs.interval_count = 'Enter how many days or months between invoices.';
    const rows = items.map((item, n) => [item, n]).filter(([item]) => !isBlank(item));
    if (!rows.length) errs.items = 'Add at least one item.';
    rows.forEach(([item, n]) => {
      const e = {};
      if (!item.description.trim()) e.description = 'Describe the item.';
      if (!(Number(item.quantity) > 0)) e.quantity = 'Enter a quantity above 0.';
      if (item.rate === '' || Number(item.rate) < 0) e.rate = 'Enter the rate.';
      if (item.discount_percent !== '' && (Number(item.discount_percent) < 0 || Number(item.discount_percent) > 100)) e.discount_percent = 'Use 0 to 100.';
      if (o.gst_applied && item.gst_rate === '' && !o.default_gst_rate) e.gst_rate = 'Choose a GST rate.';
      if (Object.keys(e).length) ierrs[n] = e;
    });
    setErrors(errs); setItemErrors(ierrs);
    return !Object.keys(errs).length && !Object.keys(ierrs).length;
  };

  const save = async () => {
    setFailure(null);
    if (!validate()) return;
    setBusy(true);
    const body = {
      name: name.trim(), customer: customerId, mode, frequency, start_date: start, end_date: end || null,
      max_occurrences: max === '' ? null : Number(max), due_days: dueDays === '' ? null : Number(dueDays),
      apply_terms: applyTerms, notes: applyTerms ? notes : '', terms: applyTerms ? terms : '', project: project || null, form: formBody(),
    };
    if (frequency === 'custom') { body.interval_unit = unit; body.interval_count = Number(count); }
    try {
      const saved = id ? await api.patch(`/sales/recurring-invoices/${id}/`, body) : await api.post('/sales/recurring-invoices/', body);
      notify(id ? 'Changes saved. They apply to the next invoices.' : 'Recurring invoice created.');
      navigate(`/sales/recurring/${saved.id}`);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };

  return (
    <>
      <datalist id="q-units">{UNITS.map((u) => <option key={u} value={u} />)}</datalist>
      <datalist id="q-sac">{SAC_CODES.map(([code, label]) => <option key={code} value={code}>{label}</option>)}</datalist>
      <Link to={id ? `/sales/recurring/${id}` : '/sales/recurring'} className="back-link">← {id ? 'Recurring invoice' : 'Recurring invoices'}</Link>
      <PageHeader title={`${id ? 'Edit' : 'New'} recurring invoice`}
        description="Describe the invoice once and how often it repeats. Each run creates a normal invoice, with the same numbering and GST as one you type yourself." />
      {id && <Notice>Changes apply to invoices made from now on. Invoices already created are not touched.</Notice>}
      <form className="card" noValidate onSubmit={(e) => e.preventDefault()}>
        <FormErrors error={failure} fields={[]} />
        {!o.gst_applied && <Notice>GST is not switched on for the company (Tax Configuration), so these invoices are printed without tax.</Notice>}

        <div className="form-grid">
          <Field id="r-name" label="Profile name" required error={errors.name} hint="For your own use, for example “Acme monthly retainer”.">
            <input id="r-name" value={name} maxLength={150} onChange={(e) => { setName(e.target.value); clear('name'); }} />
          </Field>
          <Field id="r-customer" label="Customer" required error={errors.customer}>
            <div className="toolbar">
              <select id="r-customer" className="grow" value={customerId} onChange={(e) => { setCustomerId(e.target.value); clear('customer'); }}>
                <option value="">Choose a customer…</option>
                {customer && !list.some((c) => c.id === customer.id) && <option value={customer.id}>{customer.display_name}</option>}
                {list.map((c) => <option key={c.id} value={c.id}>{c.display_name}{c.gstin ? ` · ${c.gstin}` : ''}</option>)}
              </select>
              <button type="button" className="btn" onClick={() => setNewCustomer(true)}>New customer</button>
            </div>
            {customer && <div className="hint">{customer.state ? `Place of supply ${customer.state}, taken from the customer at every run.` : 'No state on the customer: the company default place of supply is used.'}</div>}
          </Field>
        </div>

        <fieldset className="subcard">
          <legend>Schedule</legend>
          <div className="form-grid">
            <Field id="r-freq" label="Repeats" required error={errors.frequency}>
              <select id="r-freq" value={frequency} onChange={(e) => setFrequency(e.target.value)}>
                {FREQUENCIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </Field>
            {frequency === 'custom' && (
              <>
                <Field id="r-count" label="Every" required error={errors.interval_count}>
                  <input id="r-count" type="number" min="1" step="1" value={count} onChange={(e) => { setCount(e.target.value); clear('interval_count'); }} />
                </Field>
                <Field id="r-unit" label="Unit" error={errors.interval_unit}>
                  <select id="r-unit" value={unit} onChange={(e) => setUnit(e.target.value)}><option value="days">Days</option><option value="months">Months</option></select>
                </Field>
              </>
            )}
            <Field id="r-start" label="First invoice date" required error={errors.start_date}
              hint={locked ? 'Cannot change: invoices have already been made.' : !id && start && start < today() ? 'This is in the past: the invoices for the missed periods are created the next time the profile runs (up to 12 at a time).' : 'A profile that starts on the 31st bills on the last day of shorter months.'}>
              <input id="r-start" type="date" value={start} disabled={locked} onChange={(e) => { setStart(e.target.value); clear('start_date'); }} />
            </Field>
            <Field id="r-end" label="End date (optional)" error={errors.end_date}>
              <input id="r-end" type="date" value={end} onChange={(e) => { setEnd(e.target.value); clear('end_date'); }} />
            </Field>
            <Field id="r-max" label="Number of invoices (optional)" error={errors.max_occurrences} hint="Stops after this many. Leave both empty to repeat until you stop it.">
              <input id="r-max" type="number" min="1" step="1" value={max} onChange={(e) => { setMax(e.target.value); clear('max_occurrences'); }} />
            </Field>
          </div>
          {dates.length > 0 && (
            <p className="hint" aria-live="polite">Next invoices: {dates.map((d) => fmtDate(d)).join(', ')}{dates.length === 6 ? '…' : ''}</p>
          )}
        </fieldset>

        <fieldset className="subcard">
          <legend>What happens at each run</legend>
          <div className="form-grid">
            <Field id="r-mode" label="Each run" error={errors.mode}
              hint={canIssue ? 'Issued invoices are numbered, frozen and posted to the books at once. Drafts wait for someone to issue them.' : 'Your role can create drafts only.'}>
              <select id="r-mode" value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="draft">Create as a draft</option>
                <option value="issue" disabled={!canIssue && mode !== 'issue'}>Create and issue</option>
              </select>
            </Field>
            <Field id="r-due" label="Due after (days)" error={errors.due_days}
              hint={o.payment_terms_days != null ? `Leave empty to use the customer's or company's terms (${o.payment_terms_days} days).` : 'Leave empty to use the customer’s or company’s payment terms.'}>
              <input id="r-due" type="number" min="0" step="1" value={dueDays} onChange={(e) => { setDueDays(e.target.value); clear('due_days'); }} />
            </Field>
            <ProjectField id="r-project" value={project} onChange={setProject} current={p?.project_ref} error={errors.project} />
          </div>
        </fieldset>

        <fieldset className="subcard">
          <legend>Items on every invoice</legend>
          {errors.items && <p className="error-text" role="alert">{errors.items}</p>}
          {items.map((item, n) => (
            <ItemEditor key={n} item={item} n={n} many={items.length > 1} options={o} errors={itemErrors[n]} set={setItem(n)} remove={() => setItems((rows) => rows.filter((_, i) => i !== n))}
              replace={(next) => setItems((rows) => rows.map((r, i) => (i === n ? next : r)))} />
          ))}
          <button type="button" className="btn btn-small" onClick={() => setItems((rows) => [...rows, blankItem()])}>Add item</button>
          <Totals working={working} error={workingError} pending={pending} />
        </fieldset>

        <div className="form-grid">
          <Field id="r-subject" label="Subject (optional)" wide><input id="r-subject" value={meta.subject} maxLength={200} onChange={setM('subject')} /></Field>
          <Field id="r-ref" label="Customer's PO or reference (optional)"><input id="r-ref" value={meta.enquiry_ref} maxLength={100} onChange={setM('enquiry_ref')} /></Field>
          <label className="checkbox-inline field-wide"><input type="checkbox" checked={applyTerms} onChange={(e) => setApplyTerms(e.target.checked)} /> Apply these notes and terms to every invoice</label>
          {applyTerms && (
            <>
              <Field id="r-notes" label="Notes" wide error={errors.notes} hint="Printed under the totals."><textarea id="r-notes" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
              <Field id="r-terms" label="Terms and conditions" wide error={errors.terms} hint="Replaces the company's default terms on these invoices."><textarea id="r-terms" rows={3} value={terms} onChange={(e) => setTerms(e.target.value)} placeholder={o.default_terms} /></Field>
            </>
          )}
          {!applyTerms && <p className="hint field-wide">Without this, each invoice prints the company's default terms only.</p>}
          {o.bank_will_print && (
            <label className="checkbox-inline field-wide"><input type="checkbox" checked={meta.include_bank} onChange={setM('include_bank')} /> Print the bank details for payment</label>
          )}
          <label className="checkbox-inline field-wide"><input type="checkbox" checked={meta.show_signature} onChange={setM('show_signature')} /> Print the "For (company name) / Authorised Signatory" block</label>
        </div>
        <div className="actions">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={save}>{busy ? 'Saving…' : id ? 'Save changes' : 'Create profile'}</button>
          <Link className="btn" to={id ? `/sales/recurring/${id}` : '/sales/recurring'}>Cancel</Link>
        </div>
      </form>
      {newCustomer && <CustomerDialog onClose={() => setNewCustomer(false)} onSaved={(c) => { setNewCustomer(false); customers.reload(); setCustomerId(c.id); }} />}
    </>
  );
}
