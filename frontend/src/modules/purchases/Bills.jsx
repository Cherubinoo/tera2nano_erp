import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { useAuth } from '../../context';
import { money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, useReasonDialog, useToast, fmtDate } from '../../ui';
import { blankItem, itemsFromRecord, itemsPayload, ItemsEditor, LinesTable, ProjectSelect, Status, Totals, useVendors, usePurchaseOptions, useWorking, validateItems, VendorSelect } from './common';
import { ApprovalPanel } from '../approvals/ApprovalBadge';
import DocList from './DocList';

const STATUSES = [['', 'All'], ['draft', 'Draft'], ['pending_approval', 'Pending approval'], ['open', 'Unpaid'], ['overdue', 'Overdue'], ['partial', 'Partially paid'], ['paid', 'Paid'], ['void', 'Void']];

export function Bills() {
  const { user } = useAuth();
  const [params] = useSearchParams();
  return (
    <DocList title="Bills" description="What vendors have billed you, and what is still unpaid." url="/purchases/bills/" statuses={STATUSES}
      searchHint="Our number, the vendor's bill number or vendor" emptyText="No bills yet." rowTo={(r) => `/purchases/bills/${r.id}`}
      extraParams={params.get('vendor') ? { vendor: params.get('vendor') } : undefined} totals={[['Total', 'total_amount'], ['Balance due', 'total_balance']]}
      actions={user.permissions.includes('purchases.manage') && <Link className="btn btn-primary" to="/purchases/bills/new">New bill</Link>}
      csv={{ file: 'bills.csv', head: ['Date', 'Number', 'Vendor bill no', 'Vendor', 'Due date', 'Status', 'ITC', 'Taxable', 'GST', 'Total', 'TDS', 'Net payable', 'Balance'],
        row: (r) => [r.bill_date, r.number, r.vendor_bill_no, r.vendor.name, r.due_date || '', r.status_label, r.itc_status, r.taxable_total, r.tax_total, r.grand_total, r.tds_amount, r.net_payable, r.balance ?? ''] }}
      columns={[{ h: 'Date', v: (r) => fmtDate(r.bill_date) }, { h: 'Number', v: (r) => <strong>{r.number || 'Draft'}</strong> }, { h: 'Vendor bill no', v: (r) => r.vendor_bill_no },
        { h: 'Vendor', v: (r) => r.vendor.name }, { h: 'Due', v: (r) => fmtDate(r.due_date) }, { h: 'Status', v: (r) => <Status k={r.status_key} label={r.status_label} /> },
        { h: 'Total', amount: true, v: (r) => plain(r.grand_total) }, { h: 'Balance', amount: true, v: (r) => (r.balance === null ? '—' : <strong>{plain(r.balance)}</strong>) }]} />
  );
}

