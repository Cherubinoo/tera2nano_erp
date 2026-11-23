import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, downloadFile } from '../../api';
import Attachments from '../../components/Attachments';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, fmtDate, useConfirm, useReasonDialog, useToast } from '../../ui';
import { useLoad } from '../../hooks';
import { Rows } from '../../pages/Minutes';
import { Avatar, MeetingStatus, timeRange, useCan, usePeople } from './common';
import MeetingFormDialog from './MeetingForm';
import { TaskDialog, TaskFormDialog, TaskStatusBadge } from './tasks-shared';

const ATTENDANCE = [{ value: 'present', label: 'Present' }, { value: 'absent', label: 'Absent' }, { value: 'apologies', label: 'Apologies' }];

function RescheduleDialog({ m, onClose, onDone }) {
  const notify = useToast();
  const [v, setV] = useState({ date: m.date, start_time: m.start_time, end_time: m.end_time, reason: '' });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const errs = {};
    if (!v.date) errs.date = 'Choose the new date.';
    if (v.start_time && v.end_time && v.end_time <= v.start_time) errs.end_time = 'The end time must be after the start time.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    try { await api.post(`/workplace/meetings/${m.id}/reschedule/`, v); notify('Meeting rescheduled.'); onDone(); } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
  };
  return (
    <Dialog title="Reschedule meeting" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={[{ name: 'date' }, { name: 'start_time' }, { name: 'end_time' }, { name: 'reason' }]} />
        <div className="form-grid">
          <Field id="rs-date" label="New date" required error={errors.date}><input id="rs-date" type="date" value={v.date} onChange={set('date')} /></Field>
          <Field id="rs-start" label="Start time" error={errors.start_time}><input id="rs-start" type="time" value={v.start_time} onChange={set('start_time')} /></Field>
          <Field id="rs-end" label="End time" error={errors.end_time}><input id="rs-end" type="time" value={v.end_time} onChange={set('end_time')} /></Field>
          <Field id="rs-reason" label="Reason" wide error={errors.reason}><input id="rs-reason" value={v.reason} maxLength={300} onChange={set('reason')} /></Field>
        </div>
        <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary">Reschedule</button></div>
      </form>
    </Dialog>
  );
}

function GenerateDialog({ m, onClose, onDone }) {
  const notify = useToast();
  const [count, setCount] = useState(3);
  const [failure, setFailure] = useState(null);
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      const r = await api.post(`/workplace/meetings/${m.id}/generate/`, { count: Number(count) });
      notify(`${r.created.length} meeting${r.created.length === 1 ? '' : 's'} created.`);
      onDone();
    } catch (err) { setFailure(err); }
  };
  return (
    <Dialog title="Create the next meetings" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <p className="muted">This meeting repeats {m.recurrence === 'weekly' ? 'every week' : 'every month'}. New meetings follow the latest one in the series, with the same attendees and agenda.</p>
        <FormErrors error={failure} fields={[]} />
        <Field id="gn-count" label="How many more" required hint="1 to 52"><input id="gn-count" type="number" min="1" max="52" value={count} onChange={(e) => setCount(e.target.value)} /></Field>
        <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary">Create meetings</button></div>
      </form>
    </Dialog>
  );
}

