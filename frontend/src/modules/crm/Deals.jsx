import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, downloadFile } from '../../api';
import { useAuth } from '../../context';
import { money, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, Tabs, fmtDate, useToast } from '../../ui';
import { DealStatus, EntityPicker, OwnerSelect, Pager, initials, query, quotationUrl, useLookups } from './common';

export function DealDialog({ deal, account, contact, lookups, onClose, onSaved }) {
  const notify = useToast();
  const [v, setV] = useState({
    name: deal?.name || '', amount: deal?.amount ?? '', expected_close_date: deal?.expected_close_date || '', source: deal?.source || '', next_step: deal?.next_step || '',
    competitor: deal?.competitor || '', notes: deal?.notes || '', stage: '', owner: deal?.owner?.id || '', probability: deal ? String(deal.probability) : '' });
  const [acct, setAcct] = useState({ id: deal?.account?.id || account?.id || '', name: deal?.account?.name || account?.name || '' });
  const [contactId, setContactId] = useState(deal?.contact?.id || contact?.id || '');
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const contacts = useLoad(() => (acct.id ? api.get('/crm/contacts/', { account: acct.id, page_size: 100 }) : Promise.resolve({ results: [] })), [acct.id]);
  const list = contacts.data?.results || [];
  const hasItems = !!deal && deal.items?.length > 0;
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const openStages = (lookups.stages || []).filter((s) => s.kind === 'open');
  const submit = async (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    setFailure(null);
    const e = {};
    if (!v.name.trim()) e.name = 'Enter the deal name.';
    if (!acct.id) e.account = 'Choose the account.';
    if (v.amount !== '' && !(num(v.amount) >= 0)) e.amount = 'Enter an amount of 0 or more.';
    if (v.probability !== '' && !(num(v.probability) >= 0 && num(v.probability) <= 100)) e.probability = 'Enter a percentage from 0 to 100.';
    setErrors(e);
    if (Object.keys(e).length) return;
    setBusy(true);
    const body = { name: v.name.trim(), account: acct.id, contact: contactId || null, expected_close_date: v.expected_close_date || null, source: v.source, next_step: v.next_step,
      competitor: v.competitor, notes: v.notes, owner: v.owner || null };
    if (!hasItems) body.amount = v.amount === '' ? '0' : v.amount;
    if (deal) { if (deal.status === 'open' && v.probability !== '') body.probability = v.probability; } else { if (v.stage) body.stage = v.stage; if (v.probability !== '') body.probability = v.probability; }
    try {
      const saved = deal ? await api.patch(`/crm/deals/${deal.id}/`, body) : await api.post('/crm/deals/', body);
      notify(deal ? 'Deal saved.' : `Deal ${saved.reference_code} created.`);
      onSaved(saved);
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title={deal ? `Edit ${deal.name}` : 'New deal'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={[...Object.keys(v), 'account', 'contact'].map((name) => ({ name }))} />
        <div className="form-grid">
          <Field id="dl-name" label="Deal name" required error={errors.name}><input id="dl-name" value={v.name} onChange={set('name')} maxLength={200} /></Field>
          <EntityPicker id="dl-account" label="Account" kind="accounts" params={{ active: 'true' }} value={acct.id} valueLabel={acct.name} required error={errors.account}
            onChange={(id, name) => { setAcct({ id, name }); setContactId(''); setErrors((x) => ({ ...x, account: undefined })); }} />
          <Field id="dl-contact" label="Contact" error={errors.contact}>
            <select id="dl-contact" value={contactId} onChange={(e) => setContactId(e.target.value)} disabled={!acct.id}>
              <option value="">{acct.id ? 'No contact' : 'Choose an account first'}</option>{list.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Field>
          <Field id="dl-amount" label="Amount (INR)" error={errors.amount} hint={hasItems ? 'Worked out from the line items.' : undefined}>
            <input id="dl-amount" type="number" min="0" step="0.01" value={v.amount} onChange={set('amount')} disabled={hasItems} />
          </Field>
          <Field id="dl-close" label="Expected close" error={errors.expected_close_date}><input id="dl-close" type="date" value={v.expected_close_date} onChange={set('expected_close_date')} /></Field>
          {!deal && (
            <Field id="dl-stage" label="Stage" error={errors.stage}>
              <select id="dl-stage" value={v.stage} onChange={set('stage')}><option value="">First stage</option>{openStages.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.probability}%)</option>)}</select>
            </Field>
          )}
          {(!deal || deal.status === 'open') && (
            <Field id="dl-prob" label="Probability (%)" error={errors.probability} hint="Leave empty to use the stage's probability.">
              <input id="dl-prob" type="number" min="0" max="100" value={v.probability} onChange={set('probability')} />
            </Field>
          )}
          <Field id="dl-source" label="Source" error={errors.source}>
            <select id="dl-source" value={v.source} onChange={set('source')}><option value="">Not recorded</option>{(lookups.sources || []).map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}</select>
          </Field>
          <OwnerSelect id="dl-owner" label="Owner" value={v.owner} onChange={(x) => setV((s) => ({ ...s, owner: x }))} lookups={lookups} error={errors.owner} />
          <Field id="dl-next" label="Next step" error={errors.next_step}><input id="dl-next" value={v.next_step} onChange={set('next_step')} maxLength={200} /></Field>
          <Field id="dl-comp" label="Competitor" error={errors.competitor}><input id="dl-comp" value={v.competitor} onChange={set('competitor')} maxLength={150} /></Field>
        </div>
        <Field id="dl-notes" label="Notes" wide error={errors.notes}><textarea id="dl-notes" rows={3} value={v.notes} onChange={set('notes')} /></Field>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save deal'}</button>
        </div>
      </form>
    </Dialog>
  );
}

