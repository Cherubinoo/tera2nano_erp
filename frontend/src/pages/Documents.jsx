import { useRef, useState } from 'react';
import { api, downloadFile } from '../api';
import { useAuth, useLookups } from '../context';
import { buildPayload } from '../forms';
import { asList, useLoad, useOptions } from '../hooks';
import { DOCUMENT_FIELDS } from '../specs';
import {
  Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, SpecForm, VerificationBadge, fmtDate, fmtDateTime, useConfirm,
  useReasonSubmit, useSpecForm, useToast,
} from '../ui';

const MAX_BYTES = 10 * 1024 * 1024;
const ALLOWED = ['application/pdf', 'image/png', 'image/jpeg'];
const NEW_DEFAULTS = { access_classification: 'confidential' };
const EDIT_FIELDS = DOCUMENT_FIELDS.filter((f) => f.name !== 'document_type');

function DocumentDialog({ mode, record, onClose, onSaved }) {
  const lookups = useLookups();
  const { user } = useAuth();
  const notify = useToast();
  const { run, dialog: reasonDialog } = useReasonSubmit();
  const partners = useOptions('/company/partners/', (p) => p.full_name);
  const registrations = useOptions('/company/tax-registrations/', (r) => `${r.registration_type.toUpperCase()} ${r.registration_number}`);
  const bankAccounts = useOptions('/company/bank-accounts/', (b) => b.display_name);
  const addresses = useOptions('/company/addresses/', (a) => a.label);
  const fields = mode === 'edit' ? EDIT_FIELDS : DOCUMENT_FIELDS;
  const form = useSpecForm(fields, record, mode === 'new' ? NEW_DEFAULTS : undefined);
  const fileInput = useRef(null);
  const [file, setFile] = useState(null);
  const [fileError, setFileError] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const ctx = { lookups, isAdmin: user.is_company_admin, partners, registrations, bankAccounts, addresses };

  const pick = (e) => {
    const f = e.target.files && e.target.files[0];
    setFileError('');
    if (!f) { setFile(null); return; }
    if (!ALLOWED.includes(f.type)) { setFile(null); setFileError('Only PDF, PNG or JPEG files are accepted.'); return; }
    if (f.size > MAX_BYTES) { setFile(null); setFileError('The file is larger than 10 MB.'); return; }
    setFile(f);
    if (!form.values.title) form.set('title', f.name.replace(/\.[^.]+$/, ''));
  };

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    const okForm = form.validate();
    if (mode !== 'edit' && !file) { setFileError('Choose the file to upload.'); return; }
    if (!okForm) return;
    setBusy(true);
    try {
      if (mode === 'edit') {
        const payload = form.payload(true);
        if (!Object.keys(payload).length) { notify('No changes to save.', 'info'); onClose(); return; }
        await run((extra) => api.patch(`/company/documents/${record.id}/`, { ...payload, ...extra }));
        notify('Document details updated.');
      } else {
        const payload = buildPayload(fields, form.values);
        const body = new FormData();
        body.append('file', file);
        Object.entries(payload).forEach(([k, v]) => { if (v !== null && v !== '') body.append(k, v); });
        if (mode === 'version') body.append('replaces', record.id);
        await api.upload('/company/documents/', body);
        notify(mode === 'version' ? `Version ${record.version + 1} uploaded.` : 'Document uploaded.');
      }
      onSaved();
    } catch (err) {
      if (!err.cancelled) { setError(err); form.setErrors(err.fieldErrors || {}); if (err.fieldErrors?.file) setFileError(err.fieldErrors.file); }
    }
    setBusy(false);
  };

  const title = mode === 'edit' ? 'Edit document details' : mode === 'version' ? `Upload new version of "${record.title}"` : 'Upload document';
  return (
    <Dialog title={title} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={fields} />
        {mode !== 'edit' && (
          <Field id="doc-file" label="File" required error={fileError} hint="PDF, PNG or JPEG, up to 10 MB. Stored privately; downloads require sign-in.">
            <input id="doc-file" ref={fileInput} type="file" accept="application/pdf,image/png,image/jpeg" onChange={pick}
              aria-describedby={fileError ? 'doc-file-err' : 'doc-file-hint'} aria-invalid={!!fileError || undefined} />
          </Field>
        )}
        <SpecForm fields={fields} values={form.values} onChange={form.set} errors={form.errors} ctx={ctx} idPrefix="doc" />
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : mode === 'edit' ? 'Save' : 'Upload'}</button>
        </div>
      </form>
      {reasonDialog}
    </Dialog>
  );
}

