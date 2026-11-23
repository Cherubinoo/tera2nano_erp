import { Link, Navigate } from 'react-router-dom';
import { ColumnChart, Donut, HBars, SERIES_COLOURS, StackedBar } from '../../components/charts';
import { compact, money, num } from '../../format';
import { PageHeader, fmtDate } from '../../ui';
import { ApprovalStatus } from '../approvals/ApprovalBadge';
import { Gate, H, StatusBadge, Tile, shortMonth, useCan, useData } from './common';

const TYPE_LABEL = { full_time: 'Full-time', contract: 'Contract', intern: 'Intern', consultant: 'Consultant' };

export default function Dashboard() {
  const can = useCan();
  const state = useData(`${H}/dashboard/`, undefined, can('hr.view'));
  if (!can('hr.view')) return <Navigate to="/hr/my-profile" replace />;
  return (
    <Gate state={state}>
      {(d) => {
        const att = d.attendance_today;
        const slices = d.by_department.map((r, i) => ({ key: r.name, label: r.name, value: r.count, colour: SERIES_COLOURS[i % SERIES_COLOURS.length] }));
        const notMarked = Math.max(att.expected - att.marked, 0);
        return (
          <>
            <PageHeader title="People" description={`Headcount, time, leave and payroll at ${fmtDate(d.today)}.`}
              actions={can('hr.manage') && (<><Link className="btn btn-primary" to="/hr/employees?new=1">Add employee</Link><Link className="btn" to="/hr/attendance">Mark attendance</Link></>)} />
            <div className="tiles">
              <Tile label="Headcount" value={d.headcount} hint={`${d.active} active, ${d.on_notice} on notice`} to="/hr/employees" />
              <Tile label="Joined this month" value={d.new_joiners} hint={`${d.exits_this_month} left this month`} />
              <Tile label="On leave today" value={d.on_leave_today.length} to="/hr/leave" tone={d.on_leave_today.length ? 'info' : ''} />
              <Tile label="Leave to approve" value={d.pending_leave_count} to="/hr/leave" tone={d.pending_leave_count ? 'warn' : ''} />
              {d.payroll && (
                <Tile label={`Payroll ${shortMonth(d.payroll.month)}`} value={d.payroll.run ? money(d.payroll.run.employer_cost) : 'Not run'}
                  hint={d.payroll.run ? `${d.payroll.run.status_label}, net ${money(d.payroll.run.total_net)}` : 'No payroll run for this month yet'}
                  to={d.payroll.run ? `/hr/payroll/runs/${d.payroll.run.id}` : '/hr/payroll'} />
              )}
            </div>

            <div className="grid-2">
              <section className="card" aria-label="Headcount by department">
                <h2>People by department</h2>
                <p className="muted">Everyone who has not left.</p>
                {slices.length === 0 ? <p className="empty">No employees yet.</p> : <Donut slices={slices} centreLabel="people" />}
              </section>
              <section className="card" aria-label="Joiners and leavers">
                <h2>Joiners and leavers</h2>
                <p className="muted">Last six months.</p>
                <ColumnChart title="Joiners and leavers per month" format={(v) => String(Math.round(v))}
                  series={[{ key: 'joined', label: 'Joined', colour: 'var(--chart-2)' }, { key: 'exited', label: 'Left', colour: 'var(--chart-4)' }]}
                  data={d.trend.map((t) => ({ label: t.label, values: { joined: t.joined, exited: t.exited } }))} />
              </section>
            </div>

            <div className="grid-2">
              <section className="card" aria-label="Attendance today">
                <div className="card-head"><h2>Attendance today</h2><Link to="/hr/attendance">Open sheet</Link></div>
                {att.is_holiday && <p className="muted">Today is a holiday.</p>}
                {att.is_weekly_off && !att.is_holiday && <p className="muted">Today is a weekly off.</p>}
                <StackedBar format={(v) => String(v)} total={att.expected}
                  parts={[{ key: 'present', label: 'Present', value: att.present, colour: 'var(--chart-2)' }, { key: 'wfh', label: 'Work from home', value: att.wfh, colour: 'var(--chart-1)' },
                    { key: 'half', label: 'Half day', value: att.half_day, colour: 'var(--chart-3)' }, { key: 'leave', label: 'On leave', value: att.leave, colour: 'var(--chart-5)' },
                    { key: 'absent', label: 'Absent', value: att.absent, colour: 'var(--chart-4)' }, { key: 'none', label: 'Not marked', value: notMarked, colour: 'var(--chart-grey)' }]} />
              </section>
              <section className="card" aria-label="People by employment type">
                <h2>By employment type</h2>
                <HBars rows={d.by_type.map((t) => ({ key: t.type, label: TYPE_LABEL[t.type] || t.type, value: t.count }))} format={(v) => String(v)} />
              </section>
            </div>

            {d.payroll && (
              <section className="card" aria-label="Payroll cost">
                <div className="card-head"><h2>Payroll cost</h2><Link to="/hr/payroll">Payroll</Link></div>
                <p className="muted">Cost to the company and net pay of approved, posted and paid runs, last six months.</p>
                {d.payroll.trend.every((t) => !num(t.employer_cost)) ? <p className="empty">No approved payroll in the last six months yet.</p> : <ColumnChart title="Payroll cost per month" format={compact}
                  series={[{ key: 'cost', label: 'Cost to company', colour: 'var(--chart-1)' }, { key: 'net', label: 'Net pay', colour: 'var(--chart-2)' }]}
                  data={d.payroll.trend.map((t) => ({ label: t.label, values: { cost: num(t.employer_cost), net: num(t.net) } }))} />}
              </section>
            )}

            <div className="grid-2">
              <section className="card" aria-label="Upcoming birthdays and anniversaries">
                <h2>Birthdays and work anniversaries</h2>
                <p className="muted">The next 30 days.</p>
                {d.upcoming.length === 0 ? <p className="empty">Nothing coming up.</p> : (
                  <ul className="hr-list">
                    {d.upcoming.map((u) => (
                      <li key={`${u.kind}-${u.employee.id}`}>
                        <span><Link to={`/hr/employees/${u.employee.id}`}>{u.employee.name}</Link>
                          <div className="hint">{u.kind === 'birthday' ? 'Birthday' : `${u.years} year${u.years === 1 ? '' : 's'} with the company`}</div></span>
                        <span>{u.in_days === 0 ? 'Today' : fmtDate(u.date)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              <section className="card" aria-label="Leave waiting for approval">
                <div className="card-head"><h2>Leave waiting for approval</h2><Link to="/hr/leave">All requests</Link></div>
                {d.pending_leave.length === 0 ? <p className="empty">Nothing is waiting.</p> : (
                  <ul className="hr-list">
                    {d.pending_leave.map((r) => (
                      <li key={r.id}>
                        <span><Link to={`/hr/leave?open=${r.id}`}>{r.employee.name}</Link>
                          <div className="hint">{r.leave_type}, {fmtDate(r.start_date)}{r.end_date !== r.start_date ? ` to ${fmtDate(r.end_date)}` : ''}, {r.days} day(s)</div></span>
                        <span className="hr-row-actions"><StatusBadge status="pending_approval" label="Pending" /><ApprovalStatus kind="leave_request" sourceId={r.id} /></span>
                      </li>
                    ))}
                  </ul>
                )}
                {d.on_leave_today.length > 0 && (<><h3>Away today</h3><ul className="hr-list">{d.on_leave_today.map((r) => <li key={r.employee.id}><span>{r.employee.name}</span><span className="muted">{r.leave_type}, until {fmtDate(r.until)}</span></li>)}</ul></>)}
              </section>
            </div>
          </>
        );
      }}
    </Gate>
  );
}
