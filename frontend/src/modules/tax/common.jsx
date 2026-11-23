import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, downloadFile } from '../../api';
import { addDays, num, today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Field, useToast } from '../../ui';

export const HEADS = [['cgst', 'CGST'], ['sgst', 'SGST / UTGST'], ['igst', 'IGST']];
export const HEAD_COLOUR = { cgst: 'var(--chart-1)', sgst: 'var(--chart-2)', igst: 'var(--chart-3)' };

export const STATUS_LABEL = { pending: 'Pending', prepared: 'Prepared', filed: 'Filed', late: 'Late', recorded: 'Recorded', void: 'Void',
  expected: 'Certificate awaited', received: 'Certificate received', claimed: 'Matched in 26AS', paid: 'Paid', overdue: 'Overdue', due_soon: 'Due soon',
  upcoming: 'Upcoming', not_set: 'Not set', eligible: 'Eligible', ineligible: 'Ineligible', unverified: 'No GSTIN', match: 'Matches', mismatch: 'Difference',
  not_filed: 'Not filed' };
const TONE = { filed: 'good', recorded: 'good', claimed: 'good', paid: 'good', eligible: 'good', match: 'good', late: 'bad', void: 'bad', overdue: 'bad',
  ineligible: 'bad', mismatch: 'bad', prepared: 'info', received: 'info', due_soon: 'warn', pending: 'warn', unverified: 'warn', expected: 'warn',
  upcoming: 'neutral', not_set: 'neutral', not_filed: 'neutral' };
export const StatusBadge = ({ status, label }) => <Badge tone={TONE[status] || 'neutral'}>{label || STATUS_LABEL[status] || status || '—'}</Badge>;

export const KIND_LABEL = { gst: 'GST', tds: 'TDS', advance_tax: 'Advance tax', income_tax: 'Income tax' };
export const KIND_COLOUR = { gst: 'var(--chart-1)', tds: 'var(--chart-2)', advance_tax: 'var(--chart-3)', income_tax: 'var(--chart-4)' };

export const fyNow = () => {
  const d = new Date();
  const y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1;
  return `${y}-${String((y + 1) % 100).padStart(2, '0')}`;
};
export const fyBounds = (fy) => { const y = Number(fy.slice(0, 4)); return [`${y}-04-01`, `${y + 1}-03-31`]; };
export const monthStart = (iso) => `${iso.slice(0, 7)}-01`;
export const monthEnd = (iso) => { const [y, m] = iso.split('-').map(Number); return addDays(`${m === 12 ? y + 1 : y}-${String(m === 12 ? 1 : m + 1).padStart(2, '0')}-01`, -1); };
export const monthLabel = (key) => new Date(`${key}-01T00:00:00`).toLocaleDateString('en-IN', { month: 'short', year: 'numeric' });
export const pct = (v) => `${num(v)}%`;

export function useTaxOptions() {
  return useLoad(() => api.get('/tax/options/'), []);
}

export function Tile({ label, value, hint, tone, to }) {
  const inner = (<><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</>);
  return to ? <Link className={`tile ${tone || ''}`} to={to}>{inner}</Link> : <div className={`tile ${tone || ''}`}>{inner}</div>;
}

export function FyPicker({ value, onChange, options, id = 'tax-fy' }) {
  const list = options?.financial_years || [value];
  return (
    <Field id={id} label="Financial year">
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>{list.map((v) => <option key={v} value={v}>{v}</option>)}</select>
    </Field>
  );
}

// From / to dates with quick ranges. `onApply({from, to})`.
export function RangeBar({ from, to, onApply, children }) {
  const [f, setF] = useState(from);
  const [t, setT] = useState(to);
  const apply = (a, b) => { setF(a); setT(b); onApply({ from: a, to: b }); };
  const now = today();
  const prev = addDays(monthStart(now), -1);
  const [fyStart] = fyBounds(fyNow());
  return (
    <form className="toolbar no-print" onSubmit={(e) => { e.preventDefault(); onApply({ from: f, to: t }); }}>
      <Field id="tx-from" label="From"><input id="tx-from" type="date" value={f} onChange={(e) => setF(e.target.value)} /></Field>
      <Field id="tx-to" label="To"><input id="tx-to" type="date" value={t} onChange={(e) => setT(e.target.value)} /></Field>
      <button type="submit" className="btn">Show</button>
      <div className="chips" role="group" aria-label="Quick ranges">
        <button type="button" className="chip" onClick={() => apply(monthStart(now), monthEnd(now))}>This month</button>
        <button type="button" className="chip" onClick={() => apply(monthStart(prev), monthEnd(prev))}>Last month</button>
        <button type="button" className="chip" onClick={() => apply(fyStart, now)}>Year to date</button>
      </div>
      {children}
    </form>
  );
}

// Button that downloads a server-made file (CSV) with the sign-in token.
export function DownloadButton({ path, params, name, children = 'Export CSV' }) {
  const notify = useToast();
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      const qs = new URLSearchParams(Object.entries(params || {}).filter(([, v]) => v)).toString();
      await downloadFile(`${path}${qs ? `?${qs}` : ''}`, name);
    } catch (e) { notify(e.message, 'error'); }
    setBusy(false);
  };
  return <button type="button" className="btn" onClick={go} disabled={busy}>{busy ? 'Preparing…' : children}</button>;
}

// A chart's numbers as a table, for people who cannot read the chart.
export function ChartTable({ title, headers, rows }) {
  return (
    <details className="tax-chart-data">
      <summary>View {title} as a table</summary>
      <div className="table-wrap">
        <table>
          <thead><tr>{headers.map((h, i) => <th key={h} className={i ? 'amount' : ''}>{h}</th>)}</tr></thead>
          <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} data-label={headers[j]} className={j ? 'amount' : ''}>{c}</td>)}</tr>)}</tbody>
        </table>
      </div>
    </details>
  );
}

export const sum = (rows, key) => rows.reduce((a, r) => a + num(r[key]), 0);
export const has = (user, code) => (user.permissions || []).includes(code);

// Text input bound to a keyed values object with server error text.
export function TextField({ id, label, values, set, errors = {}, name, type = 'text', required, hint, wide, ...rest }) {
  return (
    <Field id={id} label={label} required={required} hint={hint} error={errors[name]} wide={wide}>
      <input id={id} type={type} value={values[name] ?? ''} onChange={(e) => set(name, e.target.value)}
        aria-invalid={errors[name] ? true : undefined} aria-describedby={errors[name] ? `${id}-err` : undefined} {...rest} />
    </Field>
  );
}
