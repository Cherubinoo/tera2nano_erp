import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from './api';

// Load data from the API; returns {data, error, loading, reload}.
export function useLoad(fn, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const [n, setN] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    fnRef.current()
      .then((data) => alive && setState({ data, error: null, loading: false }))
      .catch((error) => alive && setState({ data: null, error, loading: false }));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n]);
  const reload = useCallback(() => setN((x) => x + 1), []);
  return { ...state, reload };
}

export const asList = (data) => (Array.isArray(data) ? data : data?.results || []);

// Options for a <select> from an API list endpoint.
export function useOptions(endpoint, labelFn, params) {
  const { data } = useLoad(() => (endpoint ? api.get(endpoint, params) : Promise.resolve([])),
    [endpoint, JSON.stringify(params || {})]);
  return useMemo(() => asList(data).map((r) => ({ value: r.id, label: labelFn(r) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data]);
}

// Fetch a private image/file as an object URL (authenticated). 404 -> null.
export function useBlobUrl(path, deps = []) {
  const [state, setState] = useState({ url: null, loading: true });
  useEffect(() => {
    let alive = true;
    let created = null;
    setState({ url: null, loading: true });
    if (!path) { setState({ url: null, loading: false }); return undefined; }
    api.blob(path)
      .then(({ blob }) => {
        created = URL.createObjectURL(blob);
        if (alive) setState({ url: created, loading: false });
        else URL.revokeObjectURL(created);
      })
      .catch(() => alive && setState({ url: null, loading: false }));
    return () => { alive = false; if (created) URL.revokeObjectURL(created); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  return state;
}

export function useDebounced(value, ms) {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}
