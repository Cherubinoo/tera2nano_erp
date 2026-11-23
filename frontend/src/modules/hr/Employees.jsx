import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { PATTERNS, dateOrder } from '../../forms';
import { asList } from '../../hooks';
import { ErrorBox, Field, Loading, PageHeader, fmtDate, useToast } from '../../ui';
import { EMPLOYMENT_TYPES, EMP_STATUSES, FormDialog, GENDERS, H, StatusBadge, options, useCan, useData } from './common';

const opt = (key) => (ctx) => ctx[key] || [];

// The employee form. Identifier fields are only sent when typed, so an existing PAN or account number is kept unless replaced.
export const employeeFields = (creating) => [
  { name: 'first_name', label: 'First name', required: true, maxLength: 80 },
  { name: 'last_name', label: 'Last name', maxLength: 80 },
  { name: 'date_of_birth', label: 'Date of birth', type: 'date', max: new Date().toISOString().slice(0, 10) },
  { name: 'gender', label: 'Gender', type: 'select', options: GENDERS, blankLabel: '— not stated —' },
  { name: 'work_email', label: 'Work email', type: 'email' },
  { name: 'personal_email', label: 'Personal email', type: 'email' },
  { name: 'phone', label: 'Phone', pattern: PATTERNS.phone, patternMessage: 'Enter a phone number with digits, spaces and + ( ) - only.' },
  { name: 'address', label: 'Address', type: 'textarea', wide: true, rows: 2 },
  { name: 'emergency_name', label: 'Emergency contact name' },
  { name: 'emergency_relation', label: 'Relationship' },
  { name: 'emergency_phone', label: 'Emergency contact phone', pattern: PATTERNS.phone, patternMessage: 'Enter a phone number with digits, spaces and + ( ) - only.' },
  { name: 'date_of_joining', label: 'Date of joining', type: 'date', required: true },
  { name: 'employment_type', label: 'Employment type', type: 'select', required: true, options: EMPLOYMENT_TYPES },
  { name: 'department', label: 'Department', type: 'select', options: opt('departments'), blankLabel: '— none —' },
  { name: 'designation', label: 'Designation', type: 'select', options: opt('designations'), blankLabel: '— none —' },
  { name: 'manager', label: 'Reporting manager', type: 'select', options: (ctx) => (ctx.people || []).filter((p) => p.value !== ctx.selfId), blankLabel: '— none —' },
  { name: 'work_location', label: 'Work location' },
  { name: 'status', label: 'Status', type: 'select', required: true, options: EMP_STATUSES, hidden: () => creating },
  { name: 'exit_date', label: 'Last working day', type: 'date', hidden: (v) => v.status !== 'exited', required: false, validate: (v, all) => (all.status === 'exited' && !v ? 'Enter the last working day.' : dateOrder('date_of_joining', 'exit_date')(v, all)) },
  { name: 'exit_reason', label: 'Reason for leaving', wide: true, hidden: (v) => v.status !== 'exited' },
  { name: 'user_id', label: 'Sign-in user', type: 'select', options: opt('users'), blankLabel: '— not linked —', hint: 'Links this person to a login so they can see their own profile, leave and payslips.', wide: true },
  { name: 'pan', label: 'PAN', upper: true, maxLength: 10, pattern: PATTERNS.pan, patternMessage: 'PAN looks like ABCDE1234F.', omitIfEmpty: true,
    hint: creating ? undefined : 'Leave blank to keep the PAN on file.' },
  { name: 'aadhaar', label: 'Aadhaar number', maxLength: 14, pattern: /^[2-9][0-9]{3}\s?[0-9]{4}\s?[0-9]{4}$/, patternMessage: 'Aadhaar is 12 digits.', omitIfEmpty: true,
    hint: creating ? undefined : 'Leave blank to keep the number on file.' },
  { name: 'uan', label: 'UAN (provident fund)', maxLength: 12, pattern: /^[0-9]{12}$/, patternMessage: 'The UAN is 12 digits.' },
  { name: 'esi_number', label: 'ESI number', maxLength: 17, pattern: /^[0-9]{10,17}$/, patternMessage: 'The ESI number is 10 to 17 digits.' },
  { name: 'bank_account_holder', label: 'Salary account holder' },
  { name: 'bank_name', label: 'Bank' },
  { name: 'bank_ifsc', label: 'IFSC', upper: true, maxLength: 11, pattern: PATTERNS.ifsc, patternMessage: 'IFSC looks like HDFC0001234.' },
  { name: 'bank_account_number', label: 'Salary account number', maxLength: 20, pattern: PATTERNS.account, patternMessage: 'The account number is 6 to 20 digits.', omitIfEmpty: true,
    hint: creating ? undefined : 'Leave blank to keep the account on file.' },
];

export function useEmployeeCtx(selfId) {
  const deps = useData(`${H}/departments/`);
  const desigs = useData(`${H}/designations/`);
  const people = useData(`${H}/lookup/`);
  const users = useData(`${H}/employees/available-users/`);
  return useMemo(() => ({
    selfId,
    departments: options(asList(deps.data).filter((d) => d.is_active)),
    designations: options(asList(desigs.data).filter((d) => d.is_active)),
    people: options(asList(people.data), (p) => `${p.name} (${p.code.slice(-4)})`),
    users: options(asList(users.data), (u) => `${u.full_name} (${u.username})`),
    ready: !(deps.loading || desigs.loading || people.loading || users.loading),
  }), [deps.data, desigs.data, people.data, users.data, deps.loading, desigs.loading, people.loading, users.loading, selfId]);
}

