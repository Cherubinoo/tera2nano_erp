import { useMemo, useState } from 'react';
import { api } from '../api';
import { useLookups } from '../context';
import { localToday } from '../forms';
import { ErrorBox, Field, FormErrors, Notice, useConfirm, useToast } from '../ui';
import { Downloads, IssuedList } from './Letters';

const today = localToday;
const blankAttendee = () => ({ name: '', designation: '', status: 'present' });
const blankItem = () => ({ heading: '', discussion: '', decision: '' });
const blankAction = () => ({ action: '', owner: '', due_date: '' });
const lines = (text) => text.split('\n').map((l) => l.trim()).filter(Boolean);

// A list of editable rows with "add" and "remove". `columns` describe the inputs of one row.
export function Rows({ title, hint, rows, setRows, blank, columns, noun, minRows = 0 }) {
  const update = (i, key, value) => setRows(rows.map((r, n) => (n === i ? { ...r, [key]: value } : r)));
  return (
    <fieldset className="subcard">
      <legend>{title}</legend>
      {hint && <p className="hint">{hint}</p>}
      {rows.map((row, i) => (
        <div className="row-editor" key={i}>
          <div className="form-grid">
            {columns.map((c) => (
              <Field key={c.key} id={`${noun}-${i}-${c.key}`} label={`${c.label}${rows.length > 1 || c.always ? ` ${i + 1}` : ''}`} wide={c.wide}>
                {c.type === 'select' ? (
                  <select id={`${noun}-${i}-${c.key}`} value={row[c.key]} onChange={(e) => update(i, c.key, e.target.value)}>
                    {c.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                ) : c.type === 'textarea' ? (
                  <textarea id={`${noun}-${i}-${c.key}`} rows={c.rows || 3} value={row[c.key]} onChange={(e) => update(i, c.key, e.target.value)} />
                ) : (
                  <input id={`${noun}-${i}-${c.key}`} type={c.type || 'text'} value={row[c.key]} onChange={(e) => update(i, c.key, e.target.value)} />
                )}
              </Field>
            ))}
          </div>
          {rows.length > minRows && (
            <button type="button" className="btn btn-small" onClick={() => setRows(rows.filter((_, n) => n !== i))}>Remove {noun} {i + 1}</button>
          )}
        </div>
      ))}
      <button type="button" className="btn btn-small" onClick={() => setRows([...rows, blank()])}>Add {noun}</button>
    </fieldset>
  );
}

export function MinutesManager() {
  const lookups = useLookups();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const templates = useMemo(() => (lookups.letterhead_templates || []).filter((t) => t.is_active && (t.kind || 'letter') === 'letter'), [lookups]);
  const [meeting, setMeeting] = useState({
    title: '', date: today(), start_time: '', end_time: '', venue: '', mode: 'in_person', minute_taker: '',
    next_meeting: '', meeting_id: '', template: 'none',
  });
  const [attendees, setAttendees] = useState([blankAttendee()]);
  const [agenda, setAgenda] = useState('');
  const [items, setItems] = useState([blankItem()]);
  const [actions, setActions] = useState([blankAction()]);
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const set = (name) => (e) => { setMeeting((m) => ({ ...m, [name]: e.target.value })); setErrors((x) => ({ ...x, [name]: undefined })); };

  const reset = () => {
    setMeeting((m) => ({ ...m, title: '', start_time: '', end_time: '', venue: '', next_meeting: '', meeting_id: '' }));
    setAttendees([blankAttendee()]); setAgenda(''); setItems([blankItem()]); setActions([blankAction()]);
  };

  const submit = async (e) => {
    e.preventDefault();
    setFailure(null);
    const content = {
      title: meeting.title.trim(), date: meeting.date, start_time: meeting.start_time, end_time: meeting.end_time,
      venue: meeting.venue.trim(), mode: meeting.mode,
      minute_taker: meeting.minute_taker.trim(), next_meeting: meeting.next_meeting.trim(),
      attendees: attendees.filter((a) => a.name.trim()).map((a) => ({ ...a, name: a.name.trim(), designation: a.designation.trim() })),
      agenda: lines(agenda),
      items: items.filter((i) => i.heading.trim()).map((i) => ({ heading: i.heading.trim(), discussion: i.discussion.trim(), decision: i.decision.trim() })),
      actions: actions.filter((a) => a.action.trim()).map((a) => ({ action: a.action.trim(), owner: a.owner.trim(), due_date: a.due_date })),
    };
    if (meeting.meeting_id.trim()) content.meeting_id = meeting.meeting_id.trim();
    const errs = {};
    if (!content.title) errs.title = 'Enter the meeting title.';
    if (!meeting.date) errs.date = 'Choose the meeting date.';
    if (content.start_time && content.end_time && content.end_time <= content.start_time) errs.end_time = 'The end time must be after the start time.';
    if (!content.attendees.length && !content.agenda.length && !content.items.length) {
      errs.attendees = 'Add attendees, an agenda or at least one discussion item.';
    }
    setErrors(errs);
    if (Object.keys(errs).length) return;
    const template = meeting.template === 'none' ? '' : meeting.template;
    const ok = await confirm({
      title: 'Issue these minutes?',
      body: (
        <>
          <p>A unique minutes ID{meeting.meeting_id.trim() ? '' : ' and a new meeting ID'} are allocated now. The minutes are saved exactly as written and cannot be edited afterwards; if they are wrong, void them and issue again.</p>
          <p>The minutes have blank space at the bottom right for your company seal. Issuing does not record any approval.</p>
        </>
      ),
      confirmLabel: 'Issue minutes',
    });
    if (!ok) return;
    setBusy(true);
    try {
      const doc = await api.post('/company/issued-documents/', {
        document_type: 'meeting_minutes', issue_date: meeting.date, content: { minutes: content },
        ...(template ? { letterhead_template: template } : {}),
      });
      setIssued(doc);
      setRefreshKey((k) => k + 1);
      reset();
      notify(`Minutes issued: ${doc.reference_number}`);
    } catch (err) {
      setFailure(err);
      setErrors(err.fieldErrors || {});
    }
    setBusy(false);
  };

  return (
    <>
      <form className="card" onSubmit={submit} noValidate>
        <div className="card-head">
          <div>
            <h2>New minutes of meeting</h2>
            <p className="muted">Fill in the meeting, attendees, agenda, decisions and action items. You get a minutes ID, a meeting ID, a PDF with the letterhead and a plain PDF for your pre-printed paper.</p>
          </div>
        </div>
        <FormErrors error={failure} fields={[]} />
        <div className="form-grid">
          <Field id="mn-title" label="Meeting title" required error={errors.title} wide>
            <input id="mn-title" value={meeting.title} maxLength={200} onChange={set('title')} />
          </Field>
          <Field id="mn-date" label="Meeting date" required error={errors.date}>
            <input id="mn-date" type="date" value={meeting.date} onChange={set('date')} />
          </Field>
          <Field id="mn-start" label="Start time" error={errors.start_time}>
            <input id="mn-start" type="time" value={meeting.start_time} onChange={set('start_time')} />
          </Field>
          <Field id="mn-end" label="End time" error={errors.end_time}>
            <input id="mn-end" type="time" value={meeting.end_time} onChange={set('end_time')} />
          </Field>
          <Field id="mn-venue" label="Venue" error={errors.venue}>
            <input id="mn-venue" value={meeting.venue} onChange={set('venue')} />
          </Field>
          <Field id="mn-mode" label="Mode" error={errors.mode}>
            <select id="mn-mode" value={meeting.mode} onChange={set('mode')}>
              <option value="in_person">In person</option><option value="online">Online</option><option value="hybrid">Hybrid</option>
            </select>
          </Field>
          <Field id="mn-taker" label="Minutes taken by" error={errors.minute_taker}>
            <input id="mn-taker" value={meeting.minute_taker} onChange={set('minute_taker')} />
          </Field>
          <Field id="mn-template" label="Minutes ID format" hint="Standard numbering gives e.g. T2N-MOM-0001; a template gives e.g. LHT-006/26/2710/00001.">
            <select id="mn-template" value={meeting.template} onChange={set('template')}>
              <option value="none">Standard minutes numbering</option>
              {templates.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </Field>
          <Field id="mn-meeting-id" label="Existing meeting ID (optional)" error={errors.meeting_id}
            hint="Leave empty to allocate a new meeting ID. Fill it in to record another set of minutes for the same meeting.">
            <input id="mn-meeting-id" value={meeting.meeting_id} maxLength={60} onChange={set('meeting_id')} />
          </Field>
        </div>

        {errors.attendees && <p className="error-text" role="alert">{errors.attendees}</p>}
        <Rows title="Attendees" rows={attendees} setRows={setAttendees} blank={blankAttendee} noun="attendee" minRows={1}
          columns={[
            { key: 'name', label: 'Attendee name' },
            { key: 'designation', label: 'Designation / organisation' },
            { key: 'status', label: 'Attendance', type: 'select', options: [
              { value: 'present', label: 'Present' }, { value: 'absent', label: 'Absent' }, { value: 'apologies', label: 'Apologies' }] },
          ]} />

        <Field id="mn-agenda" label="Agenda (one item per line)" error={errors.agenda} wide>
          <textarea id="mn-agenda" rows={4} value={agenda} onChange={(e) => setAgenda(e.target.value)} />
        </Field>

        <Rows title="Discussion and decisions" rows={items} setRows={setItems} blank={blankItem} noun="item" minRows={1}
          hint="Rows without a heading are ignored."
          columns={[
            { key: 'heading', label: 'Heading', wide: true },
            { key: 'discussion', label: 'Discussion', type: 'textarea', wide: true },
            { key: 'decision', label: 'Decision', type: 'textarea', rows: 2, wide: true },
          ]} />

        <Rows title="Action items" rows={actions} setRows={setActions} blank={blankAction} noun="action" minRows={1}
          hint="Rows without an action are ignored."
          columns={[
            { key: 'action', label: 'Action', wide: true },
            { key: 'owner', label: 'Owner' },
            { key: 'due_date', label: 'Due date', type: 'date' },
          ]} />

        <div className="form-grid">
          <Field id="mn-next" label="Next meeting" error={errors.next_meeting} wide>
            <input id="mn-next" value={meeting.next_meeting} maxLength={300} onChange={set('next_meeting')} placeholder="e.g. 27 November 2026, 10:00" />
          </Field>
        </div>
        <div className="actions">
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Issuing…' : 'Issue minutes'}</button>
        </div>
      </form>

      {issued && (
        <Notice type="success">
          <p><strong>Minutes issued.</strong> Minutes ID: <code>{issued.reference_number}</code>
            {issued.source_id && <> &nbsp;Meeting ID: <code>{issued.source_id}</code></>}</p>
          <div className="row-actions"><Downloads doc={issued} onError={(e) => notify(e.message, 'error')} /></div>
          <p className="hint">Print the plain "Print PDF" on your letterhead paper and stamp the company seal in the space at the bottom right.</p>
        </Notice>
      )}

      <IssuedList refreshKey={refreshKey} types={['meeting_minutes']} title="Issued minutes" emptyText="No minutes issued yet." showMeeting />
      {confirmDialog}
    </>
  );
}
