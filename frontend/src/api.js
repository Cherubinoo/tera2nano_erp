// Small fetch wrapper for the ERP API. The token lives in sessionStorage and is only ever
// sent as an Authorization header - never in a URL.
const BASE = '/api/v1';
const TOKEN_KEY = 'erp_token';

export function getToken() {
  try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
}
export function setToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch { /* storage unavailable: the session simply will not survive a reload */ }
}

// Flatten DRF error bodies ({field: [msgs]}, nested {item: {field: [msgs]}}) into
// {'field': 'msg', 'item.field': 'msg'}.
function flatten(prefix, value, out) {
  if (Array.isArray(value)) {
    const strings = value.filter((v) => typeof v === 'string');
    if (strings.length) out[prefix] = strings.join(' ');
    else value.forEach((v, i) => v && typeof v === 'object' && flatten(`${prefix}.${i}`, v, out));
  } else if (value && typeof value === 'object') {
    Object.entries(value).forEach(([k, v]) => flatten(prefix ? `${prefix}.${k}` : k, v, out));
  } else if (value != null) {
    out[prefix] = String(value);
  }
}

function describe(status, data) {
  if (data && typeof data === 'object') {
    if (typeof data.detail === 'string') return data.detail;
    const fe = fieldErrorsFrom(data);
    if (fe.non_field_errors) return fe.non_field_errors;
    if (Object.keys(fe).length) return 'Please correct the highlighted fields.';
  }
  if (status === 0) return 'Cannot reach the server. Check your connection and try again.';
  if (status === 403) return 'You do not have permission to do this.';
  if (status === 404) return 'Not found.';
  if (status >= 500) return 'The server hit an unexpected error. Nothing was saved.';
  return `Request failed (${status}).`;
}

function fieldErrorsFrom(data) {
  const out = {};
  if (data && typeof data === 'object' && !Array.isArray(data)) {
    // `code` is also the name of the machine-readable error code (a string); a list under `code` is a real field's errors.
    Object.entries(data).forEach(([k, v]) => {
      if (k === 'detail' || (k === 'code' && typeof v === 'string')) return;
      flatten(k === '__all__' ? 'non_field_errors' : k, v, out);
    });
  } else if (Array.isArray(data)) {
    flatten('non_field_errors', data, out);
  }
  return out;
}

export class ApiError extends Error {
  constructor(status, data) {
    super(describe(status, data));
    this.status = status;
    this.data = data;
    this.code = data && typeof data.code === 'string' ? data.code : undefined;
    this.fieldErrors = fieldErrorsFrom(data);
  }
}

function filenameFrom(res, fallback) {
  const cd = res.headers.get('Content-Disposition') || '';
  const star = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  if (star) { try { return decodeURIComponent(star[1]); } catch { /* fall through */ } }
  const plain = /filename="?([^";]+)"?/i.exec(cd);
  return plain ? plain[1] : fallback;
}

export async function request(method, path, { body, params, form, blob, fallbackName } = {}) {
  const headers = { Accept: blob ? '*/*' : 'application/json' };
  const token = getToken();
  if (token) headers.Authorization = `Token ${token}`;
  let payload;
  if (form) payload = form; // the browser sets the multipart boundary
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  let qs = '';
  if (params) {
    const sp = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== '' && v != null) sp.set(k, v); });
    const s = sp.toString();
    qs = s ? `?${s}` : '';
  }
  let res;
  try {
    res = await fetch(`${BASE}${path}${qs}`, { method, headers, body: payload });
  } catch {
    throw new ApiError(0, null);
  }
  if (res.status === 401 && !path.startsWith('/auth/login')) {
    setToken(null);
    window.dispatchEvent(new Event('auth:expired'));
  }
  if (!res.ok) {
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, data);
  }
  if (res.status === 204) return null;
  if (blob) return { blob: await res.blob(), filename: filenameFrom(res, fallbackName), type: res.headers.get('Content-Type') };
  return res.json();
}

export const api = {
  get: (path, params) => request('GET', path, { params }),
  post: (path, body) => request('POST', path, { body }),
  put: (path, body) => request('PUT', path, { body }),
  patch: (path, body) => request('PATCH', path, { body }),
  del: (path, body) => request('DELETE', path, { body }),
  upload: (path, form, method = 'POST') => request(method, path, { form }),
  blob: (path, opts = {}) => request(opts.method || 'GET', path, { blob: true, ...opts }),
};

// Fetch a private file with the auth header and save it via a temporary object URL.
export async function downloadFile(path, fallbackName = 'download') {
  const { blob, filename } = await api.blob(path, { fallbackName });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