export function EmployeeDialog({ record, onClose, onSaved }) {
  const notify = useToast();
  const ctx = useEmployeeCtx(record?.id);
  const fields = useMemo(() => employeeFields(!record), [record]);
  const base = useMemo(() => (record ? {
    ...record, department: record.department?.id || '', designation: record.designation?.id || '', manager: record.manager?.id || '',
    user_id: record.user_id || '', pan: '', aadhaar: '', bank_account_number: '',
  } : { employment_type: 'full_time', status: 'active' }), [record]);
  if (!ctx.ready) return null;
  return (
    <FormDialog title={record ? `Edit ${record.name}` : 'New employee'} fields={fields} record={base} ctx={ctx} onlyChanged={!!record}
      submitLabel={record ? 'Save changes' : 'Add employee'}
      onSubmit={async (payload) => {
        const body = { ...payload };
        ['department', 'designation', 'manager', 'user_id'].forEach((k) => { if (k in body && body[k] === '') body[k] = null; });
        const saved = record ? await api.patch(`${H}/employees/${record.id}/`, body) : await api.post(`${H}/employees/`, body);
        notify(record ? 'Employee updated.' : `Employee added: ${saved.code}`);
        onSaved(saved);
      }} onClose={onClose} />
  );
}

const STATUS_TABS = [['current', 'Current'], ['active', 'Active'], ['on_notice', 'On notice'], ['exited', 'Exited'], ['all', 'All']];

export default function Employees() {
  const can = useCan();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState('current');
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const [department, setDepartment] = useState('');
  const [type, setType] = useState('');
  const [dialog, setDialog] = useState(params.get('new') === '1');
  useEffect(() => { if (params.get('new')) setParams({}, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const { data, error, loading, reload } = useData(`${H}/employees/`, { status, q: term || undefined, department: department || undefined, type: type || undefined });
  const depts = useData(`${H}/departments/`);
  const rows = asList(data);
  const showMasked = rows.some((r) => r.pan_masked !== undefined);

  return (
    <>
      <PageHeader title="Employees" description="Everyone who works here, with their job, contact details and status."
        actions={can('hr.manage') && <button type="button" className="btn btn-primary" onClick={() => setDialog(true)}>New employee</button>} />
      <section className="card">
        <div className="chips" role="group" aria-label="Status">
          {STATUS_TABS.map(([k, label]) => <button key={k} type="button" className="chip" aria-pressed={status === k} onClick={() => setStatus(k)}>{label}</button>)}
        </div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
          <Field id="emp-q" label="Search" className="grow"><input id="emp-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, code, email or phone" /></Field>
          <Field id="emp-dept" label="Department">
            <select id="emp-dept" value={department} onChange={(e) => setDepartment(e.target.value)}>
              <option value="">All departments</option>
              {asList(depts.data).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </Field>
          <Field id="emp-type" label="Type">
            <select id="emp-type" value={type} onChange={(e) => setType(e.target.value)}>
              <option value="">All types</option>
              {EMPLOYMENT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </Field>
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && rows.length === 0 && <p className="empty">{term || department || type ? 'No employee matches.' : 'No employees here yet.'}</p>}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Employee</th><th>Code</th><th>Department</th><th>Designation</th><th>Manager</th><th>Type</th><th>Joined</th>{showMasked && <th>PAN</th>}<th>Status</th></tr>
              </thead>
              <tbody>
                {rows.map((e) => (
                  <tr key={e.id} className={`clickable${e.status === 'exited' ? ' row-inactive' : ''}`} onClick={() => navigate(`/hr/employees/${e.id}`)}>
                    <td data-label="Employee"><Link to={`/hr/employees/${e.id}`} onClick={(ev) => ev.stopPropagation()}><strong>{e.name}</strong></Link>
                      {e.work_email && <div className="hint">{e.work_email}</div>}</td>
                    <td data-label="Code"><code>{e.code}</code></td>
                    <td data-label="Department">{e.department?.name || '—'}</td>
                    <td data-label="Designation">{e.designation?.name || '—'}</td>
                    <td data-label="Manager">{e.manager?.name || '—'}</td>
                    <td data-label="Type">{e.employment_type_label}</td>
                    <td data-label="Joined">{fmtDate(e.date_of_joining)}</td>
                    {showMasked && <td data-label="PAN"><code>{e.pan_masked || '—'}</code></td>}
                    <td data-label="Status"><StatusBadge status={e.status} label={e.status_label} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rows.length > 0 && <div className="list-foot"><span>{rows.length} employee{rows.length === 1 ? '' : 's'}</span></div>}
      </section>
      {dialog && <EmployeeDialog onClose={() => setDialog(false)} onSaved={(e) => { setDialog(false); navigate(`/hr/employees/${e.id}`); }} />}
    </>
  );
}
