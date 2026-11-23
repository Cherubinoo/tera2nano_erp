import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, downloadFile } from '../../api';
import { useAuth } from '../../context';
import { useLoad } from '../../hooks';
import { Badge, ErrorBox, Field, Loading, PageHeader, fmtDate, fmtDateTime, useConfirm, useToast } from '../../ui';
import { ActivityDialog, CompleteDialog, OwnerSelect, PRIORITY_TONE, Pager, query, relatedPath, useLookups } from './common';

const VIEWS = [['today', 'Today'], ['overdue', 'Overdue'], ['upcoming', 'Upcoming'], ['open', 'All open'], ['completed', 'Completed']];

export default function Activities() {
  const { user } = useAuth();
  const notify = useToast();
  const lookups = useLookups();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [params, setParams] = useSearchParams();
  const [dialog, setDialog] = useState(params.get('new') === '1' ? {} : null);
  const [completing, setCompleting] = useState(null);
  const [page, setPage] = useState(1);
  const view = params.get('view') || 'open', type = params.get('type') || '', q = params.get('q') || '', mine = params.get('mine') === '1', owner = params.get('owner') || '';
  const [term, setTerm] = useState(q);
  const canManage = user.permissions.includes('crm.manage');
  const who = mine ? 'me' : owner;
  const { data, error, loading, reload } = useLoad(() => api.get('/crm/activities/', { view, type, q, owner: who, page, page_size: 50 }), [view, type, q, who, page]);
  const setParam = (c) => { setPage(1); setParams(Object.fromEntries(Object.entries({ view, type, q, owner, mine: mine ? '1' : '', ...c }).filter(([, v]) => v)), { replace: true }); };
  const rows = data?.results || [];
  const counts = data?.counts || {};
  const reopen = async (a) => { try { await api.post(`/crm/activities/${a.id}/reopen/`, {}); reload(); } catch (e) { notify(e.message, 'error'); } };
  const remove = async (a) => {
    if (!(await confirm({ title: 'Delete this activity?', body: <p>{a.subject}</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/crm/activities/${a.id}/`); notify('Activity deleted.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const exportCsv = async () => { try { await downloadFile(`/crm/reports/activities.csv${query({ owner: who, type })}`, 'crm-activities.csv'); } catch (e) { notify(e.message, 'error'); } };
  const count = (k) => (k === 'completed' ? counts.completed : counts[k]);
  return (
    <>
      <PageHeader title="Activities" description="Tasks, calls, meetings, notes and logged emails."
        actions={<>
          <button type="button" className="btn" onClick={exportCsv}>Export CSV</button>
          {canManage && <button type="button" className="btn btn-primary" onClick={() => setDialog({})}>New activity</button>}
        </>} />
      <section className="card">
        <div className="chips" role="group" aria-label="Filter activities">
          {VIEWS.map(([k, l]) => <button key={k} type="button" className="chip" aria-pressed={view === k} onClick={() => setParam({ view: k })}>{l}{count(k) !== undefined ? ` (${count(k)})` : ''}</button>)}
          <button type="button" className="chip" aria-pressed={mine} onClick={() => setParam({ mine: mine ? '' : '1', owner: '' })}>My activities</button>
        </div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setParam({ q: term.trim() }); }}>
          <Field id="act-q" label="Search" className="grow"><input id="act-q" value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Subject, details or outcome" /></Field>
          <Field id="act-f-type" label="Type">
            <select id="act-f-type" value={type} onChange={(e) => setParam({ type: e.target.value })}><option value="">Any</option>{(lookups.activity_types || []).map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select>
          </Field>
          {!mine && <OwnerSelect id="act-f-owner" label="Owner" value={owner} onChange={(x) => setParam({ owner: x })} lookups={lookups} any extra={[['none', 'Unassigned']]} />}
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && !data && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {data && rows.length === 0 && <p className="empty">{view === 'completed' ? 'Nothing completed yet.' : view === 'overdue' ? 'Nothing is overdue.' : view === 'today' ? 'Nothing is due today.' : 'No activities here.'}</p>}
        {rows.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Activity</th><th>Related to</th><th>{view === 'completed' ? 'Completed' : 'Due'}</th><th>Priority</th><th>Owner</th>{canManage && <th><span className="sr-only">Actions</span></th>}</tr></thead>
            <tbody>{rows.map((a) => (
              <tr key={a.id} className={a.bucket === 'overdue' ? 'crm-row-overdue' : ''}>
                <td data-label="Activity"><Badge tone={a.status === 'completed' ? 'good' : 'info'}>{a.type_label}</Badge> <strong>{a.subject}</strong>
                  {a.description && <span className="crm-sub">{a.description}</span>}{a.outcome && <span className="crm-sub">Outcome: {a.outcome}</span>}</td>
                <td data-label="Related to">{a.related ? <Link to={relatedPath(a.related)}>{a.related.name}</Link> : '—'}<span className="crm-sub">{a.related?.type}</span></td>
                <td data-label={view === 'completed' ? 'Completed' : 'Due'}>
                  {a.status === 'completed' ? fmtDateTime(a.completed_at) : <>{fmtDate(a.due_date)}{a.due_time ? ` ${a.due_time}` : ''}{a.bucket === 'overdue' && <> <Badge tone="bad">Overdue</Badge></>}{a.bucket === 'today' && <> <Badge tone="warn">Today</Badge></>}</>}
                </td>
                <td data-label="Priority"><Badge tone={PRIORITY_TONE[a.priority]}>{a.priority}</Badge></td>
                <td data-label="Owner">{a.owner ? a.owner.name : '—'}</td>
                {canManage && (
                  <td className="row-actions">
                    {a.status === 'open' && a.type !== 'note' && <button type="button" className="btn btn-small btn-primary" aria-label={`Complete ${a.subject}`} onClick={() => setCompleting(a)}>Complete</button>}
                    {a.status === 'completed' && a.type !== 'note' && <button type="button" className="btn btn-small" onClick={() => reopen(a)}>Reopen</button>}
                    <button type="button" className="btn btn-small" onClick={() => setDialog(a)}>Edit</button>
                    <button type="button" className="btn btn-small" onClick={() => remove(a)}>Delete</button>
                  </td>
                )}
              </tr>))}</tbody>
          </table></div>
        )}
        {data && <Pager page={data.page} count={data.count} size={data.page_size} onPage={setPage} />}
        {rows.length > 0 && <div className="list-foot"><span>{data.count} activit{data.count === 1 ? 'y' : 'ies'}</span></div>}
      </section>
      {dialog && lookups.ready && <ActivityDialog record={dialog.id ? dialog : null} lookups={lookups} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); reload(); }} />}
      {completing && <CompleteDialog activity={completing} onClose={() => setCompleting(null)} onDone={() => { setCompleting(null); reload(); }} />}
      {confirmDialog}
    </>
  );
}
