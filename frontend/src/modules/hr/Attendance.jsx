import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { StackedBar } from '../../components/charts';
import { Badge, Field, FormErrors, PageHeader, Tabs, fmtDate, useToast } from '../../ui';
import { today } from '../../format';
import { ATT, FormDialog, Gate, H, HeatTable, NotLinked, StatusBadge, Tile, days, monthLabel, thisMonth, useCan, useData, useMe } from './common';

const STATUSES = [['present', 'Present'], ['absent', 'Absent'], ['half_day', 'Half day'], ['wfh', 'Work from home'], ['holiday', 'Holiday'], ['leave', 'On leave']];

function DaySheet() {
  const can = useCan();
  const notify = useToast();
  const [date, setDate] = useState(today());
  const [picked, setPicked] = useState({});
  const [bulk, setBulk] = useState('present');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const state = useData(`${H}/attendance/`, { date });
  const manage = can('hr.manage');
  const mark = async (records) => {
    setBusy(true); setError(null);
    try {
      const r = await api.post(`${H}/attendance/`, { date, records });
      notify(r.skipped.length ? `${r.marked} marked, ${r.skipped.length} skipped: ${r.skipped[0].reason}` : `${r.marked} marked.`, r.skipped.length ? 'info' : 'success');
      setPicked({}); state.reload();
    } catch (err) { setError(err); }
    setBusy(false);
  };
  return (
    <Gate state={state}>{(d) => {
      const counts = Object.fromEntries(STATUSES.map(([k]) => [k, d.rows.filter((r) => r.status === k).length]));
      const unmarked = d.rows.filter((r) => !r.status).length;
      const ids = Object.keys(picked).filter((k) => picked[k]);
      return (
        <>
          <section className="card" aria-label="Day sheet">
            <div className="toolbar">
              <Field id="att-date" label="Date"><input id="att-date" type="date" value={date} max={today()} onChange={(e) => e.target.value && setDate(e.target.value)} /></Field>
              {d.is_holiday && <Badge tone="info">Holiday</Badge>}{d.is_weekly_off && <Badge>Weekly off</Badge>}
            </div>
            <StackedBar format={(v) => String(v)} total={d.rows.length} parts={[
              { key: 'p', label: 'Present', value: counts.present, colour: 'var(--chart-2)' }, { key: 'w', label: 'Work from home', value: counts.wfh, colour: 'var(--chart-1)' },
              { key: 'h', label: 'Half day', value: counts.half_day, colour: 'var(--chart-3)' }, { key: 'l', label: 'On leave', value: counts.leave, colour: 'var(--chart-5)' },
              { key: 'a', label: 'Absent', value: counts.absent, colour: 'var(--chart-4)' }, { key: 'u', label: 'Not marked', value: unmarked, colour: 'var(--chart-grey)' }]} />
            {manage && (
              <form className="toolbar" onSubmit={(e) => { e.preventDefault(); if (ids.length) mark(ids.map((id) => ({ employee: id, status: bulk }))); }}>
                <Field id="att-bulk" label={`Mark ${ids.length || 'selected'} as`}>
                  <select id="att-bulk" value={bulk} onChange={(e) => setBulk(e.target.value)}>{STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
                </Field>
                <button type="submit" className="btn btn-primary" disabled={busy || ids.length === 0}>Apply to selected</button>
                <button type="button" className="btn" disabled={busy} onClick={() => { const un = d.rows.filter((r) => !r.status).map((r) => ({ employee: r.employee.id, status: 'present' })); if (un.length) mark(un); }}>Mark everyone not yet marked as present</button>
              </form>
            )}
            <FormErrors error={error} fields={[]} />
            {d.rows.length === 0 ? <p className="empty">No one was working on this date.</p> : (
              <div className="table-wrap"><table>
                <thead><tr>{manage && <th><span className="sr-only">Select</span></th>}<th>Employee</th><th>Department</th><th>Status</th><th>In</th><th>Out</th><th>Note</th>{manage && <th><span className="sr-only">Mark</span></th>}</tr></thead>
                <tbody>{d.rows.map((r) => (
                  <tr key={r.employee.id}>
                    {manage && <td data-label="Select"><input type="checkbox" aria-label={`Select ${r.employee.name}`} checked={!!picked[r.employee.id]} disabled={r.source === 'leave'} onChange={(e) => setPicked({ ...picked, [r.employee.id]: e.target.checked })} /></td>}
                    <td data-label="Employee"><Link to={`/hr/employees/${r.employee.id}`}>{r.employee.name}</Link></td><td data-label="Department">{r.employee.department || '—'}</td>
                    <td data-label="Status">{r.status ? <StatusBadge status={r.status} label={ATT[({ present: 'P', absent: 'A', half_day: 'H', leave: 'L', holiday: 'O', wfh: 'W' })[r.status]].label} /> : <span className="muted">Not marked</span>}</td>
                    <td data-label="In">{r.check_in ? r.check_in.slice(0, 5) : '—'}</td><td data-label="Out">{r.check_out ? r.check_out.slice(0, 5) : '—'}</td><td data-label="Note">{r.note || '—'}</td>
                    {manage && <td className="hr-row-actions">{r.source !== 'leave' && ['present', 'absent'].map((s) => <button key={s} type="button" className="btn btn-small" disabled={busy || r.status === s} onClick={() => mark([{ employee: r.employee.id, status: s }])}>{s === 'present' ? 'Present' : 'Absent'}</button>)}</td>}
                  </tr>
                ))}</tbody>
              </table></div>
            )}
          </section>
        </>
      );
    }}</Gate>
  );
}

function Grid() {
  const can = useCan();
  const notify = useToast();
  const [month, setMonth] = useState(thisMonth());
  const [cell, setCell] = useState(null);
  const state = useData(`${H}/attendance/grid/`, { month });
  const manage = can('hr.manage');
  return (
    <section className="card" aria-label="Monthly attendance">
      <div className="toolbar">
        <Field id="grid-month" label="Month"><input id="grid-month" type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} /></Field>
        <span className="muted">{monthLabel(month)}{manage ? '. Click a day to mark it.' : ''}</span>
      </div>
      <Gate state={state}>{(d) => (d.rows.length === 0 ? <p className="empty">No employees for this month.</p> : (
        <HeatTable days={d.days} rows={d.rows} onCell={manage ? (employee, date) => setCell({ employee, date }) : undefined} />
      ))}</Gate>
      {cell && (
        <FormDialog title={`${cell.employee.name}, ${fmtDate(cell.date)}`} wide={false} defaults={{ status: 'present' }} submitLabel="Mark"
          fields={[{ name: 'status', label: 'Status', type: 'select', required: true, options: STATUSES.map(([value, label]) => ({ value, label })) },
            { name: 'check_in', label: 'In', type: 'time' }, { name: 'check_out', label: 'Out', type: 'time' }, { name: 'note', label: 'Note', wide: true }]}
          onSubmit={async (p) => {
            const r = await api.post(`${H}/attendance/`, { date: cell.date, records: [{ employee: cell.employee.id, ...p }] });
            if (r.skipped.length) throw Object.assign(new Error(r.skipped[0].reason), { fieldErrors: { non_field_errors: r.skipped[0].reason } });
            notify('Attendance marked.'); setCell(null); state.reload();
          }} onClose={() => setCell(null)} />
      )}
    </section>
  );
}

