import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { ColumnChart } from '../../components/charts';
import { useAuth } from '../../context';
import { compact, downloadCsv, money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, ErrorBox, Field, FormErrors, Loading, PageHeader, Tabs, fmtDate, useToast } from '../../ui';
import { StatusBadge, fyOf } from './common';
import { StatementView, RangeBar } from './Statements';
import { TransactionDialog } from './Transactions';

const TYPE_LABEL = { designated_partner: 'Designated partner', partner: 'Partner', nominee_partner: 'Nominee partner', other: 'Other' };

export function PartnersPage() {
  const { user } = useAuth();
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const [active, setActive] = useState('true');
  const [dialog, setDialog] = useState(false);
  const { data, error, loading, reload } = useLoad(() => api.get('/partners/partners/', { q: term || undefined, active: active || undefined }), [term, active]);
  const canManage = user.permissions.includes('partners.manage');
  const rows = data?.results || [];
  const totals = rows.reduce((a, r) => ({ capital: a.capital + num(r.capital), current: a.current + num(r.current), loan: a.loan + num(r.loan) }), { capital: 0, current: 0, loan: 0 });
  const exportCsv = () => downloadCsv('partner-capital-accounts.csv', [['Partner', 'Designation', 'Capital', 'Current account', 'Total', 'Loan', 'Share of capital %', 'Profit share %'],
    ...rows.map((r) => [r.name, r.designation, r.capital, r.current, r.total, r.loan, r.contribution_percent, r.profit_share_percent])]);
  return (
    <>
      <PageHeader title="Partners" description="Each partner's capital account, current account and loan. Partners themselves are managed under Company."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setDialog(true)}>Record transaction</button>} />
      <section className="card">
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
          <Field id="pt-q" label="Search" className="grow"><input id="pt-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name or designation" /></Field>
          <Field id="pt-active" label="Show">
            <select id="pt-active" value={active} onChange={(e) => setActive(e.target.value)}><option value="true">Current partners</option><option value="false">Former partners</option><option value="">All</option></select>
          </Field>
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && rows.length === 0 && <p className="empty">No partners to show. Add partners under Company, Partners and Signatories.</p>}
        {rows.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Partner</th><th className="amount">Capital account</th><th className="amount">Current account</th><th className="amount">Total</th><th className="amount">Loan</th><th className="amount">Share of capital</th><th className="amount">Profit share</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className={r.is_active ? '' : 'row-inactive'}>
                  <td data-label="Partner"><Link className="partners-name" to={`/partners/accounts/${r.id}`}>{r.name}</Link>
                    <div className="hint">{r.designation || TYPE_LABEL[r.partner_type]}{r.is_active ? '' : ' · ceased'}</div></td>
                  <td className="amount" data-label="Capital account">{plain(r.capital)}</td>
                  <td className="amount" data-label="Current account"><span className={num(r.current) < 0 ? 'bad-text' : ''}>{plain(r.current)}</span></td>
                  <td className="amount" data-label="Total"><strong>{plain(r.total)}</strong></td>
                  <td className="amount" data-label="Loan">{num(r.loan) ? plain(r.loan) : '—'}</td>
                  <td className="amount" data-label="Share of capital">{plain(r.contribution_percent)}%</td>
                  <td className="amount" data-label="Profit share">{plain(r.profit_share_percent)}%</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
        {rows.length > 0 && (
          <div className="list-foot"><span>{rows.length} partner{rows.length === 1 ? '' : 's'}</span><span>Capital <strong>{money(totals.capital)}</strong></span>
            <span>Current accounts <strong>{money(totals.current)}</strong></span><span>Loans <strong>{money(totals.loan)}</strong></span>
            <button type="button" className="btn btn-small" onClick={exportCsv}>Export CSV</button></div>
        )}
      </section>
      {rows.length > 0 && (
        <section className="card" aria-label="Share of capital against profit share">
          <h2>Share of capital against profit share</h2>
          <ColumnChart title="Share of capital and profit share by partner" format={(v) => `${num(v)}%`}
            series={[{ key: 'c', label: 'Share of capital', colour: 'var(--chart-1)' }, { key: 'p', label: 'Share of profit', colour: 'var(--chart-3)' }]}
            data={rows.filter((r) => r.is_active).map((r) => ({ label: r.name.split(' ')[0], values: { c: r.contribution_percent, p: r.profit_share_percent } }))} />
        </section>
      )}
      {dialog && <TransactionDialog onClose={() => setDialog(false)} onSaved={() => { setDialog(false); reload(); }} />}
    </>
  );
}

