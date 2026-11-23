import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { PATTERNS } from '../../forms';
import { PageHeader, fmtDate, useToast } from '../../ui';
import { FormDialog, Gate, H, NotLinked, StatusBadge, days, useData } from './common';
import { BalanceTiles } from './Leave';

const Facts = ({ items }) => <dl className="hr-facts">{items.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v || '—'}</dd></div>)}</dl>;

export default function MyProfile() {
  const notify = useToast();
  const [editing, setEditing] = useState(false);
  const state = useData(`${H}/me/`);
  const bal = useData(`${H}/leave/balances/`);
  return (
    <Gate state={state}>{(e) => {
      if (!e.linked) return <><PageHeader title="My profile" /><NotLinked /></>;
      return (
        <>
          <PageHeader title="My profile" description="Your details on record. Contact HR to change anything except your phone, address and emergency contact."
            actions={<button type="button" className="btn btn-primary" onClick={() => setEditing(true)}>Edit my contact details</button>} />
          <section className="card" aria-label="Job">
            <h2>{e.name} <StatusBadge status={e.status} label={e.status_label} /></h2>
            <Facts items={[['Employee ID', e.code], ['Designation', e.designation?.name], ['Department', e.department?.name], ['Reporting manager', e.manager?.name], ['Employment type', e.employment_type_label],
              ['Work location', e.work_location], ['Date of joining', fmtDate(e.date_of_joining)], ['Work email', e.work_email]]} />
          </section>
          <section className="card" aria-label="Contact">
            <h2>Contact</h2>
            <Facts items={[['Phone', e.phone], ['Personal email', e.personal_email], ['Date of birth', e.date_of_birth ? fmtDate(e.date_of_birth) : ''], ['Address', e.address],
              ['Emergency contact', [e.emergency_name, e.emergency_relation && `(${e.emergency_relation})`, e.emergency_phone].filter(Boolean).join(' ')]]} />
          </section>
          <section className="card" aria-label="Identity and salary account">
            <h2>Identity and salary account</h2>
            <p className="muted">Shown with only the last four characters. HR holds the full numbers.</p>
            <Facts items={[['PAN', e.pan_masked], ['Aadhaar', e.aadhaar_masked], ['UAN', e.uan], ['Salary account', e.bank_account_masked], ['Bank', e.bank_name], ['IFSC', e.bank_ifsc]]} />
          </section>
          {bal.data?.rows?.length > 0 && (
            <section className="card" aria-label="Leave balances">
              <div className="card-head"><h2>Leave balances</h2><Link to="/hr/my-leave">My leave</Link></div>
              <BalanceTiles rows={bal.data.rows} />
              <p className="hint">{days(bal.data.rows.reduce((a, b) => a + Number(b.available), 0))} days left across all types.</p>
            </section>
          )}
          {editing && (
            <FormDialog title="Edit my contact details" wide={false} record={e}
              fields={[{ name: 'phone', label: 'Phone', pattern: PATTERNS.phone, patternMessage: 'Enter a phone number with digits, spaces and + ( ) - only.' }, { name: 'address', label: 'Address', type: 'textarea', rows: 2, wide: true },
                { name: 'emergency_name', label: 'Emergency contact name' }, { name: 'emergency_relation', label: 'Relationship' },
                { name: 'emergency_phone', label: 'Emergency contact phone', pattern: PATTERNS.phone, patternMessage: 'Enter a phone number with digits, spaces and + ( ) - only.' }]}
              onSubmit={async (p) => { await api.patch(`${H}/me/`, p); notify('Your details were saved.'); setEditing(false); state.reload(); }} onClose={() => setEditing(false)} />
          )}
        </>
      );
    }}</Gate>
  );
}
