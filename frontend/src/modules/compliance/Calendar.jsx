import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, ErrorBox, Field, Loading, PageHeader, Tabs, fmtDate } from '../../ui';
import { ItemDialog, NewTaskDialog, Pick, StatusBadge, daysText, opt, useCanManage, useMeta } from './common';

const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const pad = (n) => String(n).padStart(2, '0');
const monthOf = (iso) => iso.slice(0, 7);
const shift = (ym, by) => { const [y, m] = ym.split('-').map(Number); const d = new Date(y, m - 1 + by, 1); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`; };
const label = (ym) => { const [y, m] = ym.split('-').map(Number); return new Date(y, m - 1, 1).toLocaleDateString('en-IN', { month: 'long', year: 'numeric' }); };

function cells(ym) {
  const [y, m] = ym.split('-').map(Number);
  const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7;
  const days = new Date(y, m, 0).getDate();
  return [...Array(lead).fill(null), ...Array.from({ length: days }, (_, i) => `${ym}-${pad(i + 1)}`)];
}

function DayList({ date, items, tax = [], onOpen }) {
  if (!items.length && !tax.length) return <p className="empty">Nothing is due on {fmtDate(date)}.</p>;
  return (
    <ul className="compliance-daylist">
      {tax.map((t, i) => (
        <li key={`tax-${t.id}-${i}`}>
          <Link to={t.link}><strong>{t.title}</strong></Link>
          <span className="muted"> {t.subtitle}</span> <Badge tone="info">Tax</Badge> <Badge tone="neutral">Read only</Badge>
        </li>
      ))}
      {items.map((i) => (
        <li key={i.id}>
          <button type="button" className="link-button" onClick={() => onOpen(i.id)}><strong>{i.title}</strong></button>
          <span className="muted"> {i.period_label}</span> <StatusBadge item={i} />
          <span className="hint"> {i.assignee?.name || 'Unassigned'}{i.form_no ? ` · ${i.form_no}` : ''}</span>
        </li>
      ))}
    </ul>
  );
}

export default function CalendarPage() {
  const canManage = useCanManage();
  const [params, setParams] = useSearchParams();
  const ym = params.get('month') || monthOf(today());
  const [view, setView] = useState('month');
  const [category, setCategory] = useState('');
  const [mine, setMine] = useState(false);
  const [selected, setSelected] = useState(today());
  const [openId, setOpenId] = useState(null);
  const [adding, setAdding] = useState(false);
  const meta = useMeta();
  const obligations = useLoad(() => (canManage ? api.get('/compliance/obligations/', { frequency: 'event' }) : Promise.resolve(null)), [canManage]);
  const { data, error, loading, reload } = useLoad(() => api.get('/compliance/items/', { month: ym, category: category || undefined, assignee: mine ? 'me' : undefined }), [ym, category, mine]);
  const taxEvents = useLoad(() => api.get('/compliance/tax-events/', { days: 400 }).catch(() => ({ results: [] })), []);
  const items = data?.results || [];
  const taxByDate = useMemo(() => (taxEvents.data?.results || []).filter((t) => t.due_date.startsWith(ym)).reduce((m, t) => { (m[t.due_date] = m[t.due_date] || []).push(t); return m; }, {}), [taxEvents.data, ym]);
  const byDate = useMemo(() => items.reduce((m, i) => { (m[i.due_date] = m[i.due_date] || []).push(i); return m; }, {}), [items]);
  const goto = (m) => { setParams({ month: m }, { replace: true }); setSelected(m === monthOf(today()) ? today() : `${m}-01`); };
  const grid = cells(ym);
  const sel = selected.startsWith(ym) ? selected : `${ym}-01`;

  return (
    <>
      <PageHeader title="Compliance Calendar" description="Every due date of the month. Open a date to see what falls on it."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>New task</button>} />
      <Tabs tabs={[{ key: 'month', label: 'Month' }, { key: 'agenda', label: 'Agenda' }]} value={view} onChange={setView} />
      <section className="card">
        <div className="toolbar">
          <button type="button" className="btn" onClick={() => goto(shift(ym, -1))} aria-label="Previous month">‹ Previous</button>
          <h2 className="compliance-month" aria-live="polite">{label(ym)}</h2>
          <button type="button" className="btn" onClick={() => goto(shift(ym, 1))} aria-label="Next month">Next ›</button>
          <button type="button" className="btn" onClick={() => goto(monthOf(today()))}>Today</button>
          <Field id="cal-month" label="Jump to month"><input id="cal-month" type="month" value={ym} onChange={(e) => e.target.value && goto(e.target.value)} /></Field>
          {meta.data && <Pick id="cal-cat" label="Category" value={category} onChange={setCategory} options={opt(meta.data.categories)} />}
          <label className="checkbox-inline"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Only mine</label>
        </div>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {data && (
          <p className="muted" role="status">
            {items.length} item{items.length === 1 ? '' : 's'} this month{Object.keys(taxByDate).length > 0 && ` (plus ${Object.values(taxByDate).flat().length} tax due dates from the Tax module)`}: {data.totals.overdue} overdue, {data.totals.due_soon} due soon, {data.totals.upcoming} upcoming, {data.totals.filed} filed.
            {items.length === 0 && <> Nothing here? <Link to="/compliance/filings">Generate the calendar</Link> from the Filings page.</>}
          </p>
        )}

        {view === 'month' && (
          <>
            <div className="compliance-grid" role="grid" aria-label={`Due dates in ${label(ym)}`}>
              <div className="compliance-row compliance-head" role="row">{DOW.map((d) => <div key={d} role="columnheader">{d}</div>)}</div>
              {Array.from({ length: Math.ceil(grid.length / 7) }, (_, w) => (
                <div className="compliance-row" role="row" key={w}>
                  {Array.from({ length: 7 }, (_, c) => {
                    const date = grid[w * 7 + c];
                    if (!date) return <div key={c} className="compliance-cell compliance-blank" role="gridcell" aria-hidden="true" />;
                    const list = byDate[date] || [];
                    const taxList = taxByDate[date] || [];
                    const worst = list.some((i) => i.status_key === 'overdue') ? 'overdue' : list.some((i) => i.status_key === 'due_soon') ? 'due_soon' : list.some((i) => i.status_key === 'upcoming') ? 'upcoming' : list.length ? 'filed' : '';
                    return (
                      <div key={c} role="gridcell" className={`compliance-cell${date === today() ? ' compliance-today' : ''}${date === sel ? ' compliance-selected' : ''}`}>
                        <button type="button" className="compliance-day" onClick={() => setSelected(date)} aria-label={`${fmtDate(date)}, ${list.length + taxList.length} item${list.length + taxList.length === 1 ? '' : 's'}`} aria-pressed={date === sel}>
                          {Number(date.slice(8))}{list.length + taxList.length > 0 && <span className={`compliance-count tone-${worst || 'tax'}`}>{list.length + taxList.length}</span>}
                        </button>
                        <div className="compliance-pills">
                          {list.slice(0, 3).map((i) => (
                            <button key={i.id} type="button" className={`compliance-pill tone-${i.status_key}`} title={`${i.title} ${i.period_label}`} onClick={() => setOpenId(i.id)}>{i.form_no || i.title}</button>
                          ))}
                          {taxList.slice(0, Math.max(0, 3 - list.length)).map((t, k) => (
                            <Link key={`t${k}`} className="compliance-pill tone-tax" to={t.link} title={`Tax: ${t.title} ${t.subtitle}`}>Tax: {t.title}</Link>
                          ))}
                          {list.length > 3 && <button type="button" className="compliance-more" onClick={() => setSelected(date)}>+{list.length - 3} more</button>}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
            <div className="compliance-legend" aria-hidden="true">
              {[['overdue', 'Overdue'], ['due_soon', 'Due soon'], ['upcoming', 'Upcoming'], ['filed', 'Filed'], ['not_applicable', 'Not applicable'], ['tax', 'Tax module']].map(([k, l]) => <span key={k}><i className={`tone-${k}`} />{l}</span>)}
            </div>
            <h3>{fmtDate(sel)}{sel === today() && ' (today)'}</h3>
            <DayList date={sel} items={byDate[sel] || []} tax={taxByDate[sel] || []} onOpen={setOpenId} />
          </>
        )}

        {view === 'agenda' && (
          items.length === 0 && Object.keys(taxByDate).length === 0 ? <p className="empty">No due dates this month.</p> : (
            <div className="compliance-agenda">
              {[...new Set([...Object.keys(byDate), ...Object.keys(taxByDate)])].sort().map((date) => (
                <div key={date} className="compliance-agenda-day">
                  <h3>{fmtDate(date)} <span className="hint">{daysText((new Date(`${date}T00:00:00`) - new Date(`${today()}T00:00:00`)) / 86400000)}</span></h3>
                  <DayList date={date} items={byDate[date] || []} tax={taxByDate[date] || []} onOpen={setOpenId} />
                </div>
              ))}
            </div>
          )
        )}
      </section>
      {openId && meta.data && <ItemDialog id={openId} meta={meta.data} onClose={() => setOpenId(null)} onChanged={reload} />}
      {adding && meta.data && <NewTaskDialog meta={meta.data} obligations={obligations.data?.results} onClose={() => setAdding(false)} onDone={() => { setAdding(false); reload(); }} />}
    </>
  );
}

