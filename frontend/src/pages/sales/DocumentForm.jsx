import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { plain, today } from '../../format';
import { asList, useLoad } from '../../hooks';
import { ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, fmtDate, useConfirm, useToast } from '../../ui';
import { ItemEditor, SAC_CODES, Totals, UNITS, blankItem, isBlank, itemsPayload } from '../Quotations';
import { CustomerDialog } from './Customers';
import { ProjectField, kindLabel } from './common';

const NOTE_KINDS = ['credit_note', 'debit_note'];

// New or edited draft: invoice, proforma, credit note or debit note. Totals come from the server as you type.
export default function DocumentForm() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const existing = useLoad(() => (id ? api.get(`/sales/documents/${id}/`) : Promise.resolve(null)), [id]);
  const kind = existing.data?.kind || params.get('kind') || 'invoice';
  const isNote = NOTE_KINDS.includes(kind);
  const options = useLoad(() => api.get('/sales/documents/options/', { kind }), [kind]);
  const customers = useLoad(() => api.get('/sales/customers/', { active: 'true' }), []);

  const [customerId, setCustomerId] = useState(params.get('customer') || '');
  const [issueDate, setIssueDate] = useState(today());
  const [dueDate, setDueDate] = useState('');
  const [against, setAgainst] = useState(params.get('against') || '');
  const [reason, setReason] = useState('');
  const [items, setItems] = useState([blankItem()]);
  const [meta, setMeta] = useState({ subject: '', enquiry_ref: '', notes: '', terms: '', include_bank: true, show_signature: false });
  const [sourceQuotation, setSourceQuotation] = useState('');
  const [project, setProject] = useState('');
  const [errors, setErrors] = useState({});
  const [itemErrors, setItemErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [newCustomer, setNewCustomer] = useState(false);
  const [suggested, setSuggested] = useState(null); // the customer written on the quotation, when no customer record matches it
  const [fromQuote, setFromQuote] = useState(null);
  const [working, setWorking] = useState(null);
  const [workingError, setWorkingError] = useState('');
  const [autoDue, setAutoDue] = useState('');
  const [pending, setPending] = useState(false);
  const ticket = useRef(0);
  const filled = useRef(false);

  const invoicesOfCustomer = useLoad(
    () => (isNote && customerId ? api.get('/sales/documents/', { kind: 'invoice', customer: customerId }) : Promise.resolve(null)), [isNote, customerId]);

  // Fill the form once: from the draft being edited, a quotation being converted, or the invoice a note is raised against.
  useEffect(() => {
    if (filled.current) return;
    const d = existing.data;
    if (id) {
      if (!d) return;
      filled.current = true;
      setCustomerId(d.customer.id); setIssueDate(d.issue_date); setDueDate(d.due_date || '');
      setAgainst(d.against?.id || ''); setReason(d.reason || '');
      const f = d.form || {};
      setItems((f.items || []).map((i) => ({ ...blankItem(), ...i, gst_rate: i.gst_rate ?? '', discount_percent: i.discount_percent ?? '', hsn_sac: i.hsn_sac || '', unit: i.unit || '' })));
      setMeta({ subject: f.subject || '', enquiry_ref: f.enquiry_ref || '', notes: f.notes || '', terms: f.terms || '', include_bank: f.include_bank !== false, show_signature: !!f.show_signature });
      setSourceQuotation(d.source_quotation?.id || '');
      setProject(d.project || '');
      return;
    }
    const quotation = params.get('quotation');
    const base = params.get('against');
    if (quotation) {
      filled.current = true;
      api.get(`/sales/quotations/${quotation}/prefill/`).then((p) => {
        setItems(p.form.items.map((i) => ({ ...blankItem(), ...i, gst_rate: i.gst_rate ?? '' })));
        setMeta((m) => ({ ...m, subject: p.form.subject || '', enquiry_ref: p.form.enquiry_ref || '', notes: p.form.notes || '', terms: p.form.terms || '',
          show_signature: !!p.form.show_signature }));
        setSourceQuotation(p.quotation.id);
        setFromQuote({ number: p.quotation.number, gst: p.gst_applied ? (p.supply_type === 'intra_state' ? 'same state (CGST and SGST)' : 'other state (IGST)') : 'no GST',
          place: p.place_of_supply?.name || '' });
        if (p.matched_customer) setCustomerId(p.matched_customer);
        else {
          const c = p.customer || {};
          setSuggested({ display_name: p.customer_name, gstin: c.gstin || '', state_code: c.state_code || '', address_line1: (c.address || [])[0] || '',
            address_line2: (c.address || []).slice(1).join(', '), gst_treatment: c.gstin ? 'registered' : 'unregistered' });
          notify(`Choose or add the customer "${p.customer_name}".`, 'info');
        }
      }).catch((e) => notify(e.message, 'error'));
    } else if (base) {
      filled.current = true;
      api.get(`/sales/documents/${base}/`).then((inv) => { setCustomerId(inv.customer.id); setAgainst(inv.id); }).catch((e) => notify(e.message, 'error'));
    } else {
      filled.current = true;
    }
  }, [existing.data, id]); // eslint-disable-line react-hooks/exhaustive-deps

  const form = () => {
    const f = { items: itemsPayload(items), include_bank: meta.include_bank, show_signature: meta.show_signature };
    for (const k of ['subject', 'enquiry_ref', 'notes', 'terms']) if (meta[k].trim()) f[k] = meta[k].trim();
    return f;
  };

  const used = itemsPayload(items);
  const ready = customerId && used.length > 0 && used.every((i) => i.quantity !== '' && i.rate !== '');
  const signature = JSON.stringify([kind, customerId, issueDate, dueDate, items]);
  useEffect(() => {
    if (!options.data) return undefined;
    if (!ready) { setWorking(null); setWorkingError(''); return undefined; }
    const mine = ++ticket.current;
    setPending(true);
    const timer = setTimeout(async () => {
      try {
        const r = await api.post('/sales/documents/working/', { kind, customer: customerId, issue_date: issueDate || today(), due_date: dueDate || undefined, form: form() });
        if (mine === ticket.current) { setWorking(r.record); setAutoDue(r.due_date || ''); setWorkingError(''); }
      } catch (e) { if (mine === ticket.current) { setWorking(null); setWorkingError(e.message); } }
      if (mine === ticket.current) setPending(false);
    }, 500);
    return () => clearTimeout(timer);
  }, [signature, options.data]); // eslint-disable-line react-hooks/exhaustive-deps

  if (existing.loading || options.loading || customers.loading) return <Loading />;
  if (existing.error) return <ErrorBox error={existing.error} onRetry={existing.reload} />;
  if (options.error) return <ErrorBox error={options.error} onRetry={options.reload} />;
  if (existing.data && existing.data.status !== 'draft') return <Notice type="warn">Only a draft can be edited. <Link to={`/sales/documents/${id}`}>Back to the document</Link></Notice>;
  const o = options.data;
  const list = asList(customers.data);
  const customer = list.find((c) => c.id === customerId) || (existing.data && existing.data.customer.id === customerId ? { id: customerId, display_name: existing.data.customer.name, address_lines: [] } : null);
  const issuedInvoices = asList(invoicesOfCustomer.data?.results).filter((d) => d.status === 'issued');
  const noun = kindLabel(kind);
  const canIssue = user.permissions.includes('sales.issue');
  const setM = (k) => (e) => setMeta((m) => ({ ...m, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }));
  const setItem = (n) => (k, v) => { setItems((rows) => rows.map((r, i) => (i === n ? { ...r, [k]: v } : r))); setItemErrors((x) => ({ ...x, [n]: undefined })); };

  const validate = () => {
    const errs = {}, ierrs = {};
    if (!customerId) errs.customer = 'Choose the customer.';
    if (!issueDate) errs.issue_date = 'Choose the date.';
    if (dueDate && issueDate && dueDate < issueDate) errs.due_date = 'The due date cannot be before the document date.';
    if (isNote && !against) errs.against = 'Choose the invoice this note is raised against.';
    if (isNote && !reason.trim()) errs.reason = 'Give the reason for this note.';
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

  const save = async (intent) => {
    setFailure(null);
    if (!validate()) return;
    if (intent === 'issue' && !(await confirm({
      title: `Issue this ${noun.toLowerCase()}?`,
      body: <p>A number is allocated now and the document is frozen. If it is wrong later you can only void it and make a new one.</p>, confirmLabel: 'Issue',
    }))) return;
    setBusy(true);
    let saved;
    try {
      const body = { kind, customer: customerId, issue_date: issueDate, due_date: dueDate || undefined, form: form(), source_quotation: sourceQuotation || undefined, project: project || null };
      if (isNote) { body.against = against; body.reason = reason.trim(); }
      saved = id ? await api.patch(`/sales/documents/${id}/`, body) : await api.post('/sales/documents/', body);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); setBusy(false); return; }
    if (intent === 'issue') {
      try {
        saved = await api.post(`/sales/documents/${saved.id}/issue/`);
        notify(`${noun} issued: ${saved.number}`);
      } catch (err) {
        notify(`Saved as a draft, but it could not be issued: ${err.message}`, 'error');
      }
    } else {
      notify('Draft saved.');
    }
    setBusy(false);
    navigate(`/sales/documents/${saved.id}`);
  };

  return (
    <>
      <datalist id="q-units">{UNITS.map((u) => <option key={u} value={u} />)}</datalist>
      <datalist id="q-sac">{SAC_CODES.map(([code, label]) => <option key={code} value={code}>{label}</option>)}</datalist>
      <Link to="/sales/documents" className="back-link">← Invoices and notes</Link>
      <PageHeader title={`${id ? 'Edit' : 'New'} ${noun.toLowerCase()}`}
        description={isNote ? 'Raised against an issued invoice. A credit note reduces what the customer owes.' : 'Totals and GST are worked out for you. Save a draft to come back later, or issue it to get its number.'} />
      <form className="card" noValidate onSubmit={(e) => e.preventDefault()}>
        <FormErrors error={failure} fields={[]} />
        {!o.gst_applied && <Notice>GST is not switched on for the company (Tax Configuration), so this document is printed without tax.</Notice>}
        {fromQuote && (
          <div className="notice notice-info" role="status">
            <span>Started from quotation <strong>{fromQuote.number}</strong>: items, GST rates, notes and terms are copied; the invoice keeps a link back to it.
              {' '}GST on the quotation was {fromQuote.gst}{fromQuote.place ? `, place of supply ${fromQuote.place}` : ''}; on this invoice it follows the customer you choose.</span>
          </div>
        )}

        <div className="form-grid">
          <Field id="d-customer" label="Customer" required error={errors.customer} wide>
            <div className="toolbar">
              <select id="d-customer" className="grow" value={customerId} onChange={(e) => { setCustomerId(e.target.value); setAgainst(''); setErrors((x) => ({ ...x, customer: undefined })); }}>
                <option value="">Choose a customer…</option>
                {customer && !list.some((c) => c.id === customer.id) && <option value={customer.id}>{customer.display_name}</option>}
                {list.map((c) => <option key={c.id} value={c.id}>{c.display_name}{c.gstin ? ` · ${c.gstin}` : ''}</option>)}
              </select>
              <button type="button" className="btn" onClick={() => setNewCustomer(true)}>New customer</button>
            </div>
            {customer && (customer.address_lines || []).length > 0 && (
              <div className="hint">{customer.address_lines.join(', ')}{customer.state ? ` · place of supply ${customer.state}` : ''}</div>
            )}
          </Field>
          <Field id="d-date" label={`${noun} date`} required error={errors.issue_date}><input id="d-date" type="date" value={issueDate} onChange={(e) => setIssueDate(e.target.value)} /></Field>
          {!isNote && kind !== 'proforma_invoice' && (
            <Field id="d-due" label="Due date" error={errors.due_date} hint={autoDue && !dueDate ? `Leave empty for ${fmtDate(autoDue)} (customer or company payment terms).` : 'Leave empty to use the payment terms.'}>
              <input id="d-due" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
            </Field>
          )}
          {isNote && (
            <>
              <Field id="d-against" label="Against invoice" required error={errors.against} hint={customerId ? undefined : 'Choose the customer first.'}>
                <select id="d-against" value={against} onChange={(e) => setAgainst(e.target.value)} disabled={!customerId}>
                  <option value="">Choose an invoice…</option>
                  {issuedInvoices.map((d) => <option key={d.id} value={d.id}>{d.number} · {plain(d.grand_total)}</option>)}
                </select>
              </Field>
              <Field id="d-reason" label="Reason" required error={errors.reason} wide><input id="d-reason" value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} placeholder={kind === 'credit_note' ? 'e.g. Goods returned, price correction' : 'e.g. Late payment charge'} /></Field>
            </>
          )}
          <ProjectField id="d-project" value={project} onChange={setProject} current={existing.data?.project_ref} error={errors.project} />
          <Field id="d-ref" label="Customer's PO or reference" error={errors.enquiry_ref}><input id="d-ref" value={meta.enquiry_ref} maxLength={100} onChange={setM('enquiry_ref')} /></Field>
          <Field id="d-subject" label="Subject" error={errors.subject} wide><input id="d-subject" value={meta.subject} maxLength={200} onChange={setM('subject')} /></Field>
        </div>

        <fieldset className="subcard">
          <legend>Items</legend>
          {errors.items && <p className="error-text" role="alert">{errors.items}</p>}
          {items.map((item, n) => (
            <ItemEditor key={n} item={item} n={n} many={items.length > 1} options={o} errors={itemErrors[n]} set={setItem(n)} remove={() => setItems((rows) => rows.filter((_, i) => i !== n))} />
          ))}
          <button type="button" className="btn btn-small" onClick={() => setItems((rows) => [...rows, blankItem()])}>Add item</button>
          <Totals working={working} error={workingError} pending={pending} />
        </fieldset>

        <div className="form-grid">
          <Field id="d-notes" label="Notes (optional)" wide hint="Printed under the totals."><textarea id="d-notes" rows={2} value={meta.notes} onChange={setM('notes')} /></Field>
          {kind !== 'credit_note' && (
            <Field id="d-terms" label="Terms and conditions" wide hint={o.default_terms ? 'Leave empty to print the company default terms. Type here to replace them for this document.' : 'No default terms are set under Company Policies. Optional.'}>
              <textarea id="d-terms" rows={3} value={meta.terms} onChange={setM('terms')} placeholder={o.default_terms} />
            </Field>
          )}
          {o.bank_will_print && kind !== 'credit_note' && (
            <label className="field field-wide checkbox"><input type="checkbox" checked={meta.include_bank} onChange={setM('include_bank')} /> Print the bank details for payment</label>
          )}
          <label className="field field-wide checkbox"><input type="checkbox" checked={meta.show_signature} onChange={setM('show_signature')} /> Print the "For (company name) / Authorised Signatory" block (leave off when you stamp the company seal)</label>
        </div>
        <div className="actions">
          <button type="button" className="btn" disabled={busy} onClick={() => save('draft')}>{busy ? 'Saving…' : 'Save as draft'}</button>
          {canIssue && <button type="button" className="btn btn-primary" disabled={busy} onClick={() => save('issue')}>Save and issue</button>}
          <Link className="btn" to={id ? `/sales/documents/${id}` : '/sales/documents'}>Cancel</Link>
        </div>
      </form>
      {newCustomer && <CustomerDialog initial={suggested} onClose={() => setNewCustomer(false)} onSaved={(c) => { setNewCustomer(false); customers.reload(); setCustomerId(c.id); }} />}
      {confirmDialog}
    </>
  );
}
