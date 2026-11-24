import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { downloadCsv, money, num, plain, today } from '../../format';
import { asList, useLoad } from '../../hooks';
import { Badge, ErrorBox, Field, Loading, Notice, PageHeader, fmtDate, useConfirm, useToast } from '../../ui';
import { InOut, Problem } from './bankCommon';

// Every bank account with where its reconciliation stands, and the three steps: import, match, reconcile.
export function BankRecHub() {
  const { user } = useAuth();
  const manage = user.permissions.includes('accounting.manage');
  const { data, error, loading, reload } = useLoad(() => api.get('/accounting/bank-rec/'), []);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const rows = asList(data);
  return (
    <>
      <PageHeader title="Bank reconciliation"
        description="Check the books against the bank: import the statement your bank gives you, match each line to what is already recorded, then mark the account reconciled."
        actions={manage && rows.length > 0 && <Link className="btn btn-primary" to="/accounting/bank-import">Import statement</Link>} />
      <ol className="steps-inline" aria-label="How it works">
        <li><strong>1. Import</strong> the CSV, Excel, PDF or OFX statement.</li>
        <li><strong>2. Match</strong> each line, or create the missing entry from it.</li>
        <li><strong>3. Reconcile</strong> when the statement and the books agree.</li>
      </ol>
      {rows.length === 0 && <Notice>There is no bank account yet. Add one in Company &gt; Bank Accounts.</Notice>}
      <div className="tiles">
        {rows.map((a) => (
          <section className="tile" key={a.id} aria-label={a.name}>
            <div className="tile-label">{a.bank || 'Bank'}</div>
            <div><strong>{a.name}</strong></div>
            <div className="tile-value">{money(a.book_balance)}</div>
            <div className="hint">In the books today</div>
            <dl className="facts facts-tight">
              <div><dt>Statement balance</dt><dd>{a.statement_balance != null ? `${money(a.statement_balance)} on ${fmtDate(a.statement_balance_date)}` : 'No statement yet'}</dd></div>
              <div><dt>Lines to match</dt><dd>{a.to_match ? <Badge tone="warn">{a.to_match}</Badge> : (a.statements ? <Badge tone="good">None</Badge> : '—')}</dd></div>
              <div><dt>Reconciled up to</dt><dd>{a.reconciled_up_to ? fmtDate(a.reconciled_up_to) : 'Not yet'}</dd></div>
            </dl>
            <div className="row-actions" style={{ justifyContent: 'flex-start', marginTop: '.5rem', flexWrap: 'wrap' }}>
              {manage && <Link className="btn btn-small" to={`/accounting/bank/${a.id}/import`}>Import</Link>}
              <Link className={`btn btn-small${a.to_match ? ' btn-primary' : ''}`} to={`/accounting/bank/${a.id}/match`}>Match lines</Link>
              <Link className="btn btn-small" to={`/accounting/bank/${a.id}/reconciliation`}>Reconciliation</Link>
            </div>
          </section>
        ))}
      </div>
      <p className="hint">Bank feeds straight from the bank are not connected; statements are imported from files. Cash accounts are checked by hand in Bank and Cash.</p>
    </>
  );
}

