import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from './api';
import { Field, Loading } from './ui';

// Public page opened from an invitation link. It sits outside the signed-in layout. The form is drawn from the field list the server
// sends (INVITE_FIELDS), so the questions can change without touching this file.

const INPUT_TYPE = { text: 'text', email: 'email', tel: 'tel', date: 'date', username: 'text', password: 'password' };
const AUTOCOMPLETE = { full_name: 'name', username: 'username', password: 'new-password', password_confirm: 'new-password', phone: 'tel', personal_email: 'email' };

function clientCheck(fields, values) {
  const errs = {};
  fields.forEach((f) => {
    const val = values[f.key] || '';
    if (f.required && !val.trim()) errs[f.key] = 'This field is required.';
    else if (f.type === 'email' && val && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(val)) errs[f.key] = 'Enter a valid email address.';
    else if (f.type === 'username' && /\s/.test(val)) errs[f.key] = 'Usernames cannot contain spaces.';
    else if (f.confirm_of && val !== (values[f.confirm_of] || '')) errs[f.key] = 'The two passwords do not match.';
  });
  return errs;
}

function Brand({ company }) {
  return (
    <div className="join-brand">
      <span className="join-mark" aria-hidden="true">T</span>
      <span>{company || 'Tera2Nano ERP'}</span>
    </div>
  );
}

