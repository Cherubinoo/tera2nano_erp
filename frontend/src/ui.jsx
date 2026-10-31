import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { buildPayload, initialValues, validateSpec } from './forms';

/* ------------------------------------------------------------------ toasts */
const ToastCtx = createContext(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(0);
  const dismiss = useCallback((id) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const notify = useCallback((message, type = 'success') => {
    const id = ++idRef.current;
    setToasts((t) => [...t, { id, message, type }]);
    setTimeout(() => dismiss(id), type === 'error' ? 10000 : 5000);
  }, [dismiss]);
  return (
    <ToastCtx.Provider value={notify}>
      {children}
      <div className="toasts" aria-live="polite" aria-atomic="false">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.type}`} role={t.type === 'error' ? 'alert' : 'status'}>
            <span>{t.message}</span>
            <button type="button" className="icon-btn" onClick={() => dismiss(t.id)} aria-label="Dismiss message">×</button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ------------------------------------------------------------------ dialogs */
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

export function Dialog({ title, onClose, children, footer, wide = false }) {
  const ref = useRef(null);
  const titleId = useId();
  useEffect(() => {
    const el = ref.current;
    const previous = document.activeElement;
    const first = el.querySelector('input:not([type=hidden]),select,textarea') || el.querySelector(FOCUSABLE) || el;
    first.focus();
    const onKey = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose && onClose(); }
      if (e.key === 'Tab') {
        const items = [...el.querySelectorAll(FOCUSABLE)];
        if (!items.length) return;
        const [a, z] = [items[0], items[items.length - 1]];
        if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
        else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
      }
    };
    el.addEventListener('keydown', onKey);
    return () => { el.removeEventListener('keydown', onKey); previous && previous.focus && previous.focus(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Portal: a dialog must never be a DOM child of the page's own <form> (nested forms are invalid HTML).
  return createPortal(
    <div className="overlay">
      <div className={`dialog${wide ? ' dialog-wide' : ''}`} role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref} tabIndex={-1}>
        <div className="dialog-head">
          <h2 id={titleId}>{title}</h2>
          {onClose && <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">×</button>}
        </div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

// Promise-based confirm: const {confirm, dialog} = useConfirm(); if (await confirm({...})) ...
export function useConfirm() {
  const [st, setSt] = useState(null);
  const confirm = useCallback((opts) => new Promise((resolve) => setSt({ opts, resolve })), []);
  const done = (v) => { const r = st.resolve; setSt(null); r(v); };
  const dialog = st && (
    <Dialog title={st.opts.title || 'Please confirm'} onClose={() => done(false)}
      footer={(
        <>
          <button type="button" className="btn" onClick={() => done(false)}>{st.opts.cancelLabel || 'Cancel'}</button>
          <button type="button" className={`btn ${st.opts.danger ? 'btn-danger' : 'btn-primary'}`} onClick={() => done(true)}>
            {st.opts.confirmLabel || 'Confirm'}
          </button>
        </>
      )}>
      {st.opts.body && <div className="confirm-body">{st.opts.body}</div>}
    </Dialog>
  );
  return { confirm, dialog };
}

// Promise-based reason prompt. ask({title, message, label, required}) -> string | null (cancelled).
export function useReasonDialog() {
  const [st, setSt] = useState(null);
  const [text, setText] = useState('');
  const [err, setErr] = useState('');
  const ask = useCallback((opts = {}) => new Promise((resolve) => { setText(''); setErr(''); setSt({ opts, resolve }); }), []);
  const finish = (v) => { const r = st.resolve; setSt(null); r(v); };
  const submit = (e) => {
    e.preventDefault();
    e.stopPropagation(); // React bubbles portal events to the form that opened the dialog; keep it from re-saving
    if (st.opts.required !== false && !text.trim()) { setErr('A reason is required.'); return; }
    finish(text.trim());
  };
  const dialog = st && (
    <Dialog title={st.opts.title || 'Reason for change'} onClose={() => finish(null)}>
      <form onSubmit={submit}>
        {st.opts.message && <p className="notice notice-info">{st.opts.message}</p>}
        <Field id="reason-text" label={st.opts.label || 'Reason'} required={st.opts.required !== false} error={err}>
          <textarea id="reason-text" rows={3} value={text} onChange={(e) => { setText(e.target.value); setErr(''); }}
            aria-describedby={err ? 'reason-text-err' : undefined} aria-invalid={!!err || undefined} />
        </Field>
        <p className="hint">This is recorded in the company change history.</p>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={() => finish(null)}>Cancel</button>
          <button type="submit" className="btn btn-primary">{st.opts.confirmLabel || 'Continue'}</button>
        </div>
      </form>
    </Dialog>
  );
  return { ask, dialog };
}

// ReasonDialog flow: run(fn) calls fn({}); if the server answers 400 with `change_reason`
// it asks for a reason and resubmits fn({change_reason}). Cancelling rejects with {cancelled:true}.
export function useReasonSubmit() {
  const { ask, dialog } = useReasonDialog();
  const run = useCallback(async (fn) => {
    try {
      return await fn({});
    } catch (e) {
      if (e && e.status === 400 && e.fieldErrors && e.fieldErrors.change_reason) {
        const reason = await ask({ message: e.fieldErrors.change_reason, required: true, confirmLabel: 'Save change' });
        if (reason === null) throw Object.assign(new Error('cancelled'), { cancelled: true });
        return fn({ change_reason: reason });
      }
      throw e;
    }
  }, [ask]);
  return { run, ask, dialog };
}

/* ------------------------------------------------------------------ small pieces */
export function Loading({ label = 'Loading…' }) {
  return <div className="loading" role="status"><span className="spinner" aria-hidden="true" />{label}</div>;
}

export function ErrorBox({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="notice notice-error" role="alert">
      <span>{error.message || String(error)}</span>
      {onRetry && <button type="button" className="btn btn-small" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Notice({ type = 'info', children }) {
  return <div className={`notice notice-${type}`} role={type === 'error' ? 'alert' : undefined}>{children}</div>;
}

export function Badge({ tone = 'neutral', children }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

const VERIFY_TONE = { verified: 'good', pending: 'warn', rejected: 'bad', unverified: 'neutral' };
export function VerificationBadge({ status }) {
  return <Badge tone={VERIFY_TONE[status] || 'neutral'}>{status ? status[0].toUpperCase() + status.slice(1) : '—'}</Badge>;
}

export function PageHeader({ title, description, actions }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {description && <p className="muted">{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.key} type="button" role="tab" aria-selected={value === t.key}
          className={`tab${value === t.key ? ' active' : ''}`} onClick={() => onChange(t.key)}>{t.label}</button>
      ))}
    </div>
  );
}

export const fmtDate = (d) => (d ? new Date(`${d}T00:00:00`).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '—');
export const fmtDateTime = (d) => (d ? new Date(d).toLocaleString('en-IN') : '—');

/* ------------------------------------------------------------------ form fields */
export function Field({ id, label, required, hint, error, children, wide, className = '' }) {
  return (
    <div className={`field${error ? ' has-error' : ''}${wide ? ' field-wide' : ''} ${className}`}>
      <label htmlFor={id}>{label}{required && <span className="req" aria-hidden="true"> *</span>}</label>
      {children}
      {hint && <div id={`${id}-hint`} className="hint">{hint}</div>}
      {error && <div id={`${id}-err`} className="error-text" role="alert">{error}</div>}
    </div>
  );
}

const resolveOptions = (spec, ctx) => (typeof spec.options === 'function' ? spec.options(ctx) : spec.options) || [];

function SpecField({ spec, value, onChange, error, disabled, ctx, id }) {
  const describedBy = [error && `${id}-err`, spec.hint && `${id}-hint`].filter(Boolean).join(' ') || undefined;
  const common = {
    id, name: spec.name, disabled, 'aria-invalid': error ? true : undefined, 'aria-describedby': describedBy,
    'aria-required': spec.required || undefined,
  };
  if (spec.type === 'checkbox') {
    return (
      <div className={`field field-check${error ? ' has-error' : ''}${spec.wide ? ' field-wide' : ''}`}>
        <label className="check"><input type="checkbox" {...common} checked={!!value} onChange={(e) => onChange(e.target.checked)} /> {spec.label}</label>
        {spec.hint && <div id={`${id}-hint`} className="hint">{spec.hint}</div>}
        {error && <div id={`${id}-err`} className="error-text" role="alert">{error}</div>}
      </div>
    );
  }
  if (spec.type === 'checkgroup') {
    const opts = resolveOptions(spec, ctx);
    const selected = Array.isArray(value) ? value : [];
    return (
      <fieldset className={`field field-wide checkgroup${error ? ' has-error' : ''}`} disabled={disabled} aria-describedby={describedBy}>
        <legend>{spec.label}{spec.required && <span className="req" aria-hidden="true"> *</span>}</legend>
        <div className="checkgroup-items">
          {opts.map((o) => (
            <label key={o.value} className="check">
              <input type="checkbox" checked={selected.includes(o.value)}
                onChange={(e) => onChange(e.target.checked ? [...selected, o.value] : selected.filter((x) => x !== o.value))} /> {o.label}
            </label>
          ))}
        </div>
        {spec.hint && <div id={`${id}-hint`} className="hint">{spec.hint}</div>}
        {error && <div id={`${id}-err`} className="error-text" role="alert">{error}</div>}
      </fieldset>
    );
  }
  let control;
  if (spec.type === 'textarea') {
    control = <textarea {...common} rows={spec.rows || 3} value={value ?? ''} maxLength={spec.maxLength} onChange={(e) => onChange(e.target.value)} />;
  } else if (spec.type === 'select') {
    const opts = resolveOptions(spec, ctx);
    control = (
      <select {...common} value={value ?? ''} onChange={(e) => onChange(e.target.value)}>
        {!spec.required && <option value="">{spec.blankLabel || '— none —'}</option>}
        {opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    );
  } else if (spec.type === 'color') {
    control = (
      <div className="color-row">
        <input type="color" aria-label={`${spec.label} picker`} disabled={disabled}
          value={/^#[0-9A-Fa-f]{6}$/.test(value || '') ? value : '#000000'} onChange={(e) => onChange(e.target.value.toUpperCase())} />
        <input type="text" {...common} value={value ?? ''} maxLength={7} onChange={(e) => onChange(e.target.value)} />
      </div>
    );
  } else {
    const listId = spec.datalist ? `${id}-list` : undefined;
    control = (
      <>
        <input {...common} type={spec.type || 'text'} value={value ?? ''} maxLength={spec.maxLength}
          min={typeof spec.min === 'function' ? spec.min(ctx) : spec.min} max={spec.max} step={spec.step}
          list={listId} autoComplete={spec.autoComplete || 'off'}
          placeholder={typeof spec.placeholder === 'function' ? spec.placeholder(ctx) : spec.placeholder}
          onChange={(e) => onChange(spec.upper ? e.target.value.toUpperCase() : e.target.value)} />
        {spec.datalist && <datalist id={listId}>{spec.datalist(ctx).map((o) => <option key={o} value={o} />)}</datalist>}
      </>
    );
  }
  return <Field id={id} label={spec.label} required={spec.required} hint={spec.hint} error={error} wide={spec.wide}>{control}</Field>;
}

export function SpecForm({ fields, values, onChange, errors = {}, readOnly = false, ctx = {}, idPrefix = 'f' }) {
  return (
    <div className="form-grid">
      {fields.map((spec) => {
        if (spec.hidden && spec.hidden(values)) return null;
        const disabled = readOnly || spec.readOnly || (spec.disabledWhen && spec.disabledWhen(values, ctx));
        return (
          <SpecField key={spec.name} spec={spec} id={`${idPrefix}-${spec.name}`} value={values[spec.name]}
            onChange={(v) => onChange(spec.name, v)} error={errors[spec.name]} disabled={disabled} ctx={{ ...ctx, values }} />
        );
      })}
    </div>
  );
}

// Banner for errors that do not belong to a visible field (detail / non_field_errors / unknown fields).
export function FormErrors({ error, fields = [] }) {
  if (!error) return null;
  const names = new Set(fields.map((f) => f.name));
  const stray = Object.entries(error.fieldErrors || {}).filter(([k]) => !names.has(k.split('.')[0]) && k !== 'change_reason');
  const hasDetail = error.data && typeof error.data.detail === 'string';
  if (!hasDetail && !stray.length && !error.message) return null;
  return (
    <div className="notice notice-error" role="alert">
      {stray.length === 0 || hasDetail ? error.message : null}
      {stray.length > 0 && (
        <ul>{stray.map(([k, v]) => <li key={k}>{k === 'non_field_errors' ? v : `${k.replace(/_/g, ' ')}: ${v}`}</li>)}</ul>
      )}
    </div>
  );
}

// Form state bound to a field spec list. `record` must be referentially stable between renders.
export function useSpecForm(fields, record, defaults) {
  const base = useMemo(() => initialValues(fields, record, defaults), [fields, record, defaults]);
  const [values, setValues] = useState(base);
  const [errors, setErrors] = useState({});
  useEffect(() => { setValues(base); setErrors({}); }, [base]);
  const set = useCallback((name, value) => {
    setValues((v) => ({ ...v, [name]: value }));
    setErrors((e) => (e[name] ? { ...e, [name]: undefined } : e));
  }, []);
  const dirty = fields.filter((f) => JSON.stringify(values[f.name]) !== JSON.stringify(base[f.name])).map((f) => f.name);
  const validate = (extra) => {
    const e = { ...validateSpec(fields, values), ...(extra ? extra(values) : {}) };
    Object.keys(e).forEach((k) => !e[k] && delete e[k]);
    setErrors(e);
    return Object.keys(e).length === 0;
  };
  const reset = () => { setValues(base); setErrors({}); };
  const payload = (onlyChanged) => buildPayload(fields, values, { initial: base, onlyChanged });
  return { values, set, errors, setErrors, base, dirty, validate, reset, payload };
}

export function Actions({ children }) {
  return <div className="actions">{children}</div>;
}
