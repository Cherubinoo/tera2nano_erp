import { Fragment, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { EmailStatusBadge, NOT_SET_UP } from '../components/Email';
import { useAuth } from '../context';
import { useLoad } from '../hooks';
import { Badge, ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, fmtDateTime, useToast } from '../ui';

const PAGE_SIZE = 25;
const list = (x) => (Array.isArray(x) ? x.join(', ') : x || '');

function ServerCard({ server }) {
  return (
    <section className="card" aria-label="Mail server">
      <h2>Mail server</h2>
      {server.configured ? (
        <>
          <Notice type="success"><span><strong>Email is set up.</strong> Messages are delivered through the company mail server.</span></Notice>
          <dl className="summary">
            <div><dt>Server</dt><dd>{server.host}</dd></div>
            <div><dt>Port</dt><dd>{server.port}</dd></div>
            <div><dt>Security</dt><dd>{server.security}</dd></div>
            <div><dt>Sending address</dt><dd>{server.from_email}</dd></div>
          </dl>
        </>
      ) : (
        <Notice type="warn"><span><strong>{NOT_SET_UP}</strong> Your IT person sets the mail server in the server settings (EMAIL_HOST and the other EMAIL_ values, see the README). Until then every message is kept in a folder on the server and listed under Sent emails as “Saved, not delivered”.</span></Notice>
      )}
      <p className="hint">The mail server password is kept in the server settings only. It is never stored in the app or shown here.</p>
    </section>
  );
}

function TestCard({ user }) {
  const notify = useToast();
  const [to, setTo] = useState(user.email || '');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const send = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null); setResult(null);
    try { const m = await api.post('/email/test/', { to }); setResult(m); notify(m.status === 'failed' ? 'The test email failed.' : 'Test email sent.', m.status === 'failed' ? 'error' : 'success'); } catch (err) { setError(err); }
    setBusy(false);
  };
  return (
    <section className="card" aria-label="Send a test email">
      <h2>Send a test email</h2>
      <p className="muted">Check that email works: send a short message to yourself and see that it arrives.</p>
      <form className="inline-form" onSubmit={send} noValidate>
        <Field id="test-to" label="Send to" error={error?.fieldErrors?.to}>
          <input id="test-to" type="email" value={to} onChange={(e) => setTo(e.target.value)} />
        </Field>
        <button type="submit" className="btn btn-primary" disabled={busy || !to.trim()}>{busy ? 'Sending…' : 'Send a test email'}</button>
      </form>
      {error && !error.fieldErrors?.to && <ErrorBox error={error} />}
      {result && (
        <Notice type={result.status === 'sent' ? 'success' : result.status === 'saved' ? 'warn' : 'error'}>
          <span>
            {result.status === 'sent' && <>Sent to {list(result.to)}. Check the inbox (and the spam folder).</>}
            {result.status === 'saved' && <>Saved, not delivered: {NOT_SET_UP}</>}
            {result.status === 'failed' && <>Not sent. {result.error}</>}
          </span>
        </Notice>
      )}
    </section>
  );
}

