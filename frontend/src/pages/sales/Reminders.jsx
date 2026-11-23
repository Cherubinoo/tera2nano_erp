import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { money } from '../../format';
import { asList, useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, Tabs, fmtDate, fmtDateTime, useReasonDialog, useToast } from '../../ui';

export const NO_EMAIL_NOTICE = 'No email is sent from this system. “Record as sent” keeps the message against the invoice; copy the text or open it in your own email program to actually send it.';

// Copy to the clipboard, with a fallback for browsers that refuse the modern API.
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* fall through */ }
  try {
    const t = document.createElement('textarea');
    t.value = text; t.style.position = 'fixed'; t.style.opacity = '0';
    document.body.appendChild(t); t.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(t);
    return ok;
  } catch { return false; }
}

function TextBlock({ subject, body, email }) {
  return (
    <div className="reminder-text">
      <p className="hint">To: {email || <em>no email address on file for this customer</em>}</p>
      <p><strong>Subject:</strong> {subject}</p>
      <pre aria-label="Message">{body}</pre>
    </div>
  );
}

function CopyAndMail({ subject, body, mailto, notify }) {
  const copy = async () => notify((await copyText(`${subject}\n\n${body}`)) ? 'Copied to the clipboard.' : 'Could not copy. Select the text and copy it yourself.', 'info');
  return (
    <>
      <button type="button" className="btn" onClick={copy}>Copy text</button>
      <a className="btn" href={mailto}>Open in email program</a>
    </>
  );
}

// A reminder already recorded (history rows, an invoice's reminder list).
export function ReminderTextDialog({ reminder, onClose }) {
  const notify = useToast();
  return (
    <Dialog title={`${reminder.label}${reminder.document ? ` · ${reminder.document.number}` : ''}`} onClose={onClose}
      footer={<><button type="button" className="btn" onClick={onClose}>Close</button></>}>
      {reminder.status === 'skipped' ? <p className="muted">This reminder was skipped{reminder.note ? `: ${reminder.note}` : '.'}</p> : (
        <>
          <TextBlock subject={reminder.subject} body={reminder.body} email={reminder.to_email} />
          <div className="actions"><CopyAndMail subject={reminder.subject} body={reminder.body} mailto={reminder.mailto || `mailto:${reminder.to_email || ''}?subject=${encodeURIComponent(reminder.subject)}&body=${encodeURIComponent(reminder.body)}`} notify={notify} /></div>
          <p className="hint">Recorded {fmtDateTime(reminder.created_at)}{reminder.by ? ` by ${reminder.by}` : ''}. {NO_EMAIL_NOTICE}</p>
        </>
      )}
    </Dialog>
  );
}

function SendDialog({ row, onClose, onDone }) {
  const notify = useToast();
  const [saved, setSaved] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const record = async () => {
    setBusy(true); setError(null);
    try { setSaved(await api.post('/sales/reminders/send/', { document: row.document.id, days_offset: row.rule.days_offset })); onDone(); } catch (e) { setError(e); }
    setBusy(false);
  };
  const subject = saved ? saved.subject : row.subject, body = saved ? saved.body : row.body;
  return (
    <Dialog title={`Reminder for ${row.document.number}`} onClose={onClose} wide
      footer={saved ? <button type="button" className="btn btn-primary" onClick={onClose}>Done</button> : (
        <>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={record}>{busy ? 'Recording…' : 'Record as sent'}</button>
        </>
      )}>
      <FormErrors error={error} fields={[]} />
      <Notice type={saved ? 'info' : 'warn'}>{saved ? 'Recorded against the invoice. Now copy the text or open it in your email program to send it.' : NO_EMAIL_NOTICE}</Notice>
      <TextBlock subject={subject} body={body} email={row.customer.email} />
      <div className="actions"><CopyAndMail subject={subject} body={body} mailto={saved ? saved.mailto : row.mailto} notify={notify} /></div>
    </Dialog>
  );
}