function Tile({ label, value, hint, tone }) {
  return <div className={`tile ${tone || ''}`}><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</div>;
}

function AccountSettings({ p, canManage, onSaved }) {
  const notify = useToast();
  const users = useLoad(() => (canManage ? api.get('/auth/users/').catch(() => []) : Promise.resolve([])), [canManage]);
  const [v, setV] = useState({ agreed_capital: p.agreed_capital, user: p.user || '', notes: p.notes || '' });
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const save = async (e) => {
    e.preventDefault();
    setError(null);
    if (v.agreed_capital !== '' && num(v.agreed_capital) < 0) { setErrors({ agreed_capital: 'Enter zero or more.' }); return; }
    setBusy(true);
    try {
      await api.patch(`/partners/partners/${p.id}/`, { agreed_capital: v.agreed_capital === '' ? '0' : v.agreed_capital, user: v.user, notes: v.notes });
      notify('Saved.');
      onSaved();
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  const list = Array.isArray(users.data) ? users.data : [];
  return (
    <section className="card" aria-label="Account settings">
      <h2>Account settings</h2>
      <form onSubmit={save} noValidate>
        <FormErrors error={error} fields={[{ name: 'agreed_capital' }, { name: 'user' }, { name: 'notes' }]} />
        <div className="form-grid">
          <Field id="as-cap" label="Agreed capital contribution" error={errors.agreed_capital} hint="What the LLP agreement says this partner will contribute.">
            <input id="as-cap" type="number" min="0" step="0.01" value={v.agreed_capital} onChange={set('agreed_capital')} disabled={!canManage} />
          </Field>
          <Field id="as-user" label="Sign-in user" error={errors.user} hint="A partner who is also a user can view their own account.">
            <select id="as-user" value={v.user} onChange={set('user')} disabled={!canManage || list.length === 0}>
              <option value="">{list.length === 0 && v.user ? `Linked: ${p.user_name}` : 'Not linked'}</option>
              {list.map((u) => <option key={u.id} value={u.id}>{u.username}</option>)}
            </select>
          </Field>
          <Field id="as-notes" label="Notes" wide error={errors.notes}><input id="as-notes" value={v.notes} maxLength={300} onChange={set('notes')} disabled={!canManage} /></Field>
        </div>
        {canManage && <div className="dialog-foot-inline"><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button></div>}
      </form>
    </section>
  );
}

function AgreementTab({ canView }) {
  const { data, error, loading } = useLoad(() => (canView ? api.get('/partners/agreements/') : Promise.resolve([])), [canView]);
  return (
    <section className="card" aria-label="LLP agreement versions">
      <h2>LLP agreement versions</h2>
      <p className="muted">Kept in the Company module. <Link to="/company/documents">Open Company Documents</Link> to add or download a version.</p>
      {loading && <Loading />}
      <ErrorBox error={error} />
      {!loading && !error && (data || []).length === 0 && <p className="empty">No LLP agreement has been uploaded yet.</p>}
      {(data || []).length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Document</th><th>Version</th><th>Uploaded</th><th>Expires</th><th>Status</th></tr></thead>
          <tbody>{data.map((d) => (
            <tr key={d.id}><td data-label="Document"><strong>{d.title}</strong><div className="hint">{d.original_filename}</div></td><td data-label="Version">v{d.version}</td>
              <td data-label="Uploaded">{fmtDate((d.uploaded || '').slice(0, 10))}</td><td data-label="Expires">{fmtDate(d.expiry_date)}</td>
              <td data-label="Status">{d.is_current ? <Badge tone="good">Current</Badge> : <Badge>Superseded</Badge>}</td></tr>
          ))}</tbody>
        </table></div>
      )}
    </section>
  );
}

export function PartnerDetailPage() {
  const { id } = useParams();
  const { user } = useAuth();
  const [tab, setTab] = useState('overview');
  const fy = fyOf(today());
  const [range, setRange] = useState({ from: fy.start, to: fy.end });
  const [dialog, setDialog] = useState(null);
  const { data: p, error, loading, reload } = useLoad(() => api.get(`/partners/partners/${id}/`), [id]);
  const hist = useLoad(() => api.get('/partners/history/', { partner: id }), [id]);
  if (loading && !p) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const canManage = user.permissions.includes('partners.manage');
  const canView = user.permissions.includes('partners.view');
  const tabs = [{ key: 'overview', label: 'Overview' }, { key: 'statement', label: 'Statement' }, { key: 'loan', label: 'Loan' }, { key: 'documents', label: 'Documents' }, { key: 'agreement', label: 'LLP agreement' }];
  const lo = p.loan_ledger;
  return (
    <>
      <PageHeader title={p.name} description={`${p.designation || TYPE_LABEL[p.partner_type] || 'Partner'}${p.is_active ? '' : ' (ceased)'}${p.is_self ? ' · this is you' : ''}`}
        actions={<><Link className="btn" to="/partners/accounts">All partners</Link>
          {canManage && <button type="button" className="btn btn-primary" onClick={() => setDialog({ partner: p.id })}>Record transaction</button>}</>} />
      <Tabs tabs={tabs} value={tab} onChange={setTab} />

      {tab === 'overview' && (
        <>
          <div className="tiles">
            <Tile label="Capital account" value={money(p.capital)} hint={num(p.agreed_capital) ? `Agreed ${money(p.agreed_capital)}` : undefined} />
            <Tile label="Current account" value={money(p.current)} tone={num(p.current) < 0 ? 'bad' : ''} hint={num(p.current) < 0 ? 'Overdrawn' : undefined} />
            <Tile label="Total" value={money(p.total)} />
            <Tile label="Loan to the LLP" value={money(p.loan)} />
            <Tile label="Share of capital / profit" value={`${plain(p.contribution_percent)}% / ${plain(p.profit_share_percent)}%`} />
          </div>
          <div className="grid-2">
            <section className="card" aria-label="Capital over time">
              <h2>Capital over time</h2>
              {hist.loading ? <Loading /> : hist.error ? <ErrorBox error={hist.error} /> : (
                <ColumnChart title="Capital and current account by month" format={compact}
                  series={[{ key: 'capital', label: 'Capital', colour: 'var(--chart-1)' }, { key: 'current', label: 'Current account', colour: 'var(--chart-2)' }]}
                  data={hist.data.months.map((m) => ({ label: m.label, values: { capital: Math.max(num(m.capital), 0), current: Math.max(num(m.current), 0) } }))} />
              )}
            </section>
            <AccountSettings key={`${p.agreed_capital}${p.user}${p.notes}`} p={p} canManage={canManage} onSaved={reload} />
          </div>
          <section className="card" aria-label="Recent transactions">
            <div className="card-head"><h2>Recent transactions</h2>{canView && <Link to={`/partners/transactions?partner=${p.id}`}>View all</Link>}</div>
            {p.recent.length === 0 ? <p className="empty">No transactions yet.</p> : (
              <div className="table-wrap"><table>
                <thead><tr><th>Date</th><th>Number</th><th>Type</th><th>Status</th><th className="amount">Amount</th></tr></thead>
                <tbody>{p.recent.map((t) => (
                  <tr key={t.id}><td data-label="Date">{fmtDate(t.date)}</td><td data-label="Number"><code>{t.number}</code></td><td data-label="Type">{t.kind_label}</td>
                    <td data-label="Status"><StatusBadge status={t.status} label={t.status_label} /></td><td className="amount" data-label="Amount">{plain(t.amount)}</td></tr>
                ))}</tbody>
              </table></div>
            )}
          </section>
        </>
      )}

      {tab === 'statement' && (<><RangeBar key={`${range.from}${range.to}`} range={range} onApply={setRange} /><StatementView partnerId={id} range={range} /></>)}

      {tab === 'loan' && (
        <section className="card" aria-label="Loan">
          <div className="card-head"><h2>Loan to the LLP</h2>{canManage && <button type="button" className="btn btn-small" onClick={() => setDialog({ partner: p.id, kind: 'loan_from_partner' })}>Record loan</button>}</div>
          {!lo ? <p className="empty">This partner has not lent money to the LLP.</p> : (
            <>
              <dl className="partners-kv">
                <dt>Lent</dt><dd>{money(lo.lent)}</dd><dt>Repaid</dt><dd>{money(lo.repaid)}</dd><dt>Outstanding</dt><dd><strong>{money(lo.outstanding)}</strong></dd>
                <dt>Interest accrued (estimate)</dt><dd>{money(lo.interest_estimate)}</dd>
              </dl>
              <p className="hint">Simple interest at each loan&apos;s rate on a 365-day year; repayments reduce the oldest loan first. It is an estimate and is not posted to the books.</p>
            </>
          )}
        </section>
      )}

      {tab === 'documents' && (
        <Attachments entityType="partner" entityId={p.id} canManage={canManage} title="Partner documents"
          hint="KYC, DPIN allotment, consent letters and other papers. Set an expiry date where one applies." />
      )}
      {tab === 'agreement' && <AgreementTab canView={canView} />}
      {dialog && <TransactionDialog preset={dialog} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); reload(); hist.reload(); }} />}
    </>
  );
}
