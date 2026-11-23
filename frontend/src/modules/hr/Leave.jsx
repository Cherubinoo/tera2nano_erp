import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart, HBars } from '../../components/charts';
import { dateOrder } from '../../forms';
import { asList } from '../../hooks';
import { Badge, ErrorBox, Field, Loading, PageHeader, Tabs, fmtDate, useConfirm, useReasonDialog, useToast } from '../../ui';
import { today } from '../../format';
import { ApprovalStatus } from '../approvals/ApprovalBadge';
import { FormDialog, Gate, H, NotLinked, StatusBadge, Tile, choose, days, monthLabel, options, thisMonth, useCan, useData, useMe } from './common';

const span = (r) => (r.start_date === r.end_date ? fmtDate(r.start_date) : `${fmtDate(r.start_date)} to ${fmtDate(r.end_date)}`);

// Apply for leave (for yourself, or for someone else when you manage HR).
export function LeaveRequestDialog({ record, forEmployee, onClose, onSaved }) {
  const notify = useToast();
  const types = useData(`${H}/leave/types/`);
  const people = useData(`${H}/lookup/`, undefined, !!forEmployee);
  const fields = useMemo(() => [
    ...(forEmployee ? [{ name: 'employee', label: 'Employee', type: 'select', required: true, options: choose(options(asList(people.data), (p) => p.name)) }] : []),
    { name: 'leave_type', label: 'Leave type', type: 'select', required: true, options: choose(options(asList(types.data), (t) => `${t.name}${t.is_paid ? '' : ' (unpaid)'}`)) },
    { name: 'start_date', label: 'First day', type: 'date', required: true },
    { name: 'end_date', label: 'Last day', type: 'date', hidden: (v) => v.half_day, validate: dateOrder('start_date', 'end_date', 'The last day cannot be before the first day.') },
    { name: 'half_day', label: 'Half day', type: 'checkbox' },
    { name: 'reason', label: 'Reason', type: 'textarea', rows: 2, wide: true, maxLength: 500 },
    ...(record ? [] : [{ name: 'submit', label: 'Send for approval now (otherwise it is saved as a draft)', type: 'checkbox' }]),
  ], [forEmployee, types.data, people.data, record]);
  if (types.loading || (forEmployee && people.loading)) return null;
  return (
    <FormDialog title={record ? 'Edit leave request' : 'Apply for leave'} fields={fields} wide={false} submitLabel={record ? 'Save' : 'Apply'}
      record={record ? { ...record, leave_type: record.leave_type.id } : undefined} defaults={{ submit: true, start_date: today(), end_date: today() }}
      onSubmit={async (p, v) => {
        const body = { ...p, end_date: v.half_day ? v.start_date : (p.end_date || p.start_date) };
        if (record) await api.patch(`${H}/leave/requests/${record.id}/`, body); else await api.post(`${H}/leave/requests/`, body);
        notify(record ? 'Leave request saved.' : body.submit ? 'Leave request sent for approval.' : 'Leave request saved as a draft.');
        onSaved();
      }} onClose={onClose} />
  );
}

