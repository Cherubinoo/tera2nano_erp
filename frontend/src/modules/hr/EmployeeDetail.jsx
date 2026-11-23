import { useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, downloadFile } from '../../api';
import Attachments from '../../components/Attachments';
import { money, plain } from '../../format';
import { asList } from '../../hooks';
import { Badge, ErrorBox, Loading, Tabs, fmtDate, useConfirm, useReasonDialog, useToast } from '../../ui';
import { ApprovalPanel } from '../approvals/ApprovalBadge';
import { EmployeeDialog } from './Employees';
import { AssignmentDialog } from './Assignments';
import { FormDialog, Gate, H, HeatTable, StatusBadge, Tile, days, monthLabel, thisMonth, useCan, useData } from './common';
import { LeaveTable } from './Leave';

const Facts = ({ items }) => (
  <dl className="hr-facts">{items.filter(Boolean).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v === '' || v == null ? '—' : v}</dd></div>)}</dl>
);

const SALARY_FIELDS = [
  { name: 'effective_from', label: 'Effective from', type: 'date', required: true },
  { name: 'basic', label: 'Basic (monthly)', type: 'number', min: 0, step: '0.01', required: true },
  { name: 'hra', label: 'House rent allowance', type: 'number', min: 0, step: '0.01' },
  { name: 'special_allowance', label: 'Special allowance', type: 'number', min: 0, step: '0.01' },
  { name: 'other_allowance', label: 'Other allowances', type: 'number', min: 0, step: '0.01' },
  { name: 'fixed_deductions', label: 'Fixed monthly deduction', type: 'number', min: 0, step: '0.01', hint: 'A recurring deduction other than PF, ESI, professional tax and TDS.' },
  { name: 'tds_monthly', label: 'Income tax (TDS) per month', type: 'number', min: 0, step: '0.01', hint: 'Set by hand from the employee\'s tax declaration.' },
  { name: 'pf_enabled', label: 'Provident fund applies', type: 'checkbox' },
  { name: 'pf_on_full_basic', label: 'PF on the full basic (above the wage ceiling)', type: 'checkbox', hidden: (v) => !v.pf_enabled },
  { name: 'esi_enabled', label: 'ESI applies when the gross is within the ceiling', type: 'checkbox' },
  { name: 'pt_enabled', label: 'Professional tax applies', type: 'checkbox' },
  { name: 'notes', label: 'Notes (for example "Annual revision")', wide: true },
];

const REVISION_TONE = { pending_approval: 'warn', approved: 'good', rejected: 'bad', withdrawn: 'neutral' };

// Raises that fall under an approval rule wait here; payroll sees them only once they are approved.
function RevisionsCard({ employeeId, data, canPayroll, onChange }) {
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const revisions = data?.revisions || [];
  if (revisions.length === 0) return null;
  const act = async (r, verb, msg, body) => { try { await api.post(`${H}/employees/${employeeId}/salary-revisions/${r.id}/${verb}/`, body || {}); notify(msg); onChange(); } catch (err) { notify(err.message, 'error'); } };
  const reject = async (r) => { const reason = await ask({ title: 'Reject salary revision', confirmLabel: 'Reject' }); if (reason !== null) act(r, 'reject', 'Salary revision rejected.', { change_reason: reason }); };
  return (
    <section className="card" aria-label="Salary revisions">
      <div className="card-head"><h2>Salary revisions</h2></div>
      <p className="muted">A raise above an approval limit is not paid until it is approved. The yearly increase is the change in monthly gross times 12.</p>
      <div className="table-wrap"><table>
        <thead><tr><th>From</th><th className="num">Monthly gross</th><th className="num">Yearly increase</th><th>Status</th><th>Approval</th>{canPayroll && <th />}</tr></thead>
        <tbody>{revisions.map((r) => (
          <tr key={r.id}>
            <td data-label="From">{fmtDate(r.effective_from)}</td>
            <td className="num" data-label="Monthly gross">{plain(r.previous_gross)} → {plain(r.new_gross)}</td>
            <td className="num" data-label="Yearly increase">{plain(r.annual_increase)}</td>
            <td data-label="Status"><Badge tone={REVISION_TONE[r.status] || 'neutral'}>{r.status_label}</Badge>{r.decision_note && <div className="hint">{r.decision_note}</div>}</td>
            <td data-label="Approval"><ApprovalPanel kind="salary_revision" sourceId={r.id} waiting={r.status === 'pending_approval'} canSubmit={canPayroll} onChange={onChange} refreshKey={r.status} /></td>
            {canPayroll && <td>{r.status === 'pending_approval' && <>
              <button type="button" className="btn btn-small" onClick={() => act(r, 'withdraw', 'Salary revision withdrawn.')}>Withdraw</button>
              {!data.approval_managed && <> <button type="button" className="btn btn-small btn-primary" onClick={() => act(r, 'approve', 'Salary revision approved.')}>Approve</button> <button type="button" className="btn btn-small" onClick={() => reject(r)}>Reject</button></>}</>}</td>}
          </tr>))}</tbody>
      </table></div>
      {dialog}
    </section>
  );
}

