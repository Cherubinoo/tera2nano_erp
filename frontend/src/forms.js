// Spec-driven form helpers. A "spec" describes one field:
// {name,label,type,required,pattern,patternMessage,options,hint,upper,hidden,clearWhenHidden,...}

export const PATTERNS = {
  pan: /^[A-Z]{5}[0-9]{4}[A-Z]$/,
  tan: /^[A-Z]{4}[0-9]{5}[A-Z]$/,
  gstin: /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/,
  llpin: /^[A-Z]{3}-[0-9]{4}$/,
  ifsc: /^[A-Z]{4}0[A-Z0-9]{6}$/,
  pin: /^[1-9][0-9]{5}$/,
  phone: /^\+?[0-9][0-9 \-()]{6,19}$/,
  hex: /^#[0-9A-Fa-f]{6}$/,
  currency: /^[A-Za-z]{3}$/,
  dpin: /^[0-9]{8}$/,
  account: /^[0-9]{6,20}$/,
  email: /^[^@\s]+@[^@\s]+\.[^@\s]+$/,
};

export const emptyFor = (f) => (f.type === 'checkbox' ? false : f.type === 'checkgroup' ? [] : '');

export function initialValues(fields, record, defaults = {}) {
  const out = {};
  fields.forEach((f) => {
    const src = record && record[f.name] !== undefined ? record[f.name] : defaults[f.name];
    out[f.name] = src === undefined || src === null ? emptyFor(f) : src;
  });
  return out;
}

// Normalise a value the way it will be sent, so changed/unchanged can be compared.
function norm(f, v) {
  if (v === '' || v === undefined) {
    return f.type === 'date' || f.type === 'number' || f.nullable ? null : '';
  }
  if (f.type === 'number' && v !== null) return Number(v);
  if (typeof v === 'string' && f.type !== 'textarea') return v.trim();
  return v;
}

export function buildPayload(fields, values, { initial, onlyChanged = false } = {}) {
  const out = {};
  fields.forEach((f) => {
    if (f.readOnly || f.noSubmit) return;
    let v = values[f.name];
    if (f.hidden && f.hidden(values)) {
      if (!f.clearWhenHidden) return;
      v = emptyFor(f);
    }
    if (f.omitIfEmpty && (v === '' || v == null)) return;
    const next = norm(f, v);
    if (onlyChanged && initial && !f.alwaysSend) {
      let prev = initial[f.name];
      if (f.hidden && f.hidden(initial) && f.clearWhenHidden) prev = emptyFor(f);
      if (JSON.stringify(norm(f, prev)) === JSON.stringify(next)) return;
    }
    out[f.name] = next;
  });
  return out;
}

const isEmpty = (v) => v === '' || v == null || (Array.isArray(v) && v.length === 0);

export function validateSpec(fields, values) {
  const errors = {};
  fields.forEach((f) => {
    if (f.hidden && f.hidden(values)) return;
    const v = values[f.name];
    const empty = isEmpty(v);
    if (f.required && f.type !== 'checkbox' && empty) { errors[f.name] = 'This field is required.'; return; }
    if (empty || f.type === 'checkbox') { /* optional and blank */ } else {
      const s = String(v).trim();
      if (f.pattern && !f.pattern.test(s)) errors[f.name] = f.patternMessage || 'Enter a valid value.';
      else if (f.type === 'email' && !PATTERNS.email.test(s)) errors[f.name] = 'Enter a valid email address.';
      else if (f.type === 'url' && !/^https?:\/\/\S+$/i.test(s)) errors[f.name] = 'Enter a full web address starting with http:// or https://.';
      else if (f.type === 'number') {
        const n = Number(s);
        if (Number.isNaN(n)) errors[f.name] = 'Enter a number.';
        else if (f.min !== undefined && n < f.min) errors[f.name] = `Must be at least ${f.min}.`;
        else if (f.max !== undefined && n > f.max) errors[f.name] = `Must be at most ${f.max}.`;
        else if (f.integer && !Number.isInteger(n)) errors[f.name] = 'Enter a whole number.';
      }
    }
    if (!errors[f.name] && f.validate) {
      const m = f.validate(v, values);
      if (m) errors[f.name] = m;
    }
  });
  return errors;
}

export function changedLabels(fields, names) {
  return names.map((n) => fields.find((f) => f.name === n)?.label || n);
}

export function dateOrder(fromName, toName, message = 'End date cannot be before the start date.') {
  return (v, values) => (v && values[fromName] && v < values[fromName] ? message : null);
}

// Today's date in the user's own time zone, as YYYY-MM-DD. (toISOString would give the UTC date, which is
// still "yesterday" for the first hours of the day in India.)
export function localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