const endOfLastMonth = () => {
  const d = new Date();
  d.setDate(0);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

// The reconciliation of one account on a date: summary, the report (printable and exportable), open items, and "reconciled up to".
export function BankReconciliationPage() {
  const { id } = useParams();
  const { user } = useAuth();
  const manage = user.permissions.includes('accounting.manage');
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const [range, setRange] = useState({ from: '', to: today() });
  const [form, setForm] = useState({ from: '', to: today() });
  const [balance, setBalance] = useState('');
  const [entered, setEntered] = useState('');
  const [picked, setPicked] = useState(new Set());
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const { data: r, error: loadError, loading, reload } = useLoad(
    () => api.get(`/accounting/bank/${id}/reconciliation/`, { from: range.from, to: range.to, statement_balance: entered }), [id, range.from, range.to, entered]);

  if (loading && !r) return <Loading />;
  if (loadError) return <ErrorBox error={loadError} onRetry={reload} />;

  const close = async () => {
    if (!(await confirm({ title: `Mark reconciled up to ${fmtDate(r.as_of)}?`, body: <p>The statement lines and entries up to this date are locked: their matches cannot be undone unless this reconciliation is undone first.</p>, confirmLabel: 'Mark reconciled' }))) return;
    setBusy(true); setError(null);
    try {
      await api.post(`/accounting/bank/${id}/reconciliation/close/`, { as_of: r.as_of, statement_balance: entered });
      notify(`Reconciled up to ${fmtDate(r.as_of)}.`); reload();
    } catch (err) { setError(err); }
    setBusy(false);
  };
  const undo = async (h) => {
    if (!(await confirm({ title: 'Undo this reconciliation?', body: <p>Its lines are unlocked again. Matches stay as they are.</p>, confirmLabel: 'Undo', danger: true }))) return;
    try { await api.post(`/accounting/reconciliations/${h.id}/undo/`); notify('Reconciliation undone.'); reload(); } catch (err) { notify(err.message, 'error'); }
  };
  const markCleared = async () => {
    setBusy(true); setError(null);
    try {
      await api.post(`/accounting/bank/${id}/cleared/`, { journal_lines: [...picked] });
      notify('Marked as already cleared.'); setPicked(new Set()); reload();
    } catch (err) { setError(err); }
    setBusy(false);
  };
  const toggle = (lid) => setPicked((s) => { const n = new Set(s); if (n.has(lid)) n.delete(lid); else n.add(lid); return n; });

  const exportCsv = () => downloadCsv(`bank-reconciliation-${r.account.code}-${r.as_of}.csv`, [
    ['Bank reconciliation', r.account.name, `as at ${r.as_of}`],
    ['Balance as per books', '', r.book_balance],
    ['Less: money received in the books, not yet in the bank'], ...r.uncleared_in.lines.map((l) => ['', `${l.date} ${l.reference} ${l.narration}`, `-${l.amount}`]),
    ['Add: payments made in the books, not yet through the bank'], ...r.uncleared_out.lines.map((l) => ['', `${l.date} ${l.reference} ${l.narration}`, String(-num(l.amount))]),
    ['Add: money in on the statement, not in the books'], ...r.bank_in.lines.map((l) => ['', `${l.date} ${l.description}`, l.amount]),
    ['Less: money out on the statement, not in the books'], ...r.bank_out.lines.map((l) => ['', `${l.date} ${l.description}`, l.amount]),
    ['Balance the bank should show', '', r.expected_bank_balance],
    ['Balance as per bank statement', '', r.statement_balance ?? ''],
    ['Difference', '', r.difference ?? ''],
  ]);

  const firstDate = r.first_statement_date;
  return (
    <>
      <Link to="/accounting/bank-reconciliation" className="back-link no-print">← Bank reconciliation</Link>
      <PageHeader title={`Bank reconciliation: ${r.account.name}`} description={`As at ${fmtDate(r.as_of)}${r.last_reconciled ? ` · reconciled up to ${fmtDate(r.last_reconciled)}` : ' · not reconciled yet'}`}
        actions={(
          <span className="no-print">
            <Link className="btn" to={`/accounting/bank/${id}/match`}>Match lines</Link>{' '}
            <button type="button" className="btn" onClick={exportCsv}>Export CSV</button>{' '}
            <button type="button" className="btn" onClick={() => window.print()}>Print</button>
          </span>
        )} />
      <form className="toolbar no-print" onSubmit={(e) => { e.preventDefault(); setRange(form); }}>
        <Field id="br-from" label="From" hint="Optional: for the line counts"><input id="br-from" type="date" value={form.from} onChange={(e) => setForm({ ...form, from: e.target.value })} /></Field>
        <Field id="br-to" label="As at"><input id="br-to" type="date" value={form.to} onChange={(e) => setForm({ ...form, to: e.target.value })} /></Field>
        <button type="submit" className="btn">Show</button>
        <button type="button" className="btn" onClick={() => { const v = { from: '', to: endOfLastMonth() }; setForm(v); setRange(v); }}>End of last month</button>
      </form>

      <div className="balance-strip" role="status">
        <div><span>Books</span><strong>{plain(r.book_balance)}</strong></div>
        <div><span>Statement{r.statement_balance_date && r.statement_balance_source === 'statement' ? ` (${fmtDate(r.statement_balance_date)})` : ''}</span><strong>{r.statement_balance != null ? plain(r.statement_balance) : '—'}</strong></div>
        <div className={r.difference != null && num(r.difference) !== 0 ? 'due' : ''}><span>Difference</span><strong>{r.difference != null ? plain(r.difference) : '—'}</strong></div>
        <div className={r.unmatched_count ? 'due' : ''}><span>Lines to match</span><strong>{r.unmatched_count}</strong></div>
      </div>
      {r.period_counts.lines > 0 && (
        <p className="muted">{r.start ? `From ${fmtDate(r.start)}: ` : ''}{r.period_counts.lines} statement lines, {r.period_counts.matched} matched, {r.period_counts.ignored} ignored, {r.period_counts.unmatched} to match.</p>
      )}

      <section className="card recon-report" aria-label="Reconciliation report">
        <h2>Bank reconciliation statement as at {fmtDate(r.as_of)}</h2>
        <div className="table-wrap"><table className="report-table">
          <tbody>
            <tr><th scope="row">Balance as per books</th><td className="amount"><strong>{plain(r.book_balance)}</strong></td></tr>
            <ReportGroup title="Less: money received in the books, not yet shown by the bank" total={r.uncleared_in.total} sign="-"
              rows={r.uncleared_in.lines.map((l) => [l.id, `${fmtDate(l.date)} · ${l.reference} ${l.narration}`, l.amount])} />
            <ReportGroup title="Add: payments made in the books, not yet through the bank" total={r.uncleared_out.total} sign="+"
              rows={r.uncleared_out.lines.map((l) => [l.id, `${fmtDate(l.date)} · ${l.reference} ${l.narration}`, -num(l.amount)])} />
            <ReportGroup title="Add: money in on the statement, not in the books" total={r.bank_in.total} sign="+"
              rows={r.bank_in.lines.map((l) => [l.id, `${fmtDate(l.date)} · ${l.description}${l.status === 'ignored' ? ' (ignored)' : ''}`, l.amount])} />
            <ReportGroup title="Less: money out on the statement, not in the books" total={r.bank_out.total} sign="-"
              rows={r.bank_out.lines.map((l) => [l.id, `${fmtDate(l.date)} · ${l.description}${l.status === 'ignored' ? ' (ignored)' : ''}`, -num(l.amount)])} />
            <tr className="total-row"><th scope="row">Balance the bank should show</th><td className="amount"><strong>{plain(r.expected_bank_balance)}</strong></td></tr>
            <tr><th scope="row">Balance as per bank statement</th><td className="amount"><strong>{r.statement_balance != null ? plain(r.statement_balance) : '—'}</strong></td></tr>
            <tr className={r.difference != null && num(r.difference) !== 0 ? 'bad-text' : ''}><th scope="row">Difference</th><td className="amount"><strong>{r.difference != null ? plain(r.difference) : '—'}</strong></td></tr>
          </tbody>
        </table></div>
      </section>

      <section className="card no-print" aria-label="Reconciled up to">
        <h2>Mark reconciled</h2>
        <Problem error={error} />
        {r.can_close ? <Notice type="success">Everything up to {fmtDate(r.as_of)} is matched or ignored and the statement agrees with the books.</Notice> : (
          <ul className="problem-list">{r.blockers.map((b) => <li key={b}>{b}</li>)}</ul>
        )}
        <div className="toolbar">
          <Field id="br-bal" label="Closing balance on the statement" hint={r.statement_balance_source === 'statement' ? 'Taken from the imported statement. Type a figure to use a different one.' : 'Type the closing balance printed on the statement (negative if overdrawn).'}>
            <input id="br-bal" type="number" step="0.01" value={balance} onChange={(e) => setBalance(e.target.value)} />
          </Field>
          <button type="button" className="btn" onClick={() => setEntered(balance)}>Use this balance</button>
          {manage && <button type="button" className="btn btn-primary" disabled={busy || !r.can_close} onClick={close}>Mark reconciled up to {fmtDate(r.as_of)}</button>}
        </div>
      </section>

      <section className="card" aria-label="Unmatched statement lines">
        <h2>Statement lines not in the books ({r.bank_in.lines.length + r.bank_out.lines.length})</h2>
        {r.bank_in.lines.length + r.bank_out.lines.length === 0 ? <p className="muted">None.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Date</th><th>Description</th><th>State</th><th className="amount">Money in</th><th className="amount">Money out</th></tr></thead>
            <tbody>{[...r.bank_in.lines, ...r.bank_out.lines].sort((a, b) => (a.date < b.date ? -1 : 1)).map((l) => (
              <tr key={l.id}><td data-label="Date">{fmtDate(l.date)}</td><td data-label="Description">{l.description}</td>
                <td data-label="State">{l.status === 'ignored' ? <Badge>Ignored: {l.ignore_reason}</Badge> : l.status === 'matched' ? <Badge tone="info">Entry dated later</Badge> : <Badge tone="warn">To match</Badge>}</td>
                <InOut amount={l.amount} /></tr>
            ))}</tbody>
          </table></div>
        )}
      </section>

      <section className="card" aria-label="Unmatched book entries">
        <h2>Entries in the books not yet shown by the bank ({r.uncleared_in.lines.length + r.uncleared_out.lines.length})</h2>
        {manage && firstDate && <p className="hint">Entries from before the first imported statement ({fmtDate(firstDate)}) that already went through the bank can be ticked and marked as cleared.</p>}
        {r.uncleared_in.lines.length + r.uncleared_out.lines.length === 0 ? <p className="muted">None.</p> : (
          <div className="table-wrap"><table>
            <thead><tr>{manage && <th><span className="sr-only">Tick</span></th>}<th>Date</th><th>Entry</th><th className="amount">Money in</th><th className="amount">Money out</th></tr></thead>
            <tbody>{[...r.uncleared_in.lines, ...r.uncleared_out.lines].sort((a, b) => (a.date < b.date ? -1 : 1)).map((l) => (
              <tr key={l.id}>
                {manage && <td>{firstDate && l.date < firstDate && <input type="checkbox" checked={picked.has(l.id)} onChange={() => toggle(l.id)} aria-label={`Already cleared: ${l.reference} ${l.narration}`} />}</td>}
                <td data-label="Date">{fmtDate(l.date)}</td><td data-label="Entry">{l.source_label} <code>{l.reference}</code> {l.narration}</td>
                <InOut amount={l.amount} />
              </tr>
            ))}</tbody>
          </table></div>
        )}
        {manage && picked.size > 0 && <div className="actions"><button type="button" className="btn" disabled={busy} onClick={markCleared}>Mark {picked.size} as already cleared</button></div>}
      </section>

      <section className="card no-print" aria-label="Earlier reconciliations">
        <h2>Reconciled up to</h2>
        {r.history.length === 0 ? <p className="muted">Not reconciled yet.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Date</th><th className="amount">Statement balance</th><th>Notes</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{r.history.map((h, i) => (
              <tr key={h.id}><td data-label="Date">{fmtDate(h.statement_date)}</td><td className="amount" data-label="Statement balance">{plain(h.statement_balance)}</td>
                <td data-label="Notes">{h.notes}</td>
                <td className="row-actions">{manage && i === 0 && <button type="button" className="btn btn-small" onClick={() => undo(h)}>Undo</button>}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </section>
      {dialog}
    </>
  );
}

function ReportGroup({ title, rows, total, sign }) {
  return (
    <>
      <tr className="group-row"><th scope="rowgroup" colSpan={2}>{title}</th></tr>
      {rows.length === 0 ? <tr><td className="muted indent">None</td><td className="amount">0.00</td></tr> : rows.map(([key, text, amount]) => (
        <tr key={key}><td className="indent" data-label="Item">{text}</td><td className="amount" data-label="Amount">{plain(Math.abs(num(amount)))}</td></tr>
      ))}
      <tr><th scope="row" className="indent">Total</th><td className="amount">{sign === '-' ? '(−) ' : '(+) '}{plain(total)}</td></tr>
    </>
  );
}