function LostDialog({ deal, stage, lookups, onClose, onDone }) {
  const reasons = lookups.lost_reasons || [];
  const [choice, setChoice] = useState(reasons[0] || '__other');
  const [other, setOther] = useState('');
  const [competitor, setCompetitor] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    const reason = choice === '__other' ? other.trim() : choice;
    if (!reason) { setError('Say why the deal was lost.'); return; }
    setBusy(true);
    try { await onDone({ lost_reason: reason, competitor: competitor.trim(), note: note.trim() }); } catch (err) { setError(err.fieldErrors?.lost_reason || err.message); setBusy(false); }
  };
  return (
    <Dialog title={`Mark ${deal.name} as lost`} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        {error && <div className="notice notice-error" role="alert">{error}</div>}
        <Field id="lost-reason" label="Why was it lost?" required>
          <select id="lost-reason" value={choice} onChange={(e) => { setChoice(e.target.value); setError(''); }}>
            {reasons.map((r) => <option key={r} value={r}>{r}</option>)}<option value="__other">Other reason…</option>
          </select>
        </Field>
        {choice === '__other' && <Field id="lost-other" label="Reason" required><input id="lost-other" value={other} maxLength={100} onChange={(e) => { setOther(e.target.value); setError(''); }} /></Field>}
        <Field id="lost-comp" label="Went to (competitor)"><input id="lost-comp" value={competitor} maxLength={150} onChange={(e) => setCompetitor(e.target.value)} /></Field>
        <Field id="lost-note" label="Note"><textarea id="lost-note" rows={2} maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-danger" disabled={busy}>{busy ? 'Saving…' : `Move to ${stage.name}`}</button>
        </div>
      </form>
    </Dialog>
  );
}

function WonDialog({ deal, onClose }) {
  const navigate = useNavigate();
  return (
    <Dialog title="Deal won" onClose={onClose}>
      <p><strong>{deal.name}</strong> is won for {money(deal.amount)}.</p>
      <p>{deal.customer ? <>Customer <Link to={`/sales/customers/${deal.customer.id}`}>{deal.customer.name}</Link> ({deal.customer.reference_code}) is linked to {deal.account.name}.</> : 'No sales customer could be linked automatically; link one from the deal page.'}</p>
      <div className="dialog-foot-inline">
        <button type="button" className="btn" onClick={() => { onClose(); }}>Close</button>
        <button type="button" className="btn" onClick={() => { onClose(); navigate(`/crm/deals/${deal.id}`); }}>Open deal</button>
        <button type="button" className="btn btn-primary" onClick={() => { onClose(); navigate(quotationUrl(deal)); }}>Create quotation</button>
      </div>
    </Dialog>
  );
}

