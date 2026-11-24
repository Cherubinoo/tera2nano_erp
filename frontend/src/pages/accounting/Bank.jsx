import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { money, num, plain, today } from '../../format';
import { asList, useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, fmtDate, useConfirm, useToast } from '../../ui';

function TransferDialog({ accounts, onClose, onSaved }) {
  const notify = useToast();
  const [v, setV] = useState({ date: today(), from_account: accounts[0]?.id || '', to_account: accounts[1]?.id || '', amount: '', notes: '' });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setV((x) => ({ ...x, [k]: e.target.value }));
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    setBusy(true); setError(null);
    try { await api.post('/accounting/transfers/', v); notify('Transfer recorded.'); onSaved(); } catch (err) { setError(err); }
    setBusy(false);
  };
  return (
    <Dialog title="Transfer between accounts" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[{ name: 'amount' }, { name: 'account' }, { name: 'date' }]} />
        <div className="form-grid">
          <Field id="t-from" label="From" required><select id="t-from" value={v.from_account} onChange={set('from_account')}>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
          <Field id="t-to" label="To" required><select id="t-to" value={v.to_account} onChange={set('to_account')}>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
          <Field id="t-amount" label="Amount" required><input id="t-amount" type="number" min="0" step="0.01" value={v.amount} onChange={set('amount')} /></Field>
          <Field id="t-date" label="Date" required><input id="t-date" type="date" value={v.date} onChange={set('date')} /></Field>
          <Field id="t-notes" label="Notes" wide><input id="t-notes" value={v.notes} maxLength={300} onChange={set('notes')} placeholder="e.g. Cash deposited at the bank" /></Field>
        </div>
        <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Transfer'}</button></div>
      </form>
    </Dialog>
  );
}

export function BankPage() {
  const { user } = useAuth();
  const { data, error, loading, reload } = useLoad(() => api.get('/accounting/bank/'), []);
  const [transfer, setTransfer] = useState(false);
  const manage = user.permissions.includes('accounting.manage');
  const rows = asList(data);
  return (
    <>
      <PageHeader title="Bank and cash" description="Your cash and bank accounts, what the books say is in each, and how much is still to be checked against the statement."
        actions={manage && rows.length > 1 && <button type="button" className="btn btn-primary" onClick={() => setTransfer(true)}>Transfer</button>} />
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      <Notice>Have the bank's statement as a file? <Link to="/accounting/bank-reconciliation">Bank reconciliation</Link> imports it and matches each line to the books. Reconcile below is for ticking lines off by hand (for example a cash account).</Notice>
      <div className="tiles">
        {rows.map((a) => (
          <section className="tile" key={a.id} aria-label={a.name}>
            <div className="tile-label">{a.subtype === 'cash' ? 'Cash' : 'Bank'}</div>
            <div><strong>{a.name}</strong></div>
            <div className="tile-value">{money(a.balance)}</div>
            <div className="hint">{a.unreconciled} line{a.unreconciled === 1 ? '' : 's'} to check{a.last_reconciled ? ` · last reconciled ${fmtDate(a.last_reconciled)}` : ' · never reconciled'}</div>
            <div className="row-actions" style={{ justifyContent: 'flex-start', marginTop: '.5rem' }}>
              <Link className="btn btn-small" to={`/accounting/accounts/${a.id}`}>Ledger</Link>
              {a.subtype === 'bank' && <Link className="btn btn-small" to={`/accounting/bank/${a.id}/match`}>Statement lines</Link>}
              {manage && <Link className="btn btn-small btn-primary" to={`/accounting/bank/${a.id}/reconcile`}>Reconcile</Link>}
            </div>
          </section>
        ))}
      </div>
      {transfer && <TransferDialog accounts={rows} onClose={() => setTransfer(false)} onSaved={() => { setTransfer(false); reload(); }} />}
    </>
  );
}

