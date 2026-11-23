import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart, Donut, SERIES_COLOURS } from '../../components/charts';
import { useAuth } from '../../context';
import { compact, downloadCsv, money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, Tabs, fmtDate, useReasonDialog, useToast } from '../../ui';
import { AssetStatus, BOOKS_LABEL, DepreciationRules, termsText } from './assetCommon';

function Tile({ label, value, hint, tone, to }) {
  const inner = (<><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</>);
  return to ? <Link className={`tile ${tone || ''}`} to={to}>{inner}</Link> : <div className={`tile ${tone || ''}`}>{inner}</div>;
}

const TABS = [
  { key: 'register', label: 'Register' }, { key: 'categories', label: 'Categories' }, { key: 'depreciation', label: 'Depreciation run' },
  { key: 'years', label: 'By financial year' }, { key: 'disposals', label: 'Disposals' },
];
const STATUS_CHIPS = [['', 'All'], ['in_use', 'In use'], ['sold', 'Sold'], ['disposed', 'Disposed'], ['written_off', 'Written off']];

export default function FixedAssetsPage() {
  const { user } = useAuth();
  const manage = user.permissions.includes('accounting.manage');
  const [params, setParams] = useSearchParams();
  const tab = TABS.some((t) => t.key === params.get('tab')) ? params.get('tab') : 'register';
  const go = (key) => setParams(key === 'register' ? {} : { tab: key });
  return (
    <>
      <PageHeader title="Fixed assets" description="What the company owns, what it cost, what has been written off as depreciation, and what it is worth in the books."
        actions={manage && <Link className="btn btn-primary" to="/accounting/assets/new">New fixed asset</Link>} />
      <Tabs tabs={TABS} value={tab} onChange={go} />
      {tab === 'register' && <RegisterTab goTab={go} />}
      {tab === 'categories' && <CategoriesTab manage={manage} />}
      {tab === 'depreciation' && <DepreciationTab manage={manage} />}
      {tab === 'years' && <YearsTab />}
      {tab === 'disposals' && <DisposalsTab manage={manage} />}
    </>
  );
}

/* ------------------------------------------------------------------ register */