/** Shared "move a deal to a stage" flow: asks for a reason when losing, celebrates a win and offers the quotation. */
export function useDealMover(lookups, onMoved) {
  const notify = useToast();
  const [pending, setPending] = useState(null);
  const [won, setWon] = useState(null);
  const send = async (deal, stage, extra = {}) => {
    const r = await api.post(`/crm/deals/${deal.id}/move/`, { stage: stage.id, ...extra });
    onMoved(r);
    if (stage.kind === 'won') setWon(r); else notify(`${deal.name} moved to ${stage.name}.`);
    return r;
  };
  const move = async (deal, stage) => {
    if (!stage || stage.id === deal.stage.id) return;
    if (stage.kind === 'lost') { setPending({ deal, stage }); return; }
    try { await send(deal, stage); } catch (e) { notify(e.message, 'error'); }
  };
  const dialogs = (
    <>
      {pending && <LostDialog deal={pending.deal} stage={pending.stage} lookups={lookups} onClose={() => setPending(null)}
        onDone={async (extra) => { await send(pending.deal, pending.stage, extra); setPending(null); }} />}
      {won && <WonDialog deal={won} onClose={() => setWon(null)} />}
    </>
  );
  return { move, dialogs };
}

export function MoveMenu({ deal, stages, onPick }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', close); document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc); };
  }, [open]);
  return (
    <div className="menu-wrap" ref={ref}>
      <button type="button" className="btn btn-small" aria-haspopup="menu" aria-expanded={open} aria-label={`Move ${deal.name} to another stage`} onClick={() => setOpen((o) => !o)}>Move to…</button>
      {open && (
        <ul className="menu crm-move-menu" role="menu">
          {stages.map((s) => (
            <li key={s.id} role="none"><button type="button" role="menuitem" disabled={s.id === deal.stage.id} onClick={() => { setOpen(false); onPick(deal, s); }}>
              <i className="crm-dot" style={{ background: s.colour }} aria-hidden="true" /> {s.name}</button></li>
          ))}
        </ul>
      )}
    </div>
  );
}

function DealCard({ deal, stages, canManage, dragging, setDragging, onMove }) {
  const navigate = useNavigate();
  return (
    <article className={`crm-card${dragging === deal.id ? ' dragging' : ''}${deal.overdue ? ' overdue' : ''}`} draggable={canManage}
      onDragStart={(e) => { e.dataTransfer.setData('text/plain', deal.id); e.dataTransfer.effectAllowed = 'move'; setDragging(deal.id); }}
      onDragEnd={() => setDragging(null)} aria-label={`${deal.name}, ${deal.account.name}, ${money(deal.amount)}`}>
      <Link className="crm-card-title" to={`/crm/deals/${deal.id}`}>{deal.name}</Link>
      <div className="crm-card-account">{deal.account.name}</div>
      <div className="crm-card-meta">
        <strong>{money(deal.amount)}</strong>
        {deal.status === 'open' && <span title="Probability">{deal.probability}%</span>}
        {deal.expected_close_date && <span className={deal.overdue ? 'crm-neg' : ''}>{deal.overdue ? 'Overdue ' : ''}{fmtDate(deal.expected_close_date)}</span>}
      </div>
      <div className="crm-card-foot">
        <span className="crm-avatar" title={deal.owner ? deal.owner.name : 'Unassigned'}>{initials(deal.owner?.name)}</span>
        {deal.open_activities > 0 && <Badge tone={deal.next_due && deal.next_due < new Date().toISOString().slice(0, 10) ? 'bad' : 'info'}>{deal.open_activities} to do</Badge>}
        {canManage && <MoveMenu deal={deal} stages={stages} onPick={onMove} />}
        <button type="button" className="btn btn-small" onClick={() => navigate(`/crm/deals/${deal.id}`)}>Open</button>
      </div>
    </article>
  );
}

