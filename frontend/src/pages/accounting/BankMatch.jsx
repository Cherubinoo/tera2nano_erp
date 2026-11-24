import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { num, plain } from '../../format';
import { useDebounced, useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, Loading, Notice, PageHeader, fmtDate, useConfirm, useReasonDialog, useToast } from '../../ui';
import { ConfidenceBadge, Problem, signedText } from './bankCommon';

const FILTERS = [['unmatched', 'To match'], ['matched', 'Matched'], ['ignored', 'Ignored'], ['all', 'All']];
const cents = (v) => Math.round(num(v) * 100);
// "Payment received · T2N/PAY/..: Bright Buyers" without repeating the reference the narration already carries.
const entryText = (e) => [e.source_label, e.narration.includes(e.reference) ? '' : e.reference, e.narration].filter(Boolean).join(' · ');

// Statement lines of one bank account with suggested matches; accept, find by hand, create the missing entry, or ignore.
export default function BankMatchPage() {
  const { id } = useParams();
  const { user } = useAuth();
  const manage = user.permissions.includes('accounting.manage');
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { ask, dialog: reasonDialog } = useReasonDialog();
  const [status, setStatus] = useState('unmatched');
  const [q, setQ] = useState('');
  const query = useDebounced(q, 300);
  const [picked, setPicked] = useState(new Set());
  const [finding, setFinding] = useState(null); // statement lines to match by hand
  const [creating, setCreating] = useState(null); // the line to make an entry from
  const [busy, setBusy] = useState(false);
  const { data, error, loading, reload } = useLoad(() => api.get(`/accounting/bank/${id}/lines/`, { status, q: query }), [id, status, query]);

  const refresh = () => { setPicked(new Set()); reload(); };
  const act = async (fn, okText) => {
    setBusy(true);
    try { await fn(); if (okText) notify(okText); refresh(); } catch (err) { notify(err.message === 'Please correct the highlighted fields.' ? Object.values(err.fieldErrors)[0] : err.message, 'error'); }
    setBusy(false);
  };
  const accept = (line, s) => act(() => api.post(`/accounting/bank/${id}/matches/`, { statement_lines: [line.id], journal_lines: s.journal_lines, kind: 'suggested' }), 'Matched.');
  const undo = async (line) => {
    if (!(await confirm({ title: 'Undo this match?', body: <p>The statement line goes back to the lines to match. Any entry made from it stays in the books; void it on its own page if it was wrong.</p>, confirmLabel: 'Undo match' }))) return;
    act(() => api.post(`/accounting/bank-matches/${line.match}/undo/`), 'Match undone.');
  };
  const ignore = async (line) => {
    const reason = await ask({ title: 'Ignore this line', message: 'Ignored lines are not matched to anything. They still show in the reconciliation report as items not in the books.', label: 'Why is it ignored?', confirmLabel: 'Ignore line' });
    if (reason) act(() => api.post(`/accounting/statement-lines/${line.id}/ignore/`, { reason }), 'Line ignored.');
  };
  const unignore = (line) => act(() => api.post(`/accounting/statement-lines/${line.id}/unignore/`), 'The line is back to match.');
  const autoMatch = () => act(async () => { const r = await api.post(`/accounting/bank/${id}/matches/auto/`); notify(r.matched ? `${r.matched} line${r.matched === 1 ? '' : 's'} matched.` : 'No sure matches to accept.'); });
  const toggle = (lid) => setPicked((s) => { const n = new Set(s); if (n.has(lid)) n.delete(lid); else n.add(lid); return n; });

  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const lines = data.results;
  const counts = data.status_counts || {};
  const sure = lines.filter((l) => l.suggestions?.[0]?.confidence === 'high').length;
  const selected = lines.filter((l) => picked.has(l.id));

  return (
    <>
      <Link to="/accounting/bank-reconciliation" className="back-link">← Bank reconciliation</Link>
      <PageHeader title={`Match lines: ${data.account.name}`}
        description="Each statement line should be the same money as an entry in the books. Accept a suggestion, find the entry yourself, or create the missing entry from the line."
        actions={(
          <>
            <Link className="btn" to={`/accounting/bank/${id}/import`}>Import statement</Link>
            <Link className="btn" to={`/accounting/bank/${id}/reconciliation`}>Reconciliation</Link>
          </>
        )} />
      <div className="chips" role="group" aria-label="Show">
        {FILTERS.map(([k, label]) => (
          <button key={k} type="button" className="chip" aria-pressed={status === k} onClick={() => { setStatus(k); setPicked(new Set()); }}>
            {label}{k !== 'all' && counts[k] != null ? ` (${counts[k]})` : ''}
          </button>
        ))}
      </div>
      <div className="toolbar">
        <Field id="bm-q" label="Search the narration" className="grow"><input id="bm-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. NEFT, cheque number, name" /></Field>
        {manage && status === 'unmatched' && sure > 0 && <button type="button" className="btn btn-primary" disabled={busy} onClick={autoMatch}>Accept all sure matches ({sure})</button>}
        {manage && selected.length > 1 && <button type="button" className="btn" onClick={() => setFinding(selected)}>Match {selected.length} selected lines together</button>}
      </div>
      {lines.length === 0 && (
        <p className="empty">{status === 'unmatched' ? (counts.matched || counts.ignored ? 'Every statement line is matched or ignored. Open the reconciliation to finish.' : 'No statement lines yet. Import a statement first.') : 'Nothing here.'}</p>
      )}
      {data.count > lines.length && <Notice>Showing the first {lines.length} of {data.count} lines. Narrow them with the search.</Notice>}
      <ul className="stmt-lines">
        {lines.map((l) => (
          <li key={l.id} className={`stmt-line stmt-${l.status}`}>
            <div className="stmt-main">
              {manage && l.status === 'unmatched' && (
                <input type="checkbox" checked={picked.has(l.id)} onChange={() => toggle(l.id)} aria-label={`Select ${l.description}`} />
              )}
              <div className="stmt-text">
                <div className="stmt-date">{fmtDate(l.date)}{l.reference && <> · <code>{l.reference}</code></>}</div>
                <div className="stmt-desc">{l.description || '(no narration)'}</div>
              </div>
              <div className={`stmt-amount ${num(l.amount) > 0 ? 'good-text' : ''}`}>{signedText(l.amount)}</div>
            </div>
            {l.status === 'unmatched' && (
              <>
                {l.waiting_for && <p className="hint">An entry was made from this line and is waiting for approval. It will be offered here once approved.</p>}
                {l.suggestions.length > 0 ? (
                  <ul className="suggestions" aria-label="Suggested matches">
                    {l.suggestions.map((s) => (
                      <li key={s.journal_lines.join()}>
                        <div className="suggestion-text">
                          <ConfidenceBadge level={s.confidence} />{' '}
                          {s.entries.map((e) => <span key={e.id} className="suggestion-entry">{fmtDate(e.date)} · {entryText(e)} · {plain(Math.abs(num(e.amount)))}</span>)}
                          <div className="hint">{s.reasons.join(' · ')}</div>
                        </div>
                        {manage && <button type="button" className="btn btn-small btn-primary" disabled={busy} onClick={() => accept(l, s)}>Accept</button>}
                      </li>
                    ))}
                  </ul>
                ) : <p className="hint">No entry in the books has this amount near this date.</p>}
                {manage && (
                  <div className="row-actions stmt-actions">
                    <button type="button" className="btn btn-small" onClick={() => setFinding([l])}>Find entry</button>
                    <button type="button" className="btn btn-small" onClick={() => setCreating(l)} disabled={!!l.waiting_for}>Create entry</button>
                    <button type="button" className="btn btn-small" onClick={() => ignore(l)}>Ignore</button>
                  </div>
                )}
              </>
            )}
            {l.status === 'matched' && (
              <div className="stmt-matched">
                <Badge tone="good">Matched</Badge>{l.reconciled && <> <Badge tone="info">Reconciled</Badge></>}{' '}
                {(l.matched_with || []).map((e) => <span key={e.id} className="suggestion-entry">{fmtDate(e.date)} · {entryText(e)}</span>)}
                {manage && !l.reconciled && <button type="button" className="btn btn-small" onClick={() => undo(l)}>Undo match</button>}
              </div>
            )}
            {l.status === 'ignored' && (
              <div className="stmt-matched">
                <Badge>Ignored</Badge> <span className="muted">{l.ignore_reason}</span>
                {manage && !l.reconciled && <button type="button" className="btn btn-small" onClick={() => unignore(l)}>Stop ignoring</button>}
              </div>
            )}
          </li>
        ))}
      </ul>
      {finding && <FindDialog accountId={id} lines={finding} onClose={() => setFinding(null)} onDone={() => { setFinding(null); notify('Matched.'); refresh(); }} />}
      {creating && <CreateDialog line={creating} onClose={() => setCreating(null)}
        onDone={(r) => { setCreating(null); notify(r.pending ? 'Saved. It is waiting for approval; match the line once it is approved.' : 'Entry recorded and matched.'); refresh(); }} />}
      {confirmDialog}{reasonDialog}
    </>
  );
}

