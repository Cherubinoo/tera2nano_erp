import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, downloadFile } from '../../api';
import { ColumnChart, HBars } from '../../components/charts';
import { compact, money, num, plain } from '../../format';
import { asList } from '../../hooks';
import { Badge, Field, FormErrors, Loading, Notice, PageHeader, Tabs, fmtDate, useToast } from '../../ui';
import { FormDialog, Gate, H, StatusBadge, Tile, monthLabel, shortMonth, thisMonth, useCan, useData } from './common';
import { PayslipView } from './PayrollRun';

function NewRun({ onClose }) {
  const navigate = useNavigate();
  const notify = useToast();
  return (
    <FormDialog title="New payroll run" wide={false} submitLabel="Generate payslips" defaults={{ month: thisMonth() }}
      intro={<p className="muted">Creates a draft payslip for everyone employed in the month, from their salary, attendance and leave. You can adjust each one before locking.</p>}
      fields={[{ name: 'month', label: 'Month', type: 'month', required: true, max: thisMonth() }, { name: 'notes', label: 'Notes', wide: true, maxLength: 300 }]}
      onSubmit={async (p) => { const run = await api.post(`${H}/payroll/runs/`, p); notify(`Payroll run ${run.number} created.`); onClose(); navigate(`/hr/payroll/runs/${run.id}`, { state: { skipped: run.skipped } }); }}
      onClose={onClose} />
  );
}

function Runs() {
  const [adding, setAdding] = useState(false);
  const state = useData(`${H}/payroll/runs/`);
  const trend = useData(`${H}/payroll/reports/monthly-cost/`);
  return (
    <>
      <section className="card" aria-label="Cost trend">
        <h2>Payroll cost</h2>
        <p className="muted">Approved, posted and paid runs this financial year.</p>
        {trend.loading ? <Loading /> : asList(trend.data?.rows).length === 0 ? <p className="empty">No finished payroll runs this year yet.</p> : (
          <ColumnChart title="Payroll cost by month" format={compact}
            series={[{ key: 'cost', label: 'Cost to company', colour: 'var(--chart-1)' }, { key: 'net', label: 'Net pay', colour: 'var(--chart-2)' }]}
            data={trend.data.rows.map((m) => ({ label: shortMonth(m.month), values: { cost: num(m.employer_cost), net: num(m.net) } }))} />
        )}
      </section>
      <section className="card">
        <div className="card-head"><h2>Payroll runs</h2><button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>New payroll run</button></div>
        <Gate state={state}>{(rows) => (rows.length === 0 ? <p className="empty">No payroll has been run yet. Set up salary structures for your employees, then start a run.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Month</th><th>Run</th><th className="num">Employees</th><th className="num">Gross</th><th className="num">Net pay</th><th className="num">Cost to company</th><th>Status</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id} className={r.status === 'void' ? 'row-inactive' : ''}>
                <td data-label="Month"><Link to={`/hr/payroll/runs/${r.id}`}><strong>{r.month_label}</strong></Link></td><td data-label="Run"><code>{r.number}</code></td>
                <td className="num" data-label="Employees">{r.employee_count}</td><td className="num" data-label="Gross">{plain(r.total_gross)}</td>
                <td className="num" data-label="Net pay">{plain(r.total_net)}</td><td className="num" data-label="Cost to company">{plain(r.total_employer_cost)}</td>
                <td data-label="Status"><StatusBadge status={r.status} label={r.status_label} /></td>
              </tr>
            ))}</tbody>
          </table></div>
        ))}</Gate>
      </section>
      {adding && <NewRun onClose={() => setAdding(false)} />}
    </>
  );
}

const fy = () => { const d = new Date(); return d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1; };

