import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, fmtDate, useConfirm, useToast } from '../../ui';
import { PersonSelect, Tile, usePeople } from './common';

const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }) : '—');
const localNow = () => { const d = new Date(); d.setSeconds(0, 0); return `${today()}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };

function VisitorDialog({ people, onClose, onSaved }) {
  const notify = useToast();
  const [v, setV] = useState({ name: '', visitor_company: '', phone: '', purpose: '', host: '', host_name: '', time_in: localNow(), notes: '' });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setFailure(null);
    const errs = {};
    if (!v.name.trim()) errs.name = 'Enter the visitor\'s name.';
    if (!v.purpose.trim()) errs.purpose = 'Enter the purpose of the visit.';
    if (!v.time_in) errs.time_in = 'Enter the time in.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      await api.post('/workplace/visitors/', { ...v, name: v.name.trim(), purpose: v.purpose.trim(), host: v.host || null });
      notify('Visitor signed in.');
      onSaved();
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title="Sign in a visitor" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={Object.keys(v).map((name) => ({ name }))} />
        <div className="form-grid">
          <Field id="vs-name" label="Visitor name" required error={errors.name}><input id="vs-name" value={v.name} maxLength={200} onChange={set('name')} /></Field>
          <Field id="vs-co" label="Company" error={errors.visitor_company}><input id="vs-co" value={v.visitor_company} onChange={set('visitor_company')} /></Field>
          <Field id="vs-phone" label="Phone" error={errors.phone}><input id="vs-phone" type="tel" value={v.phone} onChange={set('phone')} /></Field>
          <Field id="vs-in" label="Time in" required error={errors.time_in}><input id="vs-in" type="datetime-local" value={v.time_in} onChange={set('time_in')} /></Field>
          <Field id="vs-purpose" label="Purpose of visit" required wide error={errors.purpose}><input id="vs-purpose" value={v.purpose} maxLength={300} onChange={set('purpose')} /></Field>
          <PersonSelect id="vs-host" label="Meeting (host)" value={v.host} onChange={(x) => setV((s) => ({ ...s, host: x }))} people={people} none="Someone else" error={errors.host} />
          {!v.host && <Field id="vs-hn" label="Host name" error={errors.host_name}><input id="vs-hn" value={v.host_name} onChange={set('host_name')} /></Field>}
          <Field id="vs-notes" label="Notes" wide error={errors.notes}><input id="vs-notes" value={v.notes} maxLength={300} onChange={set('notes')} /></Field>
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Sign in'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export default function VisitorsPage() {
  const people = usePeople();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [params, setParams] = useSearchParams();
  const [scope, setScope] = useState('inside');
  const [date, setDate] = useState(today());
  const [q, setQ] = useState('');
  const [creating, setCreating] = useState(params.get('new') === '1');
  useEffect(() => { if (params.get('new')) setParams({}, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const query = { q: q || undefined, inside: scope === 'inside' ? 1 : undefined, date: scope === 'day' ? date : undefined };
  const { data, error, loading, reload } = useLoad(() => api.get('/workplace/visitors/', query), [JSON.stringify(query)]);
  const todayStats = useLoad(() => api.get('/workplace/visitors/', { date: today() }), [data?.count]);
  const rows = data?.results || [];

  const checkout = async (v) => {
    try { await api.post(`/workplace/visitors/${v.id}/checkout/`); notify(`${v.name} signed out.`); reload(); } catch (err) { notify(err.message, 'error'); }
  };
  const remove = async (v) => {
    if (!(await confirm({ title: `Delete the entry for ${v.name}?`, body: <p>The entry is removed from the log. This is recorded in the change history.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/workplace/visitors/${v.id}/`); reload(); } catch (err) { notify(err.message, 'error'); }
  };

  return (
    <>
      <PageHeader title="Visitor log" description="Who came to the office, whom they met, and when they left."
        actions={<button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>Sign in a visitor</button>} />
      <div className="tiles">
        <Tile label="In the office now" value={todayStats.data?.inside ?? '…'} hint="Not yet signed out" />
        <Tile label="Visitors today" value={todayStats.data?.count ?? '…'} hint="Signed in since midnight" />
      </div>
      <section className="card">
        <div className="chips" role="group" aria-label="Show">
          {[['inside', 'In the office'], ['day', 'A day'], ['all', 'All']].map(([k, l]) => <button key={k} type="button" className="chip" aria-pressed={scope === k} onClick={() => setScope(k)}>{l}</button>)}
        </div>
        <form className="toolbar" onSubmit={(e) => e.preventDefault()}>
          <Field id="vs-q" label="Search" className="grow"><input id="vs-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, company or purpose" /></Field>
          {scope === 'day' && <Field id="vs-date" label="Day"><input id="vs-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>}
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && rows.length === 0 && <p className="empty">No visitors {scope === 'inside' ? 'in the office' : 'found'}.</p>}
        {rows.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Date</th><th>Visitor</th><th>Purpose</th><th>Host</th><th>In</th><th>Out</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((v) => (
                <tr key={v.id}>
                  <td data-label="Date">{fmtDate(v.time_in.slice(0, 10))}</td>
                  <td data-label="Visitor"><strong>{v.name}</strong>{v.visitor_company && <div className="hint">{v.visitor_company}</div>}</td>
                  <td data-label="Purpose">{v.purpose}</td>
                  <td data-label="Host">{v.host_name || '—'}</td>
                  <td data-label="In">{clock(v.time_in)}</td>
                  <td data-label="Out">{v.inside ? <Badge tone="info">Inside</Badge> : clock(v.time_out)}</td>
                  <td><div className="row-actions">
                    {v.inside && <button type="button" className="btn btn-small" onClick={() => checkout(v)}>Sign out</button>}
                    <button type="button" className="btn btn-small btn-danger-outline" onClick={() => remove(v)}>Delete</button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
        {data && rows.length > 0 && <div className="list-foot"><span>{data.count} visitor{data.count === 1 ? '' : 's'}</span></div>}
      </section>
      {creating && <VisitorDialog people={people} onClose={() => setCreating(false)} onSaved={() => { setCreating(false); reload(); }} />}
      {confirmDialog}
    </>
  );
}
