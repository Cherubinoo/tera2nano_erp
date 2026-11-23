import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { money, num, plain, today } from '../../format';
import { asList, useLoad } from '../../hooks';
import { ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, fmtDate, useConfirm, useToast } from '../../ui';

const TYPES = [['asset', 'Assets'], ['liability', 'Liabilities'], ['equity', 'Equity']];
const cents = (v) => Math.round(num(v) * 100);

// Balances brought forward when the books start, one row per balance-sheet account, as at a date. The entry is posted once; the
// difference between the two sides goes to "Opening balance adjustments", so the books always balance.
export default function OpeningBalancesPage() {
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const accounts = useLoad(() => api.get('/accounting/accounts/', { active: 'true' }), []);
  const state = useLoad(() => api.get('/accounting/opening-balances/'), []);
  const [date, setDate] = useState(today());
  const [values, setValues] = useState({}); // account id -> { debit, credit }
  const [filter, setFilter] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);

  const list = useMemo(() => asList(accounts.data).filter((a) => ['asset', 'liability', 'equity'].includes(a.type) && a.system_key !== 'opening_balance_equity'), [accounts.data]);
  const shown = list.filter((a) => !filter.trim() || `${a.code} ${a.name}`.toLowerCase().includes(filter.trim().toLowerCase()));
  const get = (id) => values[id] || { debit: '', credit: '' };
  const set = (id, side) => (e) => {
    const v = e.target.value;
    // A balance is on one side only: typing on one side clears the other.
    setValues((x) => ({ ...x, [id]: { debit: side === 'debit' ? v : '', credit: side === 'credit' ? v : '' } }));
  };

  const dr = useMemo(() => Object.values(values).reduce((a, r) => a + cents(r.debit), 0), [values]);
  const cr = useMemo(() => Object.values(values).reduce((a, r) => a + cents(r.credit), 0), [values]);
  const diff = dr - cr;
  const rowsUsed = Object.entries(values).filter(([, r]) => cents(r.debit) > 0 || cents(r.credit) > 0);
  const posted = done || state.data?.entry;

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    if (!date) { setError(new Error('Choose the date the balances are as at.')); return; }
    if (!rowsUsed.length) { setError(new Error('Enter at least one balance.')); return; }
    const ok = await confirm({
      title: 'Post the opening balances?',
      body: (
        <>
          <p>{rowsUsed.length} balance{rowsUsed.length === 1 ? '' : 's'} as at <strong>{fmtDate(date)}</strong>: debits {plain(dr / 100)}, credits {plain(cr / 100)}.</p>
          {diff !== 0 && <p>The difference of <strong>{plain(Math.abs(diff) / 100)}</strong> is posted to Opening balance adjustments so the books balance.</p>}
          <p>Opening balances are entered once. To change them later, reverse the entry in the Journal and enter them again.</p>
        </>
      ),
      confirmLabel: 'Post opening balances',
    });
    if (!ok) return;
    setBusy(true);
    try {
      const r = await api.post('/accounting/opening-balances/', { date, rows: rowsUsed.map(([account, r2]) => ({ account, debit: r2.debit || 0, credit: r2.credit || 0 })) });
      notify('Opening balances posted.');
      setDone(r);
      state.reload();
    } catch (err) { setError(err); }
    setBusy(false);
  };

  return (
    <>
      <PageHeader title="Opening balances" description="The balances you already have when you start using the books: cash, bank, what customers owe you, what you owe, capital. Enter each as at one date." />
      {(accounts.loading || state.loading) && <Loading />}
      <ErrorBox error={accounts.error || state.error} onRetry={() => { accounts.reload(); state.reload(); }} />
      {posted && (
        <Notice type="success">
          <p><strong>Opening balances are in the books</strong> as at {fmtDate(posted.date)} (<Link to={`/accounting/journal/${posted.id}`}>{posted.reference || 'view the entry'}</Link>, total {money(posted.amount)}).
            They are entered once. To change them, reverse that entry in the Journal; this form opens again once it is reversed.</p>
        </Notice>
      )}
      {!posted && !state.loading && !accounts.loading && (
        <form className="card" onSubmit={submit} noValidate>
          <FormErrors error={error} fields={[]} />
          <div className="form-grid">
            <Field id="ob-date" label="Balances as at" required hint="Usually the day before your first transaction, for example 31 March when the year starts on 1 April.">
              <input id="ob-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </Field>
            <Field id="ob-filter" label="Find an account"><input id="ob-filter" value={filter} placeholder="Code or name" onChange={(e) => setFilter(e.target.value)} /></Field>
          </div>
          <p className="hint">Assets normally have a debit balance; liabilities and capital a credit balance. Leave an account empty when it starts at zero.</p>
          {TYPES.map(([type, label]) => {
            const rows = shown.filter((a) => a.type === type);
            if (!rows.length) return null;
            return (
              <div className="table-wrap" key={type}>
                <table className="items-table alloc-table">
                  <caption>{label}</caption>
                  <thead><tr><th>Account</th><th className="amount">Debit</th><th className="amount">Credit</th></tr></thead>
                  <tbody>
                    {rows.map((a) => (
                      <tr key={a.id}>
                        <td data-label="Account"><code>{a.code}</code> {a.name}<div className="hint">{a.subtype_label}</div></td>
                        <td className="amount" data-label="Debit"><input type="number" min="0" step="0.01" aria-label={`Debit ${a.code} ${a.name}`} value={get(a.id).debit} onChange={set(a.id, 'debit')} /></td>
                        <td className="amount" data-label="Credit"><input type="number" min="0" step="0.01" aria-label={`Credit ${a.code} ${a.name}`} value={get(a.id).credit} onChange={set(a.id, 'credit')} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })}
          <div className="table-wrap">
            <table>
              <caption>Totals</caption>
              <tbody>
                <tr><th scope="row">Total debit</th><td className="amount" data-label="Total debit"><strong id="ob-total-debit">{plain(dr / 100)}</strong></td></tr>
                <tr><th scope="row">Total credit</th><td className="amount" data-label="Total credit"><strong id="ob-total-credit">{plain(cr / 100)}</strong></td></tr>
                <tr><th scope="row">Difference</th><td className="amount" data-label="Difference"><strong id="ob-diff">{plain(Math.abs(diff) / 100)}</strong></td></tr>
              </tbody>
            </table>
          </div>
          <div role="status" aria-live="polite">
            {dr === 0 && cr === 0 ? <p className="hint">Enter the balances; the totals appear here.</p>
              : diff === 0 ? <p className="hint">Debits and credits are equal. The balances are complete.</p>
                : <Notice type="warn">Debits and credits do not match: {diff > 0 ? 'credits are short by' : 'debits are short by'} {plain(Math.abs(diff) / 100)}.
                  If you post now, this amount goes to Opening balance adjustments so the books still balance. Check the figures first if you did not expect a difference.</Notice>}
          </div>
          <div className="actions"><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Posting…' : 'Post opening balances'}</button></div>
        </form>
      )}
      {dialog}
    </>
  );
}