function Reports() {
  const notify = useToast();
  const [from, setFrom] = useState(`${fy()}-04`);
  const [to, setTo] = useState(thisMonth());
  const [month, setMonth] = useState(thisMonth());
  const [report, setReport] = useState('statutory');
  const range = { from, to };
  const stat = useData(`${H}/payroll/reports/statutory/`, range, report === 'statutory');
  const dept = useData(`${H}/payroll/reports/department-cost/`, range, report === 'department');
  const ytd = useData(`${H}/payroll/reports/ytd/`, { year: Number(from.slice(0, 4)) - (Number(from.slice(5)) < 4 ? 1 : 0) }, report === 'ytd');
  const reg = useData(`${H}/payroll/reports/register/`, { month }, report === 'register');
  const csv = (path, params, name) => downloadFile(`${H}/payroll/reports/${path}/?${new URLSearchParams({ ...params, csv: '1' })}`, name).catch((err) => notify(err.message, 'error'));
  const tabs = [['statutory', 'Statutory (PF, ESI, PT, TDS)'], ['department', 'Cost by department'], ['ytd', 'Year to date by employee'], ['register', 'Salary register']];
  return (
    <section className="card" aria-label="Payroll reports">
      <div className="chips" role="group" aria-label="Report">{tabs.map(([k, l]) => <button key={k} type="button" className="chip" aria-pressed={report === k} onClick={() => setReport(k)}>{l}</button>)}</div>
      <div className="toolbar">
        {report === 'register' ? (
          <Field id="rp-month" label="Month"><input id="rp-month" type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} /></Field>
        ) : (
          <>
            <Field id="rp-from" label="From"><input id="rp-from" type="month" value={from} onChange={(e) => e.target.value && setFrom(e.target.value)} /></Field>
            <Field id="rp-to" label="To"><input id="rp-to" type="month" value={to} onChange={(e) => e.target.value && setTo(e.target.value)} /></Field>
          </>
        )}
        <button type="button" className="btn" onClick={() => (report === 'register' ? csv('register', { month }, `salary-register-${month}.csv`) : report === 'ytd' ? csv('ytd', { year: Number(from.slice(0, 4)) }, 'payroll-ytd.csv') : report === 'statutory' ? csv('statutory', range, 'payroll-statutory.csv') : csv('department-cost', range, 'payroll-department-cost.csv'))}>Download CSV</button>
      </div>
      {report === 'statutory' && <Gate state={stat}>{(d) => (d.months.length === 0 ? <p className="empty">No finished payroll runs in this period.</p> : (
        <>
          <div className="tiles">
            <Tile label="Provident fund" value={money(num(d.total.pf_employee) + num(d.total.pf_employer))} hint={`Employee ${plain(d.total.pf_employee)}, employer ${plain(d.total.pf_employer)}`} />
            <Tile label="ESI" value={money(num(d.total.esi_employee) + num(d.total.esi_employer))} hint={`Employee ${plain(d.total.esi_employee)}, employer ${plain(d.total.esi_employer)}`} />
            <Tile label="Professional tax" value={money(d.total.pt)} /><Tile label="TDS on salary" value={money(d.total.tds)} />
          </div>
          <div className="table-wrap"><table>
            <thead><tr><th>Month</th><th className="num">PF employee</th><th className="num">PF employer</th><th className="num">ESI employee</th><th className="num">ESI employer</th><th className="num">Professional tax</th><th className="num">TDS</th></tr></thead>
            <tbody>{d.months.map((m) => <tr key={m.month}><td data-label="Month">{monthLabel(m.month)}</td><td className="num" data-label="PF employee">{plain(m.pf_employee)}</td><td className="num" data-label="PF employer">{plain(m.pf_employer)}</td><td className="num" data-label="ESI employee">{plain(m.esi_employee)}</td><td className="num" data-label="ESI employer">{plain(m.esi_employer)}</td><td className="num" data-label="Professional tax">{plain(m.pt)}</td><td className="num" data-label="TDS">{plain(m.tds)}</td></tr>)}</tbody>
          </table></div>
        </>
      ))}</Gate>}
      {report === 'department' && <Gate state={dept}>{(d) => (d.rows.length === 0 ? <p className="empty">No finished payroll runs in this period.</p> : (
        <>
          <HBars rows={d.rows.map((r) => ({ key: r.department, label: r.department, value: num(r.employer_cost) }))} format={compact} />
          <div className="table-wrap"><table>
            <thead><tr><th>Department</th><th className="num">Payslips</th><th className="num">Gross</th><th className="num">Net pay</th><th className="num">Cost to company</th></tr></thead>
            <tbody>{d.rows.map((r) => <tr key={r.department}><td data-label="Department">{r.department}</td><td className="num" data-label="Payslips">{r.employees}</td><td className="num" data-label="Gross">{plain(r.gross)}</td><td className="num" data-label="Net pay">{plain(r.net)}</td><td className="num" data-label="Cost to company"><strong>{plain(r.employer_cost)}</strong></td></tr>)}</tbody>
          </table></div>
        </>
      ))}</Gate>}
      {report === 'ytd' && <Gate state={ytd}>{(d) => (d.rows.length === 0 ? <p className="empty">No finished payroll in the financial year starting {fmtDate(d.start)}.</p> : (
        <div className="table-wrap"><table>
          <caption className="hint">Financial year {fmtDate(d.start)} to {fmtDate(d.end)}</caption>
          <thead><tr><th>Employee</th><th className="num">Months</th><th className="num">Gross</th><th className="num">PF</th><th className="num">ESI</th><th className="num">Prof. tax</th><th className="num">TDS</th><th className="num">Net pay</th></tr></thead>
          <tbody>{d.rows.map((r) => <tr key={r.employee_id}><td data-label="Employee"><Link to={`/hr/employees/${r.employee_id}`}>{r.name}</Link></td><td className="num" data-label="Months">{r.months}</td><td className="num" data-label="Gross">{plain(r.gross)}</td><td className="num" data-label="PF">{plain(r.pf_employee)}</td><td className="num" data-label="ESI">{plain(r.esi_employee)}</td><td className="num" data-label="Prof. tax">{plain(r.pt)}</td><td className="num" data-label="TDS">{plain(r.tds)}</td><td className="num" data-label="Net pay"><strong>{plain(r.net)}</strong></td></tr>)}</tbody>
        </table></div>
      ))}</Gate>}
      {report === 'register' && <Gate state={reg}>{(d) => (d.rows.length === 0 ? <p className="empty">No finished payroll run for {monthLabel(month)}.</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Employee</th><th className="num">Paid days</th><th className="num">Gross</th><th className="num">PF</th><th className="num">ESI</th><th className="num">Prof. tax</th><th className="num">TDS</th><th className="num">Other</th><th className="num">Net pay</th></tr></thead>
          <tbody>{d.rows.map((r) => <tr key={r.code}><td data-label="Employee">{r.employee}<div className="hint">{r.department}</div></td><td className="num" data-label="Paid days">{r.paid_days}</td><td className="num" data-label="Gross">{plain(r.gross)}</td><td className="num" data-label="PF">{plain(r.pf_employee)}</td><td className="num" data-label="ESI">{plain(r.esi_employee)}</td><td className="num" data-label="Prof. tax">{plain(r.professional_tax)}</td><td className="num" data-label="TDS">{plain(r.tds)}</td><td className="num" data-label="Other">{plain(num(r.loan_recovery) + num(r.other_deductions))}</td><td className="num" data-label="Net pay"><strong>{plain(r.net_pay)}</strong></td></tr>)}</tbody>
        </table></div>
      ))}</Gate>}
    </section>
  );
}

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const MONTHS = Array.from({ length: 12 }, (_, i) => new Date(2000, i, 1).toLocaleDateString('en-IN', { month: 'long' }));