// Leave requests with the actions the viewer may take on each.
export function LeaveTable({ rows, showEmployee = true, reload, empty = 'No leave requests.' }) {
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [editing, setEditing] = useState(null);
  const act = async (r, action, body) => {
    try { await api.post(`${H}/leave/requests/${r.id}/${action}/`, body || {}); notify(`Request ${({ submit: 'sent for approval', approve: 'approved', reject: 'rejected', cancel: 'cancelled' })[action]}.`); reload(); } catch (err) { notify(err.message, 'error'); }
  };
  const reject = async (r) => { const note = await ask({ title: `Reject ${r.employee.name}'s request?`, label: 'Reason', confirmLabel: 'Reject' }); if (note !== null) act(r, 'reject', { note }); };
  const cancel = async (r) => { if (await confirm({ title: 'Cancel this leave request?', body: <p>{r.status === 'approved' ? 'The days are given back and the attendance entries are removed.' : 'It will no longer be considered.'}</p>, confirmLabel: 'Cancel request', cancelLabel: 'Keep it', danger: true })) act(r, 'cancel'); };
  const del = async (r) => { try { await api.del(`${H}/leave/requests/${r.id}/`); notify('Draft deleted.'); reload(); } catch (err) { notify(err.message, 'error'); } };
  if (!rows.length) return <p className="empty">{empty}</p>;
  return (
    <>
      <div className="table-wrap"><table>
        <thead><tr>{showEmployee && <th>Employee</th>}<th>Number</th><th>Type</th><th>Dates</th><th className="num">Days</th><th>Reason</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.id} className={['rejected', 'cancelled'].includes(r.status) ? 'row-inactive' : ''}>
            {showEmployee && <td data-label="Employee"><Link to={`/hr/employees/${r.employee.id}`}>{r.employee.name}</Link></td>}
            <td data-label="Number">{r.number ? <code>{r.number}</code> : <em>Draft</em>}</td>
            <td data-label="Type">{r.leave_type.name}{r.half_day ? ' (half day)' : ''}{!r.leave_type.is_paid && <> <Badge tone="warn">Unpaid</Badge></>}</td>
            <td data-label="Dates">{span(r)}</td><td className="num" data-label="Days">{days(r.days)}</td>
            <td data-label="Reason">{r.reason || '—'}{r.decision_note && <div className="hint">{r.decided_by ? `${r.decided_by}: ` : ''}{r.decision_note}</div>}</td>
            <td data-label="Status"><StatusBadge status={r.status} label={r.status_label} />{r.status === 'pending_approval' && <> <ApprovalStatus kind="leave_request" sourceId={r.id} /></>}</td>
            <td className="hr-row-actions">
              {r.can_edit && r.is_mine && <button type="button" className="btn btn-small btn-primary" onClick={() => act(r, 'submit')}>Submit</button>}
              {r.can_edit && <button type="button" className="btn btn-small" onClick={() => setEditing(r)}>Edit</button>}
              {r.can_edit && <button type="button" className="btn btn-small btn-danger-outline" onClick={() => del(r)}>Delete</button>}
              {r.can_approve && <button type="button" className="btn btn-small btn-primary" onClick={() => act(r, 'approve')}>Approve</button>}
              {r.can_approve && <button type="button" className="btn btn-small" onClick={() => reject(r)}>Reject</button>}
              {r.can_cancel && r.status !== 'draft' && <button type="button" className="btn btn-small btn-danger-outline" onClick={() => cancel(r)}>Cancel</button>}
            </td>
          </tr>
        ))}</tbody>
      </table></div>
      {editing && <LeaveRequestDialog record={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />}
      {dialog}{confirmDialog}
    </>
  );
}

const BAL_COLOURS = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-5)'];
export function BalanceTiles({ rows }) {
  return (
    <div className="hr-balance">
      {rows.map((b) => (b.leave_type.is_paid ? (
        <div key={b.leave_type.id} className="tile">
          <div className="tile-label">{b.leave_type.name}</div><div className="tile-value">{days(b.available)} <span className="hint">days left</span></div>
          <div className="hint">{days(b.total)} this year ({days(b.accrued)} yearly{Number(b.carried_forward) ? ` + ${days(b.carried_forward)} carried` : ''}{Number(b.adjustments) ? ` ${Number(b.adjustments) > 0 ? '+' : ''}${days(b.adjustments)} adjusted` : ''})</div>
          <div className="hint">{days(b.taken)} taken{Number(b.pending) ? `, ${days(b.pending)} waiting` : ''}</div>
        </div>
      ) : (
        <div key={b.leave_type.id} className="tile">
          <div className="tile-label">{b.leave_type.name} (unpaid)</div><div className="tile-value">{days(b.taken)} <span className="hint">days taken</span></div>
          <div className="hint">No limit. Reduces the month's pay.{Number(b.pending) ? ` ${days(b.pending)} waiting for approval.` : ''}</div>
        </div>
      )))}
    </div>
  );
}

const STATUS_CHIPS = [['', 'All'], ['pending_approval', 'Pending'], ['approved', 'Approved'], ['rejected', 'Rejected'], ['cancelled', 'Cancelled'], ['draft', 'Drafts']];

