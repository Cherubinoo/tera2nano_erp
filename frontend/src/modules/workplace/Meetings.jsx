import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useLoad } from '../../hooks';
import { ErrorBox, Field, Loading, PageHeader, Tabs, fmtDate } from '../../ui';
import { MEETING_TYPES, MeetingStatus, addDaysTo, mondayOf, parseYmd, timeRange, useCan, usePeople, ymd } from './common';
import MeetingFormDialog from './MeetingForm';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const STATUSES = [['', 'All'], ['scheduled', 'Scheduled'], ['rescheduled', 'Rescheduled'], ['held', 'Held'], ['cancelled', 'Cancelled']];

function Chip({ m }) {
  return (
    <Link className={`wp-evt wp-evt-${m.status}`} to={`/workplace/meetings/${m.id}`} title={`${m.title} (${m.status_label})`}>
      <span className="wp-evt-time">{m.start_time || 'All day'}</span> {m.title}
    </Link>
  );
}

function Month({ cursor, meetings, onPick }) {
  const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  const start = mondayOf(first);
  const todayKey = ymd(new Date());
  const days = Array.from({ length: 42 }, (_, i) => addDaysTo(start, i));
  const by = {};
  meetings.forEach((m) => { (by[m.date] = by[m.date] || []).push(m); });
  return (
    <div className="wp-cal" role="grid" aria-label={cursor.toLocaleDateString('en-IN', { month: 'long', year: 'numeric' })}>
      {DAYS.map((d) => <div key={d} className="wp-cal-head" role="columnheader">{d}</div>)}
      {days.map((d) => {
        const key = ymd(d);
        const list = by[key] || [];
        return (
          <div key={key} role="gridcell" className={`wp-cal-day${d.getMonth() !== cursor.getMonth() ? ' other' : ''}${key === todayKey ? ' today' : ''}`}>
            <button type="button" className="wp-cal-num" onClick={() => onPick(key)} aria-label={`Schedule a meeting on ${d.toDateString()}`}>{d.getDate()}</button>
            {list.slice(0, 3).map((m) => <Chip key={m.id} m={m} />)}
            {list.length > 3 && <span className="hint">+{list.length - 3} more</span>}
          </div>
        );
      })}
    </div>
  );
}

function Week({ cursor, meetings }) {
  const start = mondayOf(cursor);
  const todayKey = ymd(new Date());
  return (
    <div className="wp-week">
      {Array.from({ length: 7 }, (_, i) => addDaysTo(start, i)).map((d) => {
        const key = ymd(d);
        const list = meetings.filter((m) => m.date === key);
        return (
          <section key={key} className={`wp-week-day${key === todayKey ? ' today' : ''}`} aria-label={d.toDateString()}>
            <h3>{DAYS[(d.getDay() + 6) % 7]} {d.getDate()}</h3>
            {list.length === 0 ? <p className="hint">Free</p> : list.map((m) => (
              <Link key={m.id} className={`wp-evt wp-evt-${m.status}`} to={`/workplace/meetings/${m.id}`}>
                <strong>{timeRange(m)}</strong><br />{m.title}{m.venue ? <span className="hint"> · {m.venue}</span> : null}
              </Link>
            ))}
          </section>
        );
      })}
    </div>
  );
}