// Record the minutes through the company module's minutes generator; the form starts from this meeting's data.
function MinutesDialog({ m, onClose, onDone }) {
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { data, error, loading } = useLoad(() => api.get(`/workplace/meetings/${m.id}/minutes/`), [m.id]);
  const [form, setForm] = useState(null);
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const draft = data?.draft;
  if (draft && !form) {
    setForm({
      title: draft.title, date: draft.date, start_time: draft.start_time, end_time: draft.end_time, venue: draft.venue, mode: draft.mode,
      minute_taker: draft.minute_taker, next_meeting: '', agenda: draft.agenda.join('\n'),
      attendees: draft.attendees.length ? draft.attendees : [{ name: '', designation: '', status: 'present' }],
      items: draft.items.length ? draft.items : [{ heading: '', discussion: '', decision: '' }],
      actions: draft.actions.length ? draft.actions : [{ action: '', owner: '', due_date: '' }],
    });
  }
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const setList = (k) => (rows) => setForm((f) => ({ ...f, [k]: rows }));

  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setFailure(null);
    const minutes = {
      title: form.title.trim(), date: form.date, start_time: form.start_time, end_time: form.end_time, venue: form.venue.trim(), mode: form.mode,
      minute_taker: form.minute_taker.trim(), next_meeting: form.next_meeting.trim(),
      attendees: form.attendees.filter((a) => a.name.trim()).map((a) => ({ ...a, name: a.name.trim(), designation: a.designation.trim() })),
      agenda: form.agenda.split('\n').map((l) => l.trim()).filter(Boolean),
      items: form.items.filter((i) => i.heading.trim()).map((i) => ({ heading: i.heading.trim(), discussion: i.discussion.trim(), decision: i.decision.trim() })),
      actions: form.actions.filter((a) => a.action.trim()).map((a) => ({ action: a.action.trim(), owner: a.owner.trim(), due_date: a.due_date })),
    };
    const errs = {};
    if (!minutes.title) errs.title = 'Enter the meeting title.';
    if (minutes.start_time && minutes.end_time && minutes.end_time <= minutes.start_time) errs.end_time = 'The end time must be after the start time.';
    if (!minutes.attendees.length && !minutes.agenda.length && !minutes.items.length) errs.attendees = 'Add attendees, an agenda or at least one discussion item.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    const ok = await confirm({
      title: 'Issue these minutes?',
      body: <><p>The minutes are numbered and saved exactly as written under meeting ID <code>{m.number}</code>. They cannot be edited afterwards; if they are wrong, void them in Company, Minutes of meeting and issue again.</p>
        <p>The PDF leaves space at the bottom right for your company seal.</p></>,
      confirmLabel: 'Issue minutes',
    });
    if (!ok) return;
    setBusy(true);
    try {
      const r = await api.post(`/workplace/meetings/${m.id}/minutes/`, { minutes });
      notify(`Minutes issued: ${r.minutes.reference_number}`);
      onDone();
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };

  return (
    <Dialog title={`Record minutes for ${m.number}`} onClose={onClose} wide>
      {loading && <Loading />}
      <ErrorBox error={error} />
      {form && (
        <form onSubmit={submit} noValidate>
          <p className="muted">Pre-filled from the meeting, its agenda, decisions and action items. These are the company's standard minutes of meeting.</p>
          <FormErrors error={failure} fields={[{ name: 'title' }, { name: 'end_time' }, { name: 'attendees' }]} />
          <div className="form-grid">
            <Field id="mn-title" label="Meeting title" required wide error={errors.title}><input id="mn-title" value={form.title} onChange={set('title')} /></Field>
            <Field id="mn-date" label="Date"><input id="mn-date" type="date" value={form.date} readOnly /></Field>
            <Field id="mn-start" label="Start time"><input id="mn-start" type="time" value={form.start_time} onChange={set('start_time')} /></Field>
            <Field id="mn-end" label="End time" error={errors.end_time}><input id="mn-end" type="time" value={form.end_time} onChange={set('end_time')} /></Field>
            <Field id="mn-venue" label="Venue"><input id="mn-venue" value={form.venue} onChange={set('venue')} /></Field>
            <Field id="mn-mode" label="Mode">
              <select id="mn-mode" value={form.mode} onChange={set('mode')}><option value="in_person">In person</option><option value="online">Online</option><option value="hybrid">Hybrid</option></select>
            </Field>
            <Field id="mn-taker" label="Minutes taken by"><input id="mn-taker" value={form.minute_taker} onChange={set('minute_taker')} /></Field>
            <Field id="mn-next" label="Next meeting"><input id="mn-next" value={form.next_meeting} onChange={set('next_meeting')} placeholder="e.g. 27 November 2026, 10:00" /></Field>
          </div>
          {errors.attendees && <div className="error-text" role="alert">{errors.attendees}</div>}
          <Rows title="Attendees" rows={form.attendees} setRows={setList('attendees')} noun="attendee" blank={() => ({ name: '', designation: '', status: 'present' })}
            columns={[{ key: 'name', label: 'Name' }, { key: 'designation', label: 'Designation' }, { key: 'status', label: 'Attendance', type: 'select', options: ATTENDANCE }]} />
          <Field id="mn-agenda" label="Agenda" hint="One item per line." wide><textarea id="mn-agenda" rows={3} value={form.agenda} onChange={set('agenda')} /></Field>
          <Rows title="Discussion and decisions" rows={form.items} setRows={setList('items')} noun="item" blank={() => ({ heading: '', discussion: '', decision: '' })}
            columns={[{ key: 'heading', label: 'Heading', wide: true }, { key: 'discussion', label: 'Discussion', type: 'textarea', wide: true }, { key: 'decision', label: 'Decision', type: 'textarea', wide: true }]} />
          <Rows title="Action items" rows={form.actions} setRows={setList('actions')} noun="action" blank={() => ({ action: '', owner: '', due_date: '' })}
            columns={[{ key: 'action', label: 'Action', wide: true }, { key: 'owner', label: 'Owner' }, { key: 'due_date', label: 'Due date', type: 'date' }]} />
          <div className="dialog-foot-inline">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Issuing…' : 'Issue minutes'}</button>
          </div>
        </form>
      )}
      {confirmDialog}
    </Dialog>
  );
}

