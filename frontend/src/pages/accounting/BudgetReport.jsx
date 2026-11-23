import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart } from '../../components/charts';
import { compact, downloadCsv, money, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, ErrorBox, Field, Loading, Notice, PageHeader, Tabs } from '../../ui';
import { useProjects } from './assetCommon';

const pct = (v) => (v === null || v === undefined ? '—' : `${num(v).toFixed(1)}%`);

function Tile({ label, value, hint, tone }) {
  return <div className={`tile ${tone || ''}`}><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</div>;
}

// The variance is signed so that positive is good: budget less actual for an expense, actual less budget for income.
function VarianceCell({ r, kind }) {
  const bad = r.over;
  return (
    <>
      <td className="amount bud-var" data-label="Variance">{num(r.variance) < 0 ? `(${plain(-num(r.variance))})` : plain(r.variance)}</td>
      <td className="amount bud-var" data-label="Variance %">{pct(r.variance_pct)}</td>
      <td data-label="Status">{bad ? <Badge tone="bad">{kind === 'expense' ? 'Over budget' : 'Below target'}</Badge> : <Badge tone="good">On track</Badge>}</td>
    </>
  );
}

function Section({ kind, rep, through }) {
  const block = rep[kind];
  const noun = kind === 'expense' ? 'Spending' : 'Income';
  const t = block.totals;
  return (
    <>
      <div className="tiles">
        <Tile label={`${noun} budget to ${through}`} value={money(t.budget_to_date)} hint={`Year ${money(t.budget_total)}`} />
        <Tile label={`${noun} actual`} value={money(t.actual_to_date)} hint="From the books" />
        <Tile label={kind === 'expense' ? 'Under (over) budget' : 'Above (below) target'} value={num(t.variance) < 0 ? `(${plain(-num(t.variance))})` : plain(t.variance)}
          tone={t.over ? 'bad' : 'good'} hint={pct(t.variance_pct)} />
        <Tile label="Left of the year's budget" value={money(t.remaining)} tone={num(t.remaining) < 0 ? 'bad' : undefined} />
      </div>
      <div className="grid-2">
        <section className="card" aria-label={`${noun} by month`}>
          <h2>{noun}, budget against actual</h2>
          <p className="muted">Each month of {rep.budget.financial_year}.</p>
          <ColumnChart title={`${noun} per month, budget against actual`} format={compact}
            series={[{ key: 'budget', label: 'Budget', colour: 'var(--chart-2)' }, { key: 'actual', label: 'Actual', colour: kind === 'expense' ? 'var(--chart-4)' : 'var(--chart-3)' }]}
            data={block.monthly.map((m) => ({ label: m.label, values: { budget: m.budget, actual: m.actual } }))} />
        </section>
        <section className="card" aria-label={`${noun} cumulative`}>
          <h2>Cumulative</h2>
          <p className="muted">Running total from the start of the year.</p>
          <ColumnChart title={`${noun} cumulative, budget against actual`} format={compact}
            series={[{ key: 'budget', label: 'Budget', colour: 'var(--chart-2)' }, { key: 'actual', label: 'Actual', colour: kind === 'expense' ? 'var(--chart-4)' : 'var(--chart-3)' }]}
            data={block.monthly.map((m) => ({ label: m.label, values: { budget: m.cum_budget, actual: m.cum_actual } }))} />
        </section>
      </div>

      <section className="card" aria-label={`${noun} by account`}>
        <h2>By account</h2>
        {block.rows.length === 0 ? <p className="empty">No {kind} lines in this budget.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Account</th><th className="amount">Budget (year)</th><th className="amount">Budget to {through}</th><th className="amount">Actual</th><th className="amount">Variance</th><th className="amount">%</th><th>Status</th></tr></thead>
            <tbody>
              {block.rows.map((r) => (
                <tr key={r.account.id} className={r.over ? 'bud-over' : 'bud-ok'}>
                  <td data-label="Account"><Link to={`/accounting/accounts/${r.account.id}`}>{r.account.code} {r.account.name}</Link>{r.cost_centres.length > 0 && <div className="hint">{r.cost_centres.join(', ')}</div>}</td>
                  <td className="amount" data-label="Budget (year)">{plain(r.budget_total)}</td><td className="amount" data-label="Budget to date">{plain(r.budget_to_date)}</td>
                  <td className="amount" data-label="Actual"><strong>{plain(r.actual_to_date)}</strong></td>
                  <VarianceCell r={r} kind={kind} />
                </tr>
              ))}
            </tbody>
            <tfoot><tr className={t.over ? 'bud-over' : ''}><th scope="row">Total</th><td className="amount"><strong>{plain(t.budget_total)}</strong></td><td className="amount"><strong>{plain(t.budget_to_date)}</strong></td><td className="amount"><strong>{plain(t.actual_to_date)}</strong></td>
              <td className="amount bud-var"><strong>{num(t.variance) < 0 ? `(${plain(-num(t.variance))})` : plain(t.variance)}</strong></td><td className="amount bud-var">{pct(t.variance_pct)}</td><td /></tr></tfoot>
          </table></div>
        )}
      </section>

      {block.by_project.length > 0 && (
        <section className="card" aria-label={`${noun} by project`}>
          <h2>By cost centre (project)</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>Cost centre</th><th className="amount">Budget (year)</th><th className="amount">Budget to {through}</th><th className="amount">Actual</th><th className="amount">Variance</th><th className="amount">%</th><th>Status</th></tr></thead>
            <tbody>{block.by_project.map((r) => (
              <tr key={r.project?.id || 'none'} className={r.over ? 'bud-over' : 'bud-ok'}>
                <td data-label="Cost centre">{r.project ? r.project.label : 'Whole company'}</td>
                <td className="amount" data-label="Budget (year)">{plain(r.budget_total)}</td><td className="amount" data-label="Budget to date">{plain(r.budget_to_date)}</td>
                <td className="amount" data-label="Actual"><strong>{plain(r.actual_to_date)}</strong></td><VarianceCell r={r} kind={kind} />
              </tr>
            ))}</tbody>
          </table></div>
        </section>
      )}

      <section className="card" aria-label={`${noun} month by month`}>
        <h2>Month by month</h2>
        <div className="table-wrap"><table>
          <thead><tr><th>Month</th><th className="amount">Budget</th><th className="amount">Actual</th><th className="amount">Variance</th><th className="amount">Cumulative budget</th><th className="amount">Cumulative actual</th><th className="amount">Cumulative variance</th></tr></thead>
          <tbody>{block.monthly.map((m) => (
            <tr key={m.month} className={m.over ? 'bud-over' : ''}>
              <td data-label="Month">{m.label}</td><td className="amount" data-label="Budget">{plain(m.budget)}</td><td className="amount" data-label="Actual">{plain(m.actual)}</td>
              <td className="amount bud-var" data-label="Variance">{num(m.variance) < 0 ? `(${plain(-num(m.variance))})` : plain(m.variance)}</td>
              <td className="amount" data-label="Cumulative budget">{plain(m.cum_budget)}</td><td className="amount" data-label="Cumulative actual">{plain(m.cum_actual)}</td>
              <td className="amount" data-label="Cumulative variance">{num(m.cum_variance) < 0 ? `(${plain(-num(m.cum_variance))})` : plain(m.cum_variance)}</td>
            </tr>
          ))}</tbody>
        </table></div>
      </section>
    </>
  );
}