function HistoryDialog({ doc, onClose }) {
  const notify = useToast();
  const { data, error, loading } = useLoad(() => api.get('/company/documents/', { history: 'true', group: doc.document_group }), [doc.document_group]);
  const rows = asList(data).sort((a, b) => b.version - a.version);
  return (
    <Dialog title={`Versions – ${doc.title}`} onClose={onClose} wide>
      {loading && <Loading />}
      <ErrorBox error={error} />
      <div className="table-wrap">
        <table>
          <thead><tr><th>Version</th><th>Uploaded</th><th>By</th><th>Checksum (SHA-256)</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td data-label="Version">v{r.version} {r.is_current && <Badge tone="info">Current</Badge>}</td>
                <td data-label="Uploaded">{fmtDateTime(r.created_at)}</td>
                <td data-label="By">{r.uploaded_by_name || '—'}</td>
                <td data-label="Checksum"><code title={r.checksum_sha256}>{r.checksum_sha256.slice(0, 16)}…</code></td>
                <td className="row-actions">
                  <button type="button" className="btn btn-small" onClick={() => downloadFile(`/company/documents/${r.id}/download/`, r.original_filename).catch((e) => notify(e.message, 'error'))}>Download</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Close</button></div>
    </Dialog>
  );
}

export function DocumentsManager() {
  const { user } = useAuth();
  const lookups = useLookups();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const canWrite = user.is_finance;
  const [type, setType] = useState('');
  const { data, error, loading, reload } = useLoad(() => api.get('/company/documents/', { document_type: type }), [type]);
  const [dialog, setDialog] = useState(null); // {mode, record}
  const [history, setHistory] = useState(null);
  const rows = asList(data);
  const labelOf = (list, v) => list.find((o) => o.value === v)?.label || v;

  const download = (d) => downloadFile(`/company/documents/${d.id}/download/`, d.original_filename).catch((e) => notify(e.message, 'error'));
  const verify = async (d, decision) => {
    const ok = await confirm({
      title: decision === 'verified' ? 'Mark this document as verified?' : 'Reject this document?',
      body: `${d.title} (v${d.version})`, confirmLabel: decision === 'verified' ? 'Verify' : 'Reject', danger: decision !== 'verified',
    });
    if (!ok) return;
    try { await api.post(`/company/documents/${d.id}/verify/`, { decision }); notify(decision === 'verified' ? 'Document verified.' : 'Document rejected.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };

  return (
    <section className="card" aria-label="Company documents">
      <div className="card-head">
        <div>
          <h2>Document repository</h2>
          <p className="muted">Certificates, agreements and proofs. Files are stored privately and served only to signed-in users with access.</p>
        </div>
        <div className="page-actions">
          <div className="field inline">
            <label htmlFor="doc-filter">Filter by type</label>
            <select id="doc-filter" value={type} onChange={(e) => setType(e.target.value)}>
              <option value="">All types</option>
              {lookups.document_kinds.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </select>
          </div>
          {canWrite && <button type="button" className="btn btn-primary" onClick={() => setDialog({ mode: 'new', record: null })}>Upload document</button>}
        </div>
      </div>
      {!canWrite && <Notice>You can see and download documents classified for your role. Uploading needs a finance or administrator role.</Notice>}
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {!loading && !error && !rows.length && <p className="empty">No documents yet.</p>}
      {rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Title</th><th>Type</th><th>Version</th><th>Access</th><th>Expiry</th><th>Verification</th><th>Uploaded</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.id}>
                  <td data-label="Title"><strong>{d.title}</strong><div className="hint">{d.original_filename}</div></td>
                  <td data-label="Type">{d.document_type_label}</td>
                  <td data-label="Version">v{d.version}</td>
                  <td data-label="Access">{labelOf(lookups.access_classes, d.access_classification)}</td>
                  <td data-label="Expiry">{d.expiry_date ? <>{fmtDate(d.expiry_date)} {d.is_expired && <Badge tone="bad">Expired</Badge>}</> : '—'}</td>
                  <td data-label="Verification"><VerificationBadge status={d.verification_status} /></td>
                  <td data-label="Uploaded">{fmtDate(d.created_at?.slice(0, 10))}<div className="hint">{d.uploaded_by_name}</div></td>
                  <td className="row-actions">
                    <button type="button" className="btn btn-small" onClick={() => download(d)}>Download</button>
                    <button type="button" className="btn btn-small" onClick={() => setHistory(d)}>Versions</button>
                    {canWrite && <button type="button" className="btn btn-small" onClick={() => setDialog({ mode: 'edit', record: d })}>Edit</button>}
                    {canWrite && <button type="button" className="btn btn-small" onClick={() => setDialog({ mode: 'version', record: { ...d, notes: '' } })}>New version</button>}
                    {user.is_company_admin && d.verification_status !== 'verified' && <button type="button" className="btn btn-small" onClick={() => verify(d, 'verified')}>Verify</button>}
                    {user.is_company_admin && d.verification_status !== 'rejected' && <button type="button" className="btn btn-small btn-danger-outline" onClick={() => verify(d, 'rejected')}>Reject</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {dialog && <DocumentDialog mode={dialog.mode} record={dialog.record}
        onClose={() => setDialog(null)} onSaved={() => { setDialog(null); reload(); }} />}
      {history && <HistoryDialog doc={history} onClose={() => setHistory(null)} />}
      {confirmDialog}
    </section>
  );
}
