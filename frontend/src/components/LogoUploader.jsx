import { useRef, useState } from 'react';
import { api } from '../api';
import { useBlobUrl } from '../hooks';
import { useConfirm, useToast, Loading, ErrorBox } from '../ui';

const MAX_BYTES = 10 * 1024 * 1024;

// Authenticated logo upload + preview. `path` serves GET (image), POST (upload), DELETE (remove).
export default function LogoUploader({ title, path, canWrite, removable = true, note, onChanged }) {
  const [version, setVersion] = useState(0);
  const { url, loading } = useBlobUrl(path, [version]);
  const input = useRef(null);
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const upload = async (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!file) return;
    setError(null);
    if (!['image/png', 'image/jpeg'].includes(file.type)) { setError(new Error('Choose a PNG or JPEG image.')); return; }
    if (file.size > MAX_BYTES) { setError(new Error('The image is larger than 10 MB.')); return; }
    setBusy(true);
    try {
      const form = new FormData();
      form.append('file', file);
      await api.upload(path, form);
      setVersion((v) => v + 1);
      notify(`${title} uploaded.`);
      onChanged && onChanged();
    } catch (err) {
      setError(err.fieldErrors && err.fieldErrors.file ? new Error(err.fieldErrors.file) : err);
    }
    setBusy(false);
  };

  const remove = async () => {
    if (!(await confirm({ title: `Remove ${title.toLowerCase()}?`, body: 'Newly generated documents will no longer use it. Issued documents keep their own copy.', confirmLabel: 'Remove', danger: true }))) return;
    setBusy(true);
    try {
      await api.del(path);
      setVersion((v) => v + 1);
      notify(`${title} removed.`);
      onChanged && onChanged();
    } catch (err) { setError(err); }
    setBusy(false);
  };

  return (
    <div className="logo-box">
      <h3>{title}</h3>
      <div className="logo-preview" aria-live="polite">
        {loading ? <Loading label="Loading image…" /> : url ? <img src={url} alt={`${title} preview`} /> : <span className="muted">No image uploaded</span>}
      </div>
      {note && <p className="hint">{note}</p>}
      <ErrorBox error={error} />
      {canWrite && (
        <div className="actions">
          <input ref={input} type="file" accept="image/png,image/jpeg" onChange={upload} hidden aria-label={`Choose ${title} file`} />
          <button type="button" className="btn" disabled={busy} onClick={() => input.current.click()}>{url ? 'Replace image' : 'Upload image'}</button>
          {removable && url && <button type="button" className="btn btn-danger-outline" disabled={busy} onClick={remove}>Remove</button>}
        </div>
      )}
      {dialog}
    </div>
  );
}