export default function BudgetReport() {
  const [params, setParams] = useSearchParams();
  const budgets = useLoad(() => api.get('/accounting/budgets/'), []);
  const projects = useProjects();
  const [kind, setKind] = useState('expense');
  const budgetId = params.get('budget') || '';
  const through = params.get('through') || '';
  const project = params.get('project') || '';
  const list = budgets.data || [];
  const chosen = budgetId || (list.find((b) => b.status === 'active') || list[0] || {}).id || '';
  const rep = useLoad(() => (chosen ? api.get('/accounting/reports/budget-vs-actual/', { budget: chosen, through: through || undefined, project: project || undefined }) : Promise.resolve(null)),
    [chosen, through, project]);
  const update = (k) => (e) => { const next = new URLSearchParams(params); if (e.target.value) next.set(k, e.target.value); else next.delete(k); setParams(next, { replace: true }); };
  const r = rep.data;

  const exportCsv = () => {
    const rows = [['Type', 'Account', 'Cost centre', 'Budget (year)', `Budget to ${r.through}`, 'Actual', 'Variance (positive is good)', 'Variance %', 'Status']];
    ['expense', 'income'].forEach((k) => r[k].rows.forEach((x) => rows.push([k, `${x.account.code} ${x.account.name}`, x.cost_centres.join('; '), x.budget_total, x.budget_to_date, x.actual_to_date, x.variance, x.variance_pct ?? '', x.over ? 'over' : 'ok'])));
    rows.push([], ['Month', 'Type', 'Budget', 'Actual', 'Variance', 'Cumulative budget', 'Cumulative actual', 'Cumulative variance']);
    ['expense', 'income'].forEach((k) => r[k].monthly.forEach((m) => rows.push([m.month, k, m.budget, m.actual, m.variance, m.cum_budget, m.cum_actual, m.cum_variance])));
    downloadCsv(`budget-vs-actual-${r.budget.financial_year}.csv`, rows);
  };

  return (
    <>
      <Link to="/accounting/budgets" className="back-link no-print">← Budgets</Link>
      <PageHeader title="Budget vs actual" description="Planned income and spending against what the books show. A positive variance is good: spending under budget, or income above target."
        actions={r && <span className="no-print"><button type="button" className="btn" onClick={exportCsv}>Export CSV</button> <button type="button" className="btn" onClick={() => window.print()}>Print</button></span>} />
      <div className="toolbar bud-controls no-print">
        <Field id="bv-budget" label="Budget">
          <select id="bv-budget" value={chosen} onChange={update('budget')}>{list.map((b) => <option key={b.id} value={b.id}>{b.name} · {b.financial_year.label}{b.status === 'active' ? ' (active)' : ''}</option>)}</select>
        </Field>
        <Field id="bv-through" label="Up to month"><input id="bv-through" type="month" value={through || r?.through || ''} min={r?.budget.start.slice(0, 7)} max={r?.budget.end.slice(0, 7)} onChange={update('through')} /></Field>
        <Field id="bv-project" label="Cost centre">
          <select id="bv-project" value={project} onChange={update('project')}><option value="">All</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.code ? `${p.code} ` : ''}{p.name}</option>)}</select>
        </Field>
      </div>
      {budgets.loading && !budgets.data && <Loading />}
      <ErrorBox error={budgets.error} onRetry={budgets.reload} />
      {budgets.data && list.length === 0 && <p className="empty">There is no budget yet. <Link to="/accounting/budgets/new">Create one</Link> to compare it with the books.</p>}
      {rep.loading && !r && chosen && <Loading />}
      <ErrorBox error={rep.error} onRetry={rep.reload} />
      {r && (
        <>
          {r.budget.status !== 'active' && <Notice type="info">This budget is {r.budget.status}. Only an active budget shows warnings on the expense form.</Notice>}
          <Tabs tabs={[{ key: 'expense', label: 'Spending' }, { key: 'income', label: 'Income' }]} value={kind} onChange={setKind} />
          <Section kind={kind} rep={r} through={new Date(`${r.through}-01T00:00:00`).toLocaleDateString('en-IN', { month: 'short', year: 'numeric' })} />
          {r.unbudgeted.rows.length > 0 && (
            <section className="card" aria-label="Not budgeted">
              <h2>Not budgeted</h2>
              <p className="muted">Posted in the books to {r.through} but covered by no budget line.</p>
              <div className="table-wrap"><table className="items-table">
                <thead><tr><th>Account</th><th>Cost centre</th><th>Type</th><th className="amount">Actual</th></tr></thead>
                <tbody>{r.unbudgeted.rows.map((u, i) => (
                  <tr key={i}><td data-label="Account"><Link to={`/accounting/accounts/${u.account.id}`}>{u.account.code} {u.account.name}</Link></td><td data-label="Cost centre">{u.project ? u.project.label : 'Whole company'}</td>
                    <td data-label="Type">{u.type === 'expense' ? 'Spending' : 'Income'}</td><td className="amount" data-label="Actual">{plain(u.actual_to_date)}</td></tr>
                ))}</tbody>
              </table></div>
            </section>
          )}
        </>
      )}
    </>
  );
}