export function EmailSettingsPage() {
  const { user } = useAuth();
  const notify = useToast();
  const { data, error, loading, reload } = useLoad(() => api.get('/email/settings/'), []);
  const [v, setV] = useState(null);
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setV({ sender_name: data.sender_name, reply_to: data.reply_to, signature: data.signature, auto_reminders: data.auto_reminders }); }, [data]);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!v) return <Loading />;
  const set = (k) => (e) => { const val = e.target.type === 'checkbox' ? e.target.checked : e.target.value; setV((x) => ({ ...x, [k]: val })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const save = async (e) => {
    e.preventDefault();
    setBusy(true); setFailure(null);
    try { await api.patch('/email/settings/', v); notify('Email settings saved.'); reload(); } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <>
      <PageHeader title="Email settings" description="How emails from the app look to customers, vendors and staff, and whether payment reminders go out on their own."
        actions={<Link className="btn" to="/company/sent-emails">Sent emails</Link>} />
      <ServerCard server={data.server} />
      <section className="card" aria-label="Sender and signature">
        <h2>Sender and signature</h2>
        <form onSubmit={save} noValidate>
          <FormErrors error={failure} fields={[{ name: 'sender_name' }, { name: 'reply_to' }, { name: 'signature' }, { name: 'auto_reminders' }]} />
          <div className="form-grid">
            <Field id="es-name" label="Sender name" error={errors.sender_name} hint={`Shown as the sender. Leave empty to use “${data.effective_sender_name}”.`}>
              <input id="es-name" value={v.sender_name} maxLength={150} onChange={set('sender_name')} />
            </Field>
            <Field id="es-reply" label="Reply-to address (optional)" error={errors.reply_to} hint="Where replies go, for example accounts@yourcompany.com.">
              <input id="es-reply" type="email" value={v.reply_to} onChange={set('reply_to')} />
            </Field>
            <Field id="es-sig" label="Signature (optional)" wide error={errors.signature} hint="Added at the end of every prefilled message, for example your name, team and phone number. Leave empty to end with “Regards” and the sender name.">
              <textarea id="es-sig" rows={4} maxLength={2000} value={v.signature} onChange={set('signature')} />
            </Field>
          </div>
          <label className="checkbox-inline">
            <input type="checkbox" checked={v.auto_reminders} onChange={set('auto_reminders')} /> Email payment reminders automatically
          </label>
          <p className="hint">Once a day the app emails the payment reminders that are due (see Sales, Reminders) to customers who have an email address. Each reminder goes out once; customers without an address stay on the Reminders page for you. Works only when email is set up on the server.</p>
          <p className="hint">Messages go out from: <strong>{data.from_address}</strong></p>
          <div className="actions"><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save settings'}</button></div>
        </form>
      </section>
      <TestCard user={user} />
    </>
  );
}

function Message({ id, onResent }) {
  const notify = useToast();
  const { data: m, error, loading } = useLoad(() => api.get(`/email/messages/${id}/`), [id]);
  const [busy, setBusy] = useState(false);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const resend = async () => {
    setBusy(true);
    try {
      const r = await api.post(`/email/messages/${id}/resend/`);
      notify(r.status === 'failed' ? `Still not sent: ${r.error}` : r.status === 'saved' ? 'Saved, not delivered (email is not set up).' : 'Sent again.', r.status === 'failed' ? 'error' : 'success');
      onResent();
    } catch (e) { notify(e.message, 'error'); }
    setBusy(false);
  };
  return (
    <div className="event-detail">
      {m.status === 'failed' && <Notice type="error"><span><strong>Why it failed:</strong> {m.error}</span></Notice>}
      <dl className="meta">
        <dt>From</dt><dd>{m.from_email}</dd>
        {m.reply_to && <><dt>Reply to</dt><dd>{m.reply_to}</dd></>}
        <dt>To</dt><dd>{list(m.to)}</dd>
        {m.cc.length > 0 && <><dt>Copy to</dt><dd>{list(m.cc)}</dd></>}
        <dt>Attached</dt><dd>{m.attachments.length ? m.attachments.join(', ') : '—'}</dd>
        <dt>About</dt><dd>{m.url ? <Link to={m.url}>{m.object_label || m.kind_label}</Link> : (m.object_label || m.kind_label)}</dd>
      </dl>
      <div className="reminder-text"><pre aria-label="Message">{m.body}</pre></div>
      {m.status === 'failed' && !m.can_resend && <p className="hint">This message held a one-time invitation link, which is not kept. Make a new invitation link and email that instead.</p>}
      {m.can_resend && !m.resent && <button type="button" className="btn btn-primary btn-small" disabled={busy} onClick={resend}>{busy ? 'Sending…' : 'Send again'}</button>}
      {m.resent && <p className="hint">This message has been sent again; see the newer entry in the list.</p>}
    </div>
  );
}

export function SentEmailsPage() {
  const [filters, setFilters] = useState({ q: '', status: '', kind: '' });
  const [applied, setApplied] = useState(filters);
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState(null);
  const { data, error, loading, reload } = useLoad(() => api.get('/email/messages/', { ...applied, page, page_size: PAGE_SIZE }), [JSON.stringify(applied), page]);
  const apply = (e) => { e.preventDefault(); setPage(1); setApplied(filters); };
  const clear = () => { const empty = { q: '', status: '', kind: '' }; setFilters(empty); setApplied(empty); setPage(1); };
  const set = (k) => (e) => setFilters({ ...filters, [k]: e.target.value });
  const quick = (status) => { const next = { ...filters, status }; setFilters(next); setApplied(next); setPage(1); };
  const rows = data?.results || [];
  const pages = data ? Math.max(1, Math.ceil(data.count / PAGE_SIZE)) : 1;
  return (
    <>
      <PageHeader title="Sent emails" description="Every email the app has sent or tried to send: invoices, quotations, purchase orders, reminders, invitations, payslips and test emails."
        actions={<Link className="btn" to="/company/email">Email settings</Link>} />
      {data && !data.server.configured && <Notice type="warn">{NOT_SET_UP}</Notice>}
      {data && (
        <div className="chips" role="group" aria-label="Show">
          <button type="button" className="chip" aria-pressed={!applied.status} onClick={() => quick('')}>All</button>
          <button type="button" className="chip" aria-pressed={applied.status === 'sent'} onClick={() => quick('sent')}>Sent ({data.counts.sent})</button>
          <button type="button" className="chip" aria-pressed={applied.status === 'saved'} onClick={() => quick('saved')}>Saved, not delivered ({data.counts.saved})</button>
          <button type="button" className="chip" aria-pressed={applied.status === 'failed'} onClick={() => quick('failed')}>Failed ({data.counts.failed})</button>
        </div>
      )}
      <form className="card filters" onSubmit={apply} noValidate>
        <div className="form-grid">
          <Field id="se-q" label="Search" hint="Subject, address, record or error"><input id="se-q" type="search" value={filters.q} onChange={set('q')} /></Field>
          <Field id="se-kind" label="About">
            <select id="se-kind" value={filters.kind} onChange={set('kind')}>
              <option value="">Anything</option>
              {(data?.kinds || []).map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </select>
          </Field>
          <Field id="se-status" label="Result">
            <select id="se-status" value={filters.status} onChange={set('status')}>
              <option value="">Any result</option><option value="sent">Sent</option><option value="saved">Saved, not delivered</option><option value="failed">Failed</option>
            </select>
          </Field>
        </div>
        <div className="actions">
          <button type="submit" className="btn btn-primary">Search</button>
          <button type="button" className="btn" onClick={clear}>Clear</button>
        </div>
      </form>
      <section className="card" aria-label="Sent emails">
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && !rows.length && <p className="empty">No emails match. Messages appear here as soon as someone sends one.</p>}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>When</th><th>To</th><th>Subject</th><th>About</th><th>Result</th><th>By</th><th><span className="sr-only">Details</span></th></tr></thead>
              <tbody>
                {rows.map((m) => (
                  <Fragment key={m.id}>
                    <tr>
                      <td data-label="When">{fmtDateTime(m.created_at)}</td>
                      <td data-label="To">{list(m.to)}{m.cc.length > 0 && <div className="hint">copy: {list(m.cc)}</div>}</td>
                      <td data-label="Subject">{m.subject}{m.attachments.length > 0 && <div className="hint">Attached: {m.attachments.join(', ')}</div>}</td>
                      <td data-label="About">{m.object_label || m.kind_label}</td>
                      <td data-label="Result">
                        <EmailStatusBadge status={m.status} label={m.status_label} />{m.resent && <> <Badge>Sent again</Badge></>}
                        {m.status === 'failed' && <div className="hint email-error">{m.error}</div>}
                      </td>
                      <td data-label="By">{m.sent_by || '—'}</td>
                      <td className="row-actions">
                        <button type="button" className="btn btn-small" aria-expanded={open === m.id} onClick={() => setOpen(open === m.id ? null : m.id)}>
                          {open === m.id ? 'Hide' : m.can_resend && !m.resent ? 'Open · Send again' : 'Open'}
                        </button>
                      </td>
                    </tr>
                    {open === m.id && <tr className="detail-row"><td colSpan={7}><Message id={m.id} onResent={() => { setOpen(null); reload(); }} /></td></tr>}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.count > 0 && (
          <nav className="pager" aria-label="Pagination">
            <button type="button" className="btn btn-small" disabled={page <= 1 || loading} onClick={() => setPage((p) => Math.max(1, p - 1))}>Previous</button>
            <span>Page {page} of {pages} · {data.count} emails</span>
            <button type="button" className="btn btn-small" disabled={page >= pages || loading} onClick={() => setPage((p) => p + 1)}>Next</button>
          </nav>
        )}
      </section>
    </>
  );
}
