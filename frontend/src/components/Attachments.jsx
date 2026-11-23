import { useRef, useState } from 'react';
import { api, downloadFile } from '../api';
import { useLoad } from '../hooks';
import { Badge, Field, FormErrors, Loading, fmtDate, useConfirm, useToast } from '../ui';

const size = (n) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / (1024 * 1024)).toFixed(1)} MB`);

// Files attached to a record (an employee, a vendor, a project ...). The record type must be registered on the server with
// apps.attachments.registry. `canManage` shows the upload form and the remove buttons.
export default function Attachments({ entityType, entityId, canManage = false, title = 'Documents', hint }) {
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const { data, error, loading, reload } = useLoad(
    () => api.get('/attachments/', { entity_type: entityType, entity_id: entityId }), [entityType, entityId]);
  const [name, setName] = useState('');
  const [expiry, setExpiry] = useState('');
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const input = useRef(null);

  const upload = async (e) => {
    e.preventDefault();
    setFailure(null);
    const file = input.current?.files?.[0];
    if (!file) { setFailure({ message: 'Choose the file to attach.', fieldErrors: {} }); return; }
    setBusy(true);
    try {
      const body = new FormData();
      body.append('entity_type', entityType);
      body.append('entity_id', entityId);
      body.append('file', file);
      if (name.trim()) body.append('title', name.trim());
      if (expiry) body.append('expiry_date', expiry);
      await api.upload('/attachments/', body);
      notify('File attached.');
      setName(''); setExpiry(''); input.current.value = '';
      reload();
    } catch (err) { setFailure(err); }
    setBusy(false);
  };
  const remove = async (a) => {
    if (!(await confirm({ title: `Remove "${a.title}"?`, body: <p>The file is deleted. This is recorded in the change history.</p>, confirmLabel: 'Remove', danger: true }))) return;
    try { await api.del(`/attachments/${a.id}/`); notify('File removed.'); reload(); } catch (err) { notify(err.message, 'error'); }
  };
  const rows = Array.isArray(data) ? data : [];

  return (
    <section className="card" aria-label={title}>
      <h2>{title}</h2>
      {hint && <p className="muted">{hint}</p>}
      {loading && <Loading />}
      {error && <p className="error-text" role="alert">{error.message}</p>}
      {!loading && !error && rows.length === 0 && <p className="empty">Nothing attached yet.</p>}
      {rows.length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Name</th><th>Added</th><th>Expires</th><th className="amount">Size</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.id}>
                <td data-label="Name"><strong>{a.title}</strong><div className="hint">{a.original_filename}</div></td>
                <td data-label="Added">{fmtDate(a.created_at.slice(0, 10))}<div className="hint">{a.uploaded_by}</div></td>
                <td data-label="Expires">{a.expiry_date ? <>{fmtDate(a.expiry_date)} {a.is_expired && <Badge tone="bad">Expired</Badge>}</> : '—'}</td>
                <td className="amount" data-label="Size">{size(a.file_size)}</td>
                <td className="row-actions">
                  <button type="button" className="btn btn-small" onClick={() => downloadFile(`/attachments/${a.id}/download/`, a.original_filename).catch((e) => notify(e.message, 'error'))}>Download</button>
                  {canManage && <button type="button" className="btn btn-small" onClick={() => remove(a)}>Remove</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {canManage && (
        <form className="subcard" onSubmit={upload} noValidate>
          <FormErrors error={failure} fields={[{ name: 'file' }, { name: 'entity_id' }, { name: 'expiry_date' }]} />
          <div className="toolbar">
            <Field id={`att-file-${entityType}`} label="File (PDF, PNG or JPEG)"><input id={`att-file-${entityType}`} ref={input} type="file" accept="application/pdf,image/png,image/jpeg" /></Field>
            <Field id={`att-name-${entityType}`} label="Name" className="grow"><input id={`att-name-${entityType}`} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} placeholder="Optional; the file name is used if empty" /></Field>
            <Field id={`att-exp-${entityType}`} label="Expires"><input id={`att-exp-${entityType}`} type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} /></Field>
            <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Uploading…' : 'Attach file'}</button>
          </div>
        </form>
      )}
      {dialog}
    </section>
  );
}