function SalaryTab({ e, canPayroll, refresh }) {
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const [adding, setAdding] = useState(false);
  const [advance, setAdvance] = useState(false);
  const sal = useData(`${H}/employees/${e.id}/salary/`);
  const adv = useData(`${H}/employees/${e.id}/advances/`);
  const rows = sal.data?.rows || [];
  const gross = (r) => Number(r.basic) + Number(r.hra) + Number(r.special_allowance) + Number(r.other_allowance);
  const remove = async (r) => {
    if (!(await confirm({ title: `Remove the structure effective ${fmtDate(r.effective_from)}?`, body: <p>Payslips already made keep their own figures.</p>, confirmLabel: 'Remove', danger: true }))) return;
    try { await api.del(`${H}/employees/${e.id}/salary/${r.id}/`); notify('Salary structure removed.'); sal.reload(); refresh(); } catch (err) { notify(err.message, 'error'); }
  };
  const dropAdvance = async (a) => {
    try { await api.del(`${H}/employees/${e.id}/advances/${a.id}/`); notify('Advance removed.'); adv.reload(); } catch (err) { notify(err.message, 'error'); }
  };
  return (
    <>
      <section className="card" aria-label="Salary">
        <div className="card-head"><h2>Salary structure</h2>{canPayroll && <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>New structure</button>}</div>
        <p className="muted">Each row applies from its date until the next one. Payroll uses the structure in force at the end of the month.</p>
        {sal.loading ? <Loading /> : <ErrorBox error={sal.error} onRetry={sal.reload} />}
        {!sal.loading && !sal.error && rows.length === 0 && <p className="empty">No salary has been set yet.</p>}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>From</th><th className="num">Basic</th><th className="num">HRA</th><th className="num">Special</th><th className="num">Other</th><th className="num">Gross</th><th className="num">TDS</th><th>Statutory</th>{canPayroll && <th />}</tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td data-label="From">{fmtDate(r.effective_from)} {sal.data.current === r.id && <Badge tone="good">In force</Badge>}</td>
                    <td className="num" data-label="Basic">{plain(r.basic)}</td><td className="num" data-label="HRA">{plain(r.hra)}</td>
                    <td className="num" data-label="Special">{plain(r.special_allowance)}</td><td className="num" data-label="Other">{plain(r.other_allowance)}</td>
                    <td className="num" data-label="Gross"><strong>{plain(gross(r))}</strong></td><td className="num" data-label="TDS">{plain(r.tds_monthly)}</td>
                    <td data-label="Statutory">{[r.pf_enabled && 'PF', r.esi_enabled && 'ESI', r.pt_enabled && 'PT'].filter(Boolean).join(', ') || 'None'}</td>
                    {canPayroll && <td><button type="button" className="btn btn-small btn-danger-outline" onClick={() => remove(r)}>Remove</button></td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <RevisionsCard employeeId={e.id} data={sal.data} canPayroll={canPayroll} onChange={() => { sal.reload(); refresh(); }} />
      <section className="card" aria-label="Advances">
        <div className="card-head"><h2>Advances and loans</h2>{canPayroll && <button type="button" className="btn" onClick={() => setAdvance(true)}>Record an advance</button>}</div>
        <p className="muted">Recovered through payroll in instalments.</p>
        {asList(adv.data).length === 0 ? <p className="empty">No advances.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Date</th><th className="num">Amount</th><th className="num">Monthly recovery</th><th className="num">Recovered</th><th className="num">Outstanding</th><th>Status</th>{canPayroll && <th />}</tr></thead>
            <tbody>{asList(adv.data).map((a) => (
              <tr key={a.id}><td data-label="Date">{fmtDate(a.date)}</td><td className="num" data-label="Amount">{plain(a.amount)}</td><td className="num" data-label="Monthly recovery">{plain(a.installment)}</td>
                <td className="num" data-label="Recovered">{plain(a.recovered)}</td><td className="num" data-label="Outstanding">{plain(a.outstanding)}</td><td data-label="Status"><StatusBadge status={a.status} /></td>
                {canPayroll && <td>{Number(a.recovered) === 0 && <button type="button" className="btn btn-small btn-danger-outline" onClick={() => dropAdvance(a)}>Delete</button>}</td>}</tr>
            ))}</tbody>
          </table></div>
        )}
      </section>
      {adding && (
        <FormDialog title="New salary structure" fields={SALARY_FIELDS} defaults={{ pf_enabled: true, esi_enabled: true, pt_enabled: true, effective_from: e.date_of_joining }} submitLabel="Save structure"
          onSubmit={async (p) => { const r = await api.post(`${H}/employees/${e.id}/salary/`, p); notify(r.pending_approval ? 'The raise is above an approval limit and is waiting for approval. Payroll keeps the current salary until then.' : 'Salary structure saved.'); setAdding(false); sal.reload(); refresh(); }} onClose={() => setAdding(false)} />
      )}
      {advance && (
        <FormDialog title="Record an advance" wide={false} submitLabel="Save advance" defaults={{ date: new Date().toISOString().slice(0, 10) }}
          fields={[{ name: 'date', label: 'Date given', type: 'date', required: true }, { name: 'amount', label: 'Amount', type: 'number', min: 1, step: '0.01', required: true },
            { name: 'installment', label: 'Recovered each month', type: 'number', min: 1, step: '0.01', required: true, validate: (v, all) => (Number(v) > Number(all.amount) ? 'Cannot be more than the amount.' : null) },
            { name: 'notes', label: 'Notes', wide: true }]}
          onSubmit={async (p) => { await api.post(`${H}/employees/${e.id}/advances/`, p); notify('Advance recorded.'); setAdvance(false); adv.reload(); }} onClose={() => setAdvance(false)} />
      )}
      {dialog}
    </>
  );
}

