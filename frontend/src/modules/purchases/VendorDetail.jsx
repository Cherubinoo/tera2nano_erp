import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { useAuth } from '../../context';
import { money, num, plain } from '../../format';
import { asList, useLoad } from '../../hooks';
import { Badge, ErrorBox, Loading, PageHeader, Tabs, fmtDate, useToast } from '../../ui';
import { Status } from './common';
import { VendorDialog } from './Vendors';

function Tile({ label, value, tone, hint }) {
  return <div className={`tile ${tone || ''}`}><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</div>;
}

function Rows({ url, params, cols, empty }) {
  const { data, error, loading } = useLoad(() => api.get(url, params), [url, JSON.stringify(params)]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const rows = asList(data);
  if (!rows.length) return <p className="empty">{empty}</p>;
  return (
    <div className="table-wrap"><table>
      <thead><tr>{cols.map((c) => <th key={c.h} className={c.amount ? 'amount' : ''}>{c.h}</th>)}</tr></thead>
      <tbody>{rows.map((r) => <tr key={r.id}>{cols.map((c) => <td key={c.h} data-label={c.h} className={c.amount ? 'amount' : ''}>{c.v(r)}</td>)}</tr>)}</tbody>
    </table></div>
  );
}

function Statement({ id }) {
  const { data, error, loading } = useLoad(() => api.get(`/purchases/vendors/${id}/statement/`), [id]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data.rows.length) return <p className="empty">No bills or payments yet.</p>;
  return (
    <div className="table-wrap"><table>
      <caption className="sr-only">Statement: what we owe the vendor after each entry</caption>
      <thead><tr><th>Date</th><th>Type</th><th>Number</th><th>Vendor's reference</th><th className="amount">Bills</th><th className="amount">Payments</th><th className="amount">Balance owed</th></tr></thead>
      <tbody>{data.rows.map((r) => (
        <tr key={`${r.kind}${r.id}`}>
          <td data-label="Date">{fmtDate(r.date)}</td><td data-label="Type">{r.type}</td><td data-label="Number">{r.number}</td><td data-label="Vendor's reference">{r.reference || '—'}</td>
          <td className="amount" data-label="Bills">{num(r.credit) ? plain(r.credit) : '—'}</td><td className="amount" data-label="Payments">{num(r.debit) ? plain(r.debit) : '—'}</td>
          <td className="amount" data-label="Balance owed"><strong>{plain(r.balance)}</strong></td>
        </tr>))}</tbody>
    </table></div>
  );
}

export default function VendorDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const [tab, setTab] = useState('overview');
  const [editing, setEditing] = useState(false);
  const notify = useToast();
  const { data: v, error, loading, reload } = useLoad(() => api.get(`/purchases/vendors/${id}/`), [id]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const canManage = user.permissions.includes('purchases.manage');
  const q = { vendor: id };
  const tabs = [['overview', 'Overview'], ['bills', 'Bills'], ['payments', 'Payments'], ['orders', 'Orders'], ['quotations', 'Quotations'], ['documents', 'Documents'], ['statement', 'Statement']];
  const detail = (label, value) => <div><dt>{label}</dt><dd>{value || '—'}</dd></div>;
  return (
    <>
      <Link to="/purchases/vendors" className="back-link">← Vendors</Link>
      <PageHeader title={v.name} description={`${v.code}${v.gstin ? ` · GSTIN ${v.gstin}` : ''}`}
        actions={<>{!v.is_active && <Badge>Inactive</Badge>}
          {canManage && <><Link className="btn" to={`/purchases/bills/new?vendor=${id}`}>New bill</Link><Link className="btn" to={`/purchases/orders/new?vendor=${id}`}>New order</Link>
            <Link className="btn" to={`/purchases/payments?new=1&vendor=${id}`}>Record payment</Link><button type="button" className="btn" onClick={() => setEditing(true)}>Edit</button>
            {v.is_active && <button type="button" className="btn" onClick={async () => {
              if (!window.confirm(`Switch off ${v.name}?`)) return;
              try { await api.del(`/purchases/vendors/${id}/`); notify('Vendor deleted.'); window.location.assign('/purchases/vendors'); }
              catch (e) { notify(e.message, 'error'); }
            }}>Delete vendor</button>}
          </>}</>} />
      <div className="tiles">
        <Tile label="Billed" value={money(v.billed)} />
        <Tile label="Paid" value={money(v.paid)} />
        <Tile label="Outstanding" value={money(v.outstanding)} hint={num(v.advance) > 0 ? `${money(v.advance)} paid in advance` : undefined} />
        <Tile label="Overdue" value={money(v.overdue)} tone={num(v.overdue) > 0 ? 'bad' : ''} />
      </div>
      <Tabs tabs={tabs.map(([key, label]) => ({ key, label }))} value={tab} onChange={setTab} />
      <section className="card">
        {tab === 'overview' && (
          <dl className="pur-facts">
            {detail('Contact person', v.contact_person)}{detail('Email', v.email)}{detail('Phone', v.phone)}
            {detail('GST registration', v.gst_treatment)}{detail('State', v.state && `${v.state} (${v.state_code})`)}{detail('PAN', v.pan)}
            {detail('Address', v.address_lines.join(', '))}{detail('Payment terms', v.payment_terms_days !== null && `${v.payment_terms_days} days`)}
            {detail('Default TDS', v.tds_section && `${v.tds_section} at ${v.tds_rate}%`)}
            {detail('Opening balance', num(v.opening_balance) > 0 && `${money(v.opening_balance)} as on ${fmtDate(v.opening_balance_date)}`)}
            {detail('Bank', [v.bank_account_holder, v.bank_name, v.bank_account_number, v.bank_ifsc].filter(Boolean).join(' · '))}{detail('Notes', v.notes)}
          </dl>
        )}
        {tab === 'bills' && <Rows url="/purchases/bills/" params={q} empty="No bills from this vendor." cols={[
          { h: 'Date', v: (r) => fmtDate(r.bill_date) }, { h: 'Number', v: (r) => <Link to={`/purchases/bills/${r.id}`}>{r.number || 'Draft'}</Link> },
          { h: 'Vendor bill no', v: (r) => r.vendor_bill_no }, { h: 'Due', v: (r) => fmtDate(r.due_date) }, { h: 'Status', v: (r) => <Status k={r.status_key} label={r.status_label} /> },
          { h: 'Total', amount: true, v: (r) => plain(r.grand_total) }, { h: 'Balance', amount: true, v: (r) => (r.balance === null ? '—' : plain(r.balance)) }]} />}
        {tab === 'payments' && <Rows url="/purchases/payments/" params={q} empty="No payments to this vendor." cols={[
          { h: 'Date', v: (r) => fmtDate(r.date) }, { h: 'Number', v: (r) => r.number }, { h: 'Mode', v: (r) => r.mode_label }, { h: 'Reference', v: (r) => r.reference || '—' },
          { h: 'Status', v: (r) => <Status k={r.status} label={r.status === 'void' ? 'Void' : 'Recorded'} /> },
          { h: 'Amount', amount: true, v: (r) => plain(r.amount) }, { h: 'Unapplied', amount: true, v: (r) => plain(r.unapplied) }]} />}
        {tab === 'orders' && <Rows url="/purchases/orders/" params={q} empty="No purchase orders for this vendor." cols={[
          { h: 'Date', v: (r) => fmtDate(r.order_date) }, { h: 'Number', v: (r) => <Link to={`/purchases/orders/${r.id}`}>{r.number || 'Draft'}</Link> },
          { h: 'Status', v: (r) => <Status k={r.status} label={r.status_label} /> }, { h: 'Total', amount: true, v: (r) => plain(r.grand_total) }]} />}
        {tab === 'quotations' && <Rows url="/purchases/quotations/" params={q} empty="No quotations from this vendor." cols={[
          { h: 'Date', v: (r) => fmtDate(r.quote_date) }, { h: 'Number', v: (r) => r.number }, { h: 'Their ref', v: (r) => r.vendor_ref || '—' },
          { h: 'Valid until', v: (r) => fmtDate(r.valid_until) }, { h: 'Status', v: (r) => <Status k={r.status_key} label={r.status_label} /> }, { h: 'Total', amount: true, v: (r) => plain(r.grand_total) }]} />}
        {tab === 'documents' && <Attachments entityType="vendor" entityId={id} canManage={canManage} title="Vendor documents" hint="Agreements, registration certificates, cancelled cheques." />}
        {tab === 'statement' && <Statement id={id} />}
      </section>
      {editing && <VendorDialog vendor={v} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} />}
    </>
  );
}