function RegisterTab({ goTab }) {
  const dash = useLoad(() => api.get('/accounting/assets/dashboard/'), []);
  const options = useLoad(() => api.get('/accounting/assets/options/'), []);
  const [status, setStatus] = useState('');
  const [category, setCategory] = useState('');
  const [asOn, setAsOn] = useState(today());
  const [asOnInput, setAsOnInput] = useState(today());
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const reg = useLoad(() => api.get('/accounting/reports/asset-register/', { as_on: asOn, status: status || undefined, category: category || undefined, q: term || undefined }),
    [asOn, status, category, term]);
  const d = dash.data;
  const r = reg.data;
  const cats = options.data?.categories || [];

  const exportCsv = () => downloadCsv(`asset-register-${asOn}.csv`, [
    ['Code', 'Asset', 'Category', 'Purchase date', 'Put to use', 'Status', 'Cost', 'Accumulated depreciation', 'Book value'],
    ...r.rows.map((a) => [a.code, a.name, a.category.name, a.purchase_date, a.put_to_use_date, a.status_label, a.cost, a.accumulated, a.wdv]),
    ['', 'Total (held on this date)', '', '', '', '', r.totals.cost, r.totals.accumulated, r.totals.wdv]]);

  return (
    <>
      {dash.error && <ErrorBox error={dash.error} onRetry={dash.reload} />}
      {d && (
        <>
          <div className="tiles">
            <Tile label="Assets in use" value={d.totals.count} hint={`${d.counts.sold + d.counts.disposed + d.counts.written_off} sold, scrapped or written off`} />
            <Tile label="Cost" value={money(d.totals.cost)} />
            <Tile label="Depreciation so far" value={money(d.totals.accumulated)} hint={`${money(d.depreciation_this_year)} in ${d.financial_year}`} />
            <Tile label="Book value" value={money(d.totals.wdv)} tone="good" hint="Cost less depreciation" />
          </div>
          {d.pending.months > 0 && (
            <Notice type="warn">
              <span>Depreciation is due: {d.pending.months} month{d.pending.months === 1 ? '' : 's'} not charged yet ({money(d.pending.amount)}).</span>
              <button type="button" className="btn btn-small" onClick={() => goTab('depreciation')}>Run depreciation</button>
            </Notice>
          )}
          {d.not_in_books > 0 && <Notice type="info">{d.not_in_books} asset{d.not_in_books === 1 ? ' is' : 's are'} not in the books yet. Open it and record the purchase so depreciation can be charged.</Notice>}
          {!d.ledger_check.agrees && (
            <Notice type="info">The register and the ledger accounts differ: cost {money(d.ledger_check.register_cost)} in the register against {money(d.ledger_check.ledger_cost)} in the ledger; depreciation {money(d.ledger_check.register_accumulated)} against {money(d.ledger_check.ledger_accumulated)}. This happens when an opening balance or a manual journal touched these accounts.</Notice>
          )}
          <div className="grid-2">
            <section className="card" aria-label="Depreciation by month">
              <h2>Depreciation this year</h2>
              <p className="muted">Posted for each month of {d.financial_year}.</p>
              <ColumnChart title="Depreciation posted per month" format={compact} series={[{ key: 'amount', label: 'Depreciation', colour: 'var(--chart-1)' }]}
                data={d.monthly.map((m) => ({ label: m.label, values: { amount: m.amount } }))} />
            </section>
            <section className="card" aria-label="Book value by category">
              <h2>Book value by category</h2>
              <p className="muted">What each kind of asset is worth in the books today.</p>
              {d.by_category.length === 0 ? <p className="muted">No assets yet.</p> : (
                <Donut slices={d.by_category.map((c, i) => ({ key: c.id, label: c.name, value: num(c.wdv), colour: SERIES_COLOURS[i % SERIES_COLOURS.length] }))}
                  centreLabel="book value" showTotal={false} format={(v) => money(v)} />
              )}
            </section>
          </div>
        </>
      )}

      <section className="card">
        <div className="chips" role="group" aria-label="Filter by status">
          {STATUS_CHIPS.map(([k, l]) => (
            <button key={k} type="button" className="chip" aria-pressed={status === k} onClick={() => setStatus(k)}>
              {l}{d && k && d.counts[k] !== undefined ? ` (${d.counts[k]})` : ''}
            </button>
          ))}
        </div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); setAsOn(asOnInput || today()); }}>
          <Field id="fa-q" label="Search" className="grow"><input id="fa-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Code, name, serial number or location" /></Field>
          <Field id="fa-cat" label="Category">
            <select id="fa-cat" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">All categories</option>{cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
          </Field>
          <Field id="fa-ason" label="As on"><input id="fa-ason" type="date" value={asOnInput} onChange={(e) => setAsOnInput(e.target.value)} /></Field>
          <button type="submit" className="btn">Show</button>
          {r && r.rows.length > 0 && <button type="button" className="btn" onClick={exportCsv}>Export CSV</button>}
        </form>
        {reg.loading && !r && <Loading />}
        <ErrorBox error={reg.error} onRetry={reg.reload} />
        {r && r.rows.length === 0 && <p className="empty">No assets match. Use “New fixed asset” to add the first one.</p>}
        {r && r.rows.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Asset</th><th>Category</th><th>Put to use</th><th className="amount">Cost</th><th className="amount">Depreciation</th><th className="amount">Book value</th><th>Status</th></tr></thead>
            <tbody>
              {r.rows.map((a) => (
                <tr key={a.id} className={a.status !== 'in_use' ? 'row-inactive' : ''}>
                  <td data-label="Asset"><Link to={`/accounting/assets/${a.id}`}><strong>{a.name}</strong></Link><div className="hint"><code>{a.code}</code>{a.location ? ` · ${a.location}` : ''}</div></td>
                  <td data-label="Category">{a.category.name}</td>
                  <td data-label="Put to use">{fmtDate(a.put_to_use_date)}</td>
                  <td className="amount" data-label="Cost">{plain(a.cost)}</td>
                  <td className="amount" data-label="Depreciation">{plain(a.accumulated)}</td>
                  <td className="amount" data-label="Book value"><strong>{plain(a.wdv)}</strong></td>
                  <td data-label="Status"><AssetStatus status={a.status} label={a.status_label} />{!a.in_books && a.status === 'in_use' && <> <Badge tone="warn">{BOOKS_LABEL.pending}</Badge></>}</td>
                </tr>
              ))}
            </tbody>
            <tfoot><tr><th scope="row" colSpan={3}>Held on {fmtDate(r.as_on)} ({r.totals.count})</th><td className="amount"><strong>{plain(r.totals.cost)}</strong></td><td className="amount"><strong>{plain(r.totals.accumulated)}</strong></td><td className="amount"><strong>{plain(r.totals.wdv)}</strong></td><td /></tr></tfoot>
          </table></div>
        )}
        {r && r.by_category.length > 0 && (
          <>
            <h3>By category</h3>
            <div className="table-wrap"><table className="items-table">
              <thead><tr><th>Category</th><th className="amount">Assets</th><th className="amount">Cost</th><th className="amount">Depreciation</th><th className="amount">Book value</th></tr></thead>
              <tbody>{r.by_category.map((c) => <tr key={c.id}><td data-label="Category">{c.name}</td><td className="amount" data-label="Assets">{c.count}</td><td className="amount" data-label="Cost">{plain(c.cost)}</td><td className="amount" data-label="Depreciation">{plain(c.accumulated)}</td><td className="amount" data-label="Book value">{plain(c.wdv)}</td></tr>)}</tbody>
            </table></div>
          </>
        )}
      </section>
    </>
  );
}