// Pick one or more book entries for the chosen statement line(s); both sides must come to the same money.
function FindDialog({ accountId, lines, onClose, onDone }) {
  const total = lines.reduce((a, l) => a + cents(l.amount), 0);
  const [q, setQ] = useState('');
  const query = useDebounced(q, 300);
  const [chosen, setChosen] = useState(new Map());
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const { data, loading } = useLoad(() => api.get(`/accounting/bank/${accountId}/book-entries/`, { q: query, direction: total > 0 ? 'in' : 'out' }), [accountId, query]);
  const picked = useMemo(() => [...chosen.values()].reduce((a, e) => a + cents(e.amount), 0), [chosen]);
  const diff = total - picked;
  const toggle = (e) => setChosen((m) => { const n = new Map(m); if (n.has(e.id)) n.delete(e.id); else n.set(e.id, e); return n; });
  const submit = async () => {
    setBusy(true); setError(null);
    try { await api.post(`/accounting/bank/${accountId}/matches/`, { statement_lines: lines.map((l) => l.id), journal_lines: [...chosen.keys()] }); onDone(); } catch (err) { setError(err); }
    setBusy(false);
  };
  return (
    <Dialog title="Find the entry in the books" onClose={onClose} wide>
      <p className="muted">{lines.length === 1 ? `${fmtDate(lines[0].date)} · ${lines[0].description}` : `${lines.length} statement lines`} · <strong>{signedText(total / 100)}</strong></p>
      <Problem error={error} />
      <Field id="fd-q" label="Search entries" hint="Reference, name, narration or amount. Tick several entries if one bank line covers them all.">
        <input id="fd-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} />
      </Field>
      {loading && !data ? <Loading /> : (
        (data?.results || []).length === 0 ? <p className="empty">No open entries found. Create the entry from the line instead.</p> : (
          <ul className="pick-list">
            {data.results.map((e) => (
              <li key={e.id}>
                <label className="check">
                  <input type="checkbox" checked={chosen.has(e.id)} onChange={() => toggle(e)} />
                  <span><strong>{fmtDate(e.date)}</strong> · {entryText(e)} · <strong>{signedText(e.amount)}</strong></span>
                </label>
              </li>
            ))}
          </ul>
        )
      )}
      <div className="balance-strip" role="status">
        <div><span>Statement</span><strong>{plain(total / 100)}</strong></div>
        <div><span>Chosen</span><strong>{plain(picked / 100)}</strong></div>
        <div className={diff !== 0 ? 'due' : ''}><span>Difference</span><strong>{plain(diff / 100)}</strong></div>
      </div>
      <div className="dialog-foot-inline">
        <button type="button" className="btn" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={busy || chosen.size === 0 || diff !== 0} onClick={submit}>{busy ? 'Matching…' : 'Match'}</button>
      </div>
    </Dialog>
  );
}

