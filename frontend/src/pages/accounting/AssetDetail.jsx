import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../api';
import Attachments from '../../components/Attachments';
import { ColumnChart } from '../../components/charts';
import { useAuth } from '../../context';
import { compact, downloadCsv, money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, Tabs, fmtDate, useReasonDialog, useToast } from '../../ui';
import { AssetStatus, BOOKS_LABEL, projectLabel, termsText, useProjects } from './assetCommon';

function Tile({ label, value, hint, tone }) {
  return <div className={`tile ${tone || ''}`}><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</div>;
}

const STATE_LABEL = { posted: 'Posted', due: 'Due', upcoming: 'Upcoming' };

function RecordDialog({ asset, options, onClose, onDone }) {
  const notify = useToast();
  const [mode, setMode] = useState('post');
  const [paid, setPaid] = useState('payable');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    setBusy(true); setError(null);
    try {
      await api.post(`/accounting/assets/${asset.id}/acquisition/`, { mode, paid_from: mode === 'post' ? paid : undefined });
      notify(mode === 'post' ? 'Purchase posted to the books.' : 'Marked as already in the books.');
      onDone();
    } catch (err) { setError(err); }
    setBusy(false);
  };
  return (
    <Dialog title="Put the purchase in the books" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[]} />
        <div className="fa-choice" role="radiogroup" aria-label="How">
          <label><input type="radio" name="rec" checked={mode === 'post'} onChange={() => setMode('post')} /><span><strong>Post it now</strong><p className="hint">Dr Fixed asset {money(asset.cost)}, Cr the account below, dated {fmtDate(asset.purchase_date)}.</p></span></label>
          <label><input type="radio" name="rec" checked={mode === 'existing'} onChange={() => setMode('existing')} /><span><strong>It is already in the books</strong><p className="hint">No entry is made (a bill or opening balance already has it).</p></span></label>
        </div>
        {mode === 'post' && (
          <Field id="rec-paid" label="Paid from">
            <select id="rec-paid" value={paid} onChange={(e) => setPaid(e.target.value)}>
              <option value="payable">Accounts payable (pay the vendor later)</option>{options.money_accounts.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </Field>
        )}
        <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Confirm'}</button></div>
      </form>
    </Dialog>
  );
}