/* ------------------------------------------------------------------ categories */

function CategoryDialog({ category, options, onClose, onSaved }) {
  const notify = useToast();
  const first = (list) => list[0]?.id || '';
  const [v, setV] = useState(category ? {
    name: category.name, method: category.method, useful_life_years: category.useful_life_years ?? '', rate_percent: category.rate_percent ?? '',
    salvage_percent: String(num(category.salvage_percent)), half_year_rule: category.half_year_rule, cost_account: category.cost_account.id,
    accumulated_account: category.accumulated_account.id, expense_account: category.expense_account.id, is_active: category.is_active,
  } : { name: '', method: 'slm', useful_life_years: '', rate_percent: '', salvage_percent: '5', half_year_rule: false, cost_account: first(options.fixed_asset_accounts),
    accumulated_account: first(options.accumulated_accounts), expense_account: options.expense_accounts.find((a) => a.code === '5800')?.id || first(options.expense_accounts), is_active: true });
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    setError(null);
    const errs = {};
    if (!v.name.trim()) errs.name = 'Enter the category name.';
    if (v.method === 'slm' && !(num(v.useful_life_years) > 0)) errs.useful_life_years = 'Enter the useful life in years.';
    if (v.method === 'wdv' && !(num(v.rate_percent) > 0)) errs.rate_percent = 'Enter the rate %.';
    if (num(v.salvage_percent) < 0 || num(v.salvage_percent) >= 100) errs.salvage_percent = 'Enter a % from 0 to less than 100.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const body = { ...v, useful_life_years: v.useful_life_years === '' ? null : v.useful_life_years, rate_percent: v.rate_percent === '' ? null : v.rate_percent };
      if (category) await api.patch(`/accounting/asset-categories/${category.id}/`, body); else await api.post('/accounting/asset-categories/', body);
      notify(category ? 'Category saved.' : 'Category added.');
      onSaved();
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  const pickAccounts = (id, label, list, key) => (
    <Field id={id} label={label} required error={errors[key]}>
      <select id={id} value={v[key]} onChange={set(key)}>{list.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</select>
    </Field>
  );
  return (
    <Dialog title={category ? 'Edit category' : 'New asset category'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[]} />
        <div className="form-grid">
          <Field id="ac-name" label="Name" required error={errors.name}><input id="ac-name" value={v.name} maxLength={120} onChange={set('name')} /></Field>
          <Field id="ac-method" label="Depreciation method" required error={errors.method}>
            <select id="ac-method" value={v.method} onChange={set('method')}><option value="slm">Straight line (Companies Act)</option><option value="wdv">Written-down value (income-tax rate)</option></select>
          </Field>
          {v.method === 'slm'
            ? <Field id="ac-life" label="Useful life (years)" required error={errors.useful_life_years}><input id="ac-life" type="number" min="0" step="0.01" value={v.useful_life_years} onChange={set('useful_life_years')} /></Field>
            : <Field id="ac-rate" label="Rate (% a year)" required error={errors.rate_percent}><input id="ac-rate" type="number" min="0" max="100" step="0.01" value={v.rate_percent} onChange={set('rate_percent')} /></Field>}
          <Field id="ac-salv" label="Residual value (% of cost)" error={errors.salvage_percent} hint="The Companies Act allows up to 5%."><input id="ac-salv" type="number" min="0" max="99.99" step="0.01" value={v.salvage_percent} onChange={set('salvage_percent')} /></Field>
          {pickAccounts('ac-cost', 'Asset (cost) account', options.fixed_asset_accounts, 'cost_account')}
          {pickAccounts('ac-acc', 'Accumulated depreciation account', options.accumulated_accounts, 'accumulated_account')}
          {pickAccounts('ac-exp', 'Depreciation expense account', options.expense_accounts, 'expense_account')}
        </div>
        {v.method === 'wdv' && (
          <label className="checkbox-inline"><input type="checkbox" checked={v.half_year_rule} onChange={set('half_year_rule')} />
            Half-year rule: in the year an asset is put to use, charge half the rate if it is used for fewer than 180 days (income-tax rule)</label>
        )}
        {category && <label className="checkbox-inline"><input type="checkbox" checked={v.is_active} onChange={set('is_active')} /> Active (new assets can use this category)</label>}
        <p className="hint">Existing assets keep the terms they were created with; a change here applies to new assets.</p>
        <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button></div>
      </form>
    </Dialog>
  );
}

function CategoriesTab({ manage }) {
  const notify = useToast();
  const cats = useLoad(() => api.get('/accounting/asset-categories/'), []);
  const options = useLoad(() => api.get('/accounting/assets/options/'), []);
  const [dialog, setDialog] = useState(null);
  const rows = cats.data || [];
  const addStandard = async () => {
    try { const r = await api.post('/accounting/asset-categories/setup/'); notify(r.created ? `${r.created} standard categories added.` : 'The standard categories already exist.'); cats.reload(); options.reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const remove = async (c) => {
    if (!window.confirm(`Delete the category "${c.name}"?`)) return;
    try { await api.del(`/accounting/asset-categories/${c.id}/`); notify('Category deleted.'); cats.reload(); options.reload(); } catch (e) { notify(e.message, 'error'); }
  };
  return (
    <section className="card">
      <div className="card-head"><h2>Asset categories</h2>
        {manage && <div className="toolbar"><button type="button" className="btn" onClick={addStandard}>Add standard categories</button>
          <button type="button" className="btn btn-primary" disabled={!options.data} onClick={() => setDialog({})}>New category</button></div>}
      </div>
      <DepreciationRules />
      {cats.loading && !cats.data && <Loading />}
      <ErrorBox error={cats.error} onRetry={cats.reload} />
      {cats.data && rows.length === 0 && <p className="empty">No categories yet. “Add standard categories” creates computers, furniture and office equipment with the usual lives and income-tax rates.</p>}
      {rows.length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Category</th><th>Depreciation</th><th>Ledger accounts</th><th className="amount">Assets</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} className={c.is_active ? '' : 'row-inactive'}>
                <td data-label="Category"><strong>{c.name}</strong>{!c.is_active && <> <Badge>Inactive</Badge></>}</td>
                <td data-label="Depreciation">{termsText(c)}</td>
                <td data-label="Accounts"><span className="hint">Cost {c.cost_account.code} · Accumulated {c.accumulated_account.code} · Expense {c.expense_account.code}</span></td>
                <td className="amount" data-label="Assets">{c.assets}</td>
                <td className="row-actions">{manage && <><button type="button" className="btn btn-small" onClick={() => setDialog(c)} disabled={!options.data}>Edit</button>{c.assets === 0 && <button type="button" className="btn btn-small" onClick={() => remove(c)}>Delete</button>}</>}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {dialog && options.data && <CategoryDialog category={dialog.id ? dialog : null} options={options.data} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); cats.reload(); options.reload(); }} />}
    </section>
  );
}

