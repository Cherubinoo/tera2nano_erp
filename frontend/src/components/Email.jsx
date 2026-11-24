import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../context';
import { Badge, Dialog, Field, FormErrors, Loading, Notice, ErrorBox, fmtDateTime } from '../ui';

// Sending email from a record's page. The dialog opens prefilled by the server (to, cc, subject, message, attached PDF);
// the person checks and edits it, then sends. A failed send is shown here and kept in Administration > Sent emails.

export const EMAIL_TONE = { sent: 'good', saved: 'warn', failed: 'bad' };
export const NOT_SET_UP = 'Email is not set up on the server; messages are saved but not delivered.';

export function EmailStatusBadge({ status, label }) {
  return <Badge tone={EMAIL_TONE[status] || 'neutral'}>{label || status}</Badge>;
}

// Whether a mail server is set up. Loaded once per page load and shared.
let statusPromise = null;
export function useEmailServer() {
  const [server, setServer] = useState(null);
  useEffect(() => {
    let alive = true;
    statusPromise = statusPromise || api.get('/email/status/').catch((e) => { statusPromise = null; throw e; });
    statusPromise.then((s) => alive && setServer(s)).catch(() => alive && setServer({ configured: false, message: NOT_SET_UP }));
    return () => { alive = false; };
  }, []);
  return server;
}

const asText = (list) => (Array.isArray(list) ? list.join(', ') : list || '');
const asList = (text) => text.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean);

function Result({ message }) {
  if (message.status === 'failed') {
    return (
      <Notice type="error">
        <span><strong>The email was not sent.</strong> {message.error} Nothing else was changed. You can send it again from Administration, Sent emails, or fix the problem and try again here.</span>
      </Notice>
    );
  }
  if (message.status === 'saved') {
    return <Notice type="warn"><span><strong>Saved, not delivered.</strong> {NOT_SET_UP} Ask your administrator to set up email, or send the PDF yourself.</span></Notice>;
  }
  return <Notice type="success"><span><strong>Email sent</strong> to {asText(message.to)}{message.cc?.length ? ` (copy to ${asText(message.cc)})` : ''}.</span></Notice>;
}

// kind/id: what to send (see the server's apps/emails/kinds.py). `load` and `submit` replace the standard draft and send
// calls (the invitation link uses its own). onSent(message) runs after every send attempt.
export function EmailDialog({ kind, id, title, onClose, onSent, load, submit }) {
  const [draft, setDraft] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [v, setV] = useState({ to: '', cc: '', subject: '', body: '' });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(null);
  useEffect(() => {
    let alive = true;
    (load ? load() : api.get('/email/draft/', { kind, id }))
      .then((d) => { if (!alive) return; setDraft(d); setV({ to: asText(d.to), cc: asText(d.cc), subject: d.subject, body: d.body }); })
      .catch((e) => alive && setLoadError(e));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, id]);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const send = async (e) => {
    e.preventDefault(); e.stopPropagation();
    const errs = {};
    if (!asList(v.to).length) errs.to = 'Enter at least one email address to send to.';
    if (!v.subject.trim()) errs.subject = 'Write a subject.';
    if (!v.body.trim()) errs.body = 'Write the message.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true); setFailure(null);
    const payload = { to: asList(v.to), cc: asList(v.cc), subject: v.subject, body: v.body };
    try {
      const message = await (submit ? submit(payload) : api.post('/email/send/', { kind, id, ...payload }));
      setSent(message);
      onSent && onSent(message);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  const server = draft?.server;
  return (
    <Dialog title={title || 'Send by email'} onClose={onClose} wide>
      {!draft && !loadError && <Loading label="Preparing the message…" />}
      {loadError && (
        <>
          <ErrorBox error={loadError} />
          <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Close</button></div>
        </>
      )}
      {draft && sent && (
        <>
          <Result message={sent} />
          <div className="dialog-foot-inline">
            {sent.status === 'failed' && <button type="button" className="btn" onClick={() => setSent(null)}>Edit and try again</button>}
            <button type="button" className="btn btn-primary" onClick={onClose}>Done</button>
          </div>
        </>
      )}
      {draft && !sent && (
        <form onSubmit={send} noValidate className="email-form">
          {server && !server.configured && <Notice type="warn">{server.message || NOT_SET_UP}</Notice>}
          <FormErrors error={failure} fields={[{ name: 'to' }, { name: 'cc' }, { name: 'subject' }, { name: 'body' }]} />
          {draft.label && <p className="muted">{draft.label}</p>}
          <div className="form-grid">
            <Field id="em-to" label="To" required wide error={errors.to} hint="Separate several addresses with commas.">
              <input id="em-to" type="text" inputMode="email" autoComplete="off" value={v.to} onChange={set('to')} aria-invalid={!!errors.to || undefined} />
            </Field>
            <Field id="em-cc" label="Copy to (optional)" wide error={errors.cc}>
              <input id="em-cc" type="text" inputMode="email" autoComplete="off" value={v.cc} onChange={set('cc')} />
            </Field>
            <Field id="em-subject" label="Subject" required wide error={errors.subject}>
              <input id="em-subject" value={v.subject} maxLength={300} onChange={set('subject')} />
            </Field>
            <Field id="em-body" label="Message" required wide error={errors.body}>
              <textarea id="em-body" rows={10} value={v.body} onChange={set('body')} />
            </Field>
          </div>
          {draft.attachments?.length > 0 && (
            <p className="email-attach"><span className="muted">Attached:</span> {draft.attachments.map((a) => <span key={a} className="email-file">{a}</span>)}</p>
          )}
          {draft.from_address && <p className="hint">Sent from {draft.from_address}.</p>}
          {draft.history?.length > 0 && (
            <details className="email-history">
              <summary>Sent before ({draft.history.length})</summary>
              <ul>{draft.history.map((h) => (
                <li key={h.id}>{fmtDateTime(h.created_at)} · {asText(h.to)} · <EmailStatusBadge status={h.status} label={h.status_label} />{h.error ? ` ${h.error}` : ''}</li>
              ))}</ul>
            </details>
          )}
          <div className="dialog-foot-inline">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Sending…' : 'Send email'}</button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

// A button that opens the dialog. Shown only to people holding `perm` (the same permission as the record's own action).
export function EmailButton({ kind, id, perm, label = 'Email', title, small = false, onSent }) {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  if (perm && !(user.permissions || []).includes(perm)) return null;
  return (
    <>
      <button type="button" className={`btn${small ? ' btn-small' : ''}`} onClick={(e) => { e.stopPropagation(); setOpen(true); }}>{label}</button>
      {open && <EmailDialog kind={kind} id={id} title={title} onClose={() => setOpen(false)} onSent={onSent} />}
    </>
  );
}

// A one-line note for pages that send email: says plainly when email is not set up.
export function EmailServerNote({ children }) {
  const server = useEmailServer();
  const { user } = useAuth();
  if (!server || server.configured) return children || null;
  return (
    <Notice type="warn">
      <span>{server.message || NOT_SET_UP}{(user.permissions || []).includes('email.manage') && <> <Link to="/company/email">Email settings</Link></>}</span>
    </Notice>
  );
}