function JoinField({ f, value, error, onChange, onBlur, shown, toggle }) {
  const id = `join-${f.key}`;
  const describedBy = [error && `${id}-err`, f.help && `${id}-hint`].filter(Boolean).join(' ') || undefined;
  const common = {
    id, name: f.key, value: value || '', 'aria-invalid': error ? true : undefined, 'aria-describedby': describedBy,
    'aria-required': f.required || undefined, onBlur,
  };
  let control;
  if (f.type === 'textarea') {
    control = <textarea {...common} rows={3} maxLength={f.max_length || 600} onChange={(e) => onChange(e.target.value)} />;
  } else if (f.type === 'select') {
    control = (
      <select {...common} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose…</option>
        {(f.options || []).map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  } else if (f.type === 'password') {
    control = (
      <div className="pw-row">
        <input {...common} type={shown ? 'text' : 'password'} autoComplete={AUTOCOMPLETE[f.key] || 'new-password'} onChange={(e) => onChange(e.target.value)} />
        <button type="button" className="btn btn-small" onClick={toggle} aria-pressed={shown} aria-label={`${shown ? 'Hide' : 'Show'} ${f.label.toLowerCase()}`}>
          {shown ? 'Hide' : 'Show'}
        </button>
      </div>
    );
  } else {
    control = (
      <input {...common} type={INPUT_TYPE[f.type] || 'text'} autoComplete={AUTOCOMPLETE[f.key] || 'off'} maxLength={f.max_length || undefined}
        onChange={(e) => onChange(e.target.value)} />
    );
  }
  return <Field id={id} label={f.label} required={f.required} hint={f.help} error={error} wide={f.type === 'textarea'}>{control}</Field>;
}

export default function Join() {
  const { token } = useParams();
  const navigate = useNavigate();
  const [state, setState] = useState({ phase: 'loading', info: null });
  const [values, setValues] = useState({});
  const [errors, setErrors] = useState({});
  const [banner, setBanner] = useState('');
  const [shown, setShown] = useState({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // Keep the secret link out of the Referer header of anything this page might open.
    const meta = document.createElement('meta');
    meta.name = 'referrer';
    meta.content = 'no-referrer';
    document.head.appendChild(meta);
    const prev = document.title;
    document.title = 'Join - Tera2Nano ERP';
    return () => { meta.remove(); document.title = prev; };
  }, []);

  useEffect(() => {
    let alive = true;
    api.post('/auth/invitations/lookup/', { token })
      .then((info) => {
        if (!alive) return;
        setValues({ full_name: info.name_hint || '', personal_email: info.email_hint || '' });
        setState({ phase: 'form', info });
      })
      .catch((err) => alive && setState({ phase: err.status === 429 ? 'busy' : err.status === 0 ? 'offline' : 'invalid', info: null }));
    return () => { alive = false; };
  }, [token]);

  const fields = state.info?.fields || [];
  const set = (key) => (val) => { setValues((v) => ({ ...v, [key]: val })); setErrors((e) => ({ ...e, [key]: undefined })); };
  const blur = (f) => () => {
    const msg = clientCheck([f], values)[f.key];
    if (msg && (values[f.key] || f.required)) setErrors((e) => ({ ...e, [f.key]: msg }));
  };

  const submit = async (e) => {
    e.preventDefault();
    setBanner('');
    const errs = clientCheck(fields, values);
    setErrors(errs);
    if (Object.keys(errs).length) {
      setBanner('Please correct the highlighted fields.');
      document.getElementById(`join-${Object.keys(errs)[0]}`)?.focus();
      return;
    }
    setBusy(true);
    try {
      await api.post('/auth/invitations/accept/', { token, ...values });
      setState((s) => ({ ...s, phase: 'done' }));
    } catch (err) {
      if (err.status === 404) setState({ phase: 'invalid', info: null });
      else if (err.status === 429) setBanner('Too many attempts. Wait a minute and try again.');
      else {
        setErrors(err.fieldErrors || {});
        setBanner(Object.keys(err.fieldErrors || {}).length ? 'Please correct the highlighted fields.' : err.message);
        const first = Object.keys(err.fieldErrors || {}).find((k) => fields.some((f) => f.key === k));
        if (first) document.getElementById(`join-${first}`)?.focus();
      }
    }
    setBusy(false);
  };

  const company = state.info?.company_name;
  return (
    <main className="login join">
      <div className="card login-card join-card">
        <Brand company={company} />
        {state.phase === 'loading' && <Loading label="Checking your link…" />}
        {state.phase === 'invalid' && (
          <div role="alert">
            <h1>This link cannot be used</h1>
            <p className="muted">The invitation link is not valid. It may have expired, already been used or been withdrawn. Ask the person who invited you for a new link.</p>
            <button type="button" className="btn btn-primary" onClick={() => navigate('/login')}>Go to sign in</button>
          </div>
        )}
        {state.phase === 'busy' && (
          <div role="alert">
            <h1>Please wait a moment</h1>
            <p className="muted">Too many attempts from this connection. Wait a minute and open the link again.</p>
          </div>
        )}
        {state.phase === 'offline' && (
          <div role="alert">
            <h1>Cannot reach the server</h1>
            <p className="muted">Check your connection and open the link again.</p>
          </div>
        )}
        {state.phase === 'done' && (
          <div role="status">
            <h1>You are all set</h1>
            <p className="muted">Your account has been created. Sign in with the username and password you just chose.</p>
            <button type="button" className="btn btn-primary" onClick={() => navigate('/login')}>Go to sign in</button>
          </div>
        )}
        {state.phase === 'form' && (
          <form onSubmit={submit} noValidate aria-labelledby="join-title">
            <h1 id="join-title">Join {company}</h1>
            <p className="join-role">You are joining as <strong>{state.info.role_label}</strong></p>
            <p className="muted">Fill in your details to create your account. This link works once.</p>
            {banner && <div className="notice notice-error" role="alert">{banner}</div>}
            <div className="form-grid join-grid">
              {fields.map((f) => (
                <JoinField key={f.key} f={f} value={values[f.key]} error={errors[f.key]} onChange={set(f.key)} onBlur={blur(f)}
                  shown={!!shown[f.key]} toggle={() => setShown((s) => ({ ...s, [f.key]: !s[f.key] }))} />
              ))}
            </div>
            <button type="submit" className="btn btn-primary join-submit" disabled={busy}>{busy ? 'Creating your account…' : 'Create my account'}</button>
          </form>
        )}
      </div>
    </main>
  );
}
