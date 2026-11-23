import { useState } from 'react';
import { api } from '../../api';
import { Donut, SERIES_COLOURS } from '../../components/charts';
import { useAuth } from '../../context';
import { money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, Tabs, fmtDate, useConfirm, useReasonDialog, useToast } from '../../ui';
import { StatusBadge, fyOf, usePartnerLookups } from './common';

const cents = (v) => Math.round(num(v) * 100);

function RatioDialog({ partners, onClose, onSaved }) {
  const notify = useToast();
  const active = partners.filter((p) => p.is_active);
  const [date, setDate] = useState(today());
  const [note, setNote] = useState('');
  const [pcts, setPcts] = useState(() => Object.fromEntries(active.map((p) => [p.id, ''])));
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const total = Object.values(pcts).reduce((a, x) => a + cents(x), 0);
  const ok = total === 10000;
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setError(null);
    const errs = {};
    if (!date) errs.effective_from = 'Choose the date the ratio starts.';
    if (!ok) errs.shares = `The shares add up to ${(total / 100).toFixed(2)}%; they must add up to exactly 100%.`;
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const shares = Object.entries(pcts).filter(([, v]) => num(v) > 0).map(([partner, percent]) => ({ partner, percent }));
      await api.post('/partners/profit-shares/', { effective_from: date, note, shares });
      notify('Profit-sharing ratio saved.');
      onSaved();
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title="New profit-sharing ratio" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[{ name: 'effective_from' }, { name: 'shares' }, { name: 'note' }]} />
        <div className="form-grid">
          <Field id="rt-date" label="Effective from" required error={errors.effective_from} hint="The ratio applies from this date until a later one replaces it.">
            <input id="rt-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field id="rt-note" label="Note" error={errors.note}><input id="rt-note" value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} placeholder="For example: amended LLP agreement" /></Field>
        </div>
        <fieldset className="subcard">
          <legend>Each partner&apos;s share (%)</legend>
          {active.map((p) => (
            <div className="partners-share-row" key={p.id}>
              <span>{p.name}</span>
              <Field id={`rt-${p.id}`} label={`${p.name} share`}>
                <input id={`rt-${p.id}`} type="number" min="0" max="100" step="0.01" value={pcts[p.id]} onChange={(e) => setPcts((x) => ({ ...x, [p.id]: e.target.value }))} />
              </Field>
            </div>
          ))}
          <p className={`partners-sum ${ok ? 'ok' : 'off'}`} role="status">Total {(total / 100).toFixed(2)}% {ok ? '(adds up)' : '(must be 100%)'}</p>
          {errors.shares && <p className="error-text" role="alert">{errors.shares}</p>}
        </fieldset>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save ratio'}</button>
        </div>
      </form>
    </Dialog>
  );
}

