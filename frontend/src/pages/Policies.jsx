import { useMemo, useState } from 'react';
import { api } from '../api';
import { useAuth, useLookups } from '../context';
import { changedLabels } from '../forms';
import { asList, useLoad } from '../hooks';
import { POLICY_FIELDS } from '../specs';
import {
  Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, SpecForm, fmtDate, useConfirm, useReasonDialog, useReasonSubmit,
  useSpecForm, useToast,
} from '../ui';

const STATUS_TONE = { active: 'good', draft: 'warn', superseded: 'neutral' };

const toRows = (list) => (list || []).map((t) => ({ scope: t.scope || '', amount: t.amount ?? '', approver_role: t.approver_role || '' }));
const retentionRows = (obj) => Object.entries(obj || {}).map(([type, years]) => ({ type, years }));

function validateRows(thresholds, retention) {
  const errs = [];
  thresholds.forEach((t, i) => {
    if (!t.scope.trim()) errs.push(`Threshold ${i + 1}: enter what it applies to.`);
    if (t.amount === '' || Number.isNaN(Number(t.amount)) || Number(t.amount) < 0) errs.push(`Threshold ${i + 1}: enter a non-negative amount.`);
  });
  const seen = new Set();
  retention.forEach((r, i) => {
    if (!r.type) errs.push(`Retention ${i + 1}: choose a document type.`);
    if (seen.has(r.type)) errs.push(`Retention ${i + 1}: duplicate document type.`);
    seen.add(r.type);
    const y = Number(r.years);
    if (!Number.isInteger(y) || y < 1 || y > 99) errs.push(`Retention ${i + 1}: years must be a whole number from 1 to 99.`);
  });
  return errs;
}

function PolicyDialog({ policy, canEdit, onClose, onSaved }) {
  const lookups = useLookups();
  const notify = useToast();
  const { run, dialog: reasonDialog } = useReasonSubmit();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const form = useSpecForm(POLICY_FIELDS, policy);
  const [thresholds, setThresholds] = useState(() => toRows(policy.approval_thresholds));
  const [retention, setRetention] = useState(() => retentionRows(policy.retention_settings));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [rowErrors, setRowErrors] = useState([]);

  const thresholdsChanged = JSON.stringify(thresholds) !== JSON.stringify(toRows(policy.approval_thresholds));
  const retentionChanged = JSON.stringify(retention) !== JSON.stringify(retentionRows(policy.retention_settings));

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    const okForm = form.validate();
    const rowErrs = validateRows(thresholds, retention);
    setRowErrors(rowErrs);
    if (!okForm || rowErrs.length) return;
    const payload = form.payload(true);
    if (thresholdsChanged) payload.approval_thresholds = thresholds.map((t) => ({ scope: t.scope.trim(), amount: String(t.amount), approver_role: t.approver_role.trim() }));
    if (retentionChanged) payload.retention_settings = Object.fromEntries(retention.map((r) => [r.type, Number(r.years)]));
    if (!Object.keys(payload).length) { notify('No changes to save.', 'info'); return; }
    const labels = changedLabels(POLICY_FIELDS, Object.keys(payload));
    if (thresholdsChanged) labels.push('Approval thresholds');
    if (retentionChanged) labels.push('Document retention');
    const ok = await confirm({ title: 'Save this draft?', body: <ul>{labels.map((l) => <li key={l}>{l}</li>)}</ul>, confirmLabel: 'Save draft' });
    if (!ok) return;
    setBusy(true);
    try {
      await run((extra) => api.patch(`/company/policies/${policy.id}/`, { ...payload, ...extra }));
      notify('Policy draft saved.');
      onSaved();
    } catch (err) {
      if (!err.cancelled) { setError(err); form.setErrors(err.fieldErrors || {}); }
    }
    setBusy(false);
  };

  return (
    <Dialog title={`Policy version ${policy.version_number} (${policy.status})`} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        {!canEdit && <Notice>{policy.status === 'draft' ? 'Only administrators can edit drafts.' : 'Active and superseded versions are fixed so issued documents stay accurate. Create a new version to change the terms.'}</Notice>}
        <FormErrors error={error} fields={POLICY_FIELDS} />
        <SpecForm fields={POLICY_FIELDS} values={form.values} onChange={form.set} errors={form.errors} readOnly={!canEdit} ctx={{ lookups }} idPrefix="pol" />

        <h3>Internal approval thresholds</h3>
        {thresholds.map((t, i) => (
          <div className="row-edit" key={i}>
            <Field id={`th-scope-${i}`} label="Applies to"><input id={`th-scope-${i}`} list="th-scopes" value={t.scope} disabled={!canEdit}
              onChange={(e) => setThresholds(thresholds.map((x, j) => (j === i ? { ...x, scope: e.target.value } : x)))} /></Field>
            <Field id={`th-amt-${i}`} label="Amount above"><input id={`th-amt-${i}`} type="number" min="0" step="0.01" value={t.amount} disabled={!canEdit}
              onChange={(e) => setThresholds(thresholds.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))} /></Field>
            <Field id={`th-role-${i}`} label="Approver"><input id={`th-role-${i}`} value={t.approver_role} disabled={!canEdit}
              onChange={(e) => setThresholds(thresholds.map((x, j) => (j === i ? { ...x, approver_role: e.target.value } : x)))} /></Field>
            {canEdit && <button type="button" className="btn btn-small" onClick={() => setThresholds(thresholds.filter((_, j) => j !== i))}>Remove</button>}
          </div>
        ))}
        <datalist id="th-scopes">{lookups.document_types.map((d) => <option key={d.value} value={d.value} />)}</datalist>
        {canEdit && <button type="button" className="btn btn-small" onClick={() => setThresholds([...thresholds, { scope: '', amount: '', approver_role: '' }])}>Add threshold</button>}

        <h3>Document retention</h3>
        {retention.map((r, i) => (
          <div className="row-edit" key={i}>
            <Field id={`rt-type-${i}`} label="Document type">
              <select id={`rt-type-${i}`} value={r.type} disabled={!canEdit} onChange={(e) => setRetention(retention.map((x, j) => (j === i ? { ...x, type: e.target.value } : x)))}>
                <option value="">— choose —</option>
                {lookups.document_types.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
              </select>
            </Field>
            <Field id={`rt-yrs-${i}`} label="Keep for (years)"><input id={`rt-yrs-${i}`} type="number" min="1" max="99" value={r.years} disabled={!canEdit}
              onChange={(e) => setRetention(retention.map((x, j) => (j === i ? { ...x, years: e.target.value } : x)))} /></Field>
            {canEdit && <button type="button" className="btn btn-small" onClick={() => setRetention(retention.filter((_, j) => j !== i))}>Remove</button>}
          </div>
        ))}
        {canEdit && <button type="button" className="btn btn-small" onClick={() => setRetention([...retention, { type: '', years: '' }])}>Add retention rule</button>}

        {rowErrors.length > 0 && <div className="notice notice-error" role="alert"><ul>{rowErrors.map((m) => <li key={m}>{m}</li>)}</ul></div>}
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>{canEdit ? 'Cancel' : 'Close'}</button>
          {canEdit && <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save draft'}</button>}
        </div>
      </form>
      {reasonDialog}
      {confirmDialog}
    </Dialog>
  );
}