function Requests() {
  const can = useCan();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState('pending_approval');
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const [adding, setAdding] = useState(false);
  const open = params.get('open');
  const state = useData(`${H}/leave/requests/`, { status: status || undefined, q: term || undefined });
  const one = useData(`${H}/leave/requests/${open}/`, undefined, !!open);
  const rows = asList(state.data);
  return (
    <>
      {open && one.data && (
        <section className="card" aria-label="Selected request">
          <div className="card-head"><h2>Request {one.data.number || 'draft'}</h2><button type="button" className="btn btn-small" onClick={() => setParams({})}>Close</button></div>
          <LeaveTable rows={[one.data]} reload={() => { one.reload(); state.reload(); }} />
        </section>
      )}
      <section className="card">
        <div className="card-head"><h2>Leave requests</h2>{can('hr.manage') && <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>New request for someone</button>}</div>
        <div className="chips" role="group" aria-label="Status">{STATUS_CHIPS.map(([k, l]) => <button key={k || 'all'} type="button" className="chip" aria-pressed={status === k} onClick={() => setStatus(k)}>{l}</button>)}</div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
          <Field id="lv-q" label="Search" className="grow"><input id="lv-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Employee or request number" /></Field>
          <button type="submit" className="btn">Search</button>
        </form>
        {state.loading ? <Loading /> : <ErrorBox error={state.error} onRetry={state.reload} />}
        {!state.loading && !state.error && <LeaveTable rows={rows} reload={state.reload} empty={status === 'pending_approval' ? 'Nothing is waiting for approval.' : 'No requests match.'} />}
      </section>
      {adding && <LeaveRequestDialog forEmployee onClose={() => setAdding(false)} onSaved={() => { setAdding(false); state.reload(); }} />}
    </>
  );
}

function TeamCalendar() {
  const [month, setMonth] = useState(thisMonth());
  const state = useData(`${H}/leave/calendar/`, { month });
  return (
    <section className="card" aria-label="Team leave calendar">
      <div className="toolbar">
        <Field id="lc-month" label="Month"><input id="lc-month" type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} /></Field>
        <span className="muted">Approved leave per day, {monthLabel(month)}.</span>
      </div>
      <Gate state={state}>{(d) => {
        const peak = Math.max(0, ...d.per_day.map((x) => x.away));
        const holidays = d.holidays.map((h) => `${fmtDate(h.date)} ${h.name}`);
        return (
          <>
            <ColumnChart title="People on approved leave each day" format={(v) => String(Math.round(v))} series={[{ key: 'away', label: 'People away', colour: 'var(--chart-5)' }]}
              data={d.per_day.map((x) => ({ label: String(Number(x.date.slice(8))), values: { away: x.away } }))} />
            <p className="hint">{peak ? `Most people away on one day: ${peak}.` : 'No approved leave this month.'}{holidays.length ? ` Holidays: ${holidays.join('; ')}.` : ''}</p>
            {d.requests.length > 0 && (
              <div className="table-wrap"><table>
                <thead><tr><th>Employee</th><th>Type</th><th>Dates</th><th className="num">Days</th><th>Status</th></tr></thead>
                <tbody>{d.requests.map((r) => (
                  <tr key={r.id}><td data-label="Employee"><Link to={`/hr/employees/${r.employee.id}`}>{r.employee.name}</Link></td><td data-label="Type">{r.leave_type.name}</td>
                    <td data-label="Dates">{span(r)}</td><td className="num" data-label="Days">{days(r.days)}</td><td data-label="Status"><StatusBadge status={r.status} label={r.status_label} /></td></tr>
                ))}</tbody>
              </table></div>
            )}
          </>
        );
      }}</Gate>
    </section>
  );
}

