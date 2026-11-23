import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { useAuth } from '../../context';
import { money, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, ErrorBox, Field, FormErrors, Loading, PageHeader, Tabs, fmtDate, fmtDateTime, useConfirm, useToast } from '../../ui';
import { ActivityDialog, DEAL_TONE, DealStatus, TimelineList, Tile, quotationUrl, useLookups } from './common';
import { DealDialog, MoveMenu, useDealMover } from './Deals';

const blankLine = () => ({ product: '', description: '', quantity: '1', unit_price: '', gst_rate: '18' });

function Items({ deal, canEdit, onSaved }) {
  const notify = useToast();
  const products = useLoad(() => api.get('/crm/products/', { active: 'true' }), []);
  const [rows, setRows] = useState(() => deal.items.map((i) => ({ product: i.product || '', description: i.description, quantity: String(i.quantity), unit_price: String(i.unit_price), gst_rate: String(i.gst_rate) })));
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setRows(deal.items.map((i) => ({ product: i.product || '', description: i.description, quantity: String(i.quantity), unit_price: String(i.unit_price), gst_rate: String(i.gst_rate) }))); }, [deal]);
  const list = products.data || [];
  const set = (i, k, v) => { setRows((r) => r.map((x, n) => (n === i ? { ...x, [k]: v } : x))); setErrors((e) => ({ ...e, [`items.${i}.${k}`]: undefined })); };
  const pick = (i, id) => {
    const p = list.find((x) => x.id === id);
    setRows((r) => r.map((x, n) => (n === i ? { ...x, product: id, ...(p ? { description: p.name, unit_price: String(p.unit_price), gst_rate: String(p.gst_rate) } : {}) } : x)));
  };
  const line = (r) => Math.round(num(r.quantity) * num(r.unit_price) * 100) / 100;
  const taxable = rows.reduce((s, r) => s + line(r), 0);
  const tax = rows.reduce((s, r) => s + Math.round(line(r) * num(r.gst_rate)) / 100, 0);
  const save = async () => {
    setFailure(null);
    const e = {};
    rows.forEach((r, i) => {
      if (!r.description.trim()) e[`items.${i}.description`] = 'Describe the item.';
      if (!(num(r.quantity) > 0)) e[`items.${i}.quantity`] = 'More than zero.';
      if (r.unit_price === '' || num(r.unit_price) < 0) e[`items.${i}.unit_price`] = 'Enter a price.';
      if (num(r.gst_rate) < 0 || num(r.gst_rate) > 100) e[`items.${i}.gst_rate`] = '0 to 100.';
    });
    setErrors(e);
    if (Object.keys(e).length) return;
    setBusy(true);
    try {
      await api.put(`/crm/deals/${deal.id}/items/`, { items: rows.map((r) => ({ ...r, product: r.product || null })) });
      notify('Line items saved. The deal amount now follows them.');
      onSaved();
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  if (!canEdit) {
    return deal.items.length === 0 ? <p className="empty">No line items. The deal amount was entered directly.</p> : (
      <div className="table-wrap"><table>
        <thead><tr><th>Item</th><th className="amount">Qty</th><th className="amount">Rate</th><th className="amount">GST %</th><th className="amount">Amount</th></tr></thead>
        <tbody>{deal.items.map((i) => <tr key={i.id}><td data-label="Item">{i.description}</td><td className="amount" data-label="Qty">{plain(i.quantity)}</td><td className="amount" data-label="Rate">{plain(i.unit_price)}</td>
          <td className="amount" data-label="GST %">{num(i.gst_rate)}</td><td className="amount" data-label="Amount">{plain(i.line_total)}</td></tr>)}</tbody>
        <tfoot><tr><th colSpan={4}>Total with GST</th><td className="amount"><strong>{plain(deal.item_totals.total)}</strong></td></tr></tfoot>
      </table></div>
    );
  }
  return (
    <div>
      <FormErrors error={failure} fields={[{ name: 'items' }]} />
      {rows.length === 0 && <p className="empty">No line items yet. Add products or services; the deal amount becomes the total before GST.</p>}
      {rows.map((r, i) => (
        <div className="crm-line" key={i}>
          <Field id={`it-p-${i}`} label="Product"><select id={`it-p-${i}`} value={r.product} onChange={(e) => pick(i, e.target.value)}><option value="">Custom item</option>{list.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
          <Field id={`it-d-${i}`} label="Description" error={errors[`items.${i}.description`]}><input id={`it-d-${i}`} value={r.description} maxLength={300} onChange={(e) => set(i, 'description', e.target.value)} /></Field>
          <Field id={`it-q-${i}`} label="Qty" error={errors[`items.${i}.quantity`]}><input id={`it-q-${i}`} type="number" min="0" step="0.01" value={r.quantity} onChange={(e) => set(i, 'quantity', e.target.value)} /></Field>
          <Field id={`it-r-${i}`} label="Rate" error={errors[`items.${i}.unit_price`]}><input id={`it-r-${i}`} type="number" min="0" step="0.01" value={r.unit_price} onChange={(e) => set(i, 'unit_price', e.target.value)} /></Field>
          <Field id={`it-g-${i}`} label="GST %" error={errors[`items.${i}.gst_rate`]}><input id={`it-g-${i}`} type="number" min="0" max="100" step="0.01" value={r.gst_rate} onChange={(e) => set(i, 'gst_rate', e.target.value)} /></Field>
          <div className="crm-line-total"><span className="muted">Amount</span><strong>{plain(line(r))}</strong></div>
          <button type="button" className="btn btn-small" aria-label={`Remove line ${i + 1}`} onClick={() => setRows((x) => x.filter((_, n) => n !== i))}>Remove</button>
        </div>
      ))}
      <div className="crm-line-actions">
        <button type="button" className="btn btn-small" onClick={() => setRows((r) => [...r, blankLine()])}>Add line</button>
        <span className="muted">Before GST <strong>{money(taxable)}</strong> · GST <strong>{money(tax)}</strong> · Total <strong>{money(taxable + tax)}</strong></span>
        <button type="button" className="btn btn-primary" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save line items'}</button>
      </div>
    </div>
  );
}

export default function DealDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const notify = useToast();
  const lookups = useLookups();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [tab, setTab] = useState('overview');
  const [dialog, setDialog] = useState(null);
  const { data: d, error, loading, reload } = useLoad(() => api.get(`/crm/deals/${id}/`), [id]);
  const tl = useLoad(() => api.get(`/crm/deals/${id}/timeline/`), [id]);
  const refresh = () => { reload(); tl.reload(); };
  const { move, dialogs } = useDealMover(lookups, refresh);
  if (loading && !d) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const canManage = user.permissions.includes('crm.manage');
  const open = d.status === 'open';
  const stages = lookups.stages || [];
  const openStages = stages.filter((s) => s.kind === 'open');
  const wonStage = stages.find((s) => s.kind === 'won'), lostStage = stages.find((s) => s.kind === 'lost');
  const remove = async () => {
    if (!(await confirm({ title: `Delete ${d.name}?`, body: <p>The deal, its line items and stage history are removed.</p>, confirmLabel: 'Delete deal', danger: true }))) return;
    try { await api.del(`/crm/deals/${id}/`); notify('Deal deleted.'); navigate('/crm/deals'); } catch (e) { notify(e.message, 'error'); }
  };
  const linkCustomer = async () => {
    try { const r = await api.post(`/crm/deals/${id}/customer/`, {}); notify(r.created ? 'Customer created and linked.' : 'Customer linked.'); refresh(); } catch (e) { notify(e.message, 'error'); }
  };
  const fact = (label, value) => <div><dt>{label}</dt><dd>{value || '—'}</dd></div>;
  const tabs = [['overview', 'Overview'], ['items', `Line items (${d.items.length})`], ['history', 'Stage history'], ['activities', 'Activities'], ['documents', 'Documents']];
  const currentIdx = openStages.findIndex((s) => s.id === d.stage.id);
  return (
    <>
      <Link to="/crm/deals" className="back-link">← Deals</Link>
      <PageHeader title={d.name} description={`${d.reference_code} · ${d.account.name}`}
        actions={<>
          <Badge tone={DEAL_TONE[d.status]}>{d.status === 'open' ? 'Open' : d.status === 'won' ? 'Won' : 'Lost'}</Badge>
          {canManage && open && wonStage && <button type="button" className="btn btn-primary" onClick={() => move(d, wonStage)}>Mark won</button>}
          {canManage && open && lostStage && <button type="button" className="btn btn-danger-outline" onClick={() => move(d, lostStage)}>Mark lost</button>}
          {d.status !== 'lost' && <Link className="btn" to={quotationUrl(d)}>Create quotation</Link>}
          {canManage && !open && <MoveMenu deal={d} stages={openStages} onPick={move} />}
          {canManage && d.status === 'won' && !d.customer_id && <button type="button" className="btn" onClick={linkCustomer}>Link customer</button>}
          {canManage && <button type="button" className="btn" onClick={() => setDialog('activity')}>Log activity</button>}
          {canManage && <button type="button" className="btn" onClick={() => setDialog('edit')}>Edit</button>}
          {canManage && d.status !== 'won' && <button type="button" className="btn btn-danger-outline" onClick={remove}>Delete</button>}
        </>} />

      {open && openStages.length > 0 && (
        <ol className="crm-path" aria-label="Pipeline stage">
          {openStages.map((s, i) => (
            <li key={s.id} className={s.id === d.stage.id ? 'current' : i < currentIdx ? 'past' : ''} aria-current={s.id === d.stage.id ? 'step' : undefined}>
              {canManage && s.id !== d.stage.id ? <button type="button" onClick={() => move(d, s)} title={`Move to ${s.name}`}>{s.name}<small>{s.probability}%</small></button>
                : <span>{s.name}<small>{s.probability}%</small></span>}
            </li>
          ))}
        </ol>
      )}
      {d.status === 'won' && (
        <div className="notice notice-success" role="status">
          Won on {fmtDate(d.closed_on)}.{' '}
          {d.customer ? <>Sales customer: <Link to={`/sales/customers/${d.customer.id}`}>{d.customer.name} ({d.customer.reference_code})</Link>.</> : 'No sales customer is linked yet.'}{' '}
          <Link to={quotationUrl(d)}>Create a quotation</Link>
        </div>
      )}
      {d.status === 'lost' && <div className="notice notice-error" role="status">Lost on {fmtDate(d.closed_on)}: {d.lost_reason || 'no reason recorded'}{d.competitor ? ` (went to ${d.competitor})` : ''}.</div>}

      <div className="tiles">
        <Tile label="Amount" value={money(d.amount)} hint={d.items.length ? `With GST ${money(d.item_totals.total)}` : undefined} />
        <Tile label="Probability" value={`${d.probability}%`} hint={`Weighted ${money(d.weighted)}`} />
        <Tile label="Stage" value={<DealStatus deal={d} />} />
        <Tile label="Expected close" value={fmtDate(d.expected_close_date)} tone={d.overdue ? 'bad' : ''} hint={d.overdue ? 'Past the expected date' : `${d.age_days} day${d.age_days === 1 ? '' : 's'} old`} />
      </div>

      <Tabs tabs={tabs.map(([key, label]) => ({ key, label }))} value={tab} onChange={setTab} />
      {tab === 'overview' && (
        <div className="grid-2">
          <section className="card" aria-label="Deal details">
            <h2>Details</h2>
            <dl className="facts">
              {fact('Account', <Link to={`/crm/accounts/${d.account.id}`}>{d.account.name}</Link>)}
              {fact('Contact', d.contact && <Link to={`/crm/contacts/${d.contact.id}`}>{d.contact.name}</Link>)}
              {fact('Owner', d.owner?.name)}{fact('Source', d.source)}{fact('Next step', d.next_step)}{fact('Competitor', d.competitor)}
              {fact('From lead', d.lead && <Link to={`/crm/leads/${d.lead.id}`}>{d.lead.name} ({d.lead.reference_code})</Link>)}
              {fact('Created', fmtDateTime(d.created_at))}
            </dl>
            {d.notes && <><h3>Notes</h3><p className="crm-pre">{d.notes}</p></>}
          </section>
          <section className="card" aria-label="Quotations">
            <h2>Quotations</h2>
            {d.quotations.length === 0 ? <p className="empty">No quotation is linked to this deal yet.</p> : (
              <div className="table-wrap"><table className="crm-mini">
                <thead><tr><th>Number</th><th>Date</th><th>Status</th><th className="amount">Total</th></tr></thead>
                <tbody>{d.quotations.map((q) => <tr key={q.id}><td data-label="Number">{q.number}</td><td data-label="Date">{fmtDate(q.issue_date)}</td><td data-label="Status">{q.status}</td><td className="amount" data-label="Total">{q.total === null ? '—' : plain(q.total)}</td></tr>)}</tbody>
              </table></div>
            )}
            {d.status !== 'lost' && <p><Link className="btn btn-small" to={quotationUrl(d)}>Create quotation</Link></p>}
          </section>
        </div>
      )}
      {tab === 'items' && <section className="card"><Items deal={d} canEdit={canManage && open} onSaved={refresh} /></section>}
      {tab === 'history' && (
        <section className="card">
          <div className="table-wrap"><table>
            <caption className="sr-only">Stage history</caption>
            <thead><tr><th>When</th><th>Change</th><th className="amount">Amount</th><th className="amount">Prob.</th><th>Days in previous stage</th><th>By</th><th>Note</th></tr></thead>
            <tbody>{[...d.history].reverse().map((h) => (
              <tr key={h.id}><td data-label="When">{fmtDateTime(h.at)}</td><td data-label="Change">{h.from ? `${h.from} → ` : ''}<strong>{h.to}</strong></td>
                <td className="amount" data-label="Amount">{plain(h.amount)}</td><td className="amount" data-label="Prob.">{h.probability}%</td>
                <td data-label="Days in previous stage">{h.days_in_previous === null ? '—' : h.days_in_previous}</td><td data-label="By">{h.by?.name || '—'}</td><td data-label="Note">{h.note || '—'}</td></tr>))}</tbody>
          </table></div>
        </section>
      )}
      {tab === 'activities' && (
        <section className="card">
          <div className="crm-head"><h2>Activities and changes</h2>{canManage && <button type="button" className="btn btn-small btn-primary" onClick={() => setDialog('activity')}>Add activity</button>}</div>
          {tl.loading ? <Loading /> : <TimelineList items={tl.data || []} canManage={canManage} onChanged={refresh} />}
        </section>
      )}
      {tab === 'documents' && <section className="card"><Attachments entityType="crm_deal" entityId={id} canManage={canManage} title="Deal documents" hint="Proposals, signed orders, requirement documents." /></section>}

      {dialog === 'edit' && lookups.ready && <DealDialog deal={d} lookups={lookups} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); refresh(); }} />}
      {dialog === 'activity' && lookups.ready && <ActivityDialog links={{ deal: id }} relatedLabel={d.name} lookups={lookups} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); setTab('activities'); refresh(); }} />}
      {dialogs}
      {confirmDialog}
    </>
  );
}