function RatioTab({ canManage }) {
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { data: lk } = usePartnerLookups();
  const { data, error, loading, reload } = useLoad(() => api.get('/partners/profit-shares/'), []);
  const [dialog, setDialog] = useState(false);
  const current = (data || []).find((r) => r.current);
  const remove = async (r) => {
    if (!(await confirm({ title: `Remove the ratio from ${fmtDate(r.effective_from)}?`, body: <p>The ratio before it applies again from that date. This is recorded in the change history.</p>, confirmLabel: 'Remove', danger: true }))) return;
    try { await api.del(`/partners/profit-shares/${r.id}/`); notify('Ratio removed.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  if (loading && !data) return <Loading />;
  return (
    <>
      <ErrorBox error={error} onRetry={reload} />
      <div className="grid-2">
        <section className="card" aria-label="Current ratio">
          <h2>Ratio in force today</h2>
          {current ? (
            <>
              <p className="muted">From {fmtDate(current.effective_from)}{current.note ? `: ${current.note}` : ''}</p>
              <Donut slices={current.shares.map((s, i) => ({ key: s.partner, label: s.name, value: s.percent, colour: SERIES_COLOURS[i % SERIES_COLOURS.length] }))}
                centreLabel="partners" format={(v) => `${num(v)}%`} showTotal={false} />
            </>
          ) : <p className="empty">No ratio is in force yet. Add the ratio from the LLP agreement.</p>}
        </section>
        <section className="card" aria-label="About the ratio">
          <h2>How it works</h2>
          <p>Each ratio lists every partner&apos;s share of profit and always adds up to exactly 100%. A new ratio applies from its date; earlier periods keep the ratio that was in force then.</p>
          <p className="muted">When profit is allocated, the ratio in force at the end of the period is used.</p>
          {canManage && <button type="button" className="btn btn-primary" onClick={() => setDialog(true)} disabled={!lk}>New ratio</button>}
        </section>
      </div>
      <section className="card" aria-label="Ratio history">
        <h2>History</h2>
        {(data || []).length === 0 ? <p className="empty">Nothing recorded yet.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>From</th><th>Shares</th><th>Note</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{data.map((r) => (
              <tr key={r.id}><td data-label="From">{fmtDate(r.effective_from)} {r.current && <Badge tone="good">In force</Badge>}</td>
                <td data-label="Shares">{r.shares.map((s) => `${s.name} ${plain(s.percent)}%`).join(' · ')}</td><td data-label="Note">{r.note || '—'}</td>
                <td className="row-actions">{canManage && !r.used && <button type="button" className="btn btn-small" onClick={() => remove(r)}>Remove</button>}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </section>
      {dialog && lk && <RatioDialog partners={lk.partners} onClose={() => setDialog(false)} onSaved={() => { setDialog(false); reload(); }} />}
      {confirmDialog}
    </>
  );
}

function SharesTable({ shares }) {
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>Partner</th><th className="amount">Share</th><th className="amount">Amount</th></tr></thead>
      <tbody>{shares.map((s) => (
        <tr key={s.partner}><td data-label="Partner">{s.name}</td><td className="amount" data-label="Share">{plain(s.percent)}%</td><td className="amount" data-label="Amount"><strong>{plain(s.amount)}</strong></td></tr>
      ))}
        <tr className="partners-total-row"><td data-label="Partner">Total</td><td className="amount" data-label="Share">{plain(shares.reduce((a, s) => a + num(s.percent), 0))}%</td>
          <td className="amount" data-label="Amount">{plain(shares.reduce((a, s) => a + cents(s.amount), 0) / 100)}</td></tr>
      </tbody>
    </table></div>
  );
}

function Workings({ c }) {
  return (
    <dl className="partners-kv">
      <dt>Net profit from the books</dt><dd>{money(c.net_profit)}</dd>
      {(num(c.remuneration_charged) > 0 || num(c.interest_charged) > 0) && (
        <><dt>Already charged in that profit</dt><dd>Remuneration {money(c.remuneration_charged)}, interest {money(c.interest_charged)}</dd></>
      )}
      {num(c.other_deductions) !== 0 && (<><dt>Other deductions</dt><dd>{money(c.other_deductions)}{c.deduction_note ? ` (${c.deduction_note})` : ''}</dd></>)}
      <dt>{num(c.distributable) < 0 ? 'Loss to share' : 'Profit to share'}</dt><dd><strong>{money(c.distributable)}</strong></dd>
    </dl>
  );
}

function AllocationDialog({ onClose, onSaved }) {
  const notify = useToast();
  const fy = fyOf(today());
  const [v, setV] = useState({ period_start: fy.start, period_end: today() < fy.end ? today() : fy.end, other_deductions: '', deduction_note: '' });
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setPreview(null); setErrors((x) => ({ ...x, [k]: undefined })); };
  const check = () => {
    const errs = {};
    if (!v.period_start) errs.period_start = 'Choose the start.';
    if (!v.period_end) errs.period_end = 'Choose the end.';
    if (v.period_start && v.period_end && v.period_end < v.period_start) errs.period_end = 'The period cannot end before it starts.';
    setErrors(errs);
    return Object.keys(errs).length === 0;
  };
  const run = async () => {
    setError(null);
    if (!check()) return;
    setBusy(true);
    try { setPreview(await api.post('/partners/allocations/preview/', v)); } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  const save = async () => {
    setBusy(true);
    try { await api.post('/partners/allocations/', v); notify('Draft saved. Review it, then post it.'); onSaved(); } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title="Allocate profit" onClose={onClose} wide>
      <form onSubmit={(e) => { e.preventDefault(); e.stopPropagation(); run(); }} noValidate>
        <FormErrors error={error} fields={[{ name: 'period_start' }, { name: 'period_end' }, { name: 'other_deductions' }, { name: 'deduction_note' }]} />
        <div className="form-grid">
          <Field id="al-from" label="Period from" required error={errors.period_start}><input id="al-from" type="date" value={v.period_start} onChange={set('period_start')} /></Field>
          <Field id="al-to" label="Period to" required error={errors.period_end}><input id="al-to" type="date" value={v.period_end} onChange={set('period_end')} /></Field>
          <Field id="al-ded" label="Other deductions" error={errors.other_deductions} hint="Anything to set aside before sharing, such as a reserve or remuneration not yet booked.">
            <input id="al-ded" type="number" step="0.01" value={v.other_deductions} onChange={set('other_deductions')} />
          </Field>
          <Field id="al-note" label="Note on deductions" error={errors.deduction_note}><input id="al-note" value={v.deduction_note} maxLength={300} onChange={set('deduction_note')} /></Field>
        </div>
        <p className="hint">Net profit comes from the profit and loss statement, so remuneration and interest already booked to the partners are already taken off it.</p>
        <div className="actions"><button type="submit" className="btn" disabled={busy}>Preview</button></div>
        {preview && (
          <section className="subcard" aria-label="Preview">
            <h3>Preview</h3>
            {preview.warnings.map((w) => <Notice key={w} type="warn">{w}</Notice>)}
            <Workings c={preview} />
            <SharesTable shares={preview.shares} />
            <p className="hint">Rounded to the paisa; any odd paisa goes to the partner with the largest share.</p>
          </section>
        )}
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={save} disabled={busy || !preview || num(preview.distributable) === 0}>Save as draft</button>
        </div>
      </form>
    </Dialog>
  );
}

function AllocationView({ id, canManage, onClose, onChanged }) {
  const notify = useToast();
  const { ask, dialog: askDialog } = useReasonDialog();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { data: a, error, reload } = useLoad(() => api.get(`/partners/allocations/${id}/`), [id]);
  const [postDate, setPostDate] = useState('');
  const [busy, setBusy] = useState(false);
  const run = async (fn, ok) => {
    setBusy(true);
    try { await fn(); notify(ok); reload(); onChanged(); } catch (e) { notify(e.message, 'error'); }
    setBusy(false);
  };
  const reverse = async () => {
    const reason = await ask({ title: `Reverse ${a.number}?`, message: 'The credits to the partners\' current accounts are taken back out.', confirmLabel: 'Reverse allocation' });
    if (reason !== null) run(() => api.post(`/partners/allocations/${id}/reverse/`, { reason }), 'Allocation reversed.');
  };
  const remove = async () => {
    if (!(await confirm({ title: 'Delete this draft?', body: <p>The draft is removed. Nothing has been posted.</p>, confirmLabel: 'Delete', danger: true }))) return;
    setBusy(true);
    try { await api.del(`/partners/allocations/${id}/`); notify('Draft deleted.'); onChanged(); onClose(); } catch (e) { notify(e.message, 'error'); setBusy(false); }
  };
  return (
    <Dialog title={a ? `Profit allocation ${fmtDate(a.period_start)} to ${fmtDate(a.period_end)}` : 'Profit allocation'} onClose={onClose} wide>
      {!a && !error && <Loading />}
      <ErrorBox error={error} />
      {a && (
        <>
          <p><StatusBadge status={a.status} /> {a.number && <code>{a.number}</code>} {a.post_date && <span className="muted">posted {fmtDate(a.post_date)}</span>}</p>
          {a.reverse_reason && <Notice type="info">Reversed: {a.reverse_reason}</Notice>}
          <Workings c={a} />
          <SharesTable shares={a.shares} />
          <p className="hint">Ratio in force from {fmtDate(a.schedule_from)}.</p>
          {canManage && a.status === 'draft' && (
            <div className="toolbar">
              <Field id="av-date" label="Post on" hint="Defaults to the end of the period."><input id="av-date" type="date" value={postDate} onChange={(e) => setPostDate(e.target.value)} /></Field>
              <button type="button" className="btn btn-primary" disabled={busy}
                onClick={() => run(() => api.post(`/partners/allocations/${id}/post/`, postDate ? { post_date: postDate } : {}), 'Allocation posted to the books.')}>Post to the books</button>
              <button type="button" className="btn" disabled={busy} onClick={() => run(() => api.patch(`/partners/allocations/${id}/`, {}), 'Refreshed with the latest profit.')}>Refresh figures</button>
              <button type="button" className="btn btn-danger" disabled={busy} onClick={remove}>Delete draft</button>
            </div>
          )}
          {canManage && a.status === 'posted' && <div className="actions"><button type="button" className="btn" disabled={busy} onClick={reverse}>Reverse</button></div>}
        </>
      )}
      {askDialog}{confirmDialog}
    </Dialog>
  );
}

function AllocationsTab({ canManage }) {
  const { data, error, loading, reload } = useLoad(() => api.get('/partners/allocations/'), []);
  const [dialog, setDialog] = useState(false);
  const [view, setView] = useState(null);
  return (
    <>
      <section className="card">
        <div className="card-head"><h2>Profit allocations</h2>{canManage && <button type="button" className="btn btn-primary" onClick={() => setDialog(true)}>Allocate profit</button>}</div>
        <p className="muted">Share a period&apos;s profit (or loss) among the partners by the ratio, then post it to their current accounts.</p>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && (data || []).length === 0 && <p className="empty">No allocation yet.</p>}
        {(data || []).length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Period</th><th>Number</th><th>Status</th><th className="amount">Profit shared</th><th>Shares</th></tr></thead>
            <tbody>{data.map((a) => (
              <tr key={a.id} className={a.status === 'reversed' ? 'row-inactive' : ''}>
                <td data-label="Period"><button type="button" className="partners-linkbtn" onClick={() => setView(a.id)}>{fmtDate(a.period_start)} to {fmtDate(a.period_end)}</button></td>
                <td data-label="Number">{a.number ? <code>{a.number}</code> : '—'}</td><td data-label="Status"><StatusBadge status={a.status} /></td>
                <td className="amount" data-label="Profit shared"><strong>{plain(a.distributable)}</strong></td>
                <td data-label="Shares">{a.shares.map((s) => `${s.name} ${plain(s.amount)}`).join(' · ')}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </section>
      {dialog && <AllocationDialog onClose={() => setDialog(false)} onSaved={() => { setDialog(false); reload(); }} />}
      {view && <AllocationView id={view} canManage={canManage} onClose={() => setView(null)} onChanged={reload} />}
    </>
  );
}

export default function ProfitSharingPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState('allocations');
  const canManage = user.permissions.includes('partners.manage');
  return (
    <>
      <PageHeader title="Profit sharing" description="The ratio from the LLP agreement, and each period's profit shared by it." />
      <Tabs tabs={[{ key: 'allocations', label: 'Allocations' }, { key: 'ratio', label: 'Ratio' }]} value={tab} onChange={setTab} />
      {tab === 'allocations' ? <AllocationsTab canManage={canManage} /> : <RatioTab canManage={canManage} />}
    </>
  );
}