const KINDS_IN = [['customer_payment', 'Customer payment', 'Money from a customer, against open invoices or as an advance.'],
  ['income', 'Other income', 'Interest, refunds and other money that is not a sale.'],
  ['transfer', 'Transfer from my other account', 'Money moved in from your own cash or another bank account.']];
const KINDS_OUT = [['vendor_payment', 'Vendor payment', 'Money paid to a vendor, against open bills or as an advance.'],
  ['expense', 'Expense', 'Rent, fees, subscriptions and other spending.'],
  ['bank_charge', 'Bank charge', 'Charges, fees and GST the bank took.'],
  ['transfer', 'Transfer to my other account', 'Money moved out to your own cash or another bank account.']];

// Record what the line shows with the normal services (numbering, approvals, postings), then match it.
function CreateDialog({ line, onClose, onDone }) {
  const moneyIn = num(line.amount) > 0;
  const kinds = moneyIn ? KINDS_IN : KINDS_OUT;
  const [kind, setKind] = useState(kinds[0][0]);
  const [q, setQ] = useState('');
  const query = useDebounced(q, 300);
  const [v, setV] = useState({});
  const [docs, setDocs] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const opts = useLoad(() => api.get(`/accounting/statement-lines/${line.id}/create-options/`, { kind, q: query }), [line.id, kind, query]);
  const o = opts.data || {};
  const set = (k) => (e) => setV((x) => ({ ...x, [k]: e.target.value }));
  const party = kind === 'customer_payment' ? 'customer' : 'vendor';
  const docList = kind === 'customer_payment' ? o.invoices : o.bills;
  // Ticking a document of another customer or vendor starts again with that party.
  const pickDoc = (d) => {
    const pid = (d.customer || d.vendor).id;
    if (v[party] !== pid) { setV((x) => ({ ...x, [party]: pid })); setDocs([d.id]); return; }
    setDocs((list) => (list.includes(d.id) ? list.filter((x) => x !== d.id) : [...list, d.id]));
  };
  const parties = useMemo(() => {
    const out = [...((kind === 'customer_payment' ? o.customers : o.vendors) || [])];
    (docList || []).forEach((d) => { const p = d.customer || d.vendor; if (!out.some((x) => x.id === p.id)) out.push(p); });
    return out;
  }, [kind, o.customers, o.vendors, docList]);
  const changeKind = (k) => { setKind(k); setV({}); setDocs([]); setQ(''); setError(null); };
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    setBusy(true); setError(null);
    const body = { kind, ...v };
    if (kind === 'customer_payment') body.invoices = docs;
    if (kind === 'vendor_payment') body.bills = docs;
    if (kind === 'bank_charge' && !body.category && o.bank_charges_account) body.category = o.bank_charges_account;
    try { onDone(await api.post(`/accounting/statement-lines/${line.id}/create/`, body)); } catch (err) { setError(err); }
    setBusy(false);
  };
  const accounts = kind === 'income' ? o.income_accounts : o.expense_accounts;
  return (
    <Dialog title="Create the entry from this line" onClose={onClose} wide>
      <p className="muted">{fmtDate(line.date)} · {line.description} · <strong>{signedText(line.amount)}</strong></p>
      <form onSubmit={submit} noValidate>
        <fieldset className="field field-wide">
          <legend>What is this line?</legend>
          <div className="op-grid">
            {kinds.map(([k, label, hint]) => (
              <button key={k} type="button" className={`op-card${kind === k ? ' selected' : ''}`} aria-pressed={kind === k} onClick={() => changeKind(k)}>
                <strong>{label}</strong><span className="hint">{hint}</span>
              </button>
            ))}
          </div>
        </fieldset>
        <Problem error={error} />
        {opts.loading && !opts.data && <Loading />}
        {(kind === 'customer_payment' || kind === 'vendor_payment') && opts.data && (
          <>
            <Field id="cd-q" label={kind === 'customer_payment' ? 'Search invoices or customers' : 'Search bills or vendors'}>
              <input id="cd-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Number or name" />
            </Field>
            <h3>{kind === 'customer_payment' ? 'Open invoices' : 'Open bills'} <span className="hint">(the same amount first; tick the ones this money settles)</span></h3>
            {(docList || []).length === 0 ? <p className="empty">None open{q ? ' for this search' : ''}.</p> : (
              <ul className="pick-list">
                {docList.map((d) => (
                  <li key={d.id}>
                    <label className="check">
                      <input type="checkbox" checked={docs.includes(d.id)} onChange={() => pickDoc(d)} />
                      <span><strong>{d.number}</strong>{d.vendor_bill_no ? ` (${d.vendor_bill_no})` : ''} · {(d.customer || d.vendor).name} · due {fmtDate(d.due_date)} · left to pay <strong>{plain(d.balance)}</strong>
                        {cents(d.balance) === cents(o.amount) && <> <Badge tone="good">Same amount</Badge></>}</span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
            <Field id="cd-party" label={kind === 'customer_payment' ? 'Customer' : 'Vendor'} required hint="Set by the invoice or bill you tick, or choose one to keep the money as an advance.">
              <select id="cd-party" value={v[party] || ''} onChange={(e) => { setDocs([]); set(party)(e); }}>
                <option value="">Choose…</option>
                {parties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </Field>
          </>
        )}
        {['expense', 'income', 'bank_charge'].includes(kind) && opts.data && (
          <div className="form-grid">
            <Field id="cd-cat" label={kind === 'income' ? 'Income account' : 'Expense account'} required={kind !== 'bank_charge'}>
              <select id="cd-cat" value={v.category || (kind === 'bank_charge' ? o.bank_charges_account || '' : '')} onChange={set('category')}>
                <option value="">Choose…</option>
                {(accounts || []).map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}
              </select>
            </Field>
            <Field id="cd-party-name" label={kind === 'income' ? 'Received from' : 'Paid to'}><input id="cd-party-name" value={v.party_name || ''} maxLength={200} onChange={set('party_name')} /></Field>
            {o.gst_applied && (
              <>
                <Field id="cd-tax" label="GST" hint="The statement amount is taken as the total including GST.">
                  <select id="cd-tax" value={v.tax_mode || 'none'} onChange={set('tax_mode')}>
                    <option value="none">No GST</option><option value="intra">Same state (CGST + SGST)</option><option value="inter">Other state (IGST)</option>
                  </select>
                </Field>
                {v.tax_mode && v.tax_mode !== 'none' && (
                  <Field id="cd-rate" label="GST rate"><select id="cd-rate" value={v.tax_rate || ''} onChange={set('tax_rate')}><option value="">Choose…</option>{o.gst_rates.map((r) => <option key={r} value={r}>{r}%</option>)}</select></Field>
                )}
              </>
            )}
            <Field id="cd-notes" label="Notes" wide><input id="cd-notes" value={v.notes || ''} maxLength={300} onChange={set('notes')} placeholder="Left empty, the statement narration is used" /></Field>
          </div>
        )}
        {kind === 'transfer' && opts.data && (
          <div className="form-grid">
            <Field id="cd-other" label={moneyIn ? 'Moved in from' : 'Moved out to'} required>
              <select id="cd-other" value={v.other_account || ''} onChange={set('other_account')}>
                <option value="">Choose…</option>
                {(o.money_accounts || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </Field>
            <Field id="cd-tnotes" label="Notes"><input id="cd-tnotes" value={v.notes || ''} maxLength={300} onChange={set('notes')} /></Field>
          </div>
        )}
        <p className="hint">The entry is recorded on {fmtDate(line.date)} for {plain(Math.abs(num(line.amount)))}, with the same checks and approval rules as entering it on its own page.</p>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Record and match'}</button>
        </div>
      </form>
    </Dialog>
  );
}
