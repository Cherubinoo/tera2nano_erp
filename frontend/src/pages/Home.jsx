import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../context';
import { useLoad } from '../hooks';
import { Badge, ErrorBox, Loading, Notice, fmtDate, useToast } from '../ui';
import { Downloads } from './Letters';

const greeting = () => {
  const h = new Date().getHours();
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
};
const money = (value, currency) => {
  try { return Number(value).toLocaleString('en-IN', { style: 'currency', currency: currency || 'INR', maximumFractionDigits: 2 }); } catch { return `${currency} ${value}`; }
};

function Tile({ label, value, hint, to }) {
  const body = (<><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</>);
  return to ? <Link className="tile" to={to}>{body}</Link> : <div className="tile">{body}</div>;
}

// Documents issued per month. Plain bars with the number on each, and the same figures as text for screen readers.
function MonthlyBars({ months }) {
  const max = Math.max(1, ...months.map((m) => m.count));
  const summary = months.map((m) => `${m.label}: ${m.count}`).join(', ');
  return (
    <div>
      <div className="bars" role="img" aria-label={`Documents issued per month. ${summary}`}>
        {months.map((m, i) => (
          <div className="bar-col" key={m.month}>
            <span className="bar-num">{m.count}</span>
            <span className="bar-track">
              <span className={`bar${i === months.length - 1 ? ' current' : ''}`} style={{ height: `${Math.max(m.count / max, m.count ? 0.04 : 0) * 100}%` }} />
            </span>
            <span className="bar-label">{m.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Home() {
  const { user } = useAuth();
  const notify = useToast();
  const dash = useLoad(() => api.get('/company/dashboard/'), []);
  const overview = useLoad(() => api.get('/company/overview/'), []);
  const [showChecklist, setShowChecklist] = useState(false);

  if (dash.loading || overview.loading) return <Loading />;
  if (dash.error) return <ErrorBox error={dash.error} onRetry={dash.reload} />;
  if (overview.error) return <ErrorBox error={overview.error} onRetry={overview.reload} />;
  const d = dash.data;
  const { attention, checklist, profile } = overview.data;
  const todo = checklist.filter((c) => !c.done);
  const first = (user.full_name || user.username).split(' ')[0];
  const today = new Date(`${d.today}T00:00:00`).toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });

  return (
    <>
      <header className="welcome-strip">
        <div className="welcome-text">
          <h1>{greeting()}, {first}</h1>
          <p>{today} · {profile.display_name}</p>
          <p className="welcome-sub">Pick up where you left off, or start something new.</p>
        </div>
        <div className="page-actions">
          <Link className="btn btn-primary" to="/company/quotations">New quotation</Link>
          <Link className="btn" to="/company/letters">New letter</Link>
          <Link className="btn" to="/company/minutes">New minutes</Link>
          <Link className="btn" to="/company/operations">New operations document</Link>
          <Link className="btn" to="/apps">All modules</Link>
        </div>
      </header>

      {profile.setup_status !== 'completed' && user.is_company_admin && (
        <Notice type="warn">Company setup is not finished. <Link to="/setup">Resume setup</Link></Notice>
      )}

      <div className="tiles">
        <Tile label={`Documents issued · ${d.month_label}`} value={d.month.documents}
          hint={`${d.month.letters} letters · ${d.month.minutes} minutes · ${d.month.operations} operations`} />
        <Tile label="Quotations this month" value={d.month.quotations} to="/company/quotations" hint="Open the quotations page" />
        <Tile label="Quotation value this month" value={money(d.month.quotation_value, d.month.currency)}
          hint="Total of quotations with worked-out items" />
        <Tile label="Needs attention" value={attention.length} hint={attention.length ? 'See the list below' : 'Nothing right now'} />
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Documents issued per month">
          <h2>Documents issued</h2>
          <p className="muted">Last six months, all types. Void documents are not counted.</p>
          <MonthlyBars months={d.monthly} />
        </section>
        <section className="card" aria-label="Needs attention">
          <h2>Needs attention</h2>
          {attention.length === 0 ? <p className="muted">Nothing needs attention right now.</p> : (
            <ul className="attention">{attention.map((a) => <li key={`${a.kind}-${a.id}`}>{a.message}</li>)}</ul>
          )}
          {todo.length > 0 && (
            <>
              <button type="button" className="btn btn-small" aria-expanded={showChecklist} onClick={() => setShowChecklist((s) => !s)}>
                {showChecklist ? 'Hide' : 'Show'} company setup ({checklist.length - todo.length} of {checklist.length} done)
              </button>
              {showChecklist && (
                <ul className="checklist">
                  {checklist.map((c) => <li key={c.label} className={c.done ? 'done' : ''}><span aria-hidden="true">{c.done ? '✓' : '○'}</span> {c.label}</li>)}
                </ul>
              )}
            </>
          )}
        </section>
      </div>

      <section className="card" aria-label="Recent documents">
        <div className="card-head"><h2>Recent documents</h2></div>
        {d.recent.length === 0 ? <p className="empty">Nothing issued yet. Start with a quotation or a letter and it will appear here.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>ID</th><th>Type</th><th>Heading</th><th>Date</th><th>By</th><th><span className="sr-only">Downloads</span></th></tr></thead>
              <tbody>
                {d.recent.map((r) => (
                  <tr key={r.id}>
                    <td data-label="ID"><code>{r.reference_number}</code></td>
                    <td data-label="Type"><Badge tone="info">{r.document_type_label}</Badge></td>
                    <td data-label="Heading"><strong>{r.title}</strong></td>
                    <td data-label="Date">{fmtDate(r.issue_date)}</td>
                    <td data-label="By">{r.generated_by_name}</td>
                    <td className="row-actions"><Downloads doc={r} onError={(e) => notify(e.message, 'error')} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