function Settings() {
  const notify = useToast();
  const state = useData(`${H}/payroll/settings/`);
  const [v, setV] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  if (state.loading) return <Loading />;
  const cur = v || { ...state.data, pt_slabs: state.data.pt_slabs.map((s) => ({ ...s })) };
  const errs = error?.fieldErrors || {};
  const set = (k) => (e) => setV({ ...cur, [k]: e.target.value });
  const slab = (i, k) => (e) => setV({ ...cur, pt_slabs: cur.pt_slabs.map((s, j) => (j === i ? { ...s, [k]: e.target.value } : s)) });
  const save = async (e) => {
    e.preventDefault(); setBusy(true); setError(null);
    try {
      const body = { ...cur, pt_slabs: cur.pt_slabs.map((s, i) => ({ up_to: i === cur.pt_slabs.length - 1 ? null : s.up_to, amount: s.amount })), leave_year_start_month: Number(cur.leave_year_start_month) };
      await api.patch(`${H}/payroll/settings/`, body); notify('Payroll settings saved.'); setV(null); state.reload();
    } catch (err) { setError(err); }
    setBusy(false);
  };
  const flip = (d) => setV({ ...cur, weekly_off_days: cur.weekly_off_days.includes(d) ? cur.weekly_off_days.filter((x) => x !== d) : [...cur.weekly_off_days, d].sort() });
  return (
    <form className="card" onSubmit={save} noValidate aria-label="Payroll settings">
      <h2>Statutory rates and calendar</h2>
      <p className="muted">Changes apply to payslips made from now on; finished payslips keep their figures.</p>
      <FormErrors error={error} fields={[{ name: 'pf_rate' }, { name: 'pf_wage_ceiling' }, { name: 'esi_ceiling' }, { name: 'esi_employee_rate' }, { name: 'esi_employer_rate' }, { name: 'pt_state' }, { name: 'pt_slabs' }, { name: 'weekly_off_days' }, { name: 'lop_basis' }, { name: 'leave_year_start_month' }]} />
      <div className="form-grid">
        <Field id="st-pf" label="Provident fund rate (%)" error={errs.pf_rate} hint="Paid by the employee, and the same by the employer."><input id="st-pf" type="number" step="0.01" min="0" max="100" value={cur.pf_rate} onChange={set('pf_rate')} /></Field>
        <Field id="st-pfc" label="PF wage ceiling (per month)" error={errs.pf_wage_ceiling} hint="PF is charged on basic up to this amount."><input id="st-pfc" type="number" step="0.01" min="0" value={cur.pf_wage_ceiling} onChange={set('pf_wage_ceiling')} /></Field>
        <Field id="st-esic" label="ESI gross ceiling (per month)" error={errs.esi_ceiling} hint="ESI applies when the contract gross is within this."><input id="st-esic" type="number" step="0.01" min="0" value={cur.esi_ceiling} onChange={set('esi_ceiling')} /></Field>
        <Field id="st-esie" label="ESI, employee (%)" error={errs.esi_employee_rate}><input id="st-esie" type="number" step="0.01" min="0" max="100" value={cur.esi_employee_rate} onChange={set('esi_employee_rate')} /></Field>
        <Field id="st-esir" label="ESI, employer (%)" error={errs.esi_employer_rate}><input id="st-esir" type="number" step="0.01" min="0" max="100" value={cur.esi_employer_rate} onChange={set('esi_employer_rate')} /></Field>
        <Field id="st-lop" label="Loss of pay is worked out on" error={errs.lop_basis}>
          <select id="st-lop" value={cur.lop_basis} onChange={set('lop_basis')}><option value="calendar">Calendar days in the month</option><option value="working">Working days (without weekly offs and holidays)</option></select>
        </Field>
        <Field id="st-year" label="Leave year starts in" error={errs.leave_year_start_month}><select id="st-year" value={cur.leave_year_start_month} onChange={set('leave_year_start_month')}>{MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}</select></Field>
        <fieldset className="field field-wide checkgroup"><legend>Weekly off days</legend>
          <div className="checkgroup-items">{WEEKDAYS.map((d, i) => <label key={d} className="check"><input type="checkbox" checked={cur.weekly_off_days.includes(i)} onChange={() => flip(i)} /> {d}</label>)}</div>
          {errs.weekly_off_days && <div className="error-text" role="alert">{errs.weekly_off_days}</div>}
        </fieldset>
      </div>
      <h3>Professional tax</h3>
      <Field id="st-state" label="State" error={errs.pt_state} hint="The slabs below are for this state, as a monthly amount on the month's gross."><input id="st-state" value={cur.pt_state} onChange={set('pt_state')} /></Field>
      {errs.pt_slabs && <div className="error-text" role="alert">{errs.pt_slabs}</div>}
      <div className="table-wrap"><table className="hr-slabs">
        <thead><tr><th>Gross up to</th><th>Tax per month</th><th /></tr></thead>
        <tbody>{cur.pt_slabs.map((s, i) => {
          const last = i === cur.pt_slabs.length - 1;
          return (
            <tr key={i}>
              <td data-label="Gross up to">{last ? <span className="muted">Above the previous slab</span> : <input aria-label={`Slab ${i + 1} upper limit`} type="number" min="0" step="0.01" value={s.up_to ?? ''} onChange={slab(i, 'up_to')} />}</td>
              <td data-label="Tax per month"><input aria-label={`Slab ${i + 1} tax`} type="number" min="0" step="0.01" value={s.amount} onChange={slab(i, 'amount')} /></td>
              <td>{cur.pt_slabs.length > 2 && !last && <button type="button" className="btn btn-small btn-danger-outline" onClick={() => setV({ ...cur, pt_slabs: cur.pt_slabs.filter((_, j) => j !== i) })}>Remove</button>}</td>
            </tr>
          );
        })}</tbody>
      </table></div>
      <div className="dialog-foot-inline">
        <button type="button" className="btn" onClick={() => setV({ ...cur, pt_slabs: [...cur.pt_slabs.slice(0, -1), { up_to: '', amount: '0.00' }, cur.pt_slabs[cur.pt_slabs.length - 1]] })}>Add a slab</button>
        <button type="submit" className="btn btn-primary" disabled={busy || !v}>{busy ? 'Saving…' : 'Save settings'}</button>
      </div>
    </form>
  );
}

