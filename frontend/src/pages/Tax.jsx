import { useMemo, useState } from 'react';
import { api } from '../api';
import { useAuth, useLookups } from '../context';
import { changedLabels } from '../forms';
import { useLoad } from '../hooks';
import { TAX_SETTINGS_FIELDS } from '../specs';
import {
  ErrorBox, Field, FormErrors, Loading, Notice, SpecForm, useConfirm, useReasonSubmit, useSpecForm, useToast,
} from '../ui';

const BLANK_CODE = { code: '', label: '', rate: '', hsn_sac: '', effective_from: '', effective_to: '' };
const cleanCodes = (codes) => codes.map((c) => {
  const out = { code: c.code.trim(), rate: Number(c.rate) };
  if (c.label && c.label.trim()) out.label = c.label.trim();
  if (c.hsn_sac && c.hsn_sac.trim()) out.hsn_sac = c.hsn_sac.trim();
  if (c.effective_from) out.effective_from = c.effective_from;
  if (c.effective_to) out.effective_to = c.effective_to;
  return out;
});
// Order- and blank-insensitive form used only to decide whether the codes changed.
const canon = (arr) => JSON.stringify(arr.map((o) => Object.fromEntries(Object.keys(o).filter((k) => o[k] !== '' && o[k] != null).sort().map((k) => [k, k === 'rate' ? Number(o[k]) : o[k]]))));
const toEditable = (codes) => (codes || []).map((c) => ({ ...BLANK_CODE, ...c, rate: c.rate ?? '' }));

