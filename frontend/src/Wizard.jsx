import { useState } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';
import { api } from './api';
import LogoUploader from './components/LogoUploader';
import ProfileEditor from './components/ProfileEditor';
import { CompanyProvider, useAuth, useCompany } from './context';
import { useLoad } from './hooks';
import { BankAccounts } from './pages/Bank';
import { BrandingDesigner } from './pages/Branding';
import { NumberingManager } from './pages/Numbering';
import { PoliciesManager } from './pages/Policies';
import {
  AddressesSection, ContactsSection, FinancialYearsManager, RegistrationsSection,
} from './pages/sections';
import { IDENTITY_FIELDS, WIZARD_GST_FIELDS, WIZARD_LEGAL_FIELDS } from './specs';
import {
  Badge, ErrorBox, FormErrors, Loading, Notice, SpecForm, useReasonSubmit, useSpecForm, useToast,
} from './ui';

const LAST_STEP = 12;
const REG_OFFICE = { address_type: 'registered_office', country: 'India', is_active: true };

function IdentityStep({ onDone }) {
  const company = useCompany();
  const notify = useToast();
  const { run, dialog } = useReasonSubmit();
  const form = useSpecForm(IDENTITY_FIELDS, company.profile);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const exists = !!company.profile;

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    if (!form.validate()) return;
    setBusy(true);
    try {
      if (!exists) {
        // Idempotent on the server: a second call returns the existing company.
        await api.post('/company/setup/start/', { legal_name: form.values.legal_name.trim(), display_name: form.values.display_name.trim() });
        if (form.values.short_code) await api.patch('/company/profile/', { short_code: form.values.short_code });
        notify('Company created. Continue with the next step.');
      } else {
        const payload = form.payload(true);
        if (Object.keys(payload).length) await run((extra) => api.patch('/company/profile/', { ...payload, ...extra }));
        notify('Company names saved.');
      }
      await company.reload();
      await onDone();
    } catch (err) {
      if (!err.cancelled) { setError(err); form.setErrors(err.fieldErrors || {}); }
    }
    setBusy(false);
  };

  return (
    <form onSubmit={submit} noValidate className="card">
      <h2>Company name</h2>
      <p className="muted">Only the legal name and display name are needed to start. Everything else can be added later.</p>
      <FormErrors error={error} fields={IDENTITY_FIELDS} />
      <SpecForm fields={IDENTITY_FIELDS} values={form.values} onChange={form.set} errors={form.errors} idPrefix="wiz-id" />
      <div className="actions">
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : exists ? 'Save and continue' : 'Create company and continue'}</button>
      </div>
      {dialog}
    </form>
  );
}

function ReviewStep({ state, onFinished }) {
  const company = useCompany();
  const navigate = useNavigate();
  const notify = useToast();
  const { data, error, loading, reload } = useLoad(() => api.get('/company/overview/'), []);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null); // warnings
  const [failure, setFailure] = useState(null);

  const complete = async () => {
    setBusy(true);
    setFailure(null);
    try {
      const res = await api.post('/company/setup/complete/');
      setDone(res.warnings || []);
      notify('Company setup saved.');
      await company.reload();
      await onFinished();
    } catch (e) { setFailure(e); }
    setBusy(false);
  };

  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const p = data.profile;
  return (
    <section className="card">
      <h2>Review and save</h2>
      <dl className="facts">
        <div><dt>Legal name</dt><dd>{p.legal_name}</dd></div>
        <div><dt>Display name</dt><dd>{p.display_name}</dd></div>
        <div><dt>PAN</dt><dd>{p.pan || '—'}</dd></div>
        <div><dt>LLPIN</dt><dd>{p.llpin || '—'}</dd></div>
        <div><dt>GST</dt><dd>{p.gstin || p.gst_registration_status.replace('_', ' ')}</dd></div>
        <div><dt>Registered office</dt><dd>{p.registered_office ? `${p.registered_office.city}, ${p.registered_office.state}` : '—'}</dd></div>
      </dl>
      <h3>Steps</h3>
      <ul className="checklist">
        {state.steps.filter((s) => s.step < LAST_STEP).map((s) => (
          <li key={s.step} className={s.complete ? 'done' : ''}>
            <span aria-hidden="true">{s.complete ? '✓' : '○'}</span> {s.title}{' '}
            {!s.complete && (s.skipped ? <Badge>Skipped – add later</Badge> : <Badge tone="warn">Not done</Badge>)}
          </li>
        ))}
      </ul>
      <Notice>Optional items you skipped do not block setup. You can complete them later from Company Management.</Notice>
      <ErrorBox error={failure} />
      {done ? (
        <>
          <Notice type="success">Setup is complete.</Notice>
          {done.length > 0 && (
            <div className="notice notice-info">
              <strong>Still to do (not blocking):</strong>
              <ul>{done.map((w) => <li key={w}>{w}</li>)}</ul>
            </div>
          )}
          <div className="actions"><button type="button" className="btn btn-primary" onClick={() => navigate('/company/overview')}>Go to Company Overview</button></div>
        </>
      ) : (
        <div className="actions"><button type="button" className="btn btn-primary" disabled={busy} onClick={complete}>{busy ? 'Saving…' : 'Save company profile'}</button></div>
      )}
    </section>
  );
}