function LeaveTab({ e }) {
  const bal = useData(`${H}/leave/balances/`, { employee: e.id });
  const reqs = useData(`${H}/leave/requests/`, { employee: e.id });
  return (
    <>
      <div className="hr-balance">
        {(bal.data?.rows || []).map((b) => (
          <div key={b.leave_type.id} className="tile"><div className="tile-label">{b.leave_type.name}</div><div className="tile-value">{days(b.available)}</div>
            <div className="hint">available of {days(b.total)} · {days(b.taken)} taken{Number(b.pending) ? ` · ${days(b.pending)} pending` : ''}</div></div>
        ))}
      </div>
      <section className="card" aria-label="Leave requests"><h2>Leave requests</h2>
        {reqs.loading ? <Loading /> : <LeaveTable rows={asList(reqs.data)} showEmployee={false} reload={reqs.reload} />}</section>
    </>
  );
}

function AttendanceTab({ e }) {
  const [month, setMonth] = useState(thisMonth());
  const att = useData(`${H}/employees/${e.id}/attendance/`, { month });
  return (
    <section className="card" aria-label="Attendance">
      <div className="card-head"><h2>Attendance, {monthLabel(month)}</h2>
        <label className="hr-month"><span className="sr-only">Month</span><input type="month" value={month} onChange={(ev) => ev.target.value && setMonth(ev.target.value)} aria-label="Month" /></label></div>
      <Gate state={att}>{(d) => (
        <>
          {d.grid && <div className="tiles">
            <Tile label="Present" value={days(d.grid.summary.present + d.grid.summary.wfh)} hint={`${days(d.grid.summary.wfh)} from home`} />
            <Tile label="Half days" value={days(d.grid.summary.half_day)} /><Tile label="Absent" value={days(d.grid.summary.absent)} tone={d.grid.summary.absent ? 'bad' : ''} />
            <Tile label="On leave" value={days(d.grid.summary.leave)} /><Tile label="Not marked" value={d.grid.summary.unmarked} />
          </div>}
          {d.grid && <HeatTable days={d.days} rows={[d.grid]} />}
        </>
      )}</Gate>
    </section>
  );
}

