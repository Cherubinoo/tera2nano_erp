import { useState } from 'react';
import { api } from '../../api';
import { today } from '../../format';
import { Dialog, Field, FormErrors, useToast } from '../../ui';
import { MEETING_TYPES, PersonSelect } from './common';

const blankExternal = () => ({ name: '', designation: '' });
const blankAgenda = () => ({ title: '', owner_name: '', minutes: '' });

// Schedule a meeting, or edit one (`meeting` is the detail record). Attendees are people with a login plus outside names.
export default function MeetingFormDialog({ meeting, people, onClose, onSaved, defaultDate }) {
  const notify = useToast();
  const editing = !!meeting;
  const [v, setV] = useState({
    title: meeting?.title || '', meeting_type: meeting?.meeting_type || 'team', date: meeting?.date || defaultDate || today(),
    start_time: meeting?.start_time || '', end_time: meeting?.end_time || '', venue: meeting?.venue || '', link: meeting?.link || '',
    organiser: meeting?.organiser?.id || '', description: meeting?.description || '', recurrence: meeting?.recurrence || 'none', occurrences: 3,
  });
  const [users, setUsers] = useState((meeting?.attendees || []).filter((a) => a.user).map((a) => a.user));
  const [externals, setExternals] = useState((meeting?.attendees || []).filter((a) => !a.user).map((a) => ({ name: a.name, designation: a.designation })));
  const [agenda, setAgenda] = useState((meeting?.agenda || []).map((a) => ({ title: a.title, owner_name: a.owner_name, minutes: a.minutes ?? '' })));
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const toggle = (id) => setUsers((u) => (u.includes(id) ? u.filter((x) => x !== id) : [...u, id]));
  const row = (list, setList, i, k) => (e) => setList(list.map((r, n) => (n === i ? { ...r, [k]: e.target.value } : r)));

  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setFailure(null);
    const errs = {};
    if (!v.title.trim()) errs.title = 'Enter the meeting title.';
    if (!v.date) errs.date = 'Choose the meeting date.';
    if (v.end_time && !v.start_time) errs.start_time = 'Enter the start time as well.';
    if (v.start_time && v.end_time && v.end_time <= v.start_time) errs.end_time = 'The end time must be after the start time.';
    if (v.link.trim() && !/^https?:\/\//i.test(v.link.trim())) errs.link = 'A meeting link must start with http:// or https://.';
    if (v.recurrence !== 'none' && !editing && !(Number(v.occurrences) >= 0 && Number(v.occurrences) <= 52)) errs.occurrences = 'Enter a number from 0 to 52.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    const body = {
      title: v.title.trim(), meeting_type: v.meeting_type, date: v.date, start_time: v.start_time, end_time: v.end_time, venue: v.venue.trim(),
      link: v.link.trim(), organiser: v.organiser || null, description: v.description.trim(), recurrence: v.recurrence,
      attendees: [...users.map((id) => ({ user: id })), ...externals.filter((x) => x.name.trim()).map((x) => ({ name: x.name.trim(), designation: x.designation.trim() }))],
      agenda: agenda.filter((a) => a.title.trim()).map((a) => ({ title: a.title.trim(), owner_name: a.owner_name.trim(), minutes: a.minutes === '' ? null : Number(a.minutes) })),
    };
    if (!editing && v.recurrence !== 'none') body.occurrences = Number(v.occurrences) || 0;
    setBusy(true);
    try {
      const saved = editing ? await api.patch(`/workplace/meetings/${meeting.id}/`, body) : await api.post('/workplace/meetings/', body);
      notify(editing ? 'Meeting saved.' : `Meeting scheduled: ${saved.number}`);
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };

  return (
    <Dialog title={editing ? `Edit ${meeting.number}` : 'Schedule a meeting'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={[{ name: 'title' }, { name: 'date' }, { name: 'start_time' }, { name: 'end_time' }, { name: 'link' }, { name: 'organiser' }, { name: 'occurrences' }, { name: 'meeting_type' }, { name: 'venue' }, { name: 'recurrence' }]} />
        <div className="form-grid">
          <Field id="mt-title" label="Title" required wide error={errors.title}><input id="mt-title" value={v.title} maxLength={200} onChange={set('title')} /></Field>
          <Field id="mt-type" label="Type" error={errors.meeting_type}>
            <select id="mt-type" value={v.meeting_type} onChange={set('meeting_type')}>{MEETING_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          </Field>
          <Field id="mt-date" label="Date" required error={errors.date}><input id="mt-date" type="date" value={v.date} onChange={set('date')} /></Field>
          <Field id="mt-start" label="Start time" error={errors.start_time}><input id="mt-start" type="time" value={v.start_time} onChange={set('start_time')} /></Field>
          <Field id="mt-end" label="End time" error={errors.end_time}><input id="mt-end" type="time" value={v.end_time} onChange={set('end_time')} /></Field>
          <Field id="mt-venue" label="Venue" error={errors.venue}><input id="mt-venue" value={v.venue} maxLength={200} onChange={set('venue')} /></Field>
          <Field id="mt-link" label="Online meeting link" error={errors.link}><input id="mt-link" type="url" value={v.link} maxLength={300} onChange={set('link')} placeholder="https://" /></Field>
          <PersonSelect id="mt-org" label="Organiser" value={v.organiser} onChange={(x) => setV((s) => ({ ...s, organiser: x }))} people={people} none="Me" error={errors.organiser} />
          <Field id="mt-rec" label="Repeats" error={errors.recurrence}>
            <select id="mt-rec" value={v.recurrence} onChange={set('recurrence')}>
              <option value="none">Does not repeat</option><option value="weekly">Every week</option><option value="monthly">Every month</option>
            </select>
          </Field>
          {v.recurrence !== 'none' && !editing && (
            <Field id="mt-occ" label="Further meetings to create" hint="Created now, one per week or month after this date." error={errors.occurrences}>
              <input id="mt-occ" type="number" min="0" max="52" value={v.occurrences} onChange={set('occurrences')} />
            </Field>
          )}
          <Field id="mt-desc" label="Description" wide error={errors.description}><textarea id="mt-desc" rows={2} value={v.description} onChange={set('description')} /></Field>
        </div>

        <fieldset className="subcard">
          <legend>Attendees from the team</legend>
          <div className="wp-people" role="group" aria-label="Attendees from the team">
            {people.map((p) => (
              <label key={p.id} className="check"><input type="checkbox" checked={users.includes(p.id)} onChange={() => toggle(p.id)} /> {p.name} <span className="hint">{p.role_label}</span></label>
            ))}
            {people.length === 0 && <p className="hint">No people to choose from.</p>}
          </div>
          <h4>Outside attendees</h4>
          {externals.map((x, i) => (
            <div className="wp-row" key={i}>
              <Field id={`mt-xn-${i}`} label={`Name ${i + 1}`}><input id={`mt-xn-${i}`} value={x.name} onChange={row(externals, setExternals, i, 'name')} /></Field>
              <Field id={`mt-xd-${i}`} label={`Designation or company ${i + 1}`}><input id={`mt-xd-${i}`} value={x.designation} onChange={row(externals, setExternals, i, 'designation')} /></Field>
              <button type="button" className="btn btn-small" onClick={() => setExternals(externals.filter((_, n) => n !== i))}>Remove</button>
            </div>
          ))}
          <button type="button" className="btn btn-small" onClick={() => setExternals([...externals, blankExternal()])}>Add outside attendee</button>
          {errors.attendees && <div className="error-text" role="alert">{errors.attendees}</div>}
        </fieldset>

        <fieldset className="subcard">
          <legend>Agenda</legend>
          {agenda.map((a, i) => (
            <div className="wp-row" key={i}>
              <Field id={`mt-at-${i}`} label={`Item ${i + 1}`}><input id={`mt-at-${i}`} value={a.title} onChange={row(agenda, setAgenda, i, 'title')} /></Field>
              <Field id={`mt-ao-${i}`} label={`Owner ${i + 1}`}><input id={`mt-ao-${i}`} value={a.owner_name} onChange={row(agenda, setAgenda, i, 'owner_name')} /></Field>
              <Field id={`mt-am-${i}`} label={`Minutes ${i + 1}`}><input id={`mt-am-${i}`} type="number" min="1" max="600" value={a.minutes} onChange={row(agenda, setAgenda, i, 'minutes')} /></Field>
              <button type="button" className="btn btn-small" onClick={() => setAgenda(agenda.filter((_, n) => n !== i))}>Remove</button>
            </div>
          ))}
          <button type="button" className="btn btn-small" onClick={() => setAgenda([...agenda, blankAgenda()])}>Add agenda item</button>
          {errors.agenda && <div className="error-text" role="alert">{errors.agenda}</div>}
        </fieldset>

        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : editing ? 'Save meeting' : 'Schedule meeting'}</button>
        </div>
      </form>
    </Dialog>
  );
}