export default function Attendance() {
  const [tab, setTab] = useState('day');
  return (
    <>
      <PageHeader title="Attendance" description="Mark who was in, and see the month at a glance." />
      <Tabs tabs={[{ key: 'day', label: 'Daily sheet' }, { key: 'month', label: 'Monthly grid' }]} value={tab} onChange={setTab} />
      {tab === 'day' ? <DaySheet /> : <Grid />}
    </>
  );
}

export function MyAttendance() {
  const notify = useToast();
  const [month, setMonth] = useState(thisMonth());
  const [status, setStatus] = useState('present');
  const [busy, setBusy] = useState(false);
  const me = useMe();
  const state = useData(`${H}/me/attendance/`, { month });
  const act = async (path, body) => {
    setBusy(true);
    try { await api.post(`${H}/me/${path}/`, body); notify(path === 'check-in' ? 'Checked in.' : 'Checked out.'); state.reload(); } catch (err) { notify(err.message, 'error'); }
    setBusy(false);
  };
  return (
    <>
      <PageHeader title="My attendance" description="Check in and out, and see your month." />
      <Gate state={state}>{(d) => {
        if (!d.linked) return <NotLinked />;
        const t = d.today;
        const s = d.grid?.summary;
        return (
          <>
            <section className="card" aria-label="Today">
              <h2>Today</h2>
              {!t ? (
                <form className="toolbar" onSubmit={(e) => { e.preventDefault(); act('check-in', { status }); }}>
                  <Field id="my-status" label="Working from"><select id="my-status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="present">Office</option><option value="wfh">Home</option></select></Field>
                  <button type="submit" className="btn btn-primary" disabled={busy}>Check in</button>
                </form>
              ) : (
                <div className="hr-strip">
                  <StatusBadge status={t.status} label={t.status_label} />
                  <span>In {t.check_in ? t.check_in.slice(0, 5) : '—'}</span><span>Out {t.check_out ? t.check_out.slice(0, 5) : '—'}</span>
                  {t.check_in && !t.check_out && t.source === 'self' && <button type="button" className="btn btn-primary" disabled={busy} onClick={() => act('check-out')}>Check out</button>}
                </div>
              )}
              {me.data?.linked && <p className="hint">Marked as {me.data.name}.</p>}
            </section>
            {s && (
              <div className="tiles">
                <Tile label="Present" value={days(s.present + s.wfh)} hint={`${days(s.wfh)} from home`} /><Tile label="Half days" value={days(s.half_day)} />
                <Tile label="Absent" value={days(s.absent)} tone={s.absent ? 'bad' : ''} /><Tile label="On leave" value={days(s.leave)} /><Tile label="Not marked" value={s.unmarked} />
              </div>
            )}
            <section className="card" aria-label="My month">
              <div className="card-head"><h2>{monthLabel(month)}</h2>
                <input type="month" value={month} aria-label="Month" onChange={(e) => e.target.value && setMonth(e.target.value)} /></div>
              {d.grid && <HeatTable days={d.days} rows={[d.grid]} />}
            </section>
          </>
        );
      }}</Gate>
    </>
  );
}