function PerformanceTab({ e }) {
  const reviews = useData(`${H}/performance/reviews/`, { employee: e.id });
  const goals = useData(`${H}/performance/goals/`, { employee: e.id });
  return (
    <>
      <section className="card" aria-label="Reviews"><h2>Reviews</h2>
        {asList(reviews.data).length === 0 ? <p className="empty">No reviews yet.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Cycle</th><th>Self rating</th><th>Manager rating</th><th>Status</th><th>Manager comments</th></tr></thead>
            <tbody>{asList(reviews.data).map((r) => (
              <tr key={r.id}><td data-label="Cycle">{r.cycle.name}</td><td data-label="Self rating">{r.self_rating ?? '—'}</td><td data-label="Manager rating">{r.manager_rating ?? '—'}</td>
                <td data-label="Status"><StatusBadge status={r.status} label={r.status_label} /></td><td data-label="Manager comments">{r.manager_comments || '—'}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </section>
      <section className="card" aria-label="Goals"><h2>Goals</h2>
        {asList(goals.data).length === 0 ? <p className="empty">No goals set.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Goal</th><th>Cycle</th><th className="num">Weight</th><th>Progress</th><th>Status</th></tr></thead>
            <tbody>{asList(goals.data).map((g) => (
              <tr key={g.id}><td data-label="Goal"><strong>{g.title}</strong></td><td data-label="Cycle">{g.cycle.name}</td><td className="num" data-label="Weight">{g.weight}%</td>
                <td data-label="Progress"><span className="hr-progress" aria-hidden="true"><span style={{ width: `${g.progress}%` }} /></span>{g.progress}%</td>
                <td data-label="Status"><StatusBadge status={g.status} label={g.status_label} /></td></tr>
            ))}</tbody>
          </table></div>
        )}
        <p className="hint"><Link to="/hr/performance">Manage reviews and goals</Link></p>
      </section>
    </>
  );
}

function PayslipsTab({ e }) {
  const notify = useToast();
  const slips = useData(`${H}/employees/${e.id}/payslips/`);
  return (
    <section className="card" aria-label="Payslips"><h2>Payslips</h2>
      <Gate state={slips}>{(rows) => (rows.length === 0 ? <p className="empty">No payslips yet.</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Month</th><th className="num">Gross</th><th className="num">Deductions</th><th className="num">Net pay</th><th>Run</th><th /></tr></thead>
          <tbody>{rows.map((s) => (
            <tr key={s.id}><td data-label="Month">{s.month_label}</td><td className="num" data-label="Gross">{plain(s.gross)}</td><td className="num" data-label="Deductions">{plain(s.total_deductions)}</td>
              <td className="num" data-label="Net pay"><strong>{plain(s.net_pay)}</strong></td><td data-label="Run"><StatusBadge status={s.run.status} /></td>
              <td><button type="button" className="btn btn-small" onClick={() => downloadFile(`${H}/payroll/payslips/${s.id}/pdf/`, 'payslip.pdf').catch((err) => notify(err.message, 'error'))}>PDF</button></td></tr>
          ))}</tbody>
        </table></div>
      ))}</Gate>
    </section>
  );
}

function ProjectsTab({ e, canManage }) {
  const [dialog, setDialog] = useState(null);
  const notify = useToast();
  const rows = useData(`${H}/assignments/`, { employee: e.id });
  const remove = async (a) => { try { await api.del(`${H}/assignments/${a.id}/`); notify('Assignment removed.'); rows.reload(); } catch (err) { notify(err.message, 'error'); } };
  return (
    <section className="card" aria-label="Projects">
      <div className="card-head"><h2>Project assignments</h2>{canManage && e.status !== 'exited' && <button type="button" className="btn btn-primary" onClick={() => setDialog({})}>Assign to a project</button>}</div>
      <Gate state={rows}>{(list) => (list.length === 0 ? <p className="empty">Not assigned to any project.</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Project</th><th>Role</th><th className="num">Allocation</th><th>From</th><th>To</th>{canManage && <th />}</tr></thead>
          <tbody>{list.map((a) => (
            <tr key={a.id}><td data-label="Project"><Link to={`/projects/projects/${a.project_id}`}>{a.project_code ? `${a.project_code} ` : ''}{a.project_name || 'Project'}</Link></td><td data-label="Role">{a.role || '—'}</td>
              <td className="num" data-label="Allocation">{a.allocation_percent}%</td><td data-label="From">{fmtDate(a.start_date)}</td><td data-label="To">{a.end_date ? fmtDate(a.end_date) : 'Ongoing'}</td>
              {canManage && <td className="hr-row-actions"><button type="button" className="btn btn-small" onClick={() => setDialog(a)}>Edit</button><button type="button" className="btn btn-small btn-danger-outline" onClick={() => remove(a)}>Remove</button></td>}</tr>
          ))}</tbody>
        </table></div>
      ))}</Gate>
      {dialog && <AssignmentDialog record={dialog.id ? dialog : null} employee={e} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); rows.reload(); }} />}
    </section>
  );
}