function DisposeDialog({ asset, options, onClose, onDone }) {
  const notify = useToast();
  const [v, setV] = useState({ kind: 'sold', date: today(), proceeds: '', proceeds_account: options.money_accounts[0]?.id || '', buyer: '', notes: '' });
  const [preview, setPreview] = useState(null);
  const [previewError, setPreviewError] = useState('');
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const proceeds = v.kind === 'written_off' ? 0 : num(v.proceeds);
  useEffect(() => {
    if (!v.date) return undefined;
    const t = setTimeout(async () => {
      try { setPreview(await api.get(`/accounting/assets/${asset.id}/disposal-preview/`, { date: v.date, proceeds })); setPreviewError(''); } catch (err) { setPreview(null); setPreviewError(err.message); }
    }, 300);
    return () => clearTimeout(t);
  }, [v.date, proceeds, asset.id]);

  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    setError(null);
    const errs = {};
    if (!v.date) errs.date = 'Enter the date.';
    if (v.kind === 'sold' && !(proceeds > 0)) errs.proceeds = 'Enter the sale amount.';
    if (proceeds > 0 && !v.proceeds_account) errs.proceeds_account = 'Choose where the money goes.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      await api.post(`/accounting/assets/${asset.id}/dispose/`, { ...v, proceeds, proceeds_account: proceeds > 0 ? v.proceeds_account : null });
      notify('Disposal posted to the books.');
      onDone();
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  const gl = preview ? num(preview.gain_loss) : 0;
  return (
    <Dialog title={`Sell, scrap or write off ${asset.name}`} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[]} />
        <div className="form-grid">
          <Field id="ds-kind" label="What happened" required error={errors.kind}>
            <select id="ds-kind" value={v.kind} onChange={set('kind')}><option value="sold">Sold</option><option value="disposed">Scrapped or given away</option><option value="written_off">Written off (no proceeds)</option></select>
          </Field>
          <Field id="ds-date" label="Date" required error={errors.date}><input id="ds-date" type="date" value={v.date} onChange={set('date')} /></Field>
          {v.kind !== 'written_off' && (
            <>
              <Field id="ds-proceeds" label={v.kind === 'sold' ? 'Sale amount' : 'Scrap value (if any)'} required={v.kind === 'sold'} error={errors.proceeds} hint="Before any GST on the sale.">
                <input id="ds-proceeds" type="number" min="0" step="0.01" value={v.proceeds} onChange={set('proceeds')} /></Field>
              <Field id="ds-account" label="Money received in" error={errors.proceeds_account}>
                <select id="ds-account" value={v.proceeds_account} onChange={set('proceeds_account')}>{options.proceeds_accounts.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</select>
              </Field>
              <Field id="ds-buyer" label="Buyer" error={errors.buyer}><input id="ds-buyer" value={v.buyer} maxLength={200} onChange={set('buyer')} /></Field>
            </>
          )}
          <Field id="ds-notes" label="Notes" wide error={errors.notes}><input id="ds-notes" value={v.notes} maxLength={300} onChange={set('notes')} /></Field>
        </div>
        {previewError && <p className="error-text" role="alert">{previewError}</p>}
        {preview && (
          <dl className="fa-figures" aria-label="What this will post" role="status">
            <div><dt>Cost</dt><dd>{money(preview.cost)}</dd></div>
            <div><dt>Depreciation to date</dt><dd>{money(preview.accumulated)}</dd></div>
            <div><dt>Charged on disposal</dt><dd>{money(preview.final_depreciation)}</dd></div>
            <div><dt>Book value</dt><dd>{money(preview.wdv)}</dd></div>
            <div><dt>{gl < 0 ? 'Loss on disposal' : 'Gain on disposal'}</dt><dd className={gl < 0 ? 'fa-loss' : 'fa-gain'}>{money(Math.abs(gl))}</dd></div>
          </dl>
        )}
        <p className="hint">Posts: money received and the depreciation to date are debited, the cost is credited, and the difference goes to profit or loss on sale of fixed assets.</p>
        <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Posting…' : 'Post disposal'}</button></div>
      </form>
    </Dialog>
  );
}