function WizardInner() {
  const company = useCompany();
  const { user } = useAuth();
  const notify = useToast();
  const { data: state, error, loading, reload } = useLoad(() => api.get('/company/setup/'), []);
  const [step, setStep] = useState(null);
  const [initial, setInitial] = useState(false);

  // Spinner on the first load only. Replacing the page during a background reload would unmount the current
  // step and lose its local state (for example the "setup complete" message or half-typed form values).
  if ((company.loading && !company.profile && !company.notConfigured) || (loading && !state)) return <Loading />;
  if (error) return <div className="page narrow"><ErrorBox error={error} onRetry={reload} /></div>;
  if (company.error) return <div className="page narrow"><ErrorBox error={company.error} onRetry={company.reload} /></div>;
  if (state.status === 'completed' && !step) return <Navigate to="/company/overview" replace />;
  if (!initial) { setInitial(true); setStep(state.exists ? Math.min(Math.max(state.current_step, 1), LAST_STEP) : 1); return <Loading />; }

  const exists = state.exists;
  const current = state.steps.find((s) => s.step === step);

  const goTo = async (n, { skip = false } = {}) => {
    const skipped = new Set(state.skipped_steps || []);
    if (skip) skipped.add(step); else skipped.delete(step);
    try {
      await api.patch('/company/setup/progress/', { step: n, skipped_steps: [...skipped] });
      await reload();
      setStep(n);
      window.scrollTo({ top: 0 });
    } catch (e) { notify(e.message, 'error'); }
  };

  let body;
  switch (step) {
    case 1: body = <IdentityStep onDone={async () => { await reload(); setStep(2); }} />; break;
    case 2: body = (<><p className="muted">Incorporation and legal registration details. Leave anything you do not have yet blank.</p><ProfileEditor fields={WIZARD_LEGAL_FIELDS} /></>); break;
    case 3: body = (<><p className="muted">Add the registered office address. Add other offices later if needed.</p><AddressesSection defaults={REG_OFFICE} /></>); break;
    case 4: body = (<><p className="muted">General, sales, finance, HR and other contacts printed on documents.</p><ContactsSection /></>); break;
    case 5: body = (
      <>
        <p className="muted">Record only the tax registrations that actually apply. Do not enter a GSTIN unless the company is registered.</p>
        <ProfileEditor fields={WIZARD_GST_FIELDS} />
        <RegistrationsSection />
      </>
    ); break;
    case 6: body = (<><Notice>A bank account is optional. Skip this step if the company has not opened one yet; add it later under Bank Accounts.</Notice><BankAccounts /></>); break;
    case 7: body = (<section className="card"><LogoUploader title="Company logo" path="/company/profile/logo/" canWrite={user.is_company_admin} note="PNG or JPEG." onChanged={() => reload()} /></section>); break;
    case 8: body = <BrandingDesigner />; break;
    case 9: body = <NumberingManager />; break;
    case 10: body = (<><p className="muted">Add the current financial year (normally 1 April to 31 March).</p><FinancialYearsManager onChanged={reload} /></>); break;
    case 11: body = (<><p className="muted">Create a draft of your default payment terms and activate it when ready.</p><PoliciesManager /></>); break;
    default: body = <ReviewStep state={state} onFinished={reload} />;
  }

  const optional = current && !current.required;
  return (
    <div className="wizard">
      <a className="skip-link" href="#wizard-main">Skip to content</a>
      <nav className="wizard-steps" aria-label="Setup steps">
        <h1>Company setup</h1>
        <ol>
          {state.steps.map((s) => (
            <li key={s.step}>
              <button type="button" className={`step-btn${s.step === step ? ' current' : ''}`} aria-current={s.step === step ? 'step' : undefined}
                disabled={!exists && s.step !== 1} onClick={() => setStep(s.step)}>
                <span className="step-mark" aria-hidden="true">{s.complete ? '✓' : s.skipped ? '–' : s.step}</span>
                <span>{s.title}{s.skipped && !s.complete && <span className="muted"> (skipped)</span>}{s.complete && <span className="sr-only"> (done)</span>}</span>
              </button>
            </li>
          ))}
        </ol>
        <Link to="/company" className="muted">Exit and finish later</Link>
      </nav>
      <main id="wizard-main" className="wizard-main" tabIndex={-1}>
        <header className="page-head">
          <div>
            <p className="muted">Step {step} of {LAST_STEP}{optional ? ' · optional' : ''}</p>
            <h1>{current?.title}</h1>
          </div>
        </header>
        {body}
        {step > 1 && step < LAST_STEP && (
          <div className="wizard-nav">
            <button type="button" className="btn" onClick={() => setStep(step - 1)}>Back</button>
            {optional && <button type="button" className="btn" onClick={() => goTo(step + 1, { skip: true })}>Skip for now</button>}
            <button type="button" className="btn btn-primary" onClick={() => goTo(step + 1)}>Continue</button>
          </div>
        )}
        {step === LAST_STEP && <div className="wizard-nav"><button type="button" className="btn" onClick={() => setStep(step - 1)}>Back</button></div>}
      </main>
    </div>
  );
}

export default function Wizard() {
  const { user } = useAuth();
  if (!user.is_company_admin) {
    return <div className="page narrow"><Notice type="warn">Only a company administrator can run company setup.</Notice></div>;
  }
  return (<CompanyProvider><WizardInner /></CompanyProvider>);
}