export default function EmployeeDetail() {
  const { id } = useParams();
  const can = useCan();
  const navigate = useNavigate();
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const [tab, setTab] = useState('profile');
  const [editing, setEditing] = useState(false);
  const [reveal, setReveal] = useState(false);
  const state = useData(`${H}/employees/${id}/`, reveal ? { reveal: 'true' } : undefined);
  const mine = useData(`${H}/me/`);
  const manage = can('hr.manage');
  const e = state.data;
  const tabs = useMemo(() => {
    if (!e) return [];
    const salaryVisible = 'current_salary' in e;
    return [{ key: 'profile', label: 'Profile' }, { key: 'job', label: 'Job & Salary' }, { key: 'documents', label: 'Documents' }, { key: 'leave', label: 'Leave' },
      { key: 'attendance', label: 'Attendance' }, { key: 'performance', label: 'Performance' }, ...(salaryVisible ? [{ key: 'payslips', label: 'Payslips' }] : []), { key: 'projects', label: 'Projects' }]
      .filter((t) => t.key !== 'documents' || can('hr.view'));
  }, [e]); // eslint-disable-line react-hooks/exhaustive-deps
  if (state.loading && !e) return <Loading />;
  if (state.error) return <ErrorBox error={state.error} onRetry={state.reload} />;
  const sensitive = e.pan_masked !== undefined;
  const salaryVisible = 'current_salary' in e;
  const isSelf = mine.data?.id === e.id;

  const remove = async () => {
    if (!(await confirm({ title: `Delete ${e.name}?`, body: <p>Only records made by mistake can be deleted. Someone with history is marked as exited instead.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`${H}/employees/${e.id}/`); notify('Employee deleted.'); navigate('/hr/employees'); } catch (err) { notify(err.message, 'error'); }
  };

  return (
    <>
      <Link to="/hr/employees" className="back-link">← Employees</Link>
      <header className="doc-head">
        <div>
          <h1>{e.name} <StatusBadge status={e.status} label={e.status_label} /></h1>
          <p className="muted"><code>{e.code}</code> · {e.designation?.name || 'No designation'}{e.department ? `, ${e.department.name}` : ''}{isSelf ? ' · this is you' : ''}</p>
        </div>
        {manage && (
          <div className="page-actions">
            <button type="button" className="btn btn-primary" onClick={() => setEditing(true)}>Edit</button>
            <button type="button" className="btn btn-danger-outline" onClick={remove}>Delete</button>
          </div>
        )}
      </header>
      {salaryVisible && e.current_salary && (
        <div className="tiles">
          <Tile label="Monthly gross" value={money(e.current_salary.gross)} hint={`From ${fmtDate(e.current_salary.effective_from)}`} />
          <Tile label="Basic" value={money(e.current_salary.basic)} />
          <Tile label="Reports" value={e.report_count} hint="People who report to them" />
        </div>
      )}
      <Tabs tabs={tabs} value={tab} onChange={setTab} />

      {tab === 'profile' && (
        <>
          <section className="card" aria-label="Contact">
            <h2>Contact</h2>
            <Facts items={[['Work email', e.work_email], ['Phone', e.phone], sensitive && ['Personal email', e.personal_email], sensitive && ['Date of birth', e.date_of_birth ? fmtDate(e.date_of_birth) : ''],
              sensitive && ['Gender', e.gender], sensitive && ['Address', e.address],
              sensitive && ['Emergency contact', [e.emergency_name, e.emergency_relation && `(${e.emergency_relation})`, e.emergency_phone].filter(Boolean).join(' ')]]} />
          </section>
          {sensitive && (
            <section className="card" aria-label="Identity and bank">
              <div className="card-head"><h2>Identity and salary account</h2>
                {manage && <button type="button" className="btn" onClick={() => setReveal((r) => !r)}>{reveal ? 'Hide full numbers' : 'Show full numbers'}</button>}</div>
              <p className="muted">{reveal ? 'Showing full numbers. This view was recorded in the change history.' : 'Numbers are masked except for the last four characters.'}</p>
              <Facts items={[['PAN', reveal ? e.pan : e.pan_masked], ['Aadhaar', reveal ? e.aadhaar : e.aadhaar_masked], ['UAN', e.uan], ['ESI number', e.esi_number],
                ['Salary account', reveal ? e.bank_account_number : e.bank_account_masked], ['IFSC', e.bank_ifsc], ['Bank', e.bank_name], ['Account holder', e.bank_account_holder]]} />
            </section>
          )}
        </>
      )}

      {tab === 'job' && (
        <>
          <section className="card" aria-label="Job">
            <h2>Job</h2>
            <Facts items={[['Employment type', e.employment_type_label], ['Date of joining', fmtDate(e.date_of_joining)], ['Department', e.department?.name], ['Designation', e.designation?.name],
              ['Reporting manager', e.manager ? <Link to={`/hr/employees/${e.manager.id}`}>{e.manager.name}</Link> : ''], ['Work location', e.work_location],
              ['Sign-in user', e.has_login ? e.username : 'Not linked'], e.status === 'exited' && ['Last working day', fmtDate(e.exit_date)], e.status === 'exited' && ['Reason', e.exit_reason]]} />
          </section>
          {salaryVisible ? <SalaryTab e={e} canPayroll={can('hr.payroll')} refresh={state.reload} /> : <p className="muted">Salary is visible only to payroll and to the employee.</p>}
        </>
      )}
      {tab === 'documents' && <Attachments entityType="employee" entityId={e.id} canManage={manage} title="Documents" hint="Offer letter, contract, ID proofs and certificates. Give a document an expiry date to see when it lapses." />}
      {tab === 'leave' && <LeaveTab e={e} />}
      {tab === 'attendance' && <AttendanceTab e={e} />}
      {tab === 'performance' && <PerformanceTab e={e} />}
      {tab === 'payslips' && <PayslipsTab e={e} />}
      {tab === 'projects' && <ProjectsTab e={e} canManage={manage} />}

      {editing && <EmployeeDialog record={e} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); state.reload(); }} />}
      {dialog}
    </>
  );
}