function DueTab({ canAct, onChanged }) {
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const { data, error, loading, reload } = useLoad(() => api.get('/sales/reminders/'), []);
  const [sending, setSending] = useState(null);
  const skip = async (row) => {
    const note = await ask({ title: `Skip the reminder for ${row.document.number}?`, message: 'The invoice will not be offered this reminder again. A later reminder (if the invoice stays unpaid) still comes up.', label: 'Why? (optional)', required: false, confirmLabel: 'Skip reminder' });
    if (note === null) return;
    try { await api.post('/sales/reminders/skip/', { document: row.document.id, days_offset: row.rule.days_offset, note }); notify('Reminder skipped.'); reload(); onChanged(); } catch (e) { notify(e.message, 'error'); reload(); }
  };
  if (loading && !data) return <Loading />;  // keep the page (and an open dialog) while it refreshes
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <section className="card">
      {data.results.length === 0 ? <p className="empty">No invoice needs a reminder today.</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Invoice</th><th>Customer</th><th>Due date</th><th className="amount">Days overdue</th><th className="amount">Amount due</th><th>Reminder</th>{canAct && <th>Action</th>}</tr></thead>
            <tbody>
              {data.results.map((r) => (
                <tr key={`${r.document.id}-${r.rule.days_offset}`}>
                  <td data-label="Invoice"><Link to={`/sales/documents/${r.document.id}`}><strong>{r.document.number}</strong></Link></td>
                  <td data-label="Customer"><Link to={`/sales/customers/${r.customer.id}`}>{r.customer.name}</Link>{!r.customer.email && <div className="hint">No email on file</div>}</td>
                  <td data-label="Due date">{fmtDate(r.due_date)}</td>
                  <td className="amount" data-label="Days overdue">{r.days_overdue > 0 ? r.days_overdue : '—'}</td>
                  <td className="amount" data-label="Amount due"><strong>{money(r.balance, r.currency)}</strong></td>
                  <td data-label="Reminder"><Badge tone={r.rule.days_offset > 0 ? 'bad' : r.rule.days_offset === 0 ? 'warn' : 'info'}>{r.rule.label}</Badge></td>
                  {canAct && (
                    <td data-label="Action">
                      <button type="button" className="btn btn-small btn-primary" onClick={() => setSending(r)}>Send</button>{' '}
                      <button type="button" className="btn btn-small" onClick={() => skip(r)}>Skip</button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.results.length > 0 && <div className="list-foot"><span>{data.count} invoice{data.count === 1 ? '' : 's'} need a reminder</span></div>}
      {sending && <SendDialog row={sending} onClose={() => { setSending(null); reload(); onChanged(); }} onDone={() => { reload(); onChanged(); }} />}
      {dialog}
    </section>
  );
}

function HistoryTab() {
  const { data, error, loading, reload } = useLoad(() => api.get('/sales/reminders/history/'), []);
  const [view, setView] = useState(null);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const rows = asList(data);
  return (
    <section className="card">
      {rows.length === 0 ? <p className="empty">No reminder has been sent or skipped yet.</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Recorded</th><th>Invoice</th><th>Customer</th><th>Reminder</th><th className="amount">Amount due</th><th>Result</th><th>By</th><th /></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td data-label="Recorded">{fmtDateTime(r.created_at)}</td>
                  <td data-label="Invoice"><Link to={`/sales/documents/${r.document.id}`}>{r.document.number}</Link></td>
                  <td data-label="Customer">{r.customer.name}</td>
                  <td data-label="Reminder">{r.rule.label}</td>
                  <td className="amount" data-label="Amount due">{money(r.amount_due)}</td>
                  <td data-label="Result"><Badge tone={r.status === 'sent' ? 'good' : 'neutral'}>{r.status_label}</Badge></td>
                  <td data-label="By">{r.by || '—'}</td>
                  <td data-label="Text"><button type="button" className="btn btn-small" onClick={() => setView(r)}>{r.status === 'sent' ? 'View text' : 'View'}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {view && <ReminderTextDialog reminder={view} onClose={() => setView(null)} />}
    </section>
  );
}

function RuleDialog({ rule, placeholders, onClose, onSaved }) {
  const notify = useToast();
  const [offset, setOffset] = useState(rule ? String(rule.days_offset) : '');
  const [subject, setSubject] = useState(rule?.subject_template || '');
  const [body, setBody] = useState(rule?.body_template || '');
  const [active, setActive] = useState(rule ? rule.is_active : true);
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const bodyRef = useRef(null);
  const insert = (key) => {
    const el = bodyRef.current;
    const text = `{${key}}`;
    if (!el) { setBody((b) => b + text); return; }
    const [a, z] = [el.selectionStart ?? body.length, el.selectionEnd ?? body.length];
    setBody(body.slice(0, a) + text + body.slice(z));
    setTimeout(() => { el.focus(); el.setSelectionRange(a + text.length, a + text.length); }, 0);
  };
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    const errs = {};
    if (offset.trim() === '' || !Number.isInteger(Number(offset))) errs.days_offset = 'Enter a whole number of days (negative = before the due date).';
    if (!subject.trim()) errs.subject_template = 'Write the subject.';
    if (!body.trim()) errs.body_template = 'Write the message.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true); setFailure(null);
    const payload = { days_offset: Number(offset), subject_template: subject, body_template: body, is_active: active };
    try {
      if (rule) await api.patch(`/sales/reminders/rules/${rule.id}/`, payload); else await api.post('/sales/reminders/rules/', payload);
      notify('Reminder rule saved.'); onSaved();
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title={rule ? 'Edit reminder rule' : 'New reminder rule'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={[{ name: 'days_offset' }, { name: 'subject_template' }, { name: 'body_template' }]} />
        <div className="form-grid">
          <Field id="rule-offset" label="Days from the due date" required error={errors.days_offset} hint="-3 = three days before, 0 = on the due date, 7 = a week after.">
            <input id="rule-offset" type="number" step="1" min="-60" max="365" value={offset} onChange={(e) => setOffset(e.target.value)} />
          </Field>
          <label className="checkbox-inline"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Rule is switched on</label>
          <Field id="rule-subject" label="Subject" required wide error={errors.subject_template}><input id="rule-subject" value={subject} maxLength={200} onChange={(e) => setSubject(e.target.value)} /></Field>
          <Field id="rule-body" label="Message" required wide error={errors.body_template}><textarea id="rule-body" ref={bodyRef} rows={8} value={body} onChange={(e) => setBody(e.target.value)} /></Field>
        </div>
        <p className="hint">Insert a placeholder into the message (it is filled in for each invoice):</p>
        <div className="chips" role="group" aria-label="Placeholders">
          {placeholders.map((p) => <button key={p.key} type="button" className="chip" title={p.label} onClick={() => insert(p.key)}>{`{${p.key}}`}</button>)}
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save rule'}</button>
        </div>
      </form>
    </Dialog>
  );
}

function RulesTab({ canAct }) {
  const { data, error, loading, reload } = useLoad(() => api.get('/sales/reminders/rules/'), []);
  const [editing, setEditing] = useState(null);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <section className="card">
      <div className="card-head">
        <h2>When reminders fall due</h2>
        {canAct && <button type="button" className="btn btn-primary" onClick={() => setEditing({})}>New rule</button>}
      </div>
      <p className="muted">Each rule offers one reminder per unpaid invoice, counted from its due date. If several rules have come due for an invoice, only the latest is offered.</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>When</th><th>Subject</th><th>Status</th>{canAct && <th />}</tr></thead>
          <tbody>
            {data.results.map((r) => (
              <tr key={r.id} className={r.is_active ? '' : 'row-inactive'}>
                <td data-label="When"><strong>{r.label}</strong></td>
                <td data-label="Subject">{r.subject_template}</td>
                <td data-label="Status"><Badge tone={r.is_active ? 'good' : 'neutral'}>{r.is_active ? 'On' : 'Off'}</Badge></td>
                {canAct && <td data-label="Action"><button type="button" className="btn btn-small" onClick={() => setEditing(r)}>Edit</button></td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && <RuleDialog rule={editing.id ? editing : null} placeholders={data.placeholders} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />}
    </section>
  );
}

export default function Reminders() {
  const { user } = useAuth();
  const canAct = user.permissions.includes('sales.manage');
  const [tab, setTab] = useState('due');
  const [stamp, setStamp] = useState(0);
  return (
    <>
      <PageHeader title="Payment reminders" description="Unpaid invoices that need a reminder today, and the wording used." />
      <Notice>{NO_EMAIL_NOTICE}</Notice>
      <Tabs tabs={[{ key: 'due', label: 'Due now' }, { key: 'history', label: 'History' }, { key: 'rules', label: 'Rules and wording' }]} value={tab} onChange={setTab} />
      {tab === 'due' && <DueTab canAct={canAct} onChanged={() => setStamp((n) => n + 1)} />}
      {tab === 'history' && <HistoryTab key={stamp} />}
      {tab === 'rules' && <RulesTab canAct={canAct} />}
    </>
  );
}
