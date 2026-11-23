import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { downloadCsv, money, num, plain, today } from '../../format';
import { asList, useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, fmtDate, useConfirm, useToast } from '../../ui';
import { PeriodBar, SOURCE_LABEL } from './common';

const TYPES = [['asset', 'Assets'], ['liability', 'Liabilities'], ['equity', 'Equity'], ['income', 'Income'], ['expense', 'Expenses']];
const SUBTYPES = [
  ['asset', [['cash', 'Cash'], ['bank', 'Bank'], ['receivable', 'Accounts receivable'], ['other_current_asset', 'Other current asset'], ['tax_asset', 'Tax receivable (GST input, TDS)'], ['fixed_asset', 'Fixed asset'], ['accumulated_depreciation', 'Accumulated depreciation']]],
  ['liability', [['payable', 'Accounts payable'], ['tax_liability', 'Tax payable (GST output, TDS)'], ['other_current_liability', 'Other current liability'], ['long_term_liability', 'Long-term liability']]],
  ['equity', [['partner_capital', "Partners' capital"], ['partner_current', "Partners' current account"], ['retained', 'Retained earnings']]],
  ['income', [['income', 'Sales / operating income'], ['other_income', 'Other income']]],
  ['expense', [['direct_cost', 'Direct cost of services'], ['expense', 'Operating expense'], ['depreciation', 'Depreciation'], ['other_expense', 'Other expense']]],
];