export function TaxSettings() {
  const { user } = useAuth();
  const lookups = useLookups();
  const notify = useToast();
  const canWrite = user.is_company_admin;
  const { data, error, loading, reload } = useLoad(() => api.get('/company/tax-settings/'), []);
  const { run, dialog: reasonDialog } = useReasonSubmit();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [codes, setCodes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [codeErrors, setCodeErrors] = useState([]);
  const form = useSpecForm(TAX_SETTINGS_FIELDS, data);

  // initialise the editable code rows once the record arrives (and after each save)
  const serverCodes = useMemo(() => JSON.stringify(data?.tax_codes || []), [data]);
  const [seen, setSeen] = useState(null);
  if (data && seen !== serverCodes) { setSeen(serverCodes); setCodes(toEditable(data.tax_codes)); }

  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data || !codes) return <Loading />;

  const allowed = data.allowed_rates;
  const codesChanged = canon(cleanCodes(codes.filter((c) => c.code || c.rate !== ''))) !== canon(data.tax_codes);
  const setCode = (i, patch) => setCodes(codes.map((c, j) => (j === i ? { ...c, ...patch } : c)));

  const validateCodes = () => {
    const errs = [];
    const seenCodes = new Set();
    codes.forEach((c, i) => {
      const n = i + 1;
      if (!c.code.trim()) errs.push(`Tax code ${n}: enter a code.`);
      if (seenCodes.has(c.code.trim())) errs.push(`Tax code ${n}: duplicate code "${c.code}".`);
      seenCodes.add(c.code.trim());
      const rate = Number(c.rate);
      if (c.rate === '' || Number.isNaN(rate)) { errs.push(`Tax code ${n}: enter a rate.`); return; }
      if (allowed.current.includes(rate)) { /* currently applicable */ } else if (allowed.historic.includes(rate)) {
        if (!c.effective_to) errs.push(`Tax code ${n}: ${rate}% is a historic rate and needs an effective-to date.`);
      } else errs.push(`Tax code ${n}: ${rate}% is not an applicable GST rate (current: ${allowed.current.join(', ')}%).`);
      if (c.effective_from && c.effective_to && c.effective_to < c.effective_from) errs.push(`Tax code ${n}: effective-to is before effective-from.`);
    });
    return errs;
  };

  const save = async (e) => {
    e.preventDefault();
    setSaveError(null);
    const okForm = form.validate();
    const errs = validateCodes();
    setCodeErrors(errs);
    if (!okForm || errs.length) return;
    const payload = form.payload(true);
    if (codesChanged) payload.tax_codes = cleanCodes(codes);
    if (!Object.keys(payload).length) { notify('No changes to save.', 'info'); return; }
    const labels = changedLabels(TAX_SETTINGS_FIELDS, Object.keys(payload).filter((n) => n !== 'tax_codes'));
    if (codesChanged) labels.push('Tax codes');
    if (!(await confirm({ title: 'Save tax configuration?', body: (<><p>This changes how GST is treated on new documents.</p><ul>{labels.map((l) => <li key={l}>{l}</li>)}</ul></>), confirmLabel: 'Save' }))) return;
    setBusy(true);
    try {
      await run((extra) => api.patch('/company/tax-settings/', { ...payload, ...extra }));
      notify('Tax configuration saved.');
      reload();
    } catch (err) {
      if (!err.cancelled) {
        setSaveError(err);
        form.setErrors(err.fieldErrors || {});
        const tc = Object.entries(err.fieldErrors || {}).filter(([k]) => k.startsWith('tax_codes')).map(([, v]) => v);
        if (tc.length) setCodeErrors(tc);
      }
    }
    setBusy(false);
  };

  return (
    <form onSubmit={save} noValidate className="card">
      {!canWrite && <Notice>Only administrators can change tax configuration.</Notice>}
      <Notice>Nothing here is assumed to apply. Switch GST on only if the company is registered and GST applies, and check rates against current GST notifications.</Notice>
      <FormErrors error={saveError} fields={TAX_SETTINGS_FIELDS} />
      <SpecForm fields={TAX_SETTINGS_FIELDS} values={form.values} onChange={form.set} errors={form.errors} readOnly={!canWrite} ctx={{ lookups, codes }} idPrefix="taxset" />

      <h3>Tax codes</h3>
      <p className="hint">Current rates: {allowed.current.join('%, ')}%. Historic rates ({allowed.historic.join('%, ')}%) are accepted only with an effective-to date.</p>
      {codes.length === 0 && <p className="empty">No tax codes configured.</p>}
      {codes.map((c, i) => (
        <div className="row-edit row-edit-wide" key={i}>
          <Field id={`tc-code-${i}`} label="Code"><input id={`tc-code-${i}`} value={c.code} disabled={!canWrite} onChange={(e) => setCode(i, { code: e.target.value })} /></Field>
          <Field id={`tc-label-${i}`} label="Description"><input id={`tc-label-${i}`} value={c.label} disabled={!canWrite} onChange={(e) => setCode(i, { label: e.target.value })} /></Field>
          <Field id={`tc-rate-${i}`} label="Rate (%)"><input id={`tc-rate-${i}`} type="number" step="0.01" min="0" max="100" list="tc-rates" value={c.rate} disabled={!canWrite} onChange={(e) => setCode(i, { rate: e.target.value })} /></Field>
          <Field id={`tc-hsn-${i}`} label="HSN/SAC"><input id={`tc-hsn-${i}`} value={c.hsn_sac} disabled={!canWrite} onChange={(e) => setCode(i, { hsn_sac: e.target.value })} /></Field>
          <Field id={`tc-from-${i}`} label="Effective from"><input id={`tc-from-${i}`} type="date" value={c.effective_from} disabled={!canWrite} onChange={(e) => setCode(i, { effective_from: e.target.value })} /></Field>
          <Field id={`tc-to-${i}`} label="Effective to"><input id={`tc-to-${i}`} type="date" value={c.effective_to} disabled={!canWrite} onChange={(e) => setCode(i, { effective_to: e.target.value })} /></Field>
          {canWrite && <button type="button" className="btn btn-small" onClick={() => setCodes(codes.filter((_, j) => j !== i))}>Remove</button>}
        </div>
      ))}
      <datalist id="tc-rates">{allowed.current.map((r) => <option key={r} value={r} />)}</datalist>
      {canWrite && <button type="button" className="btn btn-small" onClick={() => setCodes([...codes, { ...BLANK_CODE }])}>Add tax code</button>}
      {codeErrors.length > 0 && <div className="notice notice-error" role="alert"><ul>{codeErrors.map((m) => <li key={m}>{m}</li>)}</ul></div>}

      {canWrite && (
        <div className="actions">
          <button type="submit" className="btn btn-primary" disabled={busy || (!form.dirty.length && !codesChanged)}>{busy ? 'Saving…' : 'Save tax configuration'}</button>
          <button type="button" className="btn" disabled={busy} onClick={() => { form.reset(); setCodes(toEditable(data.tax_codes)); setCodeErrors([]); }}>Cancel</button>
        </div>
      )}
      {reasonDialog}
      {confirmDialog}
    </form>
  );
}