export function BillForm() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const notify = useToast();
  const opts = usePurchaseOptions();
  const { vendors } = useVendors();
  const existing = useLoad(() => (id ? api.get(`/purchases/bills/${id}/`) : Promise.resolve(null)), [id]);
  const [f, setF] = useState({ vendor: params.get('vendor') || '', vendor_bill_no: '', bill_date: today(), due_date: '', itc_status: 'eligible', tds_section: '', tds_rate: '',
    expense_account: '', project: '', notes: '' });
  const [items, setItems] = useState([blankItem()]);
  const [errors, setErrors] = useState({});
  const [itemErrors, setItemErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [filled, setFilled] = useState(!id);
  useEffect(() => {
    const b = existing.data;
    if (!b || filled) return;
    setFilled(true);
    setF({ vendor: b.vendor.id, vendor_bill_no: b.vendor_bill_no, bill_date: b.bill_date, due_date: b.due_date || '', itc_status: b.itc_status, tds_section: b.tds_section,
      tds_rate: num(b.tds_rate) ? b.tds_rate : '', expense_account: b.expense_account || '', project: b.project || '', notes: b.notes });
    setItems(itemsFromRecord(b.record.lines));
  }, [existing.data, filled]);
  // choosing a vendor suggests its TDS default and payment terms
  const pickVendor = (vid) => {
    const v = vendors.find((x) => x.id === vid);
    setF((x) => ({ ...x, vendor: vid, tds_section: v?.tds_section || '', tds_rate: v?.tds_rate && v.tds_section ? String(num(v.tds_rate)) : '' }));
  };
  const working = useWorking({ vendor: f.vendor, items, tds_section: f.tds_section, tds_rate: f.tds_rate, bill_date: f.bill_date, withAccount: true });
  if (existing.loading || opts.loading) return <Loading />;
  if (existing.error) return <ErrorBox error={existing.error} />;
  if (existing.data && existing.data.status !== 'draft') return <Notice type="warn">A recorded bill cannot be edited. <Link to={`/purchases/bills/${id}`}>Back to the bill</Link></Notice>;
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  const vendor = vendors.find((v) => v.id === f.vendor);

  const save = async (intent) => {
    setFailure(null);
    const e = {}, ie = validateItems(items);
    if (!f.vendor) e.vendor = 'Choose the vendor.';
    if (!f.vendor_bill_no.trim()) e.vendor_bill_no = "Enter the number printed on the vendor's bill.";
    if (!f.bill_date) e.bill_date = 'Choose the bill date.';
    if (f.due_date && f.due_date < f.bill_date) e.due_date = 'The due date cannot be before the bill date.';
    if (f.tds_section && f.tds_rate === '') e.tds_rate = 'Enter the TDS rate.';
    if (f.tds_rate !== '' && !f.tds_section) e.tds_section = 'Choose the TDS section.';
    if (ie._all) e.items = ie._all;
    setErrors(e); setItemErrors(ie._all ? {} : ie);
    if (Object.keys(e).length || (!ie._all && Object.keys(ie).length)) return;
    setBusy(true);
    try {
      const body = { vendor: f.vendor, vendor_bill_no: f.vendor_bill_no.trim(), bill_date: f.bill_date, due_date: f.due_date || null, itc_status: f.itc_status,
        tds_section: f.tds_section, tds_rate: f.tds_rate === '' ? null : f.tds_rate, expense_account: f.expense_account || null, project: f.project || null, notes: f.notes,
        items: itemsPayload(items, true), order: existing.data?.order?.id || params.get('order') || null };
      let saved = id ? await api.patch(`/purchases/bills/${id}/`, body) : await api.post('/purchases/bills/', body);
      if (intent === 'record') {
        try { saved = await api.post(`/purchases/bills/${saved.id}/record/`); notify(saved.status === 'pending_approval' ? 'Bill sent for approval. It is recorded once approved.' : `Bill recorded: ${saved.number}`); } catch (err) { notify(`Saved as a draft, but not recorded: ${err.message}`, 'error'); }
      } else notify('Draft saved.');
      navigate(`/purchases/bills/${saved.id}`);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <>
      <Link to={id ? `/purchases/bills/${id}` : '/purchases/bills'} className="back-link">← Bills</Link>
      <PageHeader title={`${id ? 'Edit' : 'New'} bill`} description="Recording a bill posts it to the books: expense, input GST (if the credit is eligible) and what you owe the vendor." />
      <form className="card" noValidate onSubmit={(e) => e.preventDefault()}>
        <FormErrors error={failure} fields={[{ name: 'vendor' }, { name: 'vendor_bill_no' }, { name: 'bill_date' }, { name: 'due_date' }, { name: 'items' }, { name: 'tds_section' }, { name: 'tds_rate' }]} />
        <div className="form-grid">
          <VendorSelect value={f.vendor} onChange={pickVendor} vendors={vendors} error={errors.vendor} />
          <Field id="b-no" label="Vendor's bill number" required error={errors.vendor_bill_no}><input id="b-no" value={f.vendor_bill_no} maxLength={100} onChange={set('vendor_bill_no')} /></Field>
          <Field id="b-date" label="Bill date" required error={errors.bill_date}><input id="b-date" type="date" value={f.bill_date} onChange={set('bill_date')} /></Field>
          <Field id="b-due" label="Due date" error={errors.due_date} hint={vendor?.payment_terms_days != null ? `Leave empty for ${vendor.payment_terms_days} days after the bill date.` : 'Leave empty for due on the bill date.'}>
            <input id="b-due" type="date" value={f.due_date} onChange={set('due_date')} /></Field>
          <Field id="b-itc" label="Input tax credit" error={errors.itc_status}>
            <select id="b-itc" value={f.itc_status} onChange={set('itc_status')}>{opts.data.itc.map((i) => <option key={i.value} value={i.value}>{i.label}</option>)}</select></Field>
          <Field id="b-account" label="Default expense account"><select id="b-account" value={f.expense_account} onChange={set('expense_account')}>
            <option value="">Purchases (5000)</option>{opts.data.expense_accounts.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</select></Field>
          <ProjectSelect id="b-project" value={f.project} onChange={(v) => setF((x) => ({ ...x, project: v }))} />
        </div>
        <fieldset className="subcard">
          <legend>Items</legend>
          {errors.items && <p className="error-text" role="alert">{errors.items}</p>}
          <ItemsEditor items={items} setItems={setItems} rates={opts.data.gst_rates} errors={itemErrors} accounts={opts.data.expense_accounts} idPrefix="bill" />
        </fieldset>
        <fieldset className="subcard">
          <legend>TDS deducted (optional)</legend>
          <div className="form-grid">
            <Field id="b-tds-section" label="Section" error={errors.tds_section}><select id="b-tds-section" value={f.tds_section} onChange={(e) => {
              const t = opts.data.tds_sections.find((x) => x.section === e.target.value);
              setF((x) => ({ ...x, tds_section: e.target.value, tds_rate: t ? t.rate : '' }));
            }}><option value="">No TDS</option>{opts.data.tds_sections.map((t) => <option key={t.section} value={t.section}>{t.label}</option>)}</select></Field>
            <Field id="b-tds-rate" label="Rate (%)" error={errors.tds_rate} hint="Worked out on the taxable value, before GST."><input id="b-tds-rate" type="number" min="0" max="100" step="0.01" value={f.tds_rate} onChange={set('tds_rate')} /></Field>
          </div>
          <Totals working={working} cur={opts.data.currency} showTds />
        </fieldset>
        <Field id="b-notes" label="Notes" wide><textarea id="b-notes" rows={2} value={f.notes} onChange={set('notes')} /></Field>
        <div className="actions">
          <button type="button" className="btn" disabled={busy} onClick={() => save('draft')}>{busy ? 'Saving…' : 'Save as draft'}</button>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => save('record')}>Save and record</button>
          <Link className="btn" to={id ? `/purchases/bills/${id}` : '/purchases/bills'}>Cancel</Link>
        </div>
      </form>
    </>
  );
}

export function BillDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const { data: b, error, loading, reload } = useLoad(() => api.get(`/purchases/bills/${id}/`), [id]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const canManage = user.permissions.includes('purchases.manage');
  const act = async (path, msg) => { try { const r = await api.post(`/purchases/bills/${id}/${path}/`); notify(path === 'record' && r.status === 'pending_approval' ? 'Bill sent for approval. It is recorded once approved.' : msg); reload(); } catch (e) { notify(e.message, 'error'); } };
  const voidIt = async () => {
    const reason = await ask({ title: 'Void bill', message: 'The entry in the books is reversed.', confirmLabel: 'Void bill' });
    if (reason === null) return;
    try { await api.post(`/purchases/bills/${id}/void/`, { change_reason: reason }); notify('Bill voided.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  return (
    <>
      <Link to="/purchases/bills" className="back-link">← Bills</Link>
      <PageHeader title={b.number || 'Draft bill'} description={`${b.vendor.name} · vendor's bill ${b.vendor_bill_no}`}
        actions={<>
          <Status k={b.status_key} label={b.status_label} />
          <ApprovalPanel kind="vendor_bill" sourceId={b.id} waiting={b.status === 'pending_approval'} canSubmit={canManage} onChange={reload} refreshKey={b.status} />
          {canManage && b.status === 'pending_approval' && <button type="button" className="btn" onClick={() => act('withdraw', 'Bill taken back as a draft.')}>Take back</button>}
          {canManage && b.status === 'pending_approval' && !b.approval_managed && <><button type="button" className="btn btn-primary" onClick={() => act('approve', 'Bill approved and recorded.')}>Approve</button>
            <button type="button" className="btn" onClick={async () => { const reason = await ask({ title: 'Reject bill', confirmLabel: 'Reject' }); if (reason === null) return; try { await api.post(`/purchases/bills/${id}/reject/`, { change_reason: reason }); notify('Bill sent back to draft.'); reload(); } catch (e) { notify(e.message, 'error'); } }}>Reject</button></>}
          {canManage && b.status === 'draft' && <><Link className="btn" to={`/purchases/bills/${id}/edit`}>Edit</Link>
            <button type="button" className="btn btn-primary" onClick={() => act('record', 'Bill recorded.')}>Record bill</button>
            <button type="button" className="btn" onClick={async () => { if (window.confirm('Delete this draft?')) { try { await api.del(`/purchases/bills/${id}/`); navigate('/purchases/bills'); } catch (e) { notify(e.message, 'error'); } } }}>Delete</button></>}
          {canManage && b.status === 'open' && num(b.balance) > 0 && <Link className="btn btn-primary" to={`/purchases/payments?new=1&vendor=${b.vendor.id}&bill=${id}`}>Record payment</Link>}
          {canManage && b.status === 'open' && <button type="button" className="btn" onClick={voidIt}>Void</button>}
        </>} />
      {b.status === 'pending_approval' && <Notice>This bill is above an approval limit. It is not numbered or posted to the books until it is approved.</Notice>}
      {b.status === 'draft' && b.decision_note && <Notice type="warn">{b.decision_note}</Notice>}
      {b.status === 'void' && <Notice type="warn">Voided{b.void_reason ? `: ${b.void_reason}` : ''}</Notice>}
      <div className="tiles">
        <div className="tile"><div className="tile-label">Bill total</div><div className="tile-value">{money(b.grand_total)}</div></div>
        <div className="tile"><div className="tile-label">TDS withheld</div><div className="tile-value">{money(b.tds_amount)}</div>{b.tds_section && <div className="hint">{b.tds_section} at {num(b.tds_rate)}%</div>}</div>
        <div className="tile"><div className="tile-label">Paid</div><div className="tile-value">{money(b.paid)}</div></div>
        <div className={`tile ${b.status_key === 'overdue' ? 'bad' : ''}`}><div className="tile-label">Balance due</div><div className="tile-value">{b.balance === null ? '—' : money(b.balance)}</div><div className="hint">Due {fmtDate(b.due_date)}</div></div>
      </div>
      <section className="card">
        <dl className="pur-facts">
          <div><dt>Vendor</dt><dd><Link to={`/purchases/vendors/${b.vendor.id}`}>{b.vendor.name}</Link></dd></div>
          <div><dt>Bill date</dt><dd>{fmtDate(b.bill_date)}</dd></div>
          <div><dt>Input tax credit</dt><dd>{b.itc_label}</dd></div>
          {b.order && <div><dt>Purchase order</dt><dd><Link to={`/purchases/orders/${b.order.id}`}>{b.order.number}</Link></dd></div>}
          <div><dt>Notes</dt><dd>{b.notes || '—'}</dd></div>
        </dl>
        <LinesTable record={b.record} />
        <Totals working={{ record: b.record, tds: { tds_amount: b.tds_amount, net_payable: b.net_payable }, error: '', pending: false }} showTds />
      </section>
      {b.payments.length > 0 && (
        <section className="card"><h2>Payments applied</h2>
          <div className="table-wrap"><table><thead><tr><th>Date</th><th>Payment</th><th>Mode</th><th>Status</th><th className="amount">Amount</th></tr></thead>
            <tbody>{b.payments.map((p) => <tr key={p.id}><td data-label="Date">{fmtDate(p.date)}</td><td data-label="Payment">{p.number}</td><td data-label="Mode">{p.mode}</td>
              <td data-label="Status">{p.status === 'void' ? 'Void' : 'Recorded'}</td><td className="amount" data-label="Amount">{plain(p.amount)}</td></tr>)}</tbody></table></div></section>
      )}
      <section className="card"><Attachments entityType="vendor_bill" entityId={id} canManage={canManage} title="Scanned bill and documents" hint="Attach the vendor's invoice as a PDF or image." /></section>
      {dialog}
    </>
  );
}