export default function Payroll() {
  const can = useCan();
  const [tab, setTab] = useState('runs');
  if (!can('hr.payroll')) return <><PageHeader title="Payroll" /><Notice type="warn">Your role does not allow you to see payroll.</Notice></>;
  return (
    <>
      <PageHeader title="Payroll" description="Monthly salary runs, statutory reports and settings." />
      <Tabs tabs={[{ key: 'runs', label: 'Runs' }, { key: 'reports', label: 'Reports' }, { key: 'settings', label: 'Settings' }]} value={tab} onChange={setTab} />
      {tab === 'runs' && <Runs />}
      {tab === 'reports' && <Reports />}
      {tab === 'settings' && <Settings />}
    </>
  );
}

export function MyPayslips() {
  const notify = useToast();
  const [open, setOpen] = useState(null);
  const state = useData(`${H}/me/payslips/`);
  return (
    <>
      <PageHeader title="My payslips" description="Your payslips, once each month's payroll has been posted." />
      <Gate state={state}>{(d) => {
        if (!d.linked) return <Notice type="info">Your sign-in is not linked to an employee record yet. Ask HR to link it.</Notice>;
        return (
          <section className="card" aria-label="Payslips">
            {d.rows.length === 0 ? <p className="empty">No payslips yet.</p> : (
              <div className="table-wrap"><table>
                <thead><tr><th>Month</th><th className="num">Gross</th><th className="num">Deductions</th><th className="num">Net pay</th><th /></tr></thead>
                <tbody>{d.rows.map((s) => (
                  <tr key={s.id}>
                    <td data-label="Month"><strong>{s.month_label}</strong></td><td className="num" data-label="Gross">{plain(s.gross)}</td>
                    <td className="num" data-label="Deductions">{plain(s.total_deductions)}</td><td className="num" data-label="Net pay"><strong>{plain(s.net_pay)}</strong></td>
                    <td className="hr-row-actions"><button type="button" className="btn btn-small" onClick={() => setOpen(s.id)}>View</button>
                      <button type="button" className="btn btn-small btn-primary" onClick={() => downloadFile(`${H}/payroll/payslips/${s.id}/pdf/`, 'payslip.pdf').catch((err) => notify(err.message, 'error'))}>Download PDF</button></td>
                  </tr>
                ))}</tbody>
              </table></div>
            )}
          </section>
        );
      }}</Gate>
      {open && <PayslipView id={open} onClose={() => setOpen(null)} />}
    </>
  );
}