function AccountDialog({ record, onClose, onSaved }) {
  const notify = useToast();
  const [v, setV] = useState({ code: record?.code || '', name: record?.name || '', subtype: record?.subtype || 'expense', description: record?.description || '' });
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const locked = !!record && (record.is_system || num(record.balance) !== 0);
  const set = (k) => (e) => setV((x) => ({ ...x, [k]: e.target.value }));
  const submit = async (e) => {
    e.preventDefault(); e.stopPropagation();
    setBusy(true); setError(null);
    try {
      const body = record ? { code: v.code, name: v.name, description: v.description, ...(locked ? {} : { subtype: v.subtype }) } : v;
      await (record ? api.patch(`/accounting/accounts/${record.id}/`, body) : api.post('/accounting/accounts/', body));
      notify(record ? 'Account updated.' : 'Account added.');
      onSaved();
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title={record ? `Edit ${record.name}` : 'New account'} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[]} />
        <div className="form-grid">
          <Field id="ac-code" label="Account code" required error={errors.code} hint="Letters and digits, for example 5250."><input id="ac-code" value={v.code} maxLength={10} onChange={set('code')} /></Field>
          <Field id="ac-name" label="Account name" required error={errors.name}><input id="ac-name" value={v.name} maxLength={150} onChange={set('name')} /></Field>
          <Field id="ac-sub" label="Kind of account" required error={errors.subtype} wide hint={locked ? 'An account with entries, or one the system posts to, keeps its kind.' : undefined}>
            <select id="ac-sub" value={v.subtype} disabled={locked} onChange={set('subtype')}>
              {SUBTYPES.map(([type, list]) => <optgroup key={type} label={TYPES.find((t) => t[0] === type)[1]}>{list.map(([val, label]) => <option key={val} value={val}>{label}</option>)}</optgroup>)}
            </select>
          </Field>
          <Field id="ac-desc" label="Description" wide error={errors.description}><input id="ac-desc" value={v.description} maxLength={300} onChange={set('description')} /></Field>
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export function ChartPage() {
  const { user } = useAuth();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { data, error, loading, reload } = useLoad(() => api.get('/accounting/accounts/'), []);
  const [editing, setEditing] = useState(null);
  const [showOff, setShowOff] = useState(false);
  const manage = user.permissions.includes('accounting.manage');
  const accounts = asList(data);
  const groups = useMemo(() => TYPES.map(([type, label]) => ({ type, label, rows: accounts.filter((a) => a.type === type && (showOff || a.is_active)) })), [accounts, showOff]);

  const setup = async () => {
    if (!(await confirm({ title: 'Set up the books?', body: <p>Missing standard accounts are created, and any invoice, credit note or payment made before the books existed is posted. Anything already posted is left alone.</p>, confirmLabel: 'Set up' }))) return;
    try { const r = await api.post('/accounting/accounts/setup/'); notify(`${r.accounts_created} account(s) created, ${r.documents} document(s) and ${r.payments} payment(s) posted.`); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const toggle = async (a) => {
    try { await api.patch(`/accounting/accounts/${a.id}/`, { is_active: !a.is_active }); reload(); } catch (e) { notify(e.fieldErrors?.is_active || e.message, 'error'); }
  };

  return (
    <>
      <PageHeader title="Chart of accounts" description="Every account the books use. Accounts the system posts to cannot be removed."
        actions={manage && (<>
          <button type="button" className="btn btn-primary" onClick={() => setEditing({})}>New account</button>
          <Link className="btn" to="/accounting/opening-balances">Opening balances</Link>
          <button type="button" className="btn" onClick={setup}>Set up the books</button>
        </>)} />
      <label className="checkbox-inline"><input type="checkbox" checked={showOff} onChange={(e) => setShowOff(e.target.checked)} /> Show switched-off accounts</label>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {groups.map((g) => g.rows.length > 0 && (
        <section className="card" key={g.type} aria-label={g.label}>
          <h2>{g.label}</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>Code</th><th>Account</th><th>Kind</th><th className="amount">Balance</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {g.rows.map((a) => (
                <tr key={a.id} className={a.is_active ? '' : 'row-inactive'}>
                  <td data-label="Code"><code>{a.code}</code></td>
                  <td data-label="Account"><Link to={`/accounting/accounts/${a.id}`}><strong>{a.name}</strong></Link> {a.is_system && <Badge tone="info">System</Badge>} {!a.is_active && <Badge>Switched off</Badge>}</td>
                  <td data-label="Kind">{a.subtype_label}</td>
                  <td className="amount" data-label="Balance">{num(a.balance) !== 0 ? money(a.balance) : '—'}</td>
                  <td className="row-actions">{manage && <><button type="button" className="btn btn-small" onClick={() => setEditing(a)}>Edit</button>{!a.is_system && <button type="button" className="btn btn-small" onClick={() => toggle(a)}>{a.is_active ? 'Switch off' : 'Switch on'}</button>}</>}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </section>
      ))}
      {editing && <AccountDialog record={editing.id ? editing : null} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />}
      {confirmDialog}
    </>
  );
}

export function AccountLedgerPage() {
  const { id } = useParams();
  const [range, setRange] = useState(null);
  const { data, error, loading, reload } = useLoad(() => api.get(`/accounting/accounts/${id}/ledger/`, range || undefined), [id, range?.from, range?.to]);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const l = data;
  const exportCsv = () => downloadCsv(`ledger-${l.account.code}.csv`, [
    ['Date', 'Reference', 'Description', 'Debit', 'Credit', 'Balance'],
    ['', '', 'Opening balance', '', '', l.opening],
    ...l.rows.map((r) => [r.date, r.reference, r.memo || r.narration, r.debit, r.credit, r.balance]),
    ['', '', 'Closing balance', l.total_debit, l.total_credit, l.closing]]);
  return (
    <>
      <Link to="/accounting/accounts" className="back-link">← Chart of accounts</Link>
      <PageHeader title={`${l.account.code} ${l.account.name}`} description={`General ledger from ${fmtDate(l.start)} to ${fmtDate(l.end)}. Balances run the ${l.account.normal} way.`}
        actions={<><button type="button" className="btn" onClick={exportCsv}>Export CSV</button><button type="button" className="btn" onClick={() => window.print()}>Print</button></>} />
      <PeriodBar from={l.start} to={l.end} onApply={setRange} />
      <section className="card">
        <div className="table-wrap"><table>
          <thead><tr><th>Date</th><th>Reference</th><th>Description</th><th className="amount">Debit</th><th className="amount">Credit</th><th className="amount">Balance</th></tr></thead>
          <tbody>
            <tr><td colSpan={5}><em>Opening balance</em></td><td className="amount"><strong>{plain(l.opening)}</strong></td></tr>
            {l.rows.map((r) => (
              <tr key={r.id}>
                <td data-label="Date">{fmtDate(r.date)}</td>
                <td data-label="Reference"><Link to={`/accounting/journal/${r.entry_id}`}><code>{r.reference || SOURCE_LABEL[r.source]}</code></Link></td>
                <td data-label="Description">{r.memo || r.narration}{r.reconciled && <> <Badge tone="good">Reconciled</Badge></>}</td>
                <td className="amount" data-label="Debit">{num(r.debit) ? plain(r.debit) : ''}</td>
                <td className="amount" data-label="Credit">{num(r.credit) ? plain(r.credit) : ''}</td>
                <td className="amount" data-label="Balance">{plain(r.balance)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot><tr><th colSpan={3} scope="row">Totals and closing balance</th><td className="amount"><strong>{plain(l.total_debit)}</strong></td><td className="amount"><strong>{plain(l.total_credit)}</strong></td><td className="amount"><strong>{plain(l.closing)}</strong></td></tr></tfoot>
        </table></div>
      </section>
    </>
  );
}
