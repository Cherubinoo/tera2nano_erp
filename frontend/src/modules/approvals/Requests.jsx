import { useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { downloadCsv, money, plain } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Field, Loading, PageHeader, Tabs, fmtDateTime } from '../../ui';
import { DecisionDialog, StatusBadge, ageText, canSeeAll, isInternal, useKinds, useSummary } from './common';

const STATUS_CHIPS = {
  inbox: [['', 'All waiting for me'], ['pending', 'Pending'], ['escalated', 'Escalated']],
  mine: [['', 'All'], ['open', 'Open'], ['approved', 'Approved'], ['rejected', 'Rejected'], ['cancelled', 'Withdrawn']],
  all: [['', 'All'], ['open', 'Open'], ['escalated', 'Escalated'], ['approved', 'Approved'], ['rejected', 'Rejected'], ['cancelled', 'Withdrawn']],
};
const AGES = [['', 'Any age'], ['1', 'Over 1 day'], ['3', 'Over 3 days'], ['7', 'Over 7 days'], ['30', 'Over 30 days']];
const TITLES = {
  inbox: ['Pending my approval', 'Requests waiting for your decision.'],
  mine: ['My requests', 'Everything you have sent for approval and where it stands.'],
  all: ['All approvals', 'Every approval request in the company.'],
};

