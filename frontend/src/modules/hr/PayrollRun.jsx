import { useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { api, downloadFile } from '../../api';
import { StackedBar } from '../../components/charts';
import { money, num, plain, today } from '../../format';
import { asList, useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, fmtDate, useConfirm, useReasonDialog, useToast } from '../../ui';
import { ApprovalStatus } from '../approvals/ApprovalBadge';
import { EmailButton } from '../../components/Email';
import { FormDialog, H, StatusBadge, Tile, days, options, useData } from './common';

// One payslip: the earnings and deductions table, and (while the run is a draft) the inputs that can be adjusted.
export function PayslipView({ id, onClose, onChanged, canEdit }) {
  const notify = useToast();
  const state = useData(`${H}/payroll/payslips/${id}/`);
  const [form, setForm] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const s = state.data;
  const edit = form || (s && { lop_days: s.lop_days, bonus: s.bonus, tds: s.tds, loan_recovery: s.loan_recovery, other_deductions: s.other_deductions, remarks: s.remarks });
  const set = (k) => (e) => setForm({ ...edit, [k]: e.target.value });
  const save = async (e) => {
    e.preventDefault(); e.stopPropagation(); setBusy(true); setError(null);
    try { await api.patch(`${H}/payroll/payslips/${id}/`, edit); notify('Payslip updated.'); setForm(null); state.reload(); onChanged && onChanged(); } catch (err) { setError(err); }
    setBusy(false);
  };
  const row = (label, v, bold) => (Number(v) !== 0 || bold) && <tr key={label} className={bold ? 'hr-total' : ''}><th scope="row">{label}</th><td className="num">{plain(v)}</td></tr>;
  const errs = error?.fieldErrors || {};
  return (
    <Dialog title={s ? `Payslip: ${s.who.name}, ${s.month_label}` : 'Payslip'} onClose={onClose} wide>
      {state.loading ? <Loading /> : state.error ? <ErrorBox error={state.error} onRetry={state.reload} /> : (
        <>
          <p className="muted"><code>{s.who.code}</code> · {s.who.designation || 'No designation'}{s.who.department ? `, ${s.who.department}` : ''} · paid {days(s.paid_days)} of {s.basis_days} days{Number(s.lop_days) ? ` (${days(s.lop_days)} days loss of pay)` : ''}</p>
          <div className="grid-2">
            <table className="hr-slip" aria-label="Earnings"><caption className="sr-only">Earnings</caption>
              <thead><tr><th scope="col">Earnings</th><th scope="col" className="num">Amount</th></tr></thead>
              <tbody>{row('Basic', s.basic)}{row('House rent allowance', s.hra)}{row('Special allowance', s.special_allowance)}{row('Other allowances', s.other_allowance)}{row('Bonus', s.bonus)}{row('Gross earnings', s.gross, true)}</tbody>
            </table>
            <table className="hr-slip" aria-label="Deductions"><caption className="sr-only">Deductions</caption>
              <thead><tr><th scope="col">Deductions</th><th scope="col" className="num">Amount</th></tr></thead>
              <tbody>{row('Provident fund', s.pf_employee)}{row('ESI', s.esi_employee)}{row('Professional tax', s.professional_tax)}{row('Income tax (TDS)', s.tds)}{row('Advance recovery', s.loan_recovery)}{row('Other deductions', s.other_deductions)}{row('Total deductions', s.total_deductions, true)}</tbody>
            </table>
          </div>
          <p><strong>Net pay {money(s.net_pay)}</strong>. Employer also pays PF {money(s.pf_employer)} and ESI {money(s.esi_employer)}; cost to the company {money(s.employer_cost)}.</p>
          {s.remarks && !canEdit && <p className="muted">Remarks: {s.remarks}</p>}
          {canEdit && s.editable && (
            <form onSubmit={save} noValidate>
              <h3>Adjust this payslip</h3>
              <FormErrors error={error} fields={[{ name: 'lop_days' }, { name: 'bonus' }, { name: 'tds' }, { name: 'loan_recovery' }, { name: 'other_deductions' }, { name: 'remarks' }]} />
              <div className="form-grid">
                <Field id="ps-lop" label="Loss of pay days" error={errs.lop_days} hint={`Worked out as ${days(s.lop_days_auto)} from attendance and leave.`}><input id="ps-lop" type="number" step="0.5" min="0" value={edit.lop_days} onChange={set('lop_days')} /></Field>
                <Field id="ps-bonus" label="Bonus" error={errs.bonus}><input id="ps-bonus" type="number" step="0.01" min="0" value={edit.bonus} onChange={set('bonus')} /></Field>
                <Field id="ps-tds" label="Income tax (TDS)" error={errs.tds}><input id="ps-tds" type="number" step="0.01" min="0" value={edit.tds} onChange={set('tds')} /></Field>
                <Field id="ps-loan" label="Advance recovery" error={errs.loan_recovery}><input id="ps-loan" type="number" step="0.01" min="0" value={edit.loan_recovery} onChange={set('loan_recovery')} /></Field>
                <Field id="ps-other" label="Other deductions" error={errs.other_deductions}><input id="ps-other" type="number" step="0.01" min="0" value={edit.other_deductions} onChange={set('other_deductions')} /></Field>
                <Field id="ps-remarks" label="Remarks" error={errs.remarks} wide><input id="ps-remarks" value={edit.remarks} maxLength={300} onChange={set('remarks')} /></Field>
              </div>
              <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Close</button><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Recalculate and save'}</button></div>
            </form>
          )}
          {!(canEdit && s.editable) && <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Close</button></div>}
        </>
      )}
    </Dialog>
  );
}

// Whether the approvals module is installed (it decides payroll runs when it is).
export function useApprovalsInstalled() {
  const { data } = useLoad(() => api.get('/approvals/kinds/').then(() => true).catch(() => false), []);
  return data;
}

function PayDialog({ run, onClose, onDone }) {
  const notify = useToast();
  const banks = useData('/company/bank-accounts/');
  if (banks.loading) return null;
  const list = asList(banks.data).filter((b) => b.is_active !== false);
  return (
    <FormDialog title="Mark salaries as paid" wide={false} submitLabel="Record payment" defaults={{ paid_date: today(), reference: '' }}
      intro={<p className="muted">Records the transfer of {money(run.total_net)} and clears the salaries payable.</p>}
      fields={[{ name: 'paid_date', label: 'Payment date', type: 'date', required: true, max: today(), min: run.month },
        { name: 'bank_account', label: 'Paid from', type: 'select', options: options(list, (b) => b.display_name || b.bank_name), blankLabel: 'Default bank account', omitIfEmpty: true },
        { name: 'reference', label: 'Reference (UTR or batch number)', maxLength: 100, wide: true }]}
      onSubmit={async (p) => { await api.post(`${H}/payroll/runs/${run.id}/pay/`, p); notify('Salaries marked as paid.'); onDone(); }} onClose={onClose} />
  );
}

export default function PayrollRun() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const { ask, dialog: askDialog } = useReasonDialog();
  const [slip, setSlip] = useState(null);
  const [paying, setPaying] = useState(false);
  const [busy, setBusy] = useState(false);
  const state = useData(`${H}/payroll/runs/${id}/`);
  const approvals = useApprovalsInstalled();
  if (state.loading && !state.data) return <Loading />;
  if (state.error) return <ErrorBox error={state.error} onRetry={state.reload} />;
  const r = state.data;
  const a = r.actions;
  const skipped = location.state?.skipped || [];

  const act = async (action, body, done) => {
    setBusy(true);
    try { await api.post(`${H}/payroll/runs/${id}/${action}/`, body || {}); notify(done); state.reload(); } catch (err) { notify(err.message, 'error'); }
    setBusy(false);
  };
  const withConfirm = (action, title, text, done, confirmLabel) => async () => {
    if (await confirm({ title, body: <p>{text}</p>, confirmLabel })) act(action, {}, done);
  };
  const reject = async () => { const reason = await ask({ title: 'Send this run back to draft?', label: 'Reason', confirmLabel: 'Send back' }); if (reason !== null) act('reject', { reason }, 'Run sent back to draft.'); };
  const voidRun = async () => {
    const reason = await ask({ title: `Void ${r.number}?`, message: r.status === 'posted' || r.status === 'paid' ? 'The journal entries made for this run are reversed. The month can then be run again.' : 'The month can then be run again.', label: 'Reason', confirmLabel: 'Void run' });
    if (reason !== null) act('void', { reason }, 'Run voided.');
  };
  const remove = async () => {
    if (!(await confirm({ title: 'Delete this draft run?', body: <p>The payslips in it are deleted.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`${H}/payroll/runs/${id}/`); notify('Draft deleted.'); navigate('/hr/payroll'); } catch (err) { notify(err.message, 'error'); }
  };
  const bankFile = () => downloadFile(`${H}/payroll/runs/${id}/bank-file/`, 'salary-transfer.csv').then(() => notify('Bank file downloaded. This download is recorded in the change history.')).catch((err) => notify(err.message, 'error'));
  const t = r.totals;
  const employerExtra = num(t.pf_employer) + num(t.esi_employer);

  return (
    <>
      <Link to="/hr/payroll" className="back-link">← Payroll</Link>
      <header className="doc-head">
        <div>
          <h1>Payroll for {r.month_label} <StatusBadge status={r.status} label={r.status_label} /> <ApprovalStatus kind="payroll_run" sourceId={r.id} refreshKey={r.status} /></h1>
          <p className="muted"><code>{r.number}</code> · {r.employee_count} employees{r.paid_date ? ` · paid ${fmtDate(r.paid_date)}${r.paid_reference ? ` (${r.paid_reference})` : ''}` : ''}{r.approved_by ? ` · approved by ${r.approved_by}` : ''}</p>
        </div>
        <div className="page-actions">
          {a.lock && <button type="button" className="btn btn-primary" disabled={busy} onClick={() => act('lock', {}, 'Run locked. Payslips can no longer be edited.')}>Lock run</button>}
          {a.submit && <button type="button" className="btn btn-primary" disabled={busy} onClick={() => act('submit', {}, approvals ? 'Sent for approval.' : 'Waiting for approval.')}>Send for approval</button>}
          {a.approve && approvals === false && <button type="button" className="btn btn-primary" disabled={busy} onClick={() => act('approve', {}, 'Run approved.')}>Approve</button>}
          {a.reject && approvals === false && <button type="button" className="btn" disabled={busy} onClick={reject}>Send back</button>}
          {a.post && <button type="button" className="btn btn-primary" disabled={busy} onClick={withConfirm('post', 'Post to the books?', `Records ${money(r.total_employer_cost)} of salary cost and the amounts payable to employees, PF, ESI, professional tax and tax authorities.`, 'Posted to the books.', 'Post')}>Post to books</button>}
          {a.pay && <button type="button" className="btn btn-primary" disabled={busy} onClick={() => setPaying(true)}>Mark salaries paid</button>}
          {a.bank_file && <button type="button" className="btn" onClick={bankFile}>Bank transfer file</button>}
          {a.unlock && <button type="button" className="btn" disabled={busy} onClick={() => act('unlock', {}, 'Run reopened as a draft.')}>Reopen as draft</button>}
          {a.edit && <button type="button" className="btn" disabled={busy} onClick={withConfirm('regenerate', 'Regenerate all payslips?', 'Every payslip is worked out again from attendance, leave and salary. Changes you made to individual payslips are lost.', 'Payslips regenerated.', 'Regenerate')}>Regenerate</button>}
          {a.delete && <button type="button" className="btn btn-danger-outline" onClick={remove}>Delete</button>}
          {a.void && <button type="button" className="btn btn-danger-outline" disabled={busy} onClick={voidRun}>Void</button>}
        </div>
      </header>
      {r.status === 'void' && <Notice type="warn">This run was voided: {r.void_reason}</Notice>}
      {skipped.length > 0 && <Notice type="warn">Left out because they have no salary structure for this month: {skipped.map((s) => s.employee).join(', ')}.</Notice>}
      {r.missing_bank.length > 0 && a.bank_file && <Notice type="warn">No complete bank details for {r.missing_bank.join(', ')}. They are left out of the bank file.</Notice>}
      {r.status === 'draft' && <Notice type="info">This run is a draft. Open a payslip to adjust loss of pay, bonus or tax, then lock the run.</Notice>}

      <div className="tiles">
        <Tile label="Gross pay" value={money(t.gross)} />
        <Tile label="Deductions" value={money(t.total_deductions)} hint="From employees" />
        <Tile label="Net pay" value={money(t.net_pay)} hint="Paid to employees" tone="good" />
        <Tile label="Cost to company" value={money(t.employer_cost)} hint={`Includes ${money(employerExtra)} employer PF and ESI`} />
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Where the money goes">
          <h2>Where the money goes</h2>
          <StackedBar format={(v) => money(v)} parts={[
            { key: 'net', label: 'Net pay to employees', value: num(t.net_pay), colour: 'var(--chart-2)' },
            { key: 'pf', label: 'PF (employee and employer)', value: num(t.pf_employee) + num(t.pf_employer), colour: 'var(--chart-1)' },
            { key: 'esi', label: 'ESI (employee and employer)', value: num(t.esi_employee) + num(t.esi_employer), colour: 'var(--chart-5)' },
            { key: 'pt', label: 'Professional tax', value: num(t.professional_tax), colour: 'var(--chart-3)' },
            { key: 'tds', label: 'Income tax (TDS)', value: num(t.tds), colour: 'var(--chart-4)' },
            { key: 'other', label: 'Advance recovery and other', value: num(t.loan_recovery) + num(t.other_deductions), colour: 'var(--chart-grey)' }]} />
        </section>
        <section className="card" aria-label="Books">
          <h2>In the books</h2>
          {r.journal.length === 0 ? <p className="muted">Nothing is posted yet. Posting happens after approval.</p> : (
            <ul className="hr-list">{r.journal.map((j) => (
              <li key={j.id}><span><Link to={`/accounting/journal/${j.id}`}>{j.kind === 'payment' ? 'Salary payment' : 'Payroll posting'}</Link><div className="hint">{fmtDate(j.date)} · <code>{j.reference}</code></div></span>{j.status === 'reversed' ? <Badge>Reversed</Badge> : <Badge tone="good">Posted</Badge>}</li>
            ))}</ul>
          )}
        </section>
      </div>

      <section className="card" aria-label="Payslips">
        <h2>Payslips</h2>
        <div className="table-wrap"><table>
          <thead><tr><th>Employee</th><th>Department</th><th className="num">Paid days</th><th className="num">Gross</th><th className="num">Deductions</th><th className="num">Net pay</th><th /></tr></thead>
          <tbody>{r.payslips.map((s) => (
            <tr key={s.id} className="clickable" onClick={() => setSlip(s.id)}>
              <td data-label="Employee"><Link to={`/hr/employees/${s.employee.id}`} onClick={(e) => e.stopPropagation()}>{s.employee.name}</Link>{s.edited && <> <Badge tone="info">Adjusted</Badge></>}</td>
              <td data-label="Department">{s.department || '—'}</td>
              <td className="num" data-label="Paid days">{days(s.paid_days)}{Number(s.lop_days) ? <span className="hint"> ({days(s.lop_days)} LOP)</span> : ''}</td>
              <td className="num" data-label="Gross">{plain(s.gross)}</td><td className="num" data-label="Deductions">{plain(s.total_deductions)}</td>
              <td className="num" data-label="Net pay"><strong>{plain(s.net_pay)}</strong></td>
              <td className="hr-row-actions"><button type="button" className="btn btn-small" onClick={(e) => { e.stopPropagation(); setSlip(s.id); }}>{a.edit ? 'Open and adjust' : 'Open'}</button>
                {a.bank_file && <button type="button" className="btn btn-small" onClick={(e) => { e.stopPropagation(); downloadFile(`${H}/payroll/payslips/${s.id}/pdf/`, 'payslip.pdf').catch((err) => notify(err.message, 'error')); }}>PDF</button>}
                {a.bank_file && <span onClick={(e) => e.stopPropagation()} role="presentation"><EmailButton kind="payslip" id={s.id} perm="hr.payroll" small label="Email" title={`Email payslip to ${s.employee.name}`} /></span>}</td>
            </tr>
          ))}</tbody>
        </table></div>
        <div className="list-foot"><span>{r.payslips.length} payslips</span><span>Net pay <strong>{money(t.net_pay)}</strong></span></div>
      </section>

      {slip && <PayslipView id={slip} canEdit onClose={() => setSlip(null)} onChanged={state.reload} />}
      {paying && <PayDialog run={r} onClose={() => setPaying(false)} onDone={() => { setPaying(false); state.reload(); }} />}
      {dialog}{askDialog}
    </>
  );
}
