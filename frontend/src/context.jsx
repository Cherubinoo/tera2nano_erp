import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { api, getToken, setToken } from './api';
import { Loading, ErrorBox } from './ui';
import { useLoad } from './hooks';

/* ------------------------------------------------------------------ auth */
const AuthCtx = createContext(null);
export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const expired = () => setUser(null);
    window.addEventListener('auth:expired', expired);
    if (getToken()) {
      api.get('/auth/me/').then(setUser).catch(() => setToken(null)).finally(() => setReady(true));
    } else {
      setReady(true);
    }
    return () => window.removeEventListener('auth:expired', expired);
  }, []);
  const login = useCallback(async (username, password) => {
    const res = await api.post('/auth/login/', { username, password });
    setToken(res.token);
    setUser(res.user);
  }, []);
  const logout = useCallback(async () => {
    try { await api.post('/auth/logout/'); } catch { /* token is dropped locally regardless */ }
    setToken(null);
    setUser(null);
  }, []);
  const value = useMemo(() => ({ user, ready, login, logout }), [user, ready, login, logout]);
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

/* ------------------------------------------------------------------ lookups */
const LookupsCtx = createContext(null);
export const useLookups = () => useContext(LookupsCtx);

export function LookupsProvider({ children }) {
  const { data, error, reload } = useLoad(() => api.get('/company/lookups/'), []);
  if (error) return <div className="page"><ErrorBox error={error} onRetry={reload} /></div>;
  if (!data) return <Loading label="Loading reference data…" />;
  return <LookupsCtx.Provider value={data}>{children}</LookupsCtx.Provider>;
}

/* ------------------------------------------------------------------ company profile */
const CompanyCtx = createContext(null);
export const useCompany = () => useContext(CompanyCtx);

export function CompanyProvider({ children }) {
  const [state, setState] = useState({ loading: true, profile: null, notConfigured: false, error: null });
  const reload = useCallback(async () => {
    try {
      const profile = await api.get('/company/profile/');
      setState({ loading: false, profile, notConfigured: false, error: null });
    } catch (e) {
      if (e.status === 404 && e.code === 'not_configured') setState({ loading: false, profile: null, notConfigured: true, error: null });
      else setState({ loading: false, profile: null, notConfigured: false, error: e });
    }
  }, []);
  useEffect(() => { reload(); }, [reload]);
  const value = useMemo(() => ({ ...state, reload }), [state, reload]);
  return <CompanyCtx.Provider value={value}>{children}</CompanyCtx.Provider>;
}