function Agenda({ meetings }) {
  if (meetings.length === 0) return <p className="empty">No upcoming meetings.</p>;
  const groups = [];
  meetings.forEach((m) => {
    const g = groups[groups.length - 1];
    if (g && g.date === m.date) g.items.push(m); else groups.push({ date: m.date, items: [m] });
  });
  return (
    <div className="wp-agenda">
      {groups.map((g) => (
        <section key={g.date} aria-label={fmtDate(g.date)}>
          <h3>{parseYmd(g.date).toLocaleDateString('en-IN', { weekday: 'long', day: '2-digit', month: 'short', year: 'numeric' })}</h3>
          <ul className="wp-list">
            {g.items.map((m) => (
              <li key={m.id}>
                <div className="wp-time">{timeRange(m)}</div>
                <div className="wp-list-main"><Link to={`/workplace/meetings/${m.id}`}><strong>{m.title}</strong></Link>
                  <div className="hint">{m.type_label}{m.venue ? ` · ${m.venue}` : ''}{m.link ? ' · online' : ''} · {m.attendee_count ?? 0} attendee{m.attendee_count === 1 ? '' : 's'}</div></div>
                <MeetingStatus m={m} />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

export default function MeetingsPage() {
  const can = useCan();
  const people = usePeople();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState('month');
  const [cursor, setCursor] = useState(new Date());
  const [q, setQ] = useState('');
  const [type, setType] = useState('');
  const [status, setStatus] = useState('');
  const [form, setForm] = useState(params.get('new') === '1' ? { date: undefined } : null);
  const manager = can('workplace.manage');
  useEffect(() => { if (params.get('new')) setParams({}, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const range = useMemo(() => {
    if (view === 'month') { const s = mondayOf(new Date(cursor.getFullYear(), cursor.getMonth(), 1)); return { from: ymd(s), to: ymd(addDaysTo(s, 41)) }; }
    if (view === 'week') { const s = mondayOf(cursor); return { from: ymd(s), to: ymd(addDaysTo(s, 6)) }; }
    return {};
  }, [view, cursor]);
  const query = view === 'agenda' ? { upcoming: 1 } : view === 'all' ? { q: q || undefined, type: type || undefined, status: status || undefined, order: 'desc' } : range;
  const { data, error, loading, reload } = useLoad(() => api.get('/workplace/meetings/', query), [JSON.stringify(query)]);
  const rows = data?.results || [];

  const step = (n) => setCursor((c) => (view === 'week' ? addDaysTo(c, 7 * n) : new Date(c.getFullYear(), c.getMonth() + n, 1)));
  const title = view === 'week'
    ? `${fmtDate(ymd(mondayOf(cursor)))} – ${fmtDate(ymd(addDaysTo(mondayOf(cursor), 6)))}`
    : cursor.toLocaleDateString('en-IN', { month: 'long', year: 'numeric' });

  return (
    <>
      <PageHeader title="Meetings" description="Schedule meetings, keep agendas and record minutes."
        actions={manager && <button type="button" className="btn btn-primary" onClick={() => setForm({})}>Schedule meeting</button>} />
      <section className="card">
        <Tabs tabs={[{ key: 'month', label: 'Month' }, { key: 'week', label: 'Week' }, { key: 'agenda', label: 'Agenda' }, { key: 'all', label: 'All meetings' }]} value={view} onChange={setView} />
        {(view === 'month' || view === 'week') && (
          <div className="toolbar wp-cal-nav">
            <button type="button" className="btn btn-small" onClick={() => step(-1)} aria-label={view === 'week' ? 'Previous week' : 'Previous month'}>‹ Previous</button>
            <button type="button" className="btn btn-small" onClick={() => setCursor(new Date())}>Today</button>
            <button type="button" className="btn btn-small" onClick={() => step(1)} aria-label={view === 'week' ? 'Next week' : 'Next month'}>Next ›</button>
            <h2 aria-live="polite">{title}</h2>
          </div>
        )}
        {view === 'all' && (
          <>
            <div className="chips" role="group" aria-label="Filter by status">
              {STATUSES.map(([k, l]) => <button key={k} type="button" className="chip" aria-pressed={status === k} onClick={() => setStatus(k)}>{l}</button>)}
            </div>
            <form className="toolbar" onSubmit={(e) => e.preventDefault()}>
              <Field id="mt-q" label="Search" className="grow"><input id="mt-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Title, ID or venue" /></Field>
              <Field id="mt-ty" label="Type"><select id="mt-ty" value={type} onChange={(e) => setType(e.target.value)}><option value="">Any</option>{MEETING_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
            </form>
          </>
        )}
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!error && view === 'month' && <Month cursor={cursor} meetings={rows} onPick={(d) => manager && setForm({ date: d })} />}
        {!error && view === 'week' && <Week cursor={cursor} meetings={rows} />}
        {!error && view === 'agenda' && !loading && <Agenda meetings={rows} />}
        {!error && view === 'all' && !loading && (rows.length === 0 ? <p className="empty">No meetings match.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Time</th><th>Meeting</th><th>Type</th><th>Organiser</th><th>Status</th><th>Minutes</th></tr></thead>
              <tbody>
                {rows.map((m) => (
                  <tr key={m.id} className="clickable" onClick={() => navigate(`/workplace/meetings/${m.id}`)}>
                    <td data-label="Date">{fmtDate(m.date)}</td>
                    <td data-label="Time">{timeRange(m)}</td>
                    <td data-label="Meeting"><Link to={`/workplace/meetings/${m.id}`} onClick={(e) => e.stopPropagation()}><strong>{m.title}</strong></Link><div className="hint">{m.number}</div></td>
                    <td data-label="Type">{m.type_label}</td>
                    <td data-label="Organiser">{m.organiser?.name || '—'}</td>
                    <td data-label="Status"><MeetingStatus m={m} /></td>
                    <td data-label="Minutes">{m.minutes_count ? `${m.minutes_count} issued` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
        {data && <div className="list-foot"><span>{data.count} meeting{data.count === 1 ? '' : 's'}{view === 'month' ? ' on this calendar' : ''}</span></div>}
      </section>
      {form && <MeetingFormDialog people={people} defaultDate={form.date} onClose={() => setForm(null)}
        onSaved={(m) => { setForm(null); if (m?.id) navigate(`/workplace/meetings/${m.id}`); else reload(); }} />}
    </>
  );
}