function Balances() {
  const notify = useToast();
  const people = useData(`${H}/lookup/`);
  const types = useData(`${H}/leave/types/`);
  const [emp, setEmp] = useState('');
  const [adjusting, setAdjusting] = useState(false);
  const bal = useData(`${H}/leave/balances/`, { employee: emp }, !!emp);
  const adj = useData(`${H}/leave/adjustments/`, { employee: emp }, !!emp);
  const can = useCan();
  return (
    <section className="card" aria-label="Leave balances">
      <h2>Balances</h2>
      <div className="toolbar">
        <Field id="bal-emp" label="Employee"><select id="bal-emp" value={emp} onChange={(e) => setEmp(e.target.value)}><option value="">— choose —</option>{asList(people.data).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
        {emp && can('hr.manage') && <button type="button" className="btn" onClick={() => setAdjusting(true)}>Adjust a balance</button>}
      </div>
      {!emp && <p className="empty">Choose an employee to see their leave balances.</p>}
      {emp && (bal.loading || (!bal.data && !bal.error) ? <Loading /> : bal.error ? <ErrorBox error={bal.error} onRetry={bal.reload} /> : (
        <>
          <BalanceTiles rows={bal.data.rows} />
          <HBars rows={bal.data.rows.map((b, i) => ({ key: b.leave_type.id, label: b.leave_type.name, value: Number(b.taken), colour: BAL_COLOURS[i % 4] }))} format={(v) => `${days(v)} days taken`} empty="No leave types." />
          {asList(adj.data).length > 0 && (
            <><h3>Adjustments</h3><div className="table-wrap"><table>
              <thead><tr><th>Date</th><th>Type</th><th className="num">Days</th><th>Reason</th></tr></thead>
              <tbody>{asList(adj.data).map((a) => <tr key={a.id}><td data-label="Date">{fmtDate(a.created_at.slice(0, 10))}</td><td data-label="Type">{a.leave_type}</td><td className="num" data-label="Days">{Number(a.days) > 0 ? '+' : ''}{days(a.days)}</td><td data-label="Reason">{a.reason}</td></tr>)}</tbody>
            </table></div></>
          )}
        </>
      ))}
      {adjusting && (
        <FormDialog title="Adjust a leave balance" wide={false} submitLabel="Save adjustment" defaults={{ days: '1' }}
          fields={[{ name: 'leave_type', label: 'Leave type', type: 'select', required: true, options: choose(options(asList(types.data))) },
            { name: 'days', label: 'Days to add (use a minus sign to deduct)', type: 'number', step: '0.5', required: true, validate: (v) => (Number(v) === 0 ? 'Enter a number other than zero.' : null) },
            { name: 'reason', label: 'Reason', required: true, wide: true, hint: 'For example "Opening balance" or "Encashed".' }]}
          onSubmit={async (p) => { await api.post(`${H}/leave/adjustments/`, { ...p, employee: emp }); notify('Balance adjusted.'); setAdjusting(false); bal.reload(); adj.reload(); }} onClose={() => setAdjusting(false)} />
      )}
    </section>
  );
}

function Types() {
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const can = useCan();
  const [editing, setEditing] = useState(null);
  const state = useData(`${H}/leave/types/`, { all: 'true' });
  const remove = async (t) => {
    if (!(await confirm({ title: `Delete ${t.name}?`, body: <p>A leave type that has been used cannot be deleted; switch it off instead.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`${H}/leave/types/${t.id}/`); notify('Leave type deleted.'); state.reload(); } catch (err) { notify(err.message, 'error'); }
  };
  const manage = can('hr.manage');
  return (
    <section className="card" aria-label="Leave types">
      <div className="card-head"><h2>Leave types</h2>{manage && <button type="button" className="btn btn-primary" onClick={() => setEditing({})}>New leave type</button>}</div>
      <Gate state={state}>{(rows) => (rows.length === 0 ? <p className="empty">No leave types yet. Add casual, sick and earned leave to begin.</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Type</th><th>Code</th><th className="num">Days a year</th><th>Carry forward</th><th>Pay</th><th>Status</th>{manage && <th />}</tr></thead>
          <tbody>{rows.map((t) => (
            <tr key={t.id} className={t.is_active ? '' : 'row-inactive'}>
              <td data-label="Type"><strong>{t.name}</strong>{t.description && <div className="hint">{t.description}</div>}</td><td data-label="Code"><code>{t.code}</code></td>
              <td className="num" data-label="Days a year">{days(t.annual_quota)}</td><td data-label="Carry forward">{t.carry_forward ? `Up to ${days(t.max_carry_forward)} days` : 'No'}</td>
              <td data-label="Pay">{t.is_paid ? 'Paid' : <Badge tone="warn">Unpaid (loss of pay)</Badge>}</td><td data-label="Status">{t.is_active ? <Badge tone="good">Active</Badge> : <Badge>Switched off</Badge>}</td>
              {manage && <td className="hr-row-actions"><button type="button" className="btn btn-small" onClick={() => setEditing(t)}>Edit</button><button type="button" className="btn btn-small btn-danger-outline" onClick={() => remove(t)}>Delete</button></td>}
            </tr>
          ))}</tbody>
        </table></div>
      ))}</Gate>
      {editing && (
        <FormDialog title={editing.id ? `Edit ${editing.name}` : 'New leave type'} wide={false} record={editing.id ? editing : undefined} defaults={{ is_paid: true, is_active: true, annual_quota: '12' }}
          fields={[{ name: 'name', label: 'Name', required: true, maxLength: 80 }, { name: 'code', label: 'Code', required: true, upper: true, maxLength: 10, hint: 'A short code such as CL or SL.' },
            { name: 'annual_quota', label: 'Days a year', type: 'number', min: 0, step: '0.5', required: true, hint: 'Leave 0 for unpaid leave, which has no limit.' },
            { name: 'is_paid', label: 'Paid leave (unpaid leave reduces the month\'s pay)', type: 'checkbox' },
            { name: 'carry_forward', label: 'Unused days carry forward to the next year', type: 'checkbox' },
            { name: 'max_carry_forward', label: 'Most days that carry forward', type: 'number', min: 0, step: '0.5', hidden: (v) => !v.carry_forward, required: false,
              validate: (v, all) => (all.carry_forward && !(Number(v) > 0) ? 'Say how many days can be carried forward.' : null) },
            { name: 'description', label: 'Description', wide: true }, ...(editing.id ? [{ name: 'is_active', label: 'Active', type: 'checkbox' }] : [])]}
          onSubmit={async (p) => { if (editing.id) await api.patch(`${H}/leave/types/${editing.id}/`, p); else await api.post(`${H}/leave/types/`, p); notify('Leave type saved.'); setEditing(null); state.reload(); }} onClose={() => setEditing(null)} />
      )}
      {dialog}
    </section>
  );
}

export default function Leave() {
  const [params] = useSearchParams();
  const [tab, setTab] = useState('requests');
  const can = useCan();
  useEffect(() => { if (params.get('open')) setTab('requests'); }, [params]);
  return (
    <>
      <PageHeader title="Leave" description="Requests, who is away, balances and leave types." />
      <Tabs tabs={[{ key: 'requests', label: 'Requests' }, { key: 'calendar', label: 'Team calendar' }, { key: 'balances', label: 'Balances' }, { key: 'types', label: 'Leave types' }].filter((t) => can('hr.view') || t.key === 'requests')} value={tab} onChange={setTab} />
      {tab === 'requests' && <Requests />}
      {tab === 'calendar' && <TeamCalendar />}
      {tab === 'balances' && <Balances />}
      {tab === 'types' && <Types />}
    </>
  );
}

export function MyLeave() {
  const me = useMe();
  const [tab, setTab] = useState('mine');
  const [applying, setApplying] = useState(false);
  const bal = useData(`${H}/leave/balances/`);
  const mine = useData(`${H}/leave/requests/`, { scope: 'mine' });
  const team = useData(`${H}/leave/requests/`, { scope: 'approvals' });
  const reload = () => { bal.reload(); mine.reload(); team.reload(); };
  return (
    <Gate state={me}>{(m) => {
      if (!m.linked) return <><PageHeader title="My leave" /><NotLinked /></>;
      const approvals = asList(team.data);
      return (
        <>
          <PageHeader title="My leave" description="Your balances and requests." actions={<button type="button" className="btn btn-primary" onClick={() => setApplying(true)}>Apply for leave</button>} />
          {bal.loading ? <Loading /> : bal.data?.rows?.length ? <BalanceTiles rows={bal.data.rows} /> : <p className="muted">No leave types have been set up yet.</p>}
          <Tabs tabs={[{ key: 'mine', label: 'My requests' }, ...(m.manager_user || approvals.length ? [{ key: 'team', label: `Team approvals${approvals.length ? ` (${approvals.length})` : ''}` }] : [])]} value={tab} onChange={setTab} />
          {tab === 'mine' && <section className="card" aria-label="My requests">{mine.loading ? <Loading /> : <><ErrorBox error={mine.error} onRetry={mine.reload} /><LeaveTable rows={asList(mine.data)} showEmployee={false} reload={reload} empty="You have not asked for any leave yet." /></>}</section>}
          {tab === 'team' && <section className="card" aria-label="Team approvals"><LeaveTable rows={approvals} reload={reload} empty="Nothing is waiting for your approval." /></section>}
          {applying && <LeaveRequestDialog onClose={() => setApplying(false)} onSaved={() => { setApplying(false); reload(); }} />}
        </>
      );
    }}</Gate>
  );
}
