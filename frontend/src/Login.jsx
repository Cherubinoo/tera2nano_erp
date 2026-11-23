import { useState } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './context';
import { Field } from './ui';

export default function Login() {
  const { user, login } = useAuth();
  const location = useLocation();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  if (user) return <Navigate to={location.state?.from || '/apps'} replace />;

  const submit = async (e) => {
    e.preventDefault();
    if (!username.trim() || !password) { setError('Enter your username and password.'); return; }
    setBusy(true);
    setError('');
    try { await login(username.trim(), password); } catch (err) { setError(err.status === 429 ? 'Too many attempts. Wait a minute and try again.' : err.message); }
    setBusy(false);
  };

  return (
    <main className="login">
      <form className="card login-card" onSubmit={submit} noValidate>
        <h1>Sign in</h1>
        <p className="muted">Tera2Nano ERP</p>
        {error && <div className="notice notice-error" role="alert">{error}</div>}
        <Field id="login-user" label="Username" required>
          <input id="login-user" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field id="login-pass" label="Password" required>
          <input id="login-pass" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </main>
  );
}
