import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { money, num, plain } from '../../format';
import { asList, useLoad } from '../../hooks';
import { Badge, Field, fmtDate } from '../../ui';

export const TONE = {
  draft: 'neutral', pending_approval: 'warn', approved: 'info', issued: 'info', partially_received: 'warn', received: 'good', closed: 'neutral',
  cancelled: 'neutral', open: 'info', overdue: 'bad', partial: 'warn', paid: 'good', void: 'neutral', recorded: 'good', submitted: 'warn',
  rejected: 'bad', converted: 'good', accepted: 'good', expired: 'neutral', received_q: 'info',
};
export const Status = ({ k, label }) => <Badge tone={TONE[k] || 'neutral'}>{label}</Badge>;

export const AGEING_COLOURS = { current: 'var(--chart-2)', d1_30: '#e8b923', d31_60: '#ea7a1a', d61_90: 'var(--chart-4)', d90_plus: '#7f1d1d' };

export const usePurchaseOptions = () => useLoad(() => api.get('/purchases/options/'), []);

// Projects come from the projects module; when that call fails or is empty the dropdown just offers "No project".
export function useProjects() {
  const { data } = useLoad(() => api.get('/projects/lookup/').catch(() => []), []);
  return asList(data);
}

export function ProjectSelect({ id = 'project', value, onChange, label = 'Project (optional)' }) {
  const projects = useProjects();
  return (
    <Field id={id} label={label}>
      <select id={id} value={value || ''} onChange={(e) => onChange(e.target.value)}>
        <option value="">No project</option>
        {projects.map((p) => <option key={p.id} value={p.id}>{p.code ? `${p.code} · ` : ''}{p.name}</option>)}
      </select>
    </Field>
  );
}

export function useVendors(all = false) {
  const { data, reload } = useLoad(() => api.get('/purchases/vendors/', all ? {} : { active: 'true' }), [all]);
  return { vendors: asList(data), reload };
}

export function VendorSelect({ id = 'vendor', value, onChange, vendors, error, label = 'Vendor', required = true, disabled = false }) {
  return (
    <Field id={id} label={label} required={required} error={error}>
      <select id={id} value={value || ''} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
        <option value="">Choose a vendor…</option>
        {vendors.map((v) => <option key={v.id} value={v.id}>{v.name}{v.gstin ? ` · ${v.gstin}` : ''}</option>)}
      </select>
    </Field>
  );
}

export const blankItem = () => ({ description: '', hsn_sac: '', unit: '', quantity: '1', rate: '', discount_percent: '', gst_rate: '18', account: '' });
export const isBlank = (i) => !i.description.trim() && i.rate === '' && !i.hsn_sac;
export const itemsPayload = (items, withAccount = false) => items.filter((i) => !isBlank(i)).map((i) => {
  const out = { description: i.description.trim(), hsn_sac: i.hsn_sac.trim(), unit: i.unit.trim(), quantity: i.quantity, rate: i.rate,
    discount_percent: i.discount_percent === '' ? '0' : i.discount_percent, gst_rate: i.gst_rate === '' ? null : i.gst_rate };
  if (withAccount && i.account) out.account = i.account;
  if (i.po_line !== undefined && i.po_line !== null) out.po_line = i.po_line;
  return out;
});
export const itemsFromRecord = (lines) => (lines || []).map((l) => ({ ...blankItem(), description: l.description, hsn_sac: l.hsn_sac || '', unit: l.unit || '',
  quantity: l.quantity, rate: l.rate, discount_percent: num(l.discount_percent) ? l.discount_percent : '', gst_rate: l.gst_rate ?? '', account: l.account || '',
  po_line: l.po_line ?? undefined }));