export function GstCalculator() {
  const lookups = useLookups();
  const [v, setV] = useState({ taxable_value: '', rate: '', supplier_state_code: '', place_of_supply_code: '' });
  const [errors, setErrors] = useState({});
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    const errs = {};
    if (v.taxable_value === '' || Number(v.taxable_value) < 0) errs.taxable_value = 'Enter a taxable value of 0 or more.';
    if (v.rate === '' || Number(v.rate) < 0 || Number(v.rate) > 100) errs.rate = 'Enter a rate between 0 and 100.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    setError(null);
    try {
      const body = { taxable_value: v.taxable_value, rate: v.rate };
      if (v.supplier_state_code) body.supplier_state_code = v.supplier_state_code;
      if (v.place_of_supply_code) body.place_of_supply_code = v.place_of_supply_code;
      setResult(await api.post('/company/tax-settings/calculate/', body));
    } catch (err) { setResult(null); setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  const stateSelect = (name, label, hint) => (
    <Field id={`calc-${name}`} label={label} hint={hint} error={errors[name]}>
      <select id={`calc-${name}`} value={v[name]} onChange={(e) => setV({ ...v, [name]: e.target.value })}>
        <option value="">— default —</option>
        {lookups.gst_states.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
      </select>
    </Field>
  );
  const rows = result && [
    ['Taxable value', result.taxable_value], ['CGST', result.cgst], ['SGST', result.sgst], ['UTGST', result.utgst], ['IGST', result.igst],
    ['Total tax', result.total_tax], ['Grand total', result.grand_total],
  ];
  return (
    <form onSubmit={submit} noValidate className="card">
      <h2>GST calculator</h2>
      <p className="muted">Checks how tax would split for a sale, using the company's configured GST treatment. Nothing is saved.</p>
      <FormErrors error={error} fields={[{ name: 'taxable_value' }, { name: 'rate' }, { name: 'supplier_state_code' }, { name: 'place_of_supply_code' }]} />
      <div className="form-grid">
        <Field id="calc-taxable" label="Taxable value (INR)" required error={errors.taxable_value}>
          <input id="calc-taxable" type="number" min="0" step="0.01" value={v.taxable_value} onChange={(e) => setV({ ...v, taxable_value: e.target.value })} />
        </Field>
        <Field id="calc-rate" label="GST rate (%)" required error={errors.rate}>
          <input id="calc-rate" type="number" min="0" max="100" step="0.01" value={v.rate} onChange={(e) => setV({ ...v, rate: e.target.value })} />
        </Field>
        {stateSelect('supplier_state_code', 'Supplier state', 'Defaults to the company GSTIN / registered office state.')}
        {stateSelect('place_of_supply_code', 'Place of supply', 'Defaults to the configured default place of supply.')}
      </div>
      <div className="actions"><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Calculating…' : 'Calculate'}</button></div>
      {result && (
        <div className="table-wrap" aria-live="polite">
          <table>
            <caption>{result.supply_type === 'intra_state' ? 'Intra-state supply (CGST + SGST/UTGST)' : result.supply_type === 'inter_state' ? 'Inter-state supply (IGST)' : 'No tax charged under the configured GST treatment'}</caption>
            <tbody>{rows.map(([k, val]) => <tr key={k}><th scope="row">{k}</th><td className="num">{val}</td></tr>)}</tbody>
          </table>
        </div>
      )}
    </form>
  );
}