function Board({ filters, lookups, canManage }) {
  const [dragging, setDragging] = useState(null);
  const [over, setOver] = useState(null);
  const { data, error, loading, reload } = useLoad(() => api.get('/crm/deals/board/', filters), [JSON.stringify(filters)]);
  const { move, dialogs } = useDealMover(lookups, () => reload());
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const stages = data.columns.map((c) => ({ id: c.id, name: c.name, colour: c.colour, kind: c.kind }));
  const all = data.columns.flatMap((c) => c.deals);
  const t = data.totals;
  return (
    <>
      <p className="crm-board-totals" aria-live="polite">{t.open_count} open deal{t.open_count === 1 ? '' : 's'} · <strong>{money(t.open_value)}</strong> · weighted <strong>{money(t.weighted)}</strong></p>
      <div className="crm-board" role="list">
        {data.columns.map((col) => (
          <section key={col.id} role="listitem" className={`crm-col${over === col.id ? ' over' : ''}`} aria-label={`${col.name}, ${col.count} deals, ${money(col.value)}`}
            onDragOver={(e) => { if (canManage) { e.preventDefault(); setOver(col.id); } }} onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOver(null); }}
            onDrop={(e) => {
              e.preventDefault(); setOver(null);
              const id = e.dataTransfer.getData('text/plain'); const d = all.find((x) => x.id === id);
              if (d) move(d, stages.find((s) => s.id === col.id));
            }}>
            <header className="crm-col-head" style={{ borderTopColor: col.colour }}>
              <div><strong>{col.name}</strong><span className="crm-sub">{col.kind === 'open' ? `${col.probability}%` : 'last 90 days'}</span></div>
              <div className="crm-col-sum"><Badge>{col.count}</Badge><span>{money(col.value)}</span></div>
            </header>
            {col.deals.length === 0 && <p className="crm-col-empty">{canManage ? 'Drop a deal here' : 'No deals'}</p>}
            {col.deals.map((d) => <DealCard key={d.id} deal={d} stages={stages} canManage={canManage} dragging={dragging} setDragging={setDragging} onMove={move} />)}
          </section>
        ))}
      </div>
      {dialogs}
    </>
  );
}

function DealTable({ filters, lookups, canManage }) {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState('-created_at');
  const { data, error, loading, reload } = useLoad(() => api.get('/crm/deals/', { ...filters, page, page_size: 25, sort }), [JSON.stringify(filters), page, sort]);
  useEffect(() => setPage(1), [JSON.stringify(filters)]); // eslint-disable-line react-hooks/exhaustive-deps
  const { move, dialogs } = useDealMover(lookups, () => reload());
  const rows = data?.results || [];
  return (
    <>
      <div className="toolbar">
        <Field id="dl-sort" label="Sort by">
          <select id="dl-sort" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="-created_at">Newest first</option><option value="expected_close_date">Expected close</option><option value="-amount">Largest amount</option><option value="name">Name</option>
          </select>
        </Field>
      </div>
      {loading && !data && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && rows.length === 0 && <p className="empty">No deal matches these filters.</p>}
      {rows.length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Deal</th><th>Account</th><th>Stage</th><th className="amount">Amount</th><th className="amount">Prob.</th><th className="amount">Weighted</th><th>Expected close</th><th>Owner</th>{canManage && <th><span className="sr-only">Move</span></th>}</tr></thead>
          <tbody>{rows.map((d) => (
            <tr key={d.id} className="clickable" onClick={() => navigate(`/crm/deals/${d.id}`)}>
              <td data-label="Deal"><Link to={`/crm/deals/${d.id}`} onClick={(e) => e.stopPropagation()}><strong>{d.name}</strong></Link><span className="crm-sub">{d.reference_code}</span></td>
              <td data-label="Account">{d.account.name}</td><td data-label="Stage"><DealStatus deal={d} /></td>
              <td className="amount" data-label="Amount">{plain(d.amount)}</td><td className="amount" data-label="Prob.">{d.probability}%</td><td className="amount" data-label="Weighted">{plain(d.weighted)}</td>
              <td data-label="Expected close" className={d.overdue ? 'crm-neg' : ''}>{fmtDate(d.expected_close_date)}</td><td data-label="Owner">{d.owner ? d.owner.name : '—'}</td>
              {canManage && <td data-label="Move" onClick={(e) => e.stopPropagation()}><MoveMenu deal={d} stages={lookups.stages || []} onPick={move} /></td>}
            </tr>))}</tbody>
        </table></div>
      )}
      {data && <Pager page={data.page} count={data.count} size={data.page_size} onPage={setPage} />}
      {rows.length > 0 && <div className="list-foot"><span>{data.count} deal{data.count === 1 ? '' : 's'}</span><span>Value <strong>{money(data.total_amount)}</strong></span><span>Weighted <strong>{money(data.total_weighted)}</strong></span></div>}
      {dialogs}
    </>
  );
}