export function validateItems(items) {
  const rows = items.map((item, n) => [item, n]).filter(([item]) => !isBlank(item));
  if (!rows.length) return { _all: 'Add at least one item.' };
  const errs = {};
  rows.forEach(([item, n]) => {
    const e = {};
    if (!item.description.trim()) e.description = 'Describe the item.';
    if (!(Number(item.quantity) > 0)) e.quantity = 'Quantity must be above 0.';
    if (item.rate === '' || Number(item.rate) < 0) e.rate = 'Enter the rate.';
    if (item.discount_percent !== '' && (Number(item.discount_percent) < 0 || Number(item.discount_percent) > 100)) e.discount_percent = 'Use 0 to 100.';
    if (Object.keys(e).length) errs[n] = e;
  });
  return errs;
}

// The editable table of line items. Row errors come from validateItems; `accounts` adds an account column (bills).
export function ItemsEditor({ items, setItems, rates, errors = {}, accounts, idPrefix = 'it', lockLines = false }) {
  const set = (n, k, v) => setItems((rows) => rows.map((r, i) => (i === n ? { ...r, [k]: v } : r)));
  return (
    <div className="table-wrap">
      <table className="items-table pur-items">
        <thead><tr>
          <th>Description</th><th>HSN/SAC</th><th className="amount">Qty</th><th>Unit</th><th className="amount">Rate</th>
          <th className="amount">Disc %</th><th>GST %</th>{accounts && <th>Account</th>}<th><span className="sr-only">Remove</span></th>
        </tr></thead>
        <tbody>
          {items.map((it, n) => {
            const e = errors[n] || {};
            const cell = (k, props = {}) => (
              <input id={`${idPrefix}-${n}-${k}`} aria-label={`Item ${n + 1} ${k.replace('_', ' ')}`} value={it[k]} aria-invalid={!!e[k] || undefined}
                onChange={(ev) => set(n, k, ev.target.value)} {...props} />
            );
            return (
              <tr key={n}>
                <td data-label="Description">{cell('description', { maxLength: 500 })}{e.description && <div className="error-text">{e.description}</div>}</td>
                <td data-label="HSN/SAC">{cell('hsn_sac', { maxLength: 8, inputMode: 'numeric' })}</td>
                <td data-label="Qty" className="amount">{cell('quantity', { inputMode: 'decimal' })}{e.quantity && <div className="error-text">{e.quantity}</div>}</td>
                <td data-label="Unit">{cell('unit', { maxLength: 20, size: 5 })}</td>
                <td data-label="Rate" className="amount">{cell('rate', { inputMode: 'decimal' })}{e.rate && <div className="error-text">{e.rate}</div>}</td>
                <td data-label="Disc %" className="amount">{cell('discount_percent', { inputMode: 'decimal', placeholder: '0' })}{e.discount_percent && <div className="error-text">{e.discount_percent}</div>}</td>
                <td data-label="GST %">
                  <select aria-label={`Item ${n + 1} GST rate`} value={it.gst_rate} onChange={(ev) => set(n, 'gst_rate', ev.target.value)}>
                    {rates.map((r) => <option key={r} value={r}>{r}%</option>)}
                  </select>
                </td>
                {accounts && (
                  <td data-label="Account">
                    <select aria-label={`Item ${n + 1} account`} value={it.account} onChange={(ev) => set(n, 'account', ev.target.value)}>
                      <option value="">Bill default</option>
                      {accounts.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}
                    </select>
                  </td>
                )}
                <td data-label="">{!lockLines && items.length > 1 && <button type="button" className="btn btn-small" onClick={() => setItems((rows) => rows.filter((_, i) => i !== n))} aria-label={`Remove item ${n + 1}`}>Remove</button>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!lockLines && <button type="button" className="btn btn-small" onClick={() => setItems((rows) => [...rows, blankItem()])}>Add item</button>}
    </div>
  );
}

// Live totals from the server (debounced), so the page and what is posted always agree.
export function useWorking({ vendor, items, tds_section, tds_rate, bill_date, withAccount }) {
  const [state, setState] = useState({ record: null, tds: null, error: '', pending: false });
  const ticket = useRef(0);
  const used = itemsPayload(items, withAccount);
  const ready = vendor && used.length > 0 && used.every((i) => i.description && Number(i.quantity) > 0 && i.rate !== '');
  const sig = JSON.stringify([vendor, used, tds_section, tds_rate, bill_date]);
  useEffect(() => {
    if (!ready) { setState({ record: null, tds: null, error: '', pending: false }); return undefined; }
    const mine = ++ticket.current;
    setState((s) => ({ ...s, pending: true }));
    const t = setTimeout(async () => {
      try {
        const r = await api.post('/purchases/working/', { vendor, items: used, tds_section: tds_section || undefined, tds_rate: tds_rate || undefined, bill_date: bill_date || undefined });
        if (mine === ticket.current) setState({ record: r.record, tds: r, error: '', pending: false });
      } catch (e) { if (mine === ticket.current) setState({ record: null, tds: null, error: e.message, pending: false }); }
    }, 450);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig]);
  return state;
}

export function Totals({ working, cur = 'INR', showTds = false }) {
  const { record, tds, error, pending } = working;
  if (error) return <p className="error-text" role="alert">{error}</p>;
  if (!record) return <p className="hint">Totals appear once a vendor and the items are filled in.</p>;
  const t = record.totals;
  return (
    <dl className="pur-totals" aria-busy={pending}>
      <div><dt>Gross</dt><dd>{plain(t.gross)}</dd></div>
      {num(t.discount) > 0 && <div><dt>Discount</dt><dd>-{plain(t.discount)}</dd></div>}
      <div><dt>Taxable value</dt><dd>{plain(t.taxable)}</dd></div>
      {t.tax.map((r) => <div key={r.label}><dt>{r.label}</dt><dd>{plain(r.amount)}</dd></div>)}
      {!record.gst_charged && <div><dt>GST</dt><dd>Not charged (vendor is not registered)</dd></div>}
      <div className="pur-total"><dt>Total ({cur})</dt><dd>{money(t.grand_total, cur)}</dd></div>
      {showTds && tds && num(tds.tds_amount) > 0 && <div><dt>TDS withheld</dt><dd>-{plain(tds.tds_amount)}</dd></div>}
      {showTds && tds && num(tds.tds_amount) > 0 && <div className="pur-total"><dt>Net payable to vendor</dt><dd>{money(tds.net_payable, cur)}</dd></div>}
      <div className="hint">{record.supply_type === 'intra_state' ? 'Same state as the company: CGST and SGST.' : record.supply_type === 'inter_state' ? 'Different state: IGST.' : ''}</div>
    </dl>
  );
}

// A read-only items table for a stored record.
export function LinesTable({ record, extra }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Description</th><th>HSN/SAC</th><th className="amount">Qty</th><th className="amount">Rate</th><th className="amount">GST %</th>
          <th className="amount">Taxable</th>{extra && <th className="amount">{extra.label}</th>}<th className="amount">Total</th></tr></thead>
        <tbody>
          {record.lines.map((l, i) => (
            <tr key={i}>
              <td data-label="#">{i + 1}</td><td data-label="Description">{l.description}</td><td data-label="HSN/SAC">{l.hsn_sac || '—'}</td>
              <td className="amount" data-label="Qty">{num(l.quantity)} {l.unit}</td><td className="amount" data-label="Rate">{plain(l.rate)}</td>
              <td className="amount" data-label="GST %">{num(l.gst_rate)}</td><td className="amount" data-label="Taxable">{plain(l.taxable)}</td>
              {extra && <td className="amount" data-label={extra.label}>{extra.value(l, i)}</td>}
              <td className="amount" data-label="Total">{plain(l.total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export const DocLink = ({ to, children }) => <Link to={to} onClick={(e) => e.stopPropagation()}>{children}</Link>;
export { fmtDate };