export function PoliciesManager() {
  const { user } = useAuth();
  const notify = useToast();
  const canWrite = user.is_company_admin;
  const { data, error, loading, reload } = useLoad(() => api.get('/company/policies/'), []);
  const { ask, dialog: askDialog } = useReasonDialog();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [open, setOpen] = useState(null);
  const rows = useMemo(() => asList(data), [data]);
  const active = rows.find((p) => p.status === 'active');

  const create = async (copy) => {
    const summary = await ask({ title: copy ? 'New version from the active terms' : 'New blank draft', label: 'Summary of this version', required: false, confirmLabel: 'Create draft' });
    if (summary === null) return;
    try {
      const created = await api.post('/company/policies/', { ...(copy ? { copy_from_current: true } : {}), change_summary: summary });
      notify('Draft created. Edit it, then activate it when it is ready.');
      reload();
      setOpen(created);
    } catch (e) { notify(e.message, 'error'); }
  };

  const activate = async (p) => {
    const ok = await confirm({
      title: `Activate version ${p.version_number}?`,
      body: active
        ? `Version ${active.version_number} will be superseded from the effective date. Documents already issued keep the terms they were issued with.`
        : 'These become the default terms for new quotations and invoices.',
      confirmLabel: 'Activate',
    });
    if (!ok) return;
    const reason = await ask({ title: 'Activation note', label: 'Note', required: false, confirmLabel: 'Activate' });
    if (reason === null) return;
    try {
      await api.post(`/company/policies/${p.id}/activate/`, { change_reason: reason });
      notify(`Version ${p.version_number} is now active.`);
      reload();
    } catch (e) { notify(e.message, 'error'); }
  };

  const discard = async (p) => {
    if (!(await confirm({ title: `Delete draft version ${p.version_number}?`, confirmLabel: 'Delete draft', danger: true }))) return;
    try { await api.del(`/company/policies/${p.id}/`); notify('Draft deleted.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };

  return (
    <section className="card" aria-label="Company policies">
      <div className="card-head">
        <div>
          <h2>Policy versions</h2>
          <p className="muted">Terms are versioned. Activating a new version never changes documents already issued.</p>
        </div>
        {canWrite && (
          <div className="page-actions">
            {active && <button type="button" className="btn btn-primary" onClick={() => create(true)}>New version from active</button>}
            <button type="button" className={`btn${active ? '' : ' btn-primary'}`} onClick={() => create(false)}>New blank draft</button>
          </div>
        )}
      </div>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {!loading && !rows.length && <p className="empty">No policy versions yet. Create a draft to set default payment terms.</p>}
      {rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Version</th><th>Status</th><th>Effective</th><th>Summary</th><th>Payment term</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id}>
                  <td data-label="Version"><strong>v{p.version_number}</strong></td>
                  <td data-label="Status"><Badge tone={STATUS_TONE[p.status]}>{p.status}</Badge></td>
                  <td data-label="Effective">{p.effective_from ? `${fmtDate(p.effective_from)} – ${p.effective_to ? fmtDate(p.effective_to) : 'current'}` : '—'}</td>
                  <td data-label="Summary">{p.change_summary || '—'}</td>
                  <td data-label="Payment term">{p.payment_terms_days != null ? `${p.payment_terms_days} days` : '—'}</td>
                  <td className="row-actions">
                    <button type="button" className="btn btn-small" onClick={() => setOpen(p)}>{canWrite && p.status === 'draft' ? 'Edit' : 'View'}</button>
                    {canWrite && p.status === 'draft' && <button type="button" className="btn btn-small" onClick={() => activate(p)}>Activate</button>}
                    {canWrite && p.status === 'draft' && <button type="button" className="btn btn-small btn-danger-outline" onClick={() => discard(p)}>Delete</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && <PolicyDialog key={open.id} policy={open} canEdit={canWrite && open.status === 'draft'} onClose={() => setOpen(null)} onSaved={() => { setOpen(null); reload(); }} />}
      {askDialog}
      {confirmDialog}
    </section>
  );
}
