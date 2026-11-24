import { useState } from 'react';
import { api } from '../api';
import { EmailDialog } from '../components/Email';
import { asList, useLoad } from '../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, fmtDateTime, useConfirm, useToast } from '../ui';

const STATUS = {
  pending: { label: 'Pending', tone: 'warn' },
  used: { label: 'Used', tone: 'good' },
  expired: { label: 'Expired', tone: 'neutral' },
  revoked: { label: 'Revoked', tone: 'bad' },
};

export const joinLink = (token) => `${window.location.origin}/join/${token}`;

function mailtoFor(invite, link, company) {
  const subject = `Your invitation to join ${company || 'our ERP'}`;
  const body = [
    `Hello${invite.name_hint ? ` ${invite.name_hint}` : ''},`, '',
    `You have been invited to join ${company || 'our ERP'} as ${invite.role_label}.`,
    'Open this link to create your account (it works once and expires on '
      + `${new Date(invite.expires_at).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}):`, '',
    link, '',
    invite.note || '',
  ].join('\n');
  return `mailto:${encodeURIComponent(invite.email_hint || '')}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

// Shown once, right after a link is made: the server never returns the token again.
function LinkDialog({ invite, onClose }) {
  const notify = useToast();
  const [emailing, setEmailing] = useState(false);
  const [emailed, setEmailed] = useState(null);
  const link = joinLink(invite.token);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
    } catch {
      const el = document.getElementById('invite-link');
      el.focus(); el.select();
      try { document.execCommand('copy'); } catch { notify('Select the link and copy it with Ctrl+C.', 'error'); return; }
    }
    notify('Link copied.');
  };
  return (
    <Dialog title="Invitation link" onClose={onClose} wide
      footer={<button type="button" className="btn btn-primary" onClick={onClose}>Done</button>}>
      <Notice type="warn">This is the only time the full link is shown. Copy it now. If it is lost, create a new link.</Notice>
      <Field id="invite-link" label={`Link for ${invite.role_label}${invite.name_hint ? ` · ${invite.name_hint}` : ''}`}>
        <input id="invite-link" readOnly value={link} onFocus={(e) => e.target.select()} />
      </Field>
      <div className="actions">
        <button type="button" className="btn btn-primary" onClick={copy}>Copy link</button>
        <button type="button" className="btn" onClick={() => setEmailing(true)}>Email this link</button>
        <a className="btn" href={mailtoFor(invite, link, invite.company_name)}>Open in my email program</a>
      </div>
      {emailed && emailed.status === 'sent' && <Notice type="success">Invitation emailed to {emailed.to.join(', ')}.</Notice>}
      {emailed && emailed.status === 'saved' && <Notice type="warn">The email was saved but not delivered: email is not set up on the server. Copy the link and send it yourself.</Notice>}
      {emailed && emailed.status === 'failed' && <Notice type="error">The email was not sent: {emailed.error} Copy the link and send it yourself, or try again.</Notice>}
      <p className="hint">
        “Email this link” sends the invitation from the app, to the address you entered (you can change it). “Open in my email program” prepares the same message in your own mail program instead.
        The link works once and expires on {fmtDateTime(invite.expires_at)}. The person chooses their own username and password; the access role
        ({invite.role_label}) is fixed by you. The list of sent emails never shows the link itself.
      </p>
      {emailing && (
        <EmailDialog title="Email the invitation link" onClose={() => setEmailing(false)} onSent={setEmailed}
          load={() => api.post(`/auth/invitations/${invite.id}/email-draft/`, { token: invite.token, origin: window.location.origin })}
          submit={(payload) => api.post(`/auth/invitations/${invite.id}/email/`, { token: invite.token, ...payload })} />
      )}
    </Dialog>
  );
}

function CreateDialog({ options, onClose, onCreated }) {
  const roles = options.allowed_roles;
  const [v, setV] = useState({ role: roles.some((r) => r.value === 'employee') ? 'employee' : roles[0]?.value || '', name_hint: '', email_hint: '', note: '', expires_in_days: String(options.default_days) });
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setError(null);
    const days = Number(v.expires_in_days);
    const errs = {};
    if (!v.role) errs.role = 'Choose an access role.';
    if (!Number.isInteger(days) || days < options.min_days || days > options.max_days) errs.expires_in_days = `Enter ${options.min_days} to ${options.max_days} days.`;
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const made = await api.post('/auth/invitations/', { ...v, expires_in_days: days });
      onCreated(made);
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title="Create invite link" onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[{ name: 'role' }, { name: 'name_hint' }, { name: 'email_hint' }, { name: 'note' }, { name: 'expires_in_days' }]} />
        <div className="form-grid">
          <Field id="inv-role" label="Access role" required error={errors.role}
            hint="Decides what the person can open. You choose it; the person joining cannot change it.">
            <select id="inv-role" value={v.role} onChange={set('role')}>
              {roles.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>
          </Field>
          <Field id="inv-days" label="Link valid for (days)" required error={errors.expires_in_days} hint={`${options.min_days} to ${options.max_days} days. The link works once.`}>
            <input id="inv-days" type="number" inputMode="numeric" min={options.min_days} max={options.max_days} value={v.expires_in_days} onChange={set('expires_in_days')} />
          </Field>
          <Field id="inv-name" label="Name of the person (optional)" error={errors.name_hint} hint="Only to help you recognise the link in the list.">
            <input id="inv-name" value={v.name_hint} maxLength={150} onChange={set('name_hint')} />
          </Field>
          <Field id="inv-email" label="Email of the person (optional)" error={errors.email_hint} hint="The address the invitation is emailed to when you click “Email this link”. Nothing is sent until you do.">
            <input id="inv-email" type="email" value={v.email_hint} onChange={set('email_hint')} />
          </Field>
          <Field id="inv-note" label="Note (optional)" error={errors.note} wide hint="Shown in your list and in the email message.">
            <textarea id="inv-note" rows={2} maxLength={500} value={v.note} onChange={set('note')} />
          </Field>
        </div>
        <p className="hint">The person will enter their own name, username, password, contact details and their role in the company (job title).</p>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Creating…' : 'Create link'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export function InviteManager() {
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [status, setStatus] = useState('');
  const { data, error, loading, reload } = useLoad(() => api.get('/auth/invitations/', { status: status || undefined }), [status]);
  const [creating, setCreating] = useState(false);
  const [shown, setShown] = useState(null); // the invitation (with its one-time token) whose link is on screen
  const rows = asList(data);
  const company = data?.company_name;

  const revoke = async (row) => {
    const ok = await confirm({ title: 'Revoke this link?', confirmLabel: 'Revoke link', danger: true,
      body: <p>The link{row.name_hint ? ` for ${row.name_hint}` : ''} stops working at once. You can create a new one later.</p> });
    if (!ok) return;
    try { await api.post(`/auth/invitations/${row.id}/revoke/`); notify('Link revoked.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const regenerate = async (row) => {
    try {
      const made = await api.post(`/auth/invitations/${row.id}/regenerate/`);
      setShown({ ...made, company_name: company });
      reload();
    } catch (e) { notify(e.message, 'error'); }
  };

  return (
    <section className="card" aria-label="Invitations">
      <div className="card-head">
        <div>
          <h2>Invite members</h2>
          <p className="muted">
            Create a one-time link and email it to the person (or copy it and send it yourself). They open it, enter their details and set their own password. You choose the access role; they cannot.
          </p>
        </div>
        <div className="page-actions">
          <button type="button" className="btn btn-primary" onClick={() => setCreating(true)} disabled={!data}>Create invite link</button>
        </div>
      </div>
      <div className="inline-form">
        <Field id="inv-filter" label="Show">
          <select id="inv-filter" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All links</option>
            {Object.entries(STATUS).map(([k, s]) => <option key={k} value={k}>{s.label}</option>)}
          </select>
        </Field>
      </div>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {!loading && !error && !rows.length && <p className="empty">No invitation links yet. Use “Create invite link” to add the first one.</p>}
      {rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Status</th><th>Invitee</th><th>Access role</th><th>Invited by</th><th>Expires</th><th>Joined</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((r) => {
                const st = STATUS[r.status] || { label: r.status, tone: 'neutral' };
                return (
                  <tr key={r.id}>
                    <td data-label="Status"><Badge tone={st.tone}>{st.label}</Badge></td>
                    <td data-label="Invitee"><strong>{r.name_hint || '—'}</strong><div className="hint">{r.email_hint}{r.note ? ` · ${r.note}` : ''}</div></td>
                    <td data-label="Access role">{r.role_label}</td>
                    <td data-label="Invited by">{r.created_by?.name || '—'}<div className="hint">{fmtDateTime(r.created_at)}</div></td>
                    <td data-label="Expires">{fmtDateTime(r.expires_at)}</td>
                    <td data-label="Joined">{r.used_by ? <>{r.used_by.name}<div className="hint">{fmtDateTime(r.used_at)}</div></> : r.status === 'revoked' ? <span className="hint">Revoked by {r.revoked_by?.name || '—'}</span> : '—'}</td>
                    <td className="row-actions">
                      {r.can_manage && r.status === 'pending' && <button type="button" className="btn btn-small" onClick={() => revoke(r)}>Revoke</button>}
                      {r.can_manage && r.status !== 'used' && <button type="button" className="btn btn-small" onClick={() => regenerate(r)}>New link</button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {creating && data && (
        <CreateDialog options={data} onClose={() => setCreating(false)}
          onCreated={(made) => { setCreating(false); setShown({ ...made, company_name: company }); reload(); }} />
      )}
      {shown && <LinkDialog invite={shown} onClose={() => setShown(null)} />}
      {confirmDialog}
    </section>
  );
}
