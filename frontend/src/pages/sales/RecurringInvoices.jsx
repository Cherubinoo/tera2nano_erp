import { useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { money, plain } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, ErrorBox, Field, Loading, Notice, PageHeader, Tabs, fmtDate, fmtDateTime, useConfirm, useReasonDialog, useToast } from '../../ui';
import { StatusBadge, projectLabel } from './common';

const TONE = { active: 'good', paused: 'warn', stopped: 'neutral', completed: 'info' };
const STATUS_TABS = [['', 'All'], ['active', 'Active'], ['paused', 'Paused'], ['stopped', 'Stopped'], ['completed', 'Completed']];

const StatusPill = ({ p }) => <Badge tone={TONE[p.status] || 'neutral'}>{p.status_label}</Badge>;

function Tile({ label, value, hint, tone }) {
  return <div className={`tile ${tone || ''}`}><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</div>;
}

const runsLabel = (p) => (p.max_occurrences != null ? `${p.occurrences_done} of ${p.max_occurrences}` : `${p.occurrences_done}`);

export function RecurringList() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const [params, setParams] = useSearchParams();
  const status = params.get('status') || '';
  const term = params.get('q') || '';
  const [q, setQ] = useState(term);
  const [busy, setBusy] = useState(false);
  const { data, error, loading, reload } = useLoad(() => api.get('/sales/recurring-invoices/', { status: status || undefined, q: term || undefined }), [status, term]);
  const manage = user.permissions.includes('sales.manage');
  const setP = (o) => setParams(Object.fromEntries(Object.entries(o).filter(([, v]) => v)), { replace: true });

  const runDue = async () => {
    if (!(await confirm({
      title: 'Create the invoices that are due?',
      body: <p>Every active profile whose next date has come creates its invoice now (issued or as a draft, as set on each profile). Runs already made are never made twice.</p>,
      confirmLabel: 'Create now',
    }))) return;
    setBusy(true);
    try {
      const r = await api.post('/sales/recurring-invoices/run-due/');
      const msg = `${r.generated.length} invoice${r.generated.length === 1 ? '' : 's'} created${r.errors.length ? `, ${r.errors.length} failed (see the profiles marked "Last run failed")` : ''}${r.still_due ? '. More are waiting: run it again.' : '.'}`;
      notify(msg, r.errors.length ? 'error' : 'success');
      reload();
    } catch (e) { notify(e.message, 'error'); }
    setBusy(false);
  };

  const s = data?.summary;
  return (
    <>
      <PageHeader title="Recurring invoices" description="Invoices that repeat on a schedule: retainers, hosting, support contracts."
        actions={manage && (<>
          <Link className="btn btn-primary" to="/sales/recurring/new">New recurring invoice</Link>
          {s && s.due_now > 0 && <button type="button" className="btn" onClick={runDue} disabled={busy}>{busy ? 'Creating…' : `Create ${s.due_now} due now`}</button>}
        </>)} />
      {s && (
        <div className="tiles">
          <Tile label="Active" value={s.active} hint="Running on schedule" />
          <Tile label="Due now" value={s.due_now} tone={s.due_now > 0 ? 'warn' : ''} hint={s.due_now > 0 ? 'Next date has come' : 'Nothing waiting'} />
          <Tile label="Paused" value={s.paused} hint="Not running until resumed" />
          <Tile label="Finished" value={s.finished} hint="Stopped or completed" />
        </div>
      )}
      <section className="card">
        <div className="chips" role="group" aria-label="Filter by status">
          {STATUS_TABS.map(([key, label]) => <button key={key} type="button" className="chip" aria-pressed={status === key} onClick={() => setP({ status: key, q: term })}>{label}</button>)}
        </div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setP({ status, q: q.trim() }); }}>
          <Field id="rec-q" label="Search" className="grow"><input id="rec-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Profile name or customer" /></Field>
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && data?.results.length === 0 && <p className="empty">No recurring invoices{status || term ? ' match' : ' yet'}.{manage && !status && !term && <> <Link to="/sales/recurring/new">Create the first one</Link>.</>}</p>}
        {data?.results.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Profile</th><th>Customer</th><th>Repeats</th><th>Next invoice</th><th>Last invoice</th><th className="amount">Made</th><th>Each run</th><th>Status</th></tr></thead>
              <tbody>
                {data.results.map((p) => (
                  <tr key={p.id} className="clickable" onClick={() => navigate(`/sales/recurring/${p.id}`)}>
                    <td data-label="Profile"><Link to={`/sales/recurring/${p.id}`} onClick={(e) => e.stopPropagation()}><strong>{p.name}</strong></Link><div className="hint"><code>{p.reference_code}</code></div></td>
                    <td data-label="Customer">{p.customer.name}</td>
                    <td data-label="Repeats">{p.schedule_label}</td>
                    <td data-label="Next invoice">{p.next_run_date ? fmtDate(p.next_run_date) : '—'} {p.is_due && <Badge tone="warn">Due</Badge>}</td>
                    <td data-label="Last invoice">{fmtDate(p.last_run_date)}</td>
                    <td className="amount" data-label="Made">{runsLabel(p)}</td>
                    <td data-label="Each run">{p.mode === 'issue' ? 'Issue' : 'Draft'}</td>
                    <td data-label="Status"><StatusPill p={p} /> {p.last_error && <Badge tone="bad">Last run failed</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {dialog}
    </>
  );
}

export function RecurringDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { ask, dialog: askDialog } = useReasonDialog();
  const { data: p, error, loading, reload } = useLoad(() => api.get(`/sales/recurring-invoices/${id}/`), [id]);
  const [tab, setTab] = useState('overview');
  const [busy, setBusy] = useState(false);
  if (loading && !p) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const manage = user.permissions.includes('sales.manage');
  const issue = user.permissions.includes('sales.issue');
  const live = p.status === 'active' || p.status === 'paused';
  const cur = p.preview?.currency;

  const run = async (fn, ok) => {
    setBusy(true);
    try { const r = await fn(); if (ok) notify(typeof ok === 'function' ? ok(r) : ok); reload(); } catch (e) { notify(e.message, 'error'); reload(); }
    setBusy(false);
  };
  const generate = async () => {
    const issuing = p.mode === 'issue';
    if (!(await confirm({
      title: 'Generate the next invoice now?',
      body: <p>This creates the invoice for the run of <strong>{fmtDate(p.next_run_date)}</strong> straight away{issuing ? ', numbers and issues it' : ' as a draft'}. {new Date(`${p.next_run_date}T00:00:00`) > new Date() ? 'That run is not due yet, so the invoice is dated today. ' : ''}The schedule then moves on to the following run.</p>,
      confirmLabel: 'Generate',
    }))) return;
    run(() => api.post(`/sales/recurring-invoices/${id}/generate/`), (r) => `Created ${r.invoice.number || 'a draft invoice'}.`);
  };
  const stop = async () => {
    const reason = await ask({ title: `Stop ${p.name}?`, message: 'A stopped profile makes no more invoices and cannot be restarted. Invoices already made are kept.', label: 'Reason (optional)', required: false, confirmLabel: 'Stop profile' });
    if (reason === null) return;
    run(() => api.post(`/sales/recurring-invoices/${id}/stop/`, { reason }), 'Profile stopped.');
  };
  const del = async () => {
    if (!(await confirm({ title: 'Delete this profile?', body: <p>It has not made any invoice, so it can be removed.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/sales/recurring-invoices/${id}/`); notify('Profile deleted.'); navigate('/sales/recurring'); } catch (e) { notify(e.message, 'error'); }
  };
  const lines = p.preview?.lines || [];
  const totals = p.preview?.totals;

  return (
    <>
      <Link to="/sales/recurring" className="back-link">← Recurring invoices</Link>
      <header className="doc-head">
        <div>
          <h1>{p.name} <StatusPill p={p} /></h1>
          <p className="muted"><code>{p.reference_code}</code> · <Link to={`/sales/customers/${p.customer.id}`}>{p.customer.name}</Link> · {p.schedule_label}</p>
        </div>
        <div className="page-actions">
          {manage && p.status === 'active' && (!(p.mode === 'issue') || issue) && <button type="button" className="btn btn-primary" disabled={busy} onClick={generate}>Run now (generate next)</button>}
          {manage && live && <Link className="btn" to={`/sales/recurring/${id}/edit`}>Edit</Link>}
          {manage && p.status === 'active' && <button type="button" className="btn" disabled={busy} onClick={() => run(() => api.post(`/sales/recurring-invoices/${id}/pause/`), 'Profile paused.')}>Pause</button>}
          {manage && p.status === 'paused' && <button type="button" className="btn btn-primary" disabled={busy} onClick={() => run(() => api.post(`/sales/recurring-invoices/${id}/resume/`), 'Profile resumed.')}>Resume</button>}
          {manage && live && <button type="button" className="btn" disabled={busy} onClick={stop}>Stop</button>}
          {manage && p.occurrences_done === 0 && <button type="button" className="btn" onClick={del}>Delete</button>}
        </div>
      </header>

      {p.last_error && (
        <Notice type="error">
          <strong>The last run failed</strong>{p.last_error_at ? ` (${fmtDateTime(p.last_error_at)})` : ''}: {p.last_error}. Nothing was created for it and the schedule has not moved. Fix the cause and use “Run now”, or wait for the next scheduled run.
        </Notice>
      )}
      {p.status === 'paused' && <Notice type="warn">Paused. No invoices are made. When you resume, runs missed meanwhile are skipped and the next one is {fmtDate(p.next_run_date)} or the first date after you resume.</Notice>}
      {p.status === 'stopped' && <Notice type="warn">Stopped{p.stop_reason ? `: ${p.stop_reason}` : ''}. No more invoices will be made.</Notice>}
      {p.status === 'completed' && <Notice>Completed: all the scheduled invoices have been made.</Notice>}
      {p.is_due && <Notice type="warn">The next invoice ({fmtDate(p.next_run_date)}) is due. It is created automatically by the daily job, or with “Run now”.</Notice>}

      <Tabs tabs={[{ key: 'overview', label: 'Overview' }, { key: 'invoices', label: `Child invoices (${p.children.length})` }]} value={tab} onChange={setTab} />

      {tab === 'overview' && (
        <>
          <section className="card">
            <dl className="summary">
              <div><dt>Next invoice</dt><dd>{p.next_run_date ? fmtDate(p.next_run_date) : '—'}</dd></div>
              <div><dt>Last invoice</dt><dd>{fmtDate(p.last_run_date)}</dd></div>
              <div><dt>Invoices made</dt><dd>{runsLabel(p)}{p.remaining != null && p.status === 'active' ? ` (${p.remaining} to go)` : ''}</dd></div>
              <div><dt>Repeats</dt><dd>{p.schedule_label}, from {fmtDate(p.start_date)}{p.end_date ? ` to ${fmtDate(p.end_date)}` : ''}</dd></div>
              <div><dt>Each run</dt><dd>{p.mode_label}</dd></div>
              <div><dt>Due</dt><dd>{p.due_days != null ? `${p.due_days} days after the invoice date` : 'By the customer’s or company’s payment terms'}</dd></div>
              {p.preview?.place_of_supply && <div><dt>Place of supply</dt><dd>{p.preview.place_of_supply.name} ({p.preview.place_of_supply.code}) · {p.preview.supply_type === 'intra_state' ? 'CGST + SGST' : 'IGST'}</dd></div>}
              {p.project_ref && <div><dt>Project</dt><dd><Link to={`/projects/projects/${p.project_ref.id}`}>{projectLabel(p.project_ref)}</Link></dd></div>}
              {p.apply_terms && <div><dt>Notes and terms</dt><dd>Applied to every invoice</dd></div>}
            </dl>
            {p.upcoming.length > 0 && <p className="hint">Upcoming: {p.upcoming.map((d) => fmtDate(d)).join(', ')}{p.upcoming.length === 6 ? '…' : ''}</p>}
          </section>
          <section className="card" aria-label="Invoice template">
            <h2>What each invoice contains</h2>
            {p.preview_error && <Notice type="warn">{p.preview_error}</Notice>}
            {lines.length > 0 && (
              <div className="table-wrap">
                <table className="items-table">
                  <thead><tr><th>#</th><th>Description</th><th>HSN/SAC</th><th className="amount">Qty</th><th className="amount">Rate</th><th className="amount">Disc %</th>{p.preview.gst_applied && <th className="amount">GST %</th>}<th className="amount">Amount</th></tr></thead>
                  <tbody>
                    {lines.map((l, i) => (
                      <tr key={i}>
                        <td data-label="#">{i + 1}</td><td data-label="Description">{l.description}</td><td data-label="HSN/SAC">{l.hsn_sac || '—'}</td>
                        <td className="amount" data-label="Qty">{plain(l.quantity)} {l.unit}</td><td className="amount" data-label="Rate">{plain(l.rate)}</td>
                        <td className="amount" data-label="Disc %">{Number(l.discount_percent) ? Number(l.discount_percent) : '—'}</td>
                        {p.preview.gst_applied && <td className="amount" data-label="GST %">{Number(l.gst_rate)}</td>}
                        <td className="amount" data-label="Amount">{plain(l.taxable)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {totals && (
              <table className="items-table" style={{ marginLeft: 'auto', marginTop: '.75rem' }}>
                <tbody>
                  {p.preview.gst_applied && <tr><th scope="row">Taxable amount</th><td className="amount">{plain(totals.taxable)}</td></tr>}
                  {p.preview.gst_applied && totals.tax.map((t) => <tr key={t.label}><th scope="row">{t.label}</th><td className="amount">{plain(t.amount)}</td></tr>)}
                  <tr><th scope="row">Total per invoice ({cur})</th><td className="amount"><strong>{plain(totals.grand_total)}</strong></td></tr>
                </tbody>
              </table>
            )}
            <p className="hint">GST is worked out at every run from the customer’s place of supply, exactly as for an invoice typed by hand.</p>
          </section>
        </>
      )}

      {tab === 'invoices' && (
        <section className="card" aria-label="Child invoices">
          <h2>Child invoices</h2>
          {p.children.length === 0 ? <p className="empty">No invoice has been made from this profile yet.</p> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Run date</th><th>Number</th><th>Invoice date</th><th>Due date</th><th>Status</th><th className="amount">Amount</th><th className="amount">Balance due</th></tr></thead>
                <tbody>
                  {p.children.map((d) => (
                    <tr key={d.id} className="clickable" onClick={() => navigate(`/sales/documents/${d.id}`)}>
                      <td data-label="Run date">{fmtDate(d.recurring_run_date)}</td>
                      <td data-label="Number"><Link to={`/sales/documents/${d.id}`} onClick={(e) => e.stopPropagation()}><strong>{d.number || 'Draft'}</strong></Link></td>
                      <td data-label="Invoice date">{fmtDate(d.issue_date)}</td>
                      <td data-label="Due date">{fmtDate(d.due_date)}</td>
                      <td data-label="Status"><StatusBadge doc={d} /></td>
                      <td className="amount" data-label="Amount">{money(d.grand_total, d.currency)}</td>
                      <td className="amount" data-label="Balance due">{d.balance === null ? '—' : money(d.balance, d.currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
      {confirmDialog}
      {askDialog}
    </>
  );
}
