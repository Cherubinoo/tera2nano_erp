import { Link } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../context';
import { useLoad } from '../hooks';
import { Badge, ErrorBox, Loading, Notice, fmtDate } from '../ui';

export default function Overview() {
  const { user } = useAuth();
  const { data, error, loading, reload } = useLoad(() => api.get('/company/overview/'), []);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const { profile, counts, checklist, attention, current_financial_year: fy, active_policy_version: policyVersion } = data;
  const todo = checklist.filter((c) => !c.done);
  return (
    <>
      {profile.setup_status !== 'completed' && user.is_company_admin && (
        <Notice type="warn">Company setup is not finished. <Link to="/setup">Resume setup</Link></Notice>
      )}
      <section className="card">
        <div className="card-head">
          <div>
            <h2>{profile.display_name}</h2>
            <p className="muted">{profile.legal_name}{profile.trade_name ? ` · trading as ${profile.trade_name}` : ''}</p>
          </div>
          <Badge tone={profile.status === 'active' ? 'good' : 'warn'}>{profile.status}</Badge>
        </div>
        <dl className="facts">
          <div><dt>PAN</dt><dd>{profile.pan || '—'}</dd></div>
          <div><dt>LLPIN</dt><dd>{profile.llpin || '—'}</dd></div>
          <div><dt>GSTIN</dt><dd>{profile.gstin || (profile.gst_registration_status === 'not_declared' ? 'Not declared' : profile.gst_registration_status.replace('_', ' '))}</dd></div>
          <div><dt>Incorporated</dt><dd>{fmtDate(profile.date_of_incorporation)}</dd></div>
          <div><dt>Registered office</dt><dd>{profile.registered_office ? `${profile.registered_office.city}, ${profile.registered_office.state}` : '—'}</dd></div>
          <div><dt>Current financial year</dt><dd>{fy ? <>{fy.label}{fy.is_locked && ' (locked)'}</> : '—'}</dd></div>
          <div><dt>Active terms</dt><dd>{policyVersion ? `Version ${policyVersion}` : '—'}</dd></div>
          <div><dt>Company record version</dt><dd>{profile.version}</dd></div>
        </dl>
      </section>

      <div className="grid-2">
        <section className="card">
          <h2>Setup checklist</h2>
          <ul className="checklist">
            {checklist.map((c) => (
              <li key={c.label} className={c.done ? 'done' : ''}>
                <span aria-hidden="true">{c.done ? '✓' : '○'}</span> {c.label}
                <span className="sr-only">{c.done ? ' (done)' : ' (to do)'}</span>
              </li>
            ))}
          </ul>
          {todo.length === 0 && <p className="muted">Everything on the list is in place.</p>}
        </section>
        <section className="card">
          <h2>Needs attention</h2>
          {attention.length === 0 ? <p className="muted">Nothing needs attention right now.</p> : (
            <ul className="attention">{attention.map((a) => <li key={`${a.kind}-${a.id}`}>{a.message}</li>)}</ul>
          )}
        </section>
      </div>

      <section className="card">
        <h2>At a glance</h2>
        <dl className="facts">
          <div><dt>Active addresses</dt><dd><Link to="/company/addresses">{counts.addresses}</Link></dd></div>
          <div><dt>Active partners</dt><dd><Link to="/company/partners">{counts.partners}</Link></dd></div>
          <div><dt>Active contacts</dt><dd><Link to="/company/contacts">{counts.contacts}</Link></dd></div>
          <div><dt>Documents</dt><dd><Link to="/company/documents">{counts.documents}</Link></dd></div>
        </dl>
      </section>
    </>
  );
}
