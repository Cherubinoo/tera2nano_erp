import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { money, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, fmtDate, useReasonDialog, useToast } from '../../ui';
import { evenSplit, monthLabels, useProjects } from './assetCommon';

const TONE = { draft: 'neutral', active: 'good', closed: 'info' };
const STATUSES = [['', 'All'], ['draft', 'Draft'], ['active', 'Active'], ['closed', 'Closed']];

export function BudgetsPage() {
  const { user } = useAuth();
  const manage = user.permissions.includes('accounting.manage');
  const [status, setStatus] = useState('');
  const { data, error, loading, reload } = useLoad(() => api.get('/accounting/budgets/', { status: status || undefined }), [status]);
  return (
    <>
      <PageHeader title="Budgets" description="Plan income and spending for a financial year, month by month, then compare it with what the books show."
        actions={(<>{manage && <Link className="btn btn-primary" to="/accounting/budgets/new">New budget</Link>}<Link className="btn" to="/accounting/budgets/report">Budget vs actual</Link></>)} />
      <section className="card">
        <div className="chips" role="group" aria-label="Filter by status">
          {STATUSES.map(([k, l]) => <button key={k} type="button" className="chip" aria-pressed={status === k} onClick={() => setStatus(k)}>{l}</button>)}
        </div>
        {loading && !data && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {data && data.length === 0 && <p className="empty">No budgets yet. “New budget” starts one for a financial year; make it active and expenses that go past a budget line show a warning.</p>}
        {data?.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Budget</th><th>Financial year</th><th>Status</th><th className="amount">Income planned</th><th className="amount">Spending planned</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{data.map((b) => (
              <tr key={b.id}>
                <td data-label="Budget"><Link to={`/accounting/budgets/${b.id}`}><strong>{b.name}</strong></Link><div className="hint">Version {b.version} · {b.line_count} line{b.line_count === 1 ? '' : 's'}</div></td>
                <td data-label="Financial year">{b.financial_year.label}</td>
                <td data-label="Status"><Badge tone={TONE[b.status]}>{b.status_label}</Badge></td>
                <td className="amount" data-label="Income planned">{plain(b.total_income)}</td><td className="amount" data-label="Spending planned">{plain(b.total_expense)}</td>
                <td className="row-actions"><Link className="btn btn-small" to={`/accounting/budgets/${b.id}`}>Open</Link><Link className="btn btn-small" to={`/accounting/budgets/report?budget=${b.id}`}>vs actual</Link></td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </section>
    </>
  );
}

const blankLine = () => ({ key: Math.random().toString(36).slice(2), account: '', project: '', phasing: 'even', annual: '', monthly: Array(12).fill('') });

function LineEditor({ line, accounts, projects, labels, onChange, onRemove, locked, index }) {
  const total = line.phasing === 'even' ? num(line.annual) : line.monthly.reduce((a, x) => a + num(x), 0);
  const setPhasing = (e) => {
    const phasing = e.target.value;
    if (phasing === 'custom') onChange({ ...line, phasing, monthly: evenSplit(line.annual) });
    else onChange({ ...line, phasing, annual: total ? total.toFixed(2) : '' });
  };
  const setMonth = (i) => (e) => onChange({ ...line, monthly: line.monthly.map((x, n) => (n === i ? e.target.value : x)) });
  const expense = accounts.filter((a) => a.type === 'expense');
  const income = accounts.filter((a) => a.type === 'income');
  const n = index + 1;
  return (
    <div className="bud-line" role="group" aria-label={`Budget line ${n}`}>
      <div className="bud-line-head">
        <Field id={`bl-acc-${n}`} label="Account">
          <select id={`bl-acc-${n}`} value={line.account} disabled={locked} onChange={(e) => onChange({ ...line, account: e.target.value })}>
            <option value="">Choose…</option>
            <optgroup label="Expenses">{expense.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</optgroup>
            <optgroup label="Income">{income.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</optgroup>
          </select>
        </Field>
        <Field id={`bl-cc-${n}`} label="Cost centre (project)">
          <select id={`bl-cc-${n}`} value={line.project} disabled={locked} onChange={(e) => onChange({ ...line, project: e.target.value })}>
            <option value="">Whole company</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.code ? `${p.code} ` : ''}{p.name}</option>)}
            {line.project && !projects.some((p) => p.id === line.project) && <option value={line.project}>{labels[line.project] || 'Project'}</option>}
          </select>
        </Field>
        <Field id={`bl-ph-${n}`} label="Phasing">
          <select id={`bl-ph-${n}`} value={line.phasing} disabled={locked} onChange={setPhasing}><option value="even">Even over 12 months</option><option value="custom">Month by month</option></select>
        </Field>
        {line.phasing === 'even'
          ? <Field id={`bl-an-${n}`} label="Yearly amount"><input id={`bl-an-${n}`} type="number" min="0" step="0.01" value={line.annual} disabled={locked} onChange={(e) => onChange({ ...line, annual: e.target.value })} /></Field>
          : <Field id={`bl-tt-${n}`} label="Yearly total"><input id={`bl-tt-${n}`} value={plain(total)} readOnly /></Field>}
        {!locked && <button type="button" className="btn btn-small" onClick={onRemove} aria-label={`Remove line ${n}`}>Remove</button>}
      </div>
      {line.phasing === 'custom' && (
        <div className="bud-months">
          {line.monthly.map((m, i) => (
            <div key={i}><label htmlFor={`bl-m-${n}-${i}`}>{labels.months[i]}</label>
              <input id={`bl-m-${n}-${i}`} type="number" min="0" step="0.01" value={m} disabled={locked} onChange={setMonth(i)} /></div>
          ))}
        </div>
      )}
      {line.phasing === 'custom' && !locked && (
        <div className="bud-line-foot"><button type="button" className="btn btn-small" onClick={() => onChange({ ...line, monthly: evenSplit(total) })}>Spread {plain(total)} evenly</button></div>
      )}
      {line.phasing === 'even' && num(line.annual) > 0 && <p className="hint">About {plain(num(line.annual) / 12)} a month; the last month takes any paisa left over.</p>}
    </div>
  );
}

export function BudgetForm() {
  const { id } = useParams();
  const editing = !!id;
  const navigate = useNavigate();
  const { user } = useAuth();
  const manage = user.permissions.includes('accounting.manage');
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const options = useLoad(() => api.get('/accounting/budgets/options/'), []);
  const budget = useLoad(() => (editing ? api.get(`/accounting/budgets/${id}/`) : Promise.resolve(null)), [id]);
  const projects = useProjects();
  const [name, setName] = useState('');
  const [fy, setFy] = useState('');
  const [notes, setNotes] = useState('');
  const [lines, setLines] = useState([blankLine()]);
  const [reason, setReason] = useState('');
  const [viewing, setViewing] = useState(null);
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);

  const load = (b) => {
    setName(b.name); setFy(b.financial_year.id); setNotes(b.notes);
    setLines(b.lines.map((l) => ({ key: l.id, account: l.account.id, project: l.project?.id || '', phasing: l.phasing, annual: l.phasing === 'even' ? String(l.annual) : '',
      monthly: l.monthly.map((m) => String(m)), _label: l.project?.label })));
  };
  useEffect(() => { if (budget.data) load(budget.data); }, [budget.data]);
  useEffect(() => {
    if (!editing && options.data && !fy) setFy((options.data.financial_years.find((f) => f.current) || options.data.financial_years[0] || {}).id || '');
  }, [editing, options.data, fy]);

  const fyObj = options.data?.financial_years.find((f) => f.id === fy);
  const labels = useMemo(() => {
    const out = { months: monthLabels(fyObj?.start || budget.data?.financial_year.start) };
    lines.forEach((l) => { if (l.project && l._label) out[l.project] = l._label; });
    return out;
  }, [fyObj, budget.data, lines]);

  if (options.loading || budget.loading) return <Loading />;
  if (options.error) return <ErrorBox error={options.error} onRetry={options.reload} />;
  if (budget.error) return <ErrorBox error={budget.error} onRetry={budget.reload} />;
  const b = budget.data;
  const o = options.data;
  if (editing && !b) return <Loading />;
  const closed = editing && b.status === 'closed';
  const locked = closed || !manage;
  const totals = lines.reduce((t, l) => {
    const a = o.accounts.find((x) => x.id === l.account);
    const v = l.phasing === 'even' ? num(l.annual) : l.monthly.reduce((s, x) => s + num(x), 0);
    if (a) t[a.type] += v;
    return t;
  }, { income: 0, expense: 0 });

  const payloadLines = () => lines.filter((l) => l.account || num(l.annual) || l.monthly.some((m) => num(m))).map((l) => ({
    account: l.account, project: l.project || null, phasing: l.phasing, ...(l.phasing === 'even' ? { annual: l.annual || 0 } : { monthly: l.monthly.map((m) => m || 0) }) }));
  const validate = () => {
    const errs = {};
    if (!name.trim()) errs.name = 'Enter the budget name.';
    if (!fy) errs.financial_year = 'Choose the financial year.';
    const used = payloadLines();
    if (used.some((l) => !l.account)) errs.lines = 'Choose an account on every line.';
    const seen = new Set();
    used.forEach((l) => { const k = `${l.account}|${l.project || ''}`; if (seen.has(k) && !errs.lines) errs.lines = 'An account can appear once for each cost centre.'; seen.add(k); });
    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const save = async (e) => {
    e.preventDefault();
    setError(null);
    if (!validate()) return;
    let why = reason.trim();
    if (editing && b.status === 'active' && !why) {
      const asked = await ask({ title: 'Revise the active budget', message: 'Lines changed on an active budget are saved as a new version. Say why.', confirmLabel: 'Save revision' });
      if (asked === null) return;
      why = asked;
    }
    setBusy(true);
    try {
      if (editing) {
        const r = await api.patch(`/accounting/budgets/${id}/`, { name, notes, financial_year: fy, lines: payloadLines(), change_reason: why });
        notify(r.version !== b.version ? `Budget saved as version ${r.version}.` : 'Budget saved.');
        setReason(''); budget.reload();
      } else {
        const r = await api.post('/accounting/budgets/', { name, notes, financial_year: fy, lines: payloadLines() });
        notify('Budget created.');
        navigate(`/accounting/budgets/${r.id}`);
      }
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  const setStatus = async (status) => {
    try { await api.post(`/accounting/budgets/${id}/status/`, { status }); notify(status === 'active' ? 'Budget is now active.' : status === 'closed' ? 'Budget closed.' : 'Budget updated.'); budget.reload(); } catch (err) { notify(err.message, 'error'); }
  };
  const remove = async () => {
    if (!window.confirm('Delete this draft budget?')) return;
    try { await api.del(`/accounting/budgets/${id}/`); navigate('/accounting/budgets'); } catch (err) { notify(err.message, 'error'); }
  };
  const viewRevision = async (version) => {
    if (viewing?.version === version) { setViewing(null); return; }
    try { setViewing(await api.get(`/accounting/budgets/${id}/revisions/${version}/`)); } catch (err) { notify(err.message, 'error'); }
  };

  return (
    <>
      <Link to="/accounting/budgets" className="back-link">← Budgets</Link>
      <PageHeader title={editing ? b.name : 'New budget'} description={editing ? `${b.financial_year.label} · version ${b.version}` : 'Budget income and spending by account for one financial year.'}
        actions={editing && (
          <>
            <Badge tone={TONE[b.status]}>{b.status_label}</Badge>
            <Link className="btn" to={`/accounting/budgets/report?budget=${id}`}>Budget vs actual</Link>
            {manage && b.status === 'draft' && <button type="button" className="btn btn-primary" onClick={() => setStatus('active')}>Make active</button>}
            {manage && b.status === 'active' && <button type="button" className="btn" onClick={() => setStatus('closed')}>Close budget</button>}
            {manage && b.status === 'closed' && <button type="button" className="btn" onClick={() => setStatus('active')}>Reopen</button>}
            {manage && b.status === 'draft' && <button type="button" className="btn" onClick={remove}>Delete</button>}
          </>
        )} />
      {editing && b.status === 'active' && <Notice type="info">This is the active budget for {b.financial_year.label}: an expense that takes an account past its budget line shows a warning. Changes are saved as a new version and need a reason.</Notice>}
      {closed && <Notice type="info">This budget is closed. Reopen it to change the lines.</Notice>}
      <form className="card" onSubmit={save} noValidate>
        <FormErrors error={error} fields={[]} />
        <div className="form-grid">
          <Field id="bd-name" label="Budget name" required error={errors.name}><input id="bd-name" value={name} maxLength={150} disabled={locked} onChange={(e) => { setName(e.target.value); setErrors((x) => ({ ...x, name: undefined })); }} /></Field>
          <Field id="bd-fy" label="Financial year" required error={errors.financial_year}>
            <select id="bd-fy" value={fy} disabled={locked || (editing && b.status !== 'draft')} onChange={(e) => setFy(e.target.value)}>{o.financial_years.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}</select>
          </Field>
          <Field id="bd-notes" label="Notes" wide error={errors.notes}><input id="bd-notes" value={notes} maxLength={500} disabled={locked} onChange={(e) => setNotes(e.target.value)} /></Field>
        </div>
        <h2>Lines</h2>
        <p className="muted">One line per account and cost centre. The first month is {labels.months[0]}. Even phasing splits the yearly amount in 12; “Month by month” lets you enter each month.</p>
        {errors.lines && <p className="error-text" role="alert">{errors.lines}</p>}
        {lines.map((l, i) => (
          <LineEditor key={l.key} index={i} line={l} accounts={o.accounts} projects={projects} labels={labels} locked={locked}
            onChange={(nl) => { setLines((ls) => ls.map((x, n) => (n === i ? nl : x))); setErrors((x) => ({ ...x, lines: undefined })); }}
            onRemove={() => setLines((ls) => (ls.length > 1 ? ls.filter((_, n) => n !== i) : [blankLine()]))} />
        ))}
        {!locked && <div className="toolbar"><button type="button" className="btn btn-small" onClick={() => setLines((ls) => [...ls, blankLine()])}>Add line</button></div>}
        <div className="bud-totals" role="status"><span>Income planned <strong>{money(totals.income)}</strong></span><span>Spending planned <strong>{money(totals.expense)}</strong></span><span>Surplus <strong>{money(totals.income - totals.expense)}</strong></span></div>
        {editing && b.status === 'active' && !locked && (
          <Field id="bd-reason" label="Reason for the revision" hint="Needed when the lines change."><input id="bd-reason" value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} /></Field>
        )}
        {!locked && <div className="actions"><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : editing ? 'Save budget' : 'Create budget'}</button><Link className="btn" to="/accounting/budgets">Cancel</Link></div>}
      </form>

      {editing && (
        <section className="card" aria-label="Revision history">
          <h2>Revision history</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>Version</th><th>When</th><th>By</th><th>Reason</th><th className="amount">Income</th><th className="amount">Spending</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{b.revisions.map((r) => (
              <tr key={r.version}>
                <td data-label="Version"><strong>v{r.version}</strong>{r.version === b.version && <> <Badge tone="good">current</Badge></>}</td>
                <td data-label="When">{fmtDate(r.created_at?.slice(0, 10))}</td><td data-label="By">{r.created_by || '—'}</td><td data-label="Reason">{r.reason || '—'}</td>
                <td className="amount" data-label="Income">{plain(r.total_income)}</td><td className="amount" data-label="Spending">{plain(r.total_expense)}</td>
                <td className="row-actions"><button type="button" className="btn btn-small" aria-pressed={viewing?.version === r.version} onClick={() => viewRevision(r.version)}>{viewing?.version === r.version ? 'Hide' : 'View lines'}</button></td>
              </tr>
            ))}</tbody>
          </table></div>
          {viewing && (
            <div className="fa-plan"><h3>Version {viewing.version}</h3>
              <div className="table-wrap"><table className="items-table">
                <thead><tr><th>Account</th><th>Cost centre</th><th>Phasing</th><th className="amount">Yearly</th></tr></thead>
                <tbody>{viewing.snapshot.map((l, i) => (
                  <tr key={i}><td data-label="Account">{l.code} {l.name}</td><td data-label="Cost centre">{l.project ? (labels[l.project] || 'Project') : 'Whole company'}</td>
                    <td data-label="Phasing">{l.phasing === 'even' ? 'Even' : 'Month by month'}</td><td className="amount" data-label="Yearly">{plain(l.annual)}</td></tr>
                ))}</tbody>
              </table></div>
            </div>
          )}
        </section>
      )}
      {dialog}
    </>
  );
}
