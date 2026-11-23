import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, downloadFile } from '../../api';
import Attachments from '../../components/Attachments';
import { today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, Tabs, fmtDate, fmtDateTime, useConfirm, useToast } from '../../ui';
import { AudienceFields, AudienceText, Markdown, useCan } from './common';

const STATUS_TONE = { draft: 'neutral', issued: 'info' };

function CircularDialog({ item, onClose, onSaved }) {
  const notify = useToast();
  const [v, setV] = useState({
    subject: item?.subject || '', body: item?.body || '', addressed_to: item?.addressed_to || '', audience: item?.audience || 'all',
    roles: item?.roles || [], issue_date: item?.issue_date || today(),
  });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setFailure(null);
    const errs = {};
    if (!v.subject.trim()) errs.subject = 'Enter the subject.';
    if (!v.body.trim()) errs.body = 'Enter the text of the circular.';
    if (!v.issue_date) errs.issue_date = 'Choose the issue date.';
    if (v.audience === 'roles' && v.roles.length === 0) errs.roles = 'Choose at least one role.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const saved = item ? await api.patch(`/workplace/circulars/${item.id}/`, { ...v, subject: v.subject.trim() }) : await api.post('/workplace/circulars/', { ...v, subject: v.subject.trim() });
      notify(item ? 'Draft saved.' : 'Draft circular created. Issue it when it is ready.');
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title={item ? 'Edit draft circular' : 'New circular'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={[{ name: 'subject' }, { name: 'body' }, { name: 'addressed_to' }, { name: 'issue_date' }, { name: 'audience' }, { name: 'roles' }]} />
        <div className="form-grid">
          <Field id="ci-subject" label="Subject" required wide error={errors.subject}><input id="ci-subject" value={v.subject} maxLength={250} onChange={set('subject')} /></Field>
          <Field id="ci-to" label="Addressed to" hint="Printed after “To”. Leave empty to use the audience." error={errors.addressed_to}><input id="ci-to" value={v.addressed_to} maxLength={300} onChange={set('addressed_to')} placeholder="All employees" /></Field>
          <Field id="ci-date" label="Issue date" required error={errors.issue_date}><input id="ci-date" type="date" value={v.issue_date} onChange={set('issue_date')} /></Field>
          <AudienceFields prefix="ci" audience={v.audience} roles={v.roles} error={errors} onChange={(c) => setV((x) => ({ ...x, ...c }))} />
          <Field id="ci-body" label="Text of the circular" required wide error={errors.body} hint="Markdown works: **bold**, lists with -, headings with #."><textarea id="ci-body" rows={9} value={v.body} onChange={set('body')} /></Field>
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save draft'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export function CircularsPage() {
  const can = useCan();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const manager = can('workplace.manage');
  const [status, setStatus] = useState('');
  const [pending, setPending] = useState(params.get('pending') === '1');
  const [q, setQ] = useState('');
  const [creating, setCreating] = useState(params.get('new') === '1' && manager);
  useEffect(() => { if (params.get('new') || params.get('pending')) setParams({}, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const { data, error, loading, reload } = useLoad(() => api.get('/workplace/circulars/', { status: status || undefined, q: q || undefined, pending: pending ? 1 : undefined }), [status, q, pending]);
  const rows = data?.results || [];
  return (
    <>
      <PageHeader title="Circulars" description="Formal notices on the company letterhead, with acknowledgement tracking."
        actions={manager && <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>New circular</button>} />
      <section className="card">
        <div className="chips" role="group" aria-label="Filter">
          {(manager ? [['', 'All'], ['draft', 'Drafts'], ['issued', 'Issued']] : [['', 'All']]).map(([k, l]) => (
            <button key={k} type="button" className="chip" aria-pressed={status === k} onClick={() => setStatus(k)}>{l}</button>
          ))}
          <button type="button" className="chip" aria-pressed={pending} onClick={() => setPending(!pending)}>Waiting for my acknowledgement</button>
        </div>
        <form className="toolbar" onSubmit={(e) => e.preventDefault()}>
          <Field id="ci-q" label="Search" className="grow"><input id="ci-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Subject or number" /></Field>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && rows.length === 0 && <p className="empty">No circulars{pending ? ' waiting for you' : ''}.</p>}
        {rows.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Number</th><th>Subject</th><th>Date</th><th>To</th><th>Status</th><th>{manager ? 'Acknowledged' : 'My acknowledgement'}</th></tr></thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="clickable" onClick={() => navigate(`/workplace/circulars/${c.id}`)}>
                  <td data-label="Number">{c.number || <em>Draft</em>}</td>
                  <td data-label="Subject"><Link to={`/workplace/circulars/${c.id}`} onClick={(e) => e.stopPropagation()}><strong>{c.subject}</strong></Link></td>
                  <td data-label="Date">{fmtDate(c.issue_date)}</td>
                  <td data-label="To"><AudienceText row={c} /></td>
                  <td data-label="Status"><Badge tone={STATUS_TONE[c.status]}>{c.status_label}</Badge></td>
                  <td data-label="Acknowledged">
                    {c.status !== 'issued' ? '—' : manager ? `${c.acknowledged_count} of ${c.recipients}` : c.acknowledged ? <Badge tone="good">Acknowledged</Badge> : <Badge tone="warn">Waiting</Badge>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </section>
      {creating && <CircularDialog onClose={() => setCreating(false)} onSaved={(c) => { setCreating(false); navigate(`/workplace/circulars/${c.id}`); }} />}
    </>
  );
}

export function CircularDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const can = useCan();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const manager = can('workplace.manage');
  const { data: c, error, loading, reload } = useLoad(() => api.get(`/workplace/circulars/${id}/`), [id]);
  const report = useLoad(() => (manager ? api.get(`/workplace/circulars/${id}/acknowledgements/`).catch(() => null) : Promise.resolve(null)), [id, manager, c?.acknowledged_count]);
  const [editing, setEditing] = useState(false);
  const [failure, setFailure] = useState(null);
  const [tab, setTab] = useState('all');

  if (loading && !c) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!c) return null;
  const act = async (fn, ok) => { setFailure(null); try { await fn(); if (ok) notify(ok); reload(); } catch (err) { setFailure(err); } };
  const issue = async () => {
    if (!(await confirm({ title: 'Issue this circular?', body: <><p>It is given a reference number and printed on the company letterhead. After this it cannot be edited or deleted.</p><p>{c.audience === 'all' ? 'Everyone' : c.role_labels.join(', ')} will see it and be asked to acknowledge it.</p></>, confirmLabel: 'Issue circular' }))) return;
    act(() => api.post(`/workplace/circulars/${id}/issue/`), 'Circular issued.');
  };
  const remove = async () => {
    if (!(await confirm({ title: 'Delete this draft?', body: <p>The draft is removed. This is recorded in the change history.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/workplace/circulars/${id}/`); notify('Draft deleted.'); navigate('/workplace/circulars'); } catch (err) { setFailure(err); }
  };
  const pdf = (print) => downloadFile(`/workplace/circulars/${id}/pdf/${print ? '?variant=print' : ''}`, `${c.number.replace(/\//g, '-')}${print ? '-print' : ''}.pdf`).catch((e) => setFailure(e));
  const rep = report.data;
  const rows = (rep?.rows || []).filter((r) => tab === 'all' || (tab === 'pending' ? !r.acknowledged : r.acknowledged));
  const pct = rep && rep.recipients ? Math.round((rep.acknowledged / rep.recipients) * 100) : 0;

  return (
    <>
      <Link className="back-link" to="/workplace/circulars">‹ Circulars</Link>
      <header className="doc-head">
        <div>
          <h1>{c.subject} <Badge tone={STATUS_TONE[c.status]}>{c.status_label}</Badge></h1>
          <p className="muted">{c.number || 'Draft, not yet numbered'} · {fmtDate(c.issue_date)} · to <AudienceText row={c} /></p>
        </div>
        <div className="page-actions">
          {c.status === 'issued' && !c.acknowledged && <button type="button" className="btn btn-primary" onClick={() => act(() => api.post(`/workplace/circulars/${id}/acknowledge/`), 'Thank you. Your acknowledgement is recorded.')}>I have read and acknowledge this</button>}
          {c.status === 'issued' && <button type="button" className="btn" onClick={() => pdf(false)}>Letterhead PDF</button>}
          {c.status === 'issued' && c.issued?.has_print_file && <button type="button" className="btn" onClick={() => pdf(true)}>Print PDF</button>}
          {manager && c.status === 'draft' && (<>
            <button type="button" className="btn btn-primary" onClick={issue}>Issue circular</button>
            <button type="button" className="btn" onClick={() => setEditing(true)}>Edit</button>
            <button type="button" className="btn btn-danger-outline" onClick={remove}>Delete</button>
          </>)}
        </div>
      </header>
      <FormErrors error={failure} fields={[]} />
      {c.acknowledged && <div className="notice notice-success">You acknowledged this circular on {fmtDateTime(c.acknowledged_at)}.</div>}

      <section className="card" aria-label="Circular text">
        {c.addressed_to && <p className="hint">To: {c.addressed_to}</p>}
        <Markdown text={c.body} />
      </section>

      {manager && c.status === 'issued' && (
        <section className="card" aria-label="Acknowledgements">
          <h2>Acknowledgements</h2>
          {!rep && <Loading />}
          {rep && (
            <>
              <p><strong>{rep.acknowledged}</strong> of <strong>{rep.recipients}</strong> acknowledged · {rep.pending} still waiting</p>
              <progress className="wp-progress" max={100} value={pct} aria-label={`${pct} percent acknowledged`} />
              <Tabs tabs={[{ key: 'all', label: `Everyone (${rep.recipients})` }, { key: 'pending', label: `Waiting (${rep.pending})` }, { key: 'done', label: `Acknowledged (${rep.acknowledged})` }]} value={tab} onChange={setTab} />
              <div className="table-wrap"><table>
                <thead><tr><th>Person</th><th>Role</th><th>Status</th><th>Acknowledged on</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.user_id}><td data-label="Person">{r.name}</td><td data-label="Role">{r.role_label}</td>
                      <td data-label="Status">{r.acknowledged ? <Badge tone="good">Acknowledged</Badge> : <Badge tone="warn">Waiting</Badge>}</td>
                      <td data-label="Acknowledged on">{r.acknowledged ? fmtDateTime(r.acknowledged_at) : '—'}</td></tr>
                  ))}
                </tbody>
              </table></div>
            </>
          )}
        </section>
      )}

      <Attachments entityType="circular" entityId={c.id} canManage={manager} title="Enclosures" hint="Documents sent with this circular." />
      {editing && <CircularDialog item={c} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} />}
      {confirmDialog}
    </>
  );
}