export default function MeetingDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const can = useCan();
  const notify = useToast();
  const people = usePeople();
  const { ask, dialog: reasonDialog } = useReasonDialog();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { data: m, error, loading, reload } = useLoad(() => api.get(`/workplace/meetings/${id}/`), [id]);
  const [dialog, setDialog] = useState(null); // edit | reschedule | generate | minutes | action
  const [taskId, setTaskId] = useState(null);
  const [decision, setDecision] = useState('');
  const [failure, setFailure] = useState(null);
  const manager = can('workplace.manage');

  if (loading && !m) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!m) return null;

  const act = async (fn, ok) => { setFailure(null); try { await fn(); if (ok) notify(ok); reload(); } catch (err) { setFailure(err); } };
  const setStatus = (status, reason) => act(() => api.post(`/workplace/meetings/${id}/status/`, { status, reason }), status === 'held' ? 'Marked as held.' : status === 'scheduled' ? 'Meeting reopened.' : 'Meeting cancelled.');
  const cancel = async () => {
    const reason = await ask({ title: 'Cancel this meeting', label: 'Reason for cancelling', required: true, confirmLabel: 'Cancel meeting' });
    if (reason) setStatus('cancelled', reason);
  };
  const remove = async () => {
    if (!(await confirm({ title: `Delete ${m.number}?`, body: <p>The meeting, its agenda and attendee list are removed. Action items already created stay in Tasks. This is recorded in the change history.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/workplace/meetings/${id}/`); notify('Meeting deleted.'); navigate('/workplace/meetings'); } catch (err) { setFailure(err); }
  };
  const addDecision = (e) => {
    e.preventDefault();
    if (!decision.trim()) return;
    act(async () => { await api.post(`/workplace/meetings/${id}/decisions/`, { text: decision.trim() }); setDecision(''); });
  };
  const open = m.status === 'scheduled' || m.status === 'rescheduled';
  const pdf = (doc, print) => downloadFile(`/workplace/meetings/${id}/minutes/${doc.id}/download/${print ? '?variant=print' : ''}`, `${doc.reference_number.replace(/\//g, '-')}${print ? '-print' : ''}.pdf`).catch((e) => setFailure(e));
  const done = () => { setDialog(null); reload(); };

  return (
    <>
      <Link className="back-link" to="/workplace/meetings">‹ Meetings</Link>
      <header className="doc-head">
        <div>
          <h1>{m.title} <MeetingStatus m={m} /></h1>
          <p className="muted">{m.number} · {m.type_label} · {fmtDate(m.date)}, {timeRange(m)}{m.recurrence !== 'none' ? ` · ${m.recurrence_label.toLowerCase()}` : ''}</p>
        </div>
        {manager && (
          <div className="page-actions">
            {m.status !== 'cancelled' && <button type="button" className="btn btn-primary" onClick={() => setDialog('minutes')}>Record minutes</button>}
            {open && <button type="button" className="btn" onClick={() => setStatus('held')}>Mark as held</button>}
            {open && <button type="button" className="btn" onClick={() => setDialog('reschedule')}>Reschedule</button>}
            {open && <button type="button" className="btn" onClick={cancel}>Cancel meeting</button>}
            {m.status === 'cancelled' && <button type="button" className="btn" onClick={() => setStatus('scheduled')}>Reopen</button>}
            {m.recurrence !== 'none' && <button type="button" className="btn" onClick={() => setDialog('generate')}>Create next meetings</button>}
            <button type="button" className="btn" onClick={() => setDialog('edit')}>Edit</button>
            {m.minutes.length === 0 && <button type="button" className="btn btn-danger-outline" onClick={remove}>Delete</button>}
          </div>
        )}
      </header>
      <FormErrors error={failure} fields={[]} />

      <div className="grid-2">
        <section className="card" aria-label="Details">
          <h2>Details</h2>
          <dl className="facts">
            <div><dt>Date</dt><dd>{fmtDate(m.date)}</dd></div>
            <div><dt>Time</dt><dd>{timeRange(m)}</dd></div>
            <div><dt>Venue</dt><dd>{m.venue || '—'}</dd></div>
            <div><dt>Online link</dt><dd>{m.link ? <a href={m.link} target="_blank" rel="noopener noreferrer">Join the meeting</a> : '—'}</dd></div>
            <div><dt>Organiser</dt><dd>{m.organiser?.name || '—'}</dd></div>
            {m.series_count > 1 && <div><dt>Series</dt><dd>{m.series_count} meetings</dd></div>}
          </dl>
          {m.status_reason && <p className="muted">Reason: {m.status_reason}</p>}
          {m.description && <p className="wp-pre">{m.description}</p>}
        </section>
        <section className="card" aria-label="Attendees">
          <h2>Attendees <span className="hint">{m.attendees.length}</span></h2>
          {m.attendees.length === 0 ? <p className="empty">No attendees added.</p> : (
            <ul className="wp-list">
              {m.attendees.map((a) => (
                <li key={a.id}>
                  <Avatar person={{ name: a.name }} />
                  <div className="wp-list-main"><strong>{a.name}</strong><div className="hint">{a.external ? `Outside${a.designation ? `, ${a.designation}` : ''}` : a.designation || 'Team'}</div></div>
                  {a.attended === true && <Badge tone="good">Present</Badge>}
                  {a.attended === false && <Badge tone="neutral">Absent</Badge>}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="card" aria-label="Agenda">
        <h2>Agenda</h2>
        {m.agenda.length === 0 ? <p className="empty">No agenda items.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>#</th><th>Item</th><th>Owner</th><th className="amount">Minutes</th></tr></thead>
            <tbody>
              {m.agenda.map((a, i) => (
                <tr key={a.id}><td data-label="#">{i + 1}</td><td data-label="Item"><strong>{a.title}</strong>{a.notes && <div className="hint">{a.notes}</div>}</td>
                  <td data-label="Owner">{a.owner_name || '—'}</td><td className="amount" data-label="Minutes">{a.minutes ?? '—'}</td></tr>
              ))}
            </tbody>
            <tfoot><tr><td colSpan={3}>Time allotted</td><td className="amount">{m.agenda.reduce((s, a) => s + (a.minutes || 0), 0)}</td></tr></tfoot>
          </table></div>
        )}
      </section>

      <div className="grid-2">
        <section className="card" aria-label="Decisions">
          <h2>Decisions</h2>
          {m.decisions.length === 0 && <p className="empty">No decisions recorded.</p>}
          <ul className="wp-checks">
            {m.decisions.map((d) => (
              <li key={d.id}><span>{d.text}</span>
                {manager && <button type="button" className="icon-btn" aria-label={`Remove decision ${d.text}`} onClick={() => act(() => api.del(`/workplace/meetings/${id}/decisions/${d.id}/`))}>×</button>}</li>
            ))}
          </ul>
          {manager && (
            <form className="toolbar" onSubmit={addDecision}>
              <Field id="dc-text" label="Add a decision" className="grow"><input id="dc-text" value={decision} maxLength={1000} onChange={(e) => setDecision(e.target.value)} /></Field>
              <button type="submit" className="btn btn-small">Add</button>
            </form>
          )}
        </section>
        <section className="card" aria-label="Action items">
          <div className="card-head"><h2>Action items</h2>{manager && <button type="button" className="btn btn-small" onClick={() => setDialog('action')}>Add action item</button>}</div>
          {m.actions.length === 0 ? <p className="empty">No action items. They appear in Tasks.</p> : (
            <ul className="wp-list">
              {m.actions.map((t) => (
                <li key={t.id}>
                  <div className="wp-list-main"><button type="button" className="wp-link" onClick={() => setTaskId(t.id)}><strong>{t.title}</strong></button>
                    <div className={`hint${t.overdue ? ' bad-text' : ''}`}>{t.assignee?.name || 'Unassigned'}{t.due_date ? ` · due ${fmtDate(t.due_date)}` : ''}</div></div>
                  <TaskStatusBadge task={t} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="card" aria-label="Minutes">
        <h2>Minutes of meeting</h2>
        {m.minutes.length === 0 ? <p className="empty">No minutes recorded yet.{manager && m.status !== 'cancelled' ? ' Use "Record minutes" above.' : ''}</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Minutes ID</th><th>Issued</th><th>Status</th><th><span className="sr-only">Downloads</span></th></tr></thead>
            <tbody>
              {m.minutes.map((d) => (
                <tr key={d.id}>
                  <td data-label="Minutes ID"><strong>{d.reference_number}</strong></td><td data-label="Issued">{fmtDate(d.issue_date)}</td>
                  <td data-label="Status"><Badge tone={d.status === 'void' ? 'neutral' : 'good'}>{d.status}</Badge></td>
                  <td><div className="row-actions">
                    <button type="button" className="btn btn-small" onClick={() => pdf(d, false)}>Letterhead PDF</button>
                    {d.has_print_file && <button type="button" className="btn btn-small" onClick={() => pdf(d, true)}>Print PDF</button>}
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </section>

      <Attachments entityType="meeting" entityId={m.id} canManage={manager} title="Meeting papers" hint="Agenda packs, presentations and other papers for this meeting." />

      {dialog === 'edit' && <MeetingFormDialog meeting={m} people={people} onClose={() => setDialog(null)} onSaved={done} />}
      {dialog === 'reschedule' && <RescheduleDialog m={m} onClose={() => setDialog(null)} onDone={done} />}
      {dialog === 'generate' && <GenerateDialog m={m} onClose={() => setDialog(null)} onDone={done} />}
      {dialog === 'minutes' && <MinutesDialog m={m} onClose={() => setDialog(null)} onDone={done} />}
      {dialog === 'action' && <TaskFormDialog people={people} defaults={{ source: 'meeting', source_id: m.id }} onClose={() => setDialog(null)} onSaved={done} />}
      {taskId && <TaskDialog id={taskId} people={people} onClose={() => setTaskId(null)} onChanged={reload} />}
      {reasonDialog}
      {confirmDialog}
    </>
  );
}