export default function AssetDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const manage = user.permissions.includes('accounting.manage');
  const notify = useToast();
  const { ask, dialog: reasonDialog } = useReasonDialog();
  const [tab, setTab] = useState('schedule');
  const [dialog, setDialog] = useState(null);
  const asset = useLoad(() => api.get(`/accounting/assets/${id}/`), [id]);
  const schedule = useLoad(() => api.get(`/accounting/assets/${id}/schedule/`), [id]);
  const options = useLoad(() => api.get('/accounting/assets/options/'), []);
  const projects = useProjects();
  if (!asset.data && !asset.error) return <Loading />;
  if (asset.error) return <ErrorBox error={asset.error} onRetry={asset.reload} />;
  const a = asset.data;
  const sc = schedule.data;
  const inUse = a.status === 'in_use';
  const reloadAll = () => { asset.reload(); schedule.reload(); };

  const undo = async () => {
    const reason = await ask({ title: 'Take the purchase out of the books?', message: 'The purchase entry is reversed. The asset stays in the register as not yet in the books.', confirmLabel: 'Undo' });
    if (reason === null) return;
    try { await api.post(`/accounting/assets/${id}/acquisition/undo/`, { change_reason: reason }); notify('Purchase taken out of the books.'); reloadAll(); } catch (e) { notify(e.message, 'error'); }
  };
  const reverseDisposal = async () => {
    const reason = await ask({ title: 'Reverse this disposal?', message: 'The entry is reversed and the asset is back in use.', confirmLabel: 'Reverse' });
    if (reason === null) return;
    try { await api.post(`/accounting/asset-disposals/${a.disposal.id}/reverse/`, { change_reason: reason }); notify('Disposal reversed.'); reloadAll(); } catch (e) { notify(e.message, 'error'); }
  };
  const remove = async () => {
    if (!window.confirm(`Delete ${a.name}? Nothing has been posted for it.`)) return;
    try { await api.del(`/accounting/assets/${id}/`); navigate('/accounting/assets'); } catch (e) { notify(e.message, 'error'); }
  };
  const exportCsv = () => downloadCsv(`${a.code}-depreciation-schedule.csv`, [['Month', 'Financial year', 'Charge', 'Total depreciation', 'Book value', 'State'],
    ...sc.rows.map((r) => [r.period_start.slice(0, 7), r.fy, r.charge, r.accumulated, r.wdv, STATE_LABEL[r.state]])]);

  return (
    <>
      <Link to="/accounting/assets" className="back-link">← Fixed assets</Link>
      <PageHeader title={a.name} description={`${a.code} · ${a.category.name}${a.serial_number ? ` · Serial ${a.serial_number}` : ''}`}
        actions={(
          <>
            <AssetStatus status={a.status} label={a.status_label} />
            {manage && inUse && a.acquisition === 'pending' && <button type="button" className="btn btn-primary" onClick={() => setDialog('record')} disabled={!options.data}>Record purchase</button>}
            {manage && inUse && a.acquisition !== 'pending' && <button type="button" className="btn btn-primary" onClick={() => setDialog('dispose')} disabled={!options.data}>Sell, scrap or write off</button>}
            {manage && inUse && <Link className="btn" to={`/accounting/assets/${id}/edit`}>Edit</Link>}
            {manage && inUse && a.acquisition !== 'pending' && !a.has_depreciation && <button type="button" className="btn" onClick={undo}>Undo purchase</button>}
            {manage && inUse && a.acquisition === 'pending' && <button type="button" className="btn" onClick={remove}>Delete</button>}
          </>
        )} />
      {inUse && a.acquisition === 'pending' && <Notice type="warn">The purchase is not in the books yet, so depreciation is not charged. Record the purchase (or mark it as already in the books).</Notice>}
      {a.disposal && (
        <Notice type={num(a.disposal.gain_loss) < 0 ? 'warn' : 'success'}>
          <span>{a.disposal.kind_label} on {fmtDate(a.disposal.date)}: proceeds {money(a.disposal.proceeds)}, book value {money(a.disposal.wdv)}, {num(a.disposal.gain_loss) < 0 ? `loss ${money(-num(a.disposal.gain_loss))}` : `gain ${money(a.disposal.gain_loss)}`}.{a.disposal.buyer ? ` Buyer: ${a.disposal.buyer}.` : ''}</span>
          {manage && <button type="button" className="btn btn-small" onClick={reverseDisposal}>Reverse disposal</button>}
        </Notice>
      )}
      <div className="tiles">
        <Tile label="Cost" value={money(a.cost)} hint={`Bought ${fmtDate(a.purchase_date)}`} />
        <Tile label="Depreciation so far" value={money(a.accumulated)} />
        <Tile label="Book value" value={money(a.wdv)} tone="good" hint={inUse ? 'Cost less depreciation' : 'Left the books'} />
        <Tile label="Residual value" value={money(a.salvage_value)} hint={termsText(a)} />
      </div>
      <Tabs tabs={[{ key: 'schedule', label: 'Depreciation schedule' }, { key: 'details', label: 'Details' }, { key: 'documents', label: 'Documents' }]} value={tab} onChange={setTab} />
      {tab === 'schedule' && (
        <>
          {schedule.loading && !sc && <Loading />}
          <ErrorBox error={schedule.error} onRetry={schedule.reload} />
          {sc && sc.years.length > 0 && (
            <section className="card"><h2>Depreciation by financial year</h2>
              <div style={{ maxWidth: 640 }}><ColumnChart title="Scheduled depreciation per financial year" format={compact} series={[{ key: 'charge', label: 'Depreciation', colour: 'var(--chart-1)' }]}
                data={sc.years.slice(0, 8).map((y) => ({ label: y.fy, values: { charge: y.charge } }))} /></div>
            </section>
          )}
          {sc && (
            <section className="card">
              <div className="card-head"><h2>Month by month</h2>{sc.rows.length > 0 && <button type="button" className="btn" onClick={exportCsv}>Export CSV</button>}</div>
              {sc.rows.length === 0 ? <p className="empty">Nothing to depreciate: the residual value is the whole cost.</p> : (
                <div className="table-wrap"><table>
                  <thead><tr><th>Month</th><th>Financial year</th><th className="amount">Charge</th><th className="amount">Total depreciation</th><th className="amount">Book value</th><th>State</th></tr></thead>
                  <tbody>{sc.rows.map((r) => (
                    <tr key={r.period_start}>
                      <td data-label="Month">{new Date(`${r.period_start}T00:00:00`).toLocaleDateString('en-IN', { month: 'short', year: 'numeric' })}{num(r.fraction) < 1 && <span className="hint"> ({Math.round(num(r.fraction) * 100)}% of month)</span>}</td>
                      <td data-label="Financial year">{r.fy}</td>
                      <td className="amount" data-label="Charge">{plain(r.charge)}</td><td className="amount" data-label="Total depreciation">{plain(r.accumulated)}</td><td className="amount" data-label="Book value">{plain(r.wdv)}</td>
                      <td data-label="State"><span className={`fa-state-${r.state}`}>{STATE_LABEL[r.state]}</span></td>
                    </tr>
                  ))}</tbody>
                  <tfoot><tr><th scope="row" colSpan={2}>Total over the life</th><td className="amount"><strong>{plain(sc.total)}</strong></td><td colSpan={3} /></tr></tfoot>
                </table></div>
              )}
              {a.method === 'wdv' && <p className="hint">Written-down value never reaches the residual value exactly; ten financial years are shown.</p>}
            </section>
          )}
        </>
      )}
      {tab === 'details' && (
        <section className="card"><h2>Details</h2>
          <dl className="summary">
            <div><dt>Purchase date</dt><dd>{fmtDate(a.purchase_date)}</dd></div>
            <div><dt>Put to use</dt><dd>{fmtDate(a.put_to_use_date)}</dd></div>
            <div><dt>Depreciation</dt><dd>{termsText(a)}</dd></div>
            <div><dt>In the books</dt><dd><Badge tone={a.in_books ? 'good' : 'warn'}>{BOOKS_LABEL[a.acquisition]}</Badge></dd></div>
            <div><dt>Vendor</dt><dd>{a.vendor?.name || '—'}</dd></div>
            <div><dt>Vendor bill</dt><dd>{a.bill?.number || '—'}</dd></div>
            <div><dt>Location</dt><dd>{a.location || '—'}</dd></div>
            <div><dt>Project</dt><dd>{a.project ? projectLabel(projects, a.project) || 'Project' : '—'}</dd></div>
            <div><dt>Notes</dt><dd>{a.notes || '—'}</dd></div>
          </dl>
        </section>
      )}
      {tab === 'documents' && <Attachments entityType="fixed_asset" entityId={a.id} canManage={manage} hint="Purchase invoice, warranty, insurance and handover papers." />}
      {dialog === 'record' && options.data && <RecordDialog asset={a} options={options.data} onClose={() => setDialog(null)} onDone={() => { setDialog(null); reloadAll(); }} />}
      {dialog === 'dispose' && options.data && <DisposeDialog asset={a} options={options.data} onClose={() => setDialog(null)} onDone={() => { setDialog(null); reloadAll(); }} />}
      {reasonDialog}
    </>
  );
}