export default function Deals() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const notify = useToast();
  const lookups = useLookups();
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState('board');
  const [creating, setCreating] = useState(params.get('new') === '1');
  const q = params.get('q') || '', owner = params.get('owner') || '', stage = params.get('stage') || '', status = params.get('status') || '';
  const [term, setTerm] = useState(q);
  const canManage = user.permissions.includes('crm.manage');
  const setParam = (c) => setParams(Object.fromEntries(Object.entries({ q, owner, stage, status, ...c }).filter(([, v]) => v)), { replace: true });
  const filters = view === 'board' ? { q, owner } : { q, owner, stage, status };
  const exportCsv = async () => {
    try { await downloadFile(`/crm/reports/deals.csv${query({ owner, status })}`, 'crm-deals.csv'); } catch (e) { notify(e.message, 'error'); }
  };
  return (
    <>
      <PageHeader title="Deals" description="Your sales pipeline. Drag a card to another stage, or use Move to."
        actions={<>
          <button type="button" className="btn" onClick={exportCsv}>Export CSV</button>
          {canManage && <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>New deal</button>}
        </>} />
      <Tabs tabs={[{ key: 'board', label: 'Board' }, { key: 'list', label: 'List' }]} value={view} onChange={setView} />
      <section className="card">
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setParam({ q: term.trim() }); }}>
          <Field id="deal-q" label="Search" className="grow"><input id="deal-q" value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Deal, account, contact or ID" /></Field>
          <OwnerSelect id="deal-f-owner" label="Owner" value={owner} onChange={(x) => setParam({ owner: x })} lookups={lookups} any extra={[['me', 'Me'], ['none', 'Unassigned']]} />
          {view === 'list' && (
            <>
              <Field id="deal-f-stage" label="Stage">
                <select id="deal-f-stage" value={stage} onChange={(e) => setParam({ stage: e.target.value })}><option value="">Any</option>{(lookups.stages || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
              </Field>
              <Field id="deal-f-status" label="Status">
                <select id="deal-f-status" value={status} onChange={(e) => setParam({ status: e.target.value })}><option value="">Any</option><option value="open">Open</option><option value="won">Won</option><option value="lost">Lost</option></select>
              </Field>
            </>
          )}
          <button type="submit" className="btn">Search</button>
        </form>
        {lookups.ready && (view === 'board' ? <Board filters={filters} lookups={lookups} canManage={canManage} /> : <DealTable filters={filters} lookups={lookups} canManage={canManage} />)}
      </section>
      {creating && lookups.ready && <DealDialog lookups={lookups} onClose={() => setCreating(false)} onSaved={(d) => { setCreating(false); navigate(`/crm/deals/${d.id}`); }} />}
    </>
  );
}