// Tick the lines that appear on the statement until the cleared balance equals the statement's closing balance.
export function ReconcilePage() {
  const { id } = useParams();
  const { user } = useAuth();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [date, setDate] = useState(today());
  const [balance, setBalance] = useState('');
  const [picked, setPicked] = useState(new Set());
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const data = useLoad(() => api.get(`/accounting/bank/${id}/unreconciled/`, { to: date }), [id, date]);
  const history = useLoad(() => api.get(`/accounting/bank/${id}/reconciliations/`), [id]);
  const manage = user.permissions.includes('accounting.manage');

  const lines = data.data?.lines || [];
  const cleared = num(data.data?.cleared_balance);
  const ticked = useMemo(() => lines.filter((l) => picked.has(l.id)).reduce((a, l) => a + Math.round((num(l.debit) - num(l.credit)) * 100), 0), [lines, picked]);
  const book = Math.round(cleared * 100) + ticked;
  const diff = Math.round(num(balance) * 100) - book;
  const toggle = (lid) => setPicked((s) => { const n = new Set(s); if (n.has(lid)) n.delete(lid); else n.add(lid); return n; });

  if (data.loading && !data.data) return <Loading />;
  if (data.error) return <ErrorBox error={data.error} onRetry={data.reload} />;

  const submit = async () => {
    setError(null); setBusy(true);
    try {
      await api.post(`/accounting/bank/${id}/reconcile/`, { statement_date: date, statement_balance: balance, lines: [...picked] });
      notify('Reconciled.'); setPicked(new Set()); setBalance(''); data.reload(); history.reload();
    } catch (err) { setError(err); }
    setBusy(false);
  };
  const undo = async (rec) => {
    if (!(await confirm({ title: 'Undo this reconciliation?', body: <p>Its lines go back to unreconciled.</p>, confirmLabel: 'Undo', danger: true }))) return;
    try { await api.post(`/accounting/reconciliations/${rec.id}/undo/`); notify('Reconciliation undone.'); data.reload(); history.reload(); } catch (err) { notify(err.message, 'error'); }
  };

  return (
    <>
      <Link to="/accounting/bank" className="back-link">← Bank and cash</Link>
      <PageHeader title={`Reconcile ${data.data.account.name}`} description="Choose the statement date and its closing balance, then tick every line that appears on the statement." />
      <section className="card">
        <FormErrors error={error} fields={[{ name: 'statement_balance' }, { name: 'lines' }, { name: 'statement_date' }]} />
        <div className="toolbar">
          <Field id="rc-date" label="Statement date"><input id="rc-date" type="date" value={date} onChange={(e) => { setDate(e.target.value); setPicked(new Set()); }} /></Field>
          <Field id="rc-bal" label="Statement closing balance" hint="Negative if overdrawn."><input id="rc-bal" type="number" step="0.01" value={balance} onChange={(e) => setBalance(e.target.value)} /></Field>
        </div>
        <div className="balance-strip" role="status">
          <div><span>Already cleared</span><strong>{plain(cleared)}</strong></div>
          <div><span>Ticked now</span><strong>{plain(ticked / 100)}</strong></div>
          <div><span>Books show</span><strong>{plain(book / 100)}</strong></div>
          <div className={balance !== '' && diff !== 0 ? 'due' : ''}><span>Difference to statement</span><strong>{balance === '' ? '—' : plain(diff / 100)}</strong></div>
        </div>
        {lines.length === 0 ? <p className="empty">Nothing left to reconcile up to this date.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th><span className="sr-only">Tick</span></th><th>Date</th><th>Reference</th><th>Description</th><th className="amount">Money in</th><th className="amount">Money out</th></tr></thead>
            <tbody>
              {lines.map((l) => (
                <tr key={l.id}>
                  <td><input type="checkbox" checked={picked.has(l.id)} onChange={() => toggle(l.id)} aria-label={`Clear ${l.reference || l.narration} ${plain(num(l.debit) || num(l.credit))}`} /></td>
                  <td data-label="Date">{fmtDate(l.date)}</td><td data-label="Reference"><code>{l.reference || '—'}</code></td><td data-label="Description">{l.memo || l.narration}</td>
                  <td className="amount" data-label="Money in">{num(l.debit) ? plain(l.debit) : ''}</td><td className="amount" data-label="Money out">{num(l.credit) ? plain(l.credit) : ''}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
        <div className="actions">
          <button type="button" className="btn btn-primary" disabled={busy || !manage || balance === '' || picked.size === 0 || diff !== 0} onClick={submit}>{busy ? 'Saving…' : 'Reconcile'}</button>
          {balance !== '' && diff !== 0 && picked.size > 0 && <span className="error-text">Tick lines until the difference is zero.</span>}
        </div>
      </section>
      <section className="card" aria-label="Earlier reconciliations">
        <h2>Earlier reconciliations</h2>
        {asList(history.data).length === 0 ? <p className="muted">None yet.</p> : (
          <div className="table-wrap"><table><thead><tr><th>Statement date</th><th className="amount">Closing balance</th><th className="amount">Lines</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>
            {asList(history.data).map((r, i) => (
              <tr key={r.id}><td data-label="Statement date">{fmtDate(r.statement_date)}</td><td className="amount" data-label="Closing balance">{plain(r.statement_balance)}</td><td className="amount" data-label="Lines">{r.lines}</td>
                <td className="row-actions">{manage && i === 0 && <button type="button" className="btn btn-small" onClick={() => undo(r)}>Undo</button>}{i > 0 && <Badge tone="good">Reconciled</Badge>}</td></tr>
            ))}
          </tbody></table></div>
        )}
      </section>
      {confirmDialog}
    </>
  );
}
