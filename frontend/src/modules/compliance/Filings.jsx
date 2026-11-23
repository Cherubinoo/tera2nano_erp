import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { downloadCsv, money, today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, ErrorBox, Field, FormErrors, Loading, PageHeader, fmtDate, useReasonDialog, useToast } from '../../ui';
import { ItemDialog, NewTaskDialog, Pick, PRIORITY_TONE, StatusBadge, daysText, opt, useCanManage, useMeta } from './common';

const TABS = [['', 'All'], ['open', 'Open'], ['overdue', 'Overdue'], ['due_soon', 'Due soon'], ['upcoming', 'Upcoming'], ['filed', 'Filed'], ['not_applicable', 'Not applicable']];

function fyOptions() {
  const d = new Date();
  const start = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1;
  return [start + 1, start, start - 1, start - 2].map((y) => `${y}-${String((y + 1) % 100).padStart(2, '0')}`);
}

function BulkBar({ ids, meta, onDone, clear }) {
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const [assignee, setAssignee] = useState('');
  const [priority, setPriority] = useState('');
  const run = async (body, ok) => {
    try {
      const r = await api.post('/compliance/items/bulk/', { ids, ...body });
      notify(r.failed.length ? `${r.done} done, ${r.failed.length} could not be changed: ${r.failed[0].error}` : `${ok} (${r.done}).`, r.failed.length ? 'error' : 'success');
      onDone();
    } catch (e) { notify(e.message, 'error'); }
  };
  return (
    <div className="compliance-bulk" role="region" aria-label="Bulk actions">
      <strong>{ids.length} selected</strong>
      <select aria-label="Assign selected to" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
        <option value="">Assign to…</option><option value="__none">Nobody</option>
        {meta.assignees.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
      </select>
      <button type="button" className="btn btn-small" disabled={!assignee} onClick={() => run({ action: 'assign', assignee: assignee === '__none' ? '' : assignee }, 'Reassigned')}>Assign</button>
      <select aria-label="Set priority of selected" value={priority} onChange={(e) => setPriority(e.target.value)}>
        <option value="">Priority…</option>{meta.priorities.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
      </select>
      <button type="button" className="btn btn-small" disabled={!priority} onClick={() => run({ action: 'priority', priority }, 'Priority set')}>Set</button>
      <button type="button" className="btn btn-small" onClick={async () => { const reason = await ask({ title: 'Not applicable', label: 'Why do these not apply?', confirmLabel: 'Mark not applicable' }); if (reason) run({ action: 'not_applicable', reason }, 'Marked not applicable'); }}>Not applicable</button>
      <button type="button" className="btn btn-small" onClick={async () => { const reason = await ask({ title: 'Reopen', label: 'Why are these being reopened?', confirmLabel: 'Reopen' }); if (reason) run({ action: 'reopen', reason }, 'Reopened'); }}>Reopen</button>
      <button type="button" className="btn btn-small" onClick={clear}>Clear</button>
      {dialog}
    </div>
  );
}

function PenaltyEstimator({ rules }) {
  const [v, setV] = useState({ rule: 'llp_per_day', due_date: '', filed_date: today(), total_income: '', tax_amount: '', nil_return: false });
  const [res, setRes] = useState(null);
  const [error, setError] = useState(null);
  const set = (k) => (e) => setV((x) => ({ ...x, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }));
  const go = async (e) => {
    e.preventDefault();
    setError(null); setRes(null);
    if (!v.due_date) { setError({ message: 'Enter the due date.', fieldErrors: { due_date: 'Enter the due date.' } }); return; }
    try { setRes(await api.post('/compliance/penalty/estimate/', { ...v, total_income: v.total_income || undefined, tax_amount: v.tax_amount || undefined })); } catch (err) { setError(err); }
  };
  const fe = error?.fieldErrors || {};
  return (
    <section className="card" aria-label="Penalty estimator">
      <h2>Late filing penalty estimator</h2>
      <p className="muted">An estimate of the late fee under the current rules. Always confirm the amount on the official portal before paying.</p>
      <form onSubmit={go} noValidate>
        <FormErrors error={error} fields={[{ name: 'due_date' }, { name: 'rule' }, { name: 'filed_date' }, { name: 'total_income' }, { name: 'tax_amount' }]} />
        <div className="form-grid">
          <Field id="pe-rule" label="Rule" error={fe.rule}><select id="pe-rule" value={v.rule} onChange={set('rule')}>{rules.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}</select></Field>
          <Field id="pe-due" label="Due date" required error={fe.due_date}><input id="pe-due" type="date" value={v.due_date} onChange={set('due_date')} /></Field>
          <Field id="pe-filed" label="Filed (or will file) on" error={fe.filed_date}><input id="pe-filed" type="date" value={v.filed_date} onChange={set('filed_date')} /></Field>
          {v.rule === 'itr_234f' && <Field id="pe-inc" label="Total income (Rs)" error={fe.total_income} hint="Up to Rs 5,00,000 the fee is lower."><input id="pe-inc" type="number" min="0" value={v.total_income} onChange={set('total_income')} /></Field>}
          {v.rule === 'tds_234e' && <Field id="pe-tax" label="TDS in the return (Rs)" error={fe.tax_amount} hint="The fee cannot exceed this."><input id="pe-tax" type="number" min="0" value={v.tax_amount} onChange={set('tax_amount')} /></Field>}
          {v.rule === 'gst_late_fee' && <div className="field field-check"><label className="check"><input type="checkbox" checked={v.nil_return} onChange={set('nil_return')} /> Nil return</label></div>}
        </div>
        <button type="submit" className="btn btn-primary">Estimate</button>
      </form>
      {res && (
        <div className="notice notice-info" role="status">
          <strong>{res.days_late} day{res.days_late === 1 ? '' : 's'} late: estimated {money(res.amount)}</strong>
          <div>{res.formula}</div>
          <div className="hint">{res.basis} {res.note}</div>
        </div>
      )}
    </section>
  );
}

export default function FilingsPage() {
  const canManage = useCanManage();
  const notify = useToast();
  const [params, setParams] = useSearchParams();
  const meta = useMeta();
  const [fy, setFy] = useState(fyOptions()[1]);
  const [status, setStatus] = useState(params.get('status') || '');
  const [category, setCategory] = useState('');
  const [assignee, setAssignee] = useState('');
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const [picked, setPicked] = useState([]);
  const [openId, setOpenId] = useState(params.get('open'));
  const [adding, setAdding] = useState(params.get('new') === '1');
  const [busy, setBusy] = useState(false);
  const { data, error, loading, reload } = useLoad(
    () => api.get('/compliance/items/', { fy, status: status || undefined, category: category || undefined, assignee: assignee || undefined, q: term || undefined }),
    [fy, status, category, assignee, term]);
  const obligations = useLoad(() => (canManage ? api.get('/compliance/obligations/', { frequency: 'event' }) : Promise.resolve(null)), [canManage]);
  const rules = useLoad(() => api.get('/compliance/penalty/rules/'), []);
  useEffect(() => { if (params.get('open') || params.get('new')) setParams(status ? { status } : {}, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setPicked([]), [fy, status, category, assignee, term]);
  const rows = data?.results || [];
  const all = rows.length > 0 && picked.length === rows.length;
  const fyList = useMemo(() => fyOptions().map((f) => ({ value: f, label: f })), []);

  const generate = async () => {
    setBusy(true);
    try {
      const r = await api.post('/compliance/calendar/generate/', { fy });
      notify(`Calendar for ${fy}: ${r.created} added, ${r.updated} moved, ${r.unchanged} unchanged, ${r.skipped_closed} filed or closed left alone.`);
      reload();
    } catch (e) { notify(e.message, 'error'); }
    setBusy(false);
  };
  const exportCsv = () => downloadCsv(`compliance-filings-${fy}.csv`, [
    ['Reference', 'Filing', 'Form', 'Period', 'Category', 'Due date', 'Status', 'Assigned to', 'Priority', 'Filed on', 'SRN/ARN/Ack', 'Fee paid', 'Penalty paid', 'Remarks'],
    ...rows.map((i) => [i.reference, i.title, i.form_no, i.period_label, i.category_label, i.due_date, i.status_label, i.assignee?.name, i.priority, i.filed_date, i.ack_number, i.fee_paid, i.penalty_paid, i.remarks])]);

  return (
    <>
      <PageHeader title="Filings" description="Every statutory filing and task in the financial year: who owns it, when it is due and what was filed."
        actions={(<>
          {canManage && <button type="button" className="btn" onClick={generate} disabled={busy}>{busy ? 'Generating…' : `Generate calendar for ${fy}`}</button>}
          <button type="button" className="btn" onClick={exportCsv} disabled={!rows.length}>Export CSV</button>
          {canManage && <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>New task</button>}
        </>)} />
      <section className="card">
        <div className="chips" role="group" aria-label="Filter by status">
          {TABS.map(([key, label]) => {
            const n = data ? (key === '' ? data.totals.total : key === 'open' ? data.totals.overdue + data.totals.due_soon + data.totals.upcoming : data.totals[key]) : null;
            return <button key={key} type="button" className="chip" aria-pressed={status === key} onClick={() => setStatus(key)}>{label}{n !== null && ` (${n})`}</button>;
          })}
        </div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
          <Field id="fl-fy" label="Financial year"><select id="fl-fy" value={fy} onChange={(e) => setFy(e.target.value)}>{fyList.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}</select></Field>
          {meta.data && <Pick id="fl-cat" label="Category" value={category} onChange={setCategory} options={opt(meta.data.categories)} />}
          {meta.data && <Pick id="fl-who" label="Assigned to" value={assignee} onChange={setAssignee} options={[{ value: 'me', label: 'Me' }, { value: 'none', label: 'Unassigned' }, ...opt(meta.data.assignees)]} />}
          <Field id="fl-q" label="Search" className="grow"><input id="fl-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filing, form, period or acknowledgement no." /></Field>
          <button type="submit" className="btn">Search</button>
        </form>
        {canManage && picked.length > 0 && meta.data && <BulkBar ids={picked} meta={meta.data} clear={() => setPicked([])} onDone={() => { setPicked([]); reload(); }} />}
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && rows.length === 0 && (
          <p className="empty">{data?.totals.total === 0 && !term && !category && !assignee ? `Nothing in the ${fy} calendar yet.${canManage ? ' Use "Generate calendar" above.' : ''}` : 'Nothing matches.'}</p>
        )}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr>
                {canManage && <th><input type="checkbox" aria-label="Select all" checked={all} onChange={(e) => setPicked(e.target.checked ? rows.map((r) => r.id) : [])} /></th>}
                <th>Due</th><th>Filing</th><th>Period</th><th>Assigned to</th><th>Priority</th><th>Status</th><th className="amount">Late fee estimate</th>
              </tr></thead>
              <tbody>
                {rows.map((i) => (
                  <tr key={i.id} className="clickable" onClick={() => setOpenId(i.id)}>
                    {canManage && <td data-label="Select" onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label={`Select ${i.title} ${i.period_label}`} checked={picked.includes(i.id)} onChange={(e) => setPicked((p) => (e.target.checked ? [...p, i.id] : p.filter((x) => x !== i.id)))} /></td>}
                    <td data-label="Due">{fmtDate(i.due_date)}{i.status_key !== 'filed' && i.status_key !== 'not_applicable' && <div className="hint">{daysText(i.days_to_due)}</div>}</td>
                    <td data-label="Filing"><button type="button" className="link-button" onClick={(e) => { e.stopPropagation(); setOpenId(i.id); }}><strong>{i.title}</strong></button><div className="hint">{[i.form_no, i.category_label, i.reference].filter(Boolean).join(' · ')}</div></td>
                    <td data-label="Period">{i.period_label || '—'}</td>
                    <td data-label="Assigned to">{i.assignee?.name || <span className="muted">Unassigned</span>}</td>
                    <td data-label="Priority"><Badge tone={PRIORITY_TONE[i.priority]}>{i.priority}</Badge></td>
                    <td data-label="Status"><StatusBadge item={i} />{i.status_key === 'filed' && !i.on_time && <div className="hint">{i.days_late} days late</div>}</td>
                    <td className="amount" data-label="Late fee estimate">{i.estimated_penalty ? money(i.estimated_penalty) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rows.length > 0 && <div className="list-foot"><span>{rows.length} item{rows.length === 1 ? '' : 's'}</span></div>}
      </section>
      {rules.data && <PenaltyEstimator rules={rules.data} />}
      {openId && meta.data && <ItemDialog id={openId} meta={meta.data} onClose={() => setOpenId(null)} onChanged={reload} />}
      {adding && meta.data && <NewTaskDialog meta={meta.data} obligations={obligations.data?.results} onClose={() => setAdding(false)} onDone={() => { setAdding(false); reload(); }} />}
    </>
  );
}