export default function Requests({ scope = 'inbox' }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { kinds } = useKinds();
  const { summary, reload: reloadSummary } = useSummary();
  const f = {
    status: params.get('status') || '', kind: params.get('kind') || '', min: params.get('min') || '', max: params.get('max') || '',
    age: params.get('age') || '', q: params.get('q') || '',
  };
  const [q, setQ] = useState(f.q);
  const [selected, setSelected] = useState(new Set());
  const [dialog, setDialog] = useState(null);
  const query = useMemo(() => ({
    scope, status: f.status || undefined, kind: f.kind || undefined, min_amount: f.min || undefined, max_amount: f.max || undefined,
    older_than_days: f.age || undefined, q: f.q || undefined,
  }), [scope, f.status, f.kind, f.min, f.max, f.age, f.q]);
  const { data, error, loading, reload } = useLoad(() => api.get('/approvals/requests/', query), [JSON.stringify(query)]);
  const rows = data?.results || [];
  const setFilter = (changes) => {
    const next = { ...f, ...changes };
    setParams(Object.fromEntries(Object.entries(next).filter(([, v]) => v)), { replace: true });
    setSelected(new Set());
  };
  const tabs = [
    { key: 'inbox', label: `Pending my approval${summary.pending_for_me ? ` (${summary.pending_for_me})` : ''}` },
    { key: 'mine', label: `My requests${summary.my_open_requests ? ` (${summary.my_open_requests} open)` : ''}` },
    ...(canSeeAll(user) ? [{ key: 'all', label: 'All approvals' }] : []),
  ];
  const go = (key) => navigate(key === 'inbox' ? '/approvals' : `/approvals/${key}`);
  const actionable = rows.filter((r) => r.can_act);
  const allChecked = actionable.length > 0 && actionable.every((r) => selected.has(r.id));
  const toggle = (id) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const done = (res, close) => { if (close !== false) setDialog(null); setSelected(new Set()); reload(); reloadSummary(); };

  const exportCsv = async () => {
    const all = await api.get('/approvals/requests/', { ...query, limit: 2000 });
    downloadCsv(`approvals-${scope}.csv`, [
      ['Reference', 'Type', 'Title', 'Amount', 'Requested by', 'Status', 'Step', 'Waiting on', 'Submitted', 'Decided', 'Age (hours)', 'Rule'],
      ...all.results.map((r) => [r.reference_number, r.kind_label, r.title, r.amount ?? '', r.requested_by?.name, r.status_label,
        `${r.current_step} of ${r.total_steps}`, r.waiting_on.join('; '), r.submitted_at, r.decided_at || '', r.age_hours, r.rule_name]),
    ]);
  };

  const [title, description] = TITLES[scope];
  return (
    <>
      <PageHeader title={title} description={description}
        actions={(<>
          {scope === 'inbox' && selected.size > 0 && (
            <button type="button" className="btn btn-primary" onClick={() => setDialog({ mode: 'approve', requests: rows.filter((r) => selected.has(r.id)) })}>
              Approve selected ({selected.size})
            </button>
          )}
          {scope !== 'inbox' && rows.length > 0 && <button type="button" className="btn" onClick={exportCsv}>Export CSV</button>}
        </>)} />
      <Tabs tabs={tabs} value={scope} onChange={go} />
      <section className="card">
        <div className="chips" role="group" aria-label="Filter by status">
          {STATUS_CHIPS[scope].map(([key, label]) => (
            <button key={key} type="button" className="chip" aria-pressed={f.status === key} onClick={() => setFilter({ status: key })}>{label}</button>
          ))}
        </div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setFilter({ q: q.trim() }); }}>
          <Field id="apr-q" label="Search" className="grow"><input id="apr-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Reference, title or requester" /></Field>
          <Field id="apr-kind" label="Type">
            <select id="apr-kind" value={f.kind} onChange={(e) => setFilter({ kind: e.target.value })}>
              <option value="">All types</option>
              {kinds.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </select>
          </Field>
          <Field id="apr-min" label="Amount from"><input id="apr-min" type="number" min="0" value={f.min} onChange={(e) => setFilter({ min: e.target.value })} className="apr-amount-input" /></Field>
          <Field id="apr-max" label="Amount to"><input id="apr-max" type="number" min="0" value={f.max} onChange={(e) => setFilter({ max: e.target.value })} className="apr-amount-input" /></Field>
          <Field id="apr-age" label="Waiting">
            <select id="apr-age" value={f.age} onChange={(e) => setFilter({ age: e.target.value })}>{AGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          </Field>
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && rows.length === 0 && (
          <p className="empty">
            {scope === 'inbox' && !Object.values(f).some(Boolean) ? 'Nothing is waiting for your approval.'
              : scope === 'mine' && !Object.values(f).some(Boolean) ? 'You have not sent anything for approval yet. Use "Request approval" on a record that needs it.'
                : 'No requests match.'}
          </p>
        )}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr>
                {scope === 'inbox' && (
                  <th><input type="checkbox" aria-label="Select all requests you can approve" checked={allChecked} disabled={!actionable.length}
                    onChange={() => setSelected(allChecked ? new Set() : new Set(actionable.map((r) => r.id)))} /></th>
                )}
                <th>Reference</th><th>Request</th><th>Requested by</th><th className="amount">Amount</th><th>Step</th><th>Waiting</th><th>Status</th>
                {scope === 'inbox' && <th><span className="sr-only">Decision</span></th>}
              </tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="clickable" onClick={() => navigate(`/approvals/requests/${r.id}`)}>
                    {scope === 'inbox' && (
                      <td data-label="Select" onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" aria-label={`Select ${r.reference_number}`} checked={selected.has(r.id)} onChange={() => toggle(r.id)} />
                      </td>
                    )}
                    <td data-label="Reference"><Link to={`/approvals/requests/${r.id}`} onClick={(e) => e.stopPropagation()}><strong>{r.reference_number}</strong></Link></td>
                    <td data-label="Request">
                      <span className="apr-kind">{r.kind_label}</span>
                      <div>{r.title}{isInternal(r.url) && <> · <Link to={r.url} onClick={(e) => e.stopPropagation()}>Open record</Link></>}</div>
                    </td>
                    <td data-label="Requested by">{r.requested_by?.name}</td>
                    <td className="amount" data-label="Amount">{r.amount === null ? '—' : plain(r.amount)}</td>
                    <td data-label="Step">{r.status === 'approved' || r.status === 'rejected' || r.status === 'cancelled' ? `${r.total_steps} step${r.total_steps === 1 ? '' : 's'}` : `${r.current_step} of ${r.total_steps}`}</td>
                    <td data-label="Waiting" title={fmtDateTime(r.submitted_at)}>{ageText(r.age_hours)}{r.overdue && <span className="apr-overdue"> overdue</span>}</td>
                    <td data-label="Status"><StatusBadge status={r.status} label={r.status_label} /></td>
                    {scope === 'inbox' && (
                      <td data-label="Decision" onClick={(e) => e.stopPropagation()}>
                        <div className="apr-row-actions">
                          <button type="button" className="btn btn-small btn-primary" onClick={() => setDialog({ mode: 'approve', requests: [r] })}>Approve</button>
                          <button type="button" className="btn btn-small btn-danger-outline" onClick={() => setDialog({ mode: 'reject', requests: [r] })}>Reject</button>
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rows.length > 0 && (
          <div className="list-foot">
            <span>{data.count} request{data.count === 1 ? '' : 's'}</span>
            <span>Total value <strong>{money(data.total_amount)}</strong></span>
          </div>
        )}
      </section>
      {dialog && <DecisionDialog mode={dialog.mode} requests={dialog.requests} onClose={() => setDialog(null)} onDone={done} />}
    </>
  );
}