/* ------------------------------------------------------------------ depreciation run */

const thisMonth = () => today().slice(0, 7);

function PlanTable({ plan }) {
  return (
    <div className="fa-plan">
      <h3>{plan.label}: {plan.rows.length} asset{plan.rows.length === 1 ? '' : 's'}, {money(plan.total)}{plan.already ? ` (${plan.already} already charged)` : ''}</h3>
      {plan.rows.length > 0 && (
        <div className="table-wrap"><table className="items-table">
          <thead><tr><th>Asset</th><th>Category</th><th className="amount">Charge</th><th className="amount">Total depreciation</th><th className="amount">Book value after</th></tr></thead>
          <tbody>{plan.rows.map((r) => (
            <tr key={r.asset.id}><td data-label="Asset"><Link to={`/accounting/assets/${r.asset.id}`}>{r.asset.name}</Link> <span className="hint"><code>{r.asset.code}</code></span></td><td data-label="Category">{r.category}</td>
              <td className="amount" data-label="Charge"><strong>{plain(r.amount)}</strong></td><td className="amount" data-label="Total depreciation">{plain(r.accumulated)}</td><td className="amount" data-label="Book value after">{plain(r.wdv)}</td></tr>
          ))}</tbody>
        </table></div>
      )}
      {plan.skipped.map((s) => <p key={s.asset.id} className="hint"><Badge tone="warn">Skipped</Badge> {s.asset.name} ({s.asset.code}): {s.reason}</p>)}
    </div>
  );
}

