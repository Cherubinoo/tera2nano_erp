import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, downloadFile } from '../../api';
import { money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Field, Loading, PageHeader, useToast } from '../../ui';

// Financial years (April to March), newest first: the current one and the four before it.
export function fyOptions() {
  const t = today();
  const y = Number(t.slice(0, 4));
  const start = Number(t.slice(5, 7)) >= 4 ? y : y - 1;
  return Array.from({ length: 5 }, (_, i) => `${start - i}-${String(start - i + 1).slice(-2)}`);
}

export const pct = (v) => (v === null || v === undefined ? '—' : `${num(v).toFixed(1)}%`);

export function Delta({ value }) {
  if (value === null || value === undefined) return <span className="muted">—</span>;
  const n = num(value);
  return <span className={`rpt-delta ${n > 0 ? 'up' : n < 0 ? 'down' : ''}`}>{n > 0 ? '+' : ''}{n.toFixed(1)}%</span>;
}

export function Tile({ label, value, hint, tone, to }) {
  const inner = (<><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</>);
  return to ? <Link className={`tile ${tone || ''}`} to={to}>{inner}</Link> : <div className={`tile ${tone || ''}`}>{inner}</div>;
}

export const signTone = (v) => (num(v) < 0 ? 'bad' : num(v) > 0 ? 'good' : '');

// The period picker: a financial year, a date range, or (for reports that allow it) all dates. `params` go to the API.
function usePeriod(allowAll) {
  const [mode, setMode] = useState(allowAll ? 'all' : 'fy');
  const [fy, setFy] = useState(fyOptions()[0]);
  const [range, setRange] = useState({ from: '', to: '' });
  const invalid = mode === 'range' && (!range.from || !range.to || range.from > range.to);
  const params = mode === 'all' ? { fy: 'all' } : mode === 'fy' ? { fy } : { from: range.from, to: range.to };
  return { mode, setMode, fy, setFy, range, setRange, invalid, params };
}

function PeriodBar({ p, allowAll }) {
  const select = p.mode === 'fy' ? `fy:${p.fy}` : p.mode;
  const change = (e) => {
    const v = e.target.value;
    if (v.startsWith('fy:')) { p.setMode('fy'); p.setFy(v.slice(3)); } else p.setMode(v);
  };
  return (
    <form className="toolbar rpt-period" onSubmit={(e) => e.preventDefault()} aria-label="Report period">
      <Field id="rpt-period" label="Period">
        <select id="rpt-period" value={select} onChange={change}>
          {allowAll && <option value="all">All dates</option>}
          {fyOptions().map((f) => <option key={f} value={`fy:${f}`}>Financial year {f}</option>)}
          <option value="range">Custom date range</option>
        </select>
      </Field>
      {p.mode === 'range' && (
        <>
          <Field id="rpt-from" label="From"><input id="rpt-from" type="date" value={p.range.from} onChange={(e) => p.setRange((r) => ({ ...r, from: e.target.value }))} /></Field>
          <Field id="rpt-to" label="To"><input id="rpt-to" type="date" value={p.range.to} onChange={(e) => p.setRange((r) => ({ ...r, to: e.target.value }))} /></Field>
          {p.invalid && <span className="error-text" role="alert">{p.range.from && p.range.to ? 'The end date is before the start date.' : 'Choose both dates.'}</span>}
        </>
      )}
    </form>
  );
}

const query = (params) => new URLSearchParams(Object.entries(params).filter(([, v]) => v)).toString();

// Page frame shared by every report: title, period filters, CSV export, loading and error states.
// `children` is called with the report's data and renders the tiles, charts and tables.
export function ReportPage({ slug, title, description, allowAll = false, children }) {
  const notify = useToast();
  const period = usePeriod(allowAll);
  const [busy, setBusy] = useState(false);
  const key = JSON.stringify(period.params);
  const report = useLoad(() => (period.invalid ? Promise.resolve(null) : api.get(`/reports/${slug}/`, period.params)), [slug, key, period.invalid]);
  const exportCsv = async () => {
    setBusy(true);
    try { await downloadFile(`/reports/${slug}/export/?${query(period.params)}`, `${slug}.csv`); notify('CSV downloaded.'); } catch (e) { notify(e.message, 'error'); }
    setBusy(false);
  };
  return (
    <>
      <Link to="/reports" className="back-link">← Reports</Link>
      <PageHeader title={title} description={description}
        actions={<button type="button" className="btn" onClick={exportCsv} disabled={busy || period.invalid || !report.data}>{busy ? 'Preparing…' : 'Export CSV'}</button>} />
      <PeriodBar p={period} allowAll={allowAll} />
      {report.loading && <Loading />}
      <ErrorBox error={report.error} onRetry={report.reload} />
      {report.data && children(report.data)}
    </>
  );
}

// A table with data-label cells (they become cards on phones). columns: [{key, label, amount, render}]; foot is an optional totals row.
export function Table({ caption, columns, rows, foot, empty = 'Nothing to show for this period.', rowKey }) {
  if (!rows.length) return <p className="empty">{empty}</p>;
  const cell = (c, r) => (c.render ? c.render(r) : c.amount ? plain(r[c.key]) : r[c.key]);
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">{caption}</caption>
        <thead><tr>{columns.map((c) => <th key={c.key} className={c.amount ? 'amount' : ''}>{c.label}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={rowKey ? rowKey(r) : i}>{columns.map((c) => <td key={c.key} data-label={c.label} className={c.amount ? 'amount' : ''}>{cell(c, r)}</td>)}</tr>
          ))}
        </tbody>
        {foot && <tfoot><tr>{columns.map((c) => <td key={c.key} data-label={c.label} className={c.amount ? 'amount' : ''}><strong>{foot[c.key] ?? ''}</strong></td>)}</tr></tfoot>}
      </table>
    </div>
  );
}

export const Section = ({ title, hint, children }) => (
  <section className="card" aria-label={title}>
    <h2>{title}</h2>
    {hint && <p className="hint">{hint}</p>}
    {children}
  </section>
);

export { money, plain };