function DepreciationTab({ manage }) {
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const dash = useLoad(() => api.get('/accounting/assets/dashboard/'), []);
  const runs = useLoad(() => api.get('/accounting/depreciation/runs/'), []);
  const [from, setFrom] = useState(thisMonth());
  const [to, setTo] = useState(thisMonth());
  const [seeded, setSeeded] = useState(false);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (dash.data && !seeded) {
      setSeeded(true);
      const p = dash.data.pending;
      if (p.first) { setFrom(p.first.slice(0, 7)); setTo(p.last.slice(0, 7)); }
    }
  }, [dash.data, seeded]);

  const show = async (e) => {
    e?.preventDefault();
    setError(null); setBusy(true);
    try { setPreview(await api.get('/accounting/depreciation/preview/', { period: from, to })); } catch (err) { setError(err); setPreview(null); }
    setBusy(false);
  };
  const post = async () => {
    setError(null); setBusy(true);
    try {
      const r = await api.post('/accounting/depreciation/runs/', { period: from, to });
      notify(r.runs.length ? `Depreciation posted: ${money(r.total)} for ${r.runs.length} month${r.runs.length === 1 ? '' : 's'}.` : 'Nothing to post: every asset is already charged for this period.', r.runs.length ? 'success' : 'info');
      setPreview(null); runs.reload(); dash.reload();
    } catch (err) { setError(err); }
    setBusy(false);
  };
  const reverse = async (r) => {
    const reason = await ask({ title: `Reverse depreciation for ${r.label}?`, message: 'The entry is reversed in the books and the month can be charged again.', confirmLabel: 'Reverse' });
    if (reason === null) return;
    try { await api.post(`/accounting/depreciation/runs/${r.id}/reverse/`, { change_reason: reason }); notify('Depreciation reversed.'); runs.reload(); dash.reload(); } catch (e) { notify(e.message, 'error'); }
  };
  return (
    <>
      <section className="card">
        <h2>Run depreciation</h2>
        <p className="muted">One journal entry per month: Dr Depreciation expense, Cr Accumulated depreciation. Each asset is charged once for a month, however many times you run it.</p>
        <DepreciationRules />
        {dash.data?.pending.months > 0 && <Notice type="warn">{dash.data.pending.months} month{dash.data.pending.months === 1 ? '' : 's'} due, {money(dash.data.pending.amount)}. The period below starts with the first month not charged.</Notice>}
        <form className="toolbar" onSubmit={show}>
          <Field id="dep-from" label="From month"><input id="dep-from" type="month" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
          <Field id="dep-to" label="To month"><input id="dep-to" type="month" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
          <button type="submit" className="btn" disabled={busy}>Preview</button>
          {manage && preview && preview.total !== '0.00' && <button type="button" className="btn btn-primary" disabled={busy} onClick={post}>Post depreciation ({money(preview.total)})</button>}
        </form>
        <ErrorBox error={error} />
        {preview && preview.months.every((m) => m.rows.length === 0) && <p className="empty">Nothing to charge for this period: every asset is already charged, or none is in use yet.</p>}
        {preview && preview.months.filter((m) => m.rows.length || m.skipped.length).map((m) => <PlanTable key={m.period_start} plan={m} />)}
      </section>
      <section className="card">
        <h2>Depreciation already posted</h2>
        {runs.loading && !runs.data && <Loading />}
        <ErrorBox error={runs.error} onRetry={runs.reload} />
        {runs.data && runs.data.length === 0 && <p className="empty">No depreciation has been posted yet.</p>}
        {runs.data?.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Month</th><th className="amount">Assets</th><th className="amount">Charged</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{runs.data.map((r) => (
              <tr key={r.id} className={r.status === 'reversed' ? 'row-inactive' : ''}>
                <td data-label="Month"><strong>{r.label}</strong><div className="hint">posted {fmtDate(r.created_at?.slice(0, 10))}</div></td>
                <td className="amount" data-label="Assets">{r.asset_count}</td><td className="amount" data-label="Charged">{plain(r.total)}</td>
                <td data-label="Status"><Badge tone={r.status === 'posted' ? 'good' : 'neutral'}>{r.status}</Badge>{r.reverse_reason && <div className="hint">{r.reverse_reason}</div>}</td>
                <td className="row-actions">{manage && r.status === 'posted' && <button type="button" className="btn btn-small" onClick={() => reverse(r)}>Reverse</button>}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </section>
      {dialog}
    </>
  );
}

/* ------------------------------------------------------------------ by financial year */

function YearsTab() {
  const { data, error, loading, reload } = useLoad(() => api.get('/accounting/reports/depreciation-by-year/'), []);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const exportCsv = () => downloadCsv('depreciation-by-financial-year.csv', [
    ['Financial year', 'Category', 'Posted', 'Still to be charged (schedule)'],
    ...data.years.flatMap((y) => [...y.categories.map((c) => [y.fy, c.name, c.posted, c.scheduled]), [y.fy, 'Total', y.posted, y.scheduled]])]);
  return (
    <>
      <section className="card">
        <div className="card-head"><h2>Depreciation by financial year</h2>{data.years.length > 0 && <button type="button" className="btn" onClick={exportCsv}>Export CSV</button>}</div>
        <p className="muted">“Posted” is what is in the books. “Scheduled” is what the asset schedules charge for the year in all (posted months plus those still to come).</p>
        {data.years.length === 0 ? <p className="empty">No assets yet.</p> : (
          <>
            <ColumnChart title="Depreciation per financial year" format={compact}
              series={[{ key: 'posted', label: 'Posted', colour: 'var(--chart-1)' }, { key: 'scheduled', label: 'Scheduled', colour: 'var(--chart-2)' }]}
              data={data.years.slice(0, 7).map((y) => ({ label: y.fy, values: { posted: y.posted, scheduled: y.scheduled } }))} />
            {data.years.length > 7 && <p className="hint">The chart shows the first seven years; the table has them all.</p>}
            <div className="table-wrap"><table>
              <thead><tr><th>Financial year</th><th>Category</th><th className="amount">Posted</th><th className="amount">Scheduled</th></tr></thead>
              <tbody>
                {data.years.map((y) => (
                  <FyRows key={y.fy} y={y} />
                ))}
              </tbody>
            </table></div>
          </>
        )}
      </section>
      {data.assets.length > 0 && (
        <section className="card"><h2>Posted by asset</h2>
          <div className="table-wrap"><table className="items-table">
            <thead><tr><th>Asset</th><th>Category</th>{data.years.map((y) => <th key={y.fy} className="amount">{y.fy}</th>)}</tr></thead>
            <tbody>{data.assets.map((a) => (
              <tr key={a.id}><td data-label="Asset"><Link to={`/accounting/assets/${a.id}`}>{a.name}</Link> <span className="hint"><code>{a.code}</code></span></td><td data-label="Category">{a.category}</td>
                {data.years.map((y) => <td key={y.fy} className="amount" data-label={y.fy}>{a.by_fy[y.fy] ? plain(a.by_fy[y.fy]) : '—'}</td>)}</tr>
            ))}</tbody>
          </table></div>
        </section>
      )}
    </>
  );
}

function FyRows({ y }) {
  return (
    <>
      {y.categories.map((c, i) => (
        <tr key={c.id}><td data-label="Financial year">{i === 0 ? <strong>{y.fy}</strong> : ''}</td><td data-label="Category">{c.name}</td><td className="amount" data-label="Posted">{plain(c.posted)}</td><td className="amount" data-label="Scheduled">{plain(c.scheduled)}</td></tr>
      ))}
      <tr><th scope="row" colSpan={2}>Total {y.fy}</th><td className="amount"><strong>{plain(y.posted)}</strong></td><td className="amount"><strong>{plain(y.scheduled)}</strong></td></tr>
    </>
  );
}

/* ------------------------------------------------------------------ disposals */

function DisposalsTab({ manage }) {
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const [range, setRange] = useState({});
  const [inputs, setInputs] = useState({ from: '', to: '' });
  const { data, error, loading, reload } = useLoad(() => api.get('/accounting/reports/asset-disposals/', range), [range.from, range.to]);
  const reverse = async (r) => {
    const reason = await ask({ title: `Reverse the disposal of ${r.asset.name}?`, message: 'The entry is reversed and the asset is back in use.', confirmLabel: 'Reverse' });
    if (reason === null) return;
    try { await api.post(`/accounting/asset-disposals/${r.id}/reverse/`, { change_reason: reason }); notify('Disposal reversed.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const exportCsv = () => downloadCsv('asset-disposals.csv', [
    ['Date', 'Asset code', 'Asset', 'Category', 'What happened', 'Cost', 'Depreciation', 'Book value', 'Proceeds', 'Gain or loss'],
    ...data.rows.map((r) => [r.date, r.asset.code, r.asset.name, r.category, r.kind_label, r.cost, r.accumulated, r.wdv, r.proceeds, r.gain_loss]),
    ['', '', '', '', 'Net', '', '', '', data.total_proceeds, data.net]]);
  return (
    <section className="card">
      <h2>Disposals</h2>
      <p className="muted">Assets sold, scrapped or written off. Gain or loss = proceeds less book value on the date.</p>
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setRange({ from: inputs.from || undefined, to: inputs.to || undefined }); }}>
        <Field id="dp-from" label="From"><input id="dp-from" type="date" value={inputs.from || data?.start || ''} onChange={(e) => setInputs((x) => ({ ...x, from: e.target.value }))} /></Field>
        <Field id="dp-to" label="To"><input id="dp-to" type="date" value={inputs.to || data?.end || ''} onChange={(e) => setInputs((x) => ({ ...x, to: e.target.value }))} /></Field>
        <button type="submit" className="btn">Show</button>
        {data?.rows.length > 0 && <button type="button" className="btn" onClick={exportCsv}>Export CSV</button>}
      </form>
      {loading && !data && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && data.rows.length === 0 && <p className="empty">No disposals in this period.</p>}
      {data?.rows.length > 0 && (
        <>
          <div className="table-wrap"><table>
            <thead><tr><th>Date</th><th>Asset</th><th>What happened</th><th className="amount">Cost</th><th className="amount">Book value</th><th className="amount">Proceeds</th><th className="amount">Gain / loss</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{data.rows.map((r) => (
              <tr key={r.id}>
                <td data-label="Date">{fmtDate(r.date)}</td>
                <td data-label="Asset"><Link to={`/accounting/assets/${r.asset.id}`}>{r.asset.name}</Link><div className="hint"><code>{r.asset.code}</code> · {r.category}</div></td>
                <td data-label="What happened">{r.kind_label}{r.buyer && <div className="hint">{r.buyer}</div>}</td>
                <td className="amount" data-label="Cost">{plain(r.cost)}</td><td className="amount" data-label="Book value">{plain(r.wdv)}</td><td className="amount" data-label="Proceeds">{plain(r.proceeds)}</td>
                <td className={`amount ${num(r.gain_loss) < 0 ? 'fa-loss' : 'fa-gain'}`} data-label="Gain / loss"><strong>{num(r.gain_loss) < 0 ? `(${plain(-num(r.gain_loss))})` : plain(r.gain_loss)}</strong></td>
                <td className="row-actions">{manage && <button type="button" className="btn btn-small" onClick={() => reverse(r)}>Reverse</button>}</td>
              </tr>
            ))}</tbody>
          </table></div>
          <div className="list-foot"><span>Proceeds <strong>{money(data.total_proceeds)}</strong></span><span>Gains <strong>{money(data.total_gain)}</strong></span><span>Losses <strong>{money(data.total_loss)}</strong></span><span>Net <strong>{money(data.net)}</strong></span></div>
        </>
      )}
      {dialog}
    </section>
  );
}
