import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { ProjectField, projectLabel } from '../sales/common';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, Tabs, fmtDate, useReasonDialog, useToast } from '../../ui';

export const VOUCHER_TONE = { draft: 'neutral', pending_approval: 'warn', recorded: 'good', void: 'neutral' };
export const VoucherStatus = ({ v }) => <Badge tone={VOUCHER_TONE[v.status] || 'neutral'}>{v.status_label}</Badge>;
const EXPENSE_STATUSES = [['', 'All'], ['draft', 'Draft'], ['pending_approval', 'Pending approval'], ['recorded', 'Recorded'], ['void', 'Void']];
const taxModeOf = (v) => (!v.gst_split || !v.gst_split.length ? 'none' : v.gst_split.some((g) => g.account.startsWith('gst_igst')) ? 'inter' : 'intra');

const r2 = (n) => Math.round(n * 100) / 100;

// `voucher`: change a draft expense instead of recording a new one.
export function VoucherDialog({ kind, options, voucher, onClose, onSaved }) {
  const notify = useToast();
  const isExpense = kind === 'expense';
  const [v, setV] = useState(voucher ? {
    date: voucher.date, category: voucher.category.id, money_account: voucher.money_account.id, amount: String(voucher.amount),
    tax_mode: taxModeOf(voucher), tax_rate: taxModeOf(voucher) === 'none' ? '' : String(num(voucher.tax_rate)), party_name: voucher.party_name,
    reference: voucher.reference, notes: voucher.notes, project: voucher.project || '',
  } : { date: today(), category: '', money_account: options.money_accounts[0]?.id || '', amount: '', tax_mode: 'none', tax_rate: '', party_name: '', reference: '', notes: '', project: '' });
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const accounts = isExpense ? options.expense_accounts : options.income_accounts;
  const net = num(v.amount);
  const rate = v.tax_mode === 'none' ? 0 : num(v.tax_rate);
  const tax = v.tax_mode === 'intra' ? r2(r2((net * rate) / 200) * 2) : v.tax_mode === 'inter' ? r2((net * rate) / 100) : 0;
  // A warning, never a block: the expense would take this account past its line in the active budget.
  const [budgetWarning, setBudgetWarning] = useState(null);
  useEffect(() => {
    if (!isExpense || !v.category || !(net > 0)) { setBudgetWarning(null); return undefined; }
    const t = setTimeout(async () => {
      try { const r = await api.get('/accounting/budgets/check/', { account: v.category, amount: v.amount, date: v.date }); setBudgetWarning(r.warning); } catch { setBudgetWarning(null); }
    }, 400);
    return () => clearTimeout(t);
  }, [isExpense, v.category, v.amount, v.date]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async (e, asDraft = false) => {
    e.preventDefault(); e.stopPropagation();
    setError(null);
    const errs = {};
    if (!v.category) errs.category = `Choose the ${isExpense ? 'expense' : 'income'} account.`;
    if (!v.money_account) errs.money_account = 'Choose the cash or bank account.';
    if (!(net > 0)) errs.amount = 'Enter the amount.';
    if (v.tax_mode !== 'none' && !v.tax_rate) errs.tax_rate = 'Choose the GST rate.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const body = { ...v, tax_rate: v.tax_mode === 'none' ? 0 : v.tax_rate, project: v.project || null };
      if (voucher) {
        await api.patch(`/accounting/vouchers/${voucher.id}/`, body);
        notify('Draft expense saved.');
        onSaved();
      } else {
        const saved = await api.post('/accounting/vouchers/', { ...body, kind, record: !asDraft });
        notify(asDraft ? 'Expense saved as a draft.' : saved.status === 'pending_approval'
          ? 'Expense sent for approval. It is posted to the books once approved.' : `${isExpense ? 'Expense' : 'Income'} recorded: ${saved.number}`);
        if (saved.budget_warning) notify(saved.budget_warning.message, 'info');
        onSaved(saved);
      }
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };

  return (
    <Dialog title={voucher ? 'Edit draft expense' : isExpense ? 'Record an expense' : 'Record other income'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[]} />
        <div className="form-grid">
          <Field id="v-date" label="Date" required error={errors.date}><input id="v-date" type="date" value={v.date} onChange={set('date')} /></Field>
          <Field id="v-cat" label={isExpense ? 'Expense account' : 'Income account'} required error={errors.category}>
            <select id="v-cat" value={v.category} onChange={set('category')}><option value="">Choose…</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</select>
          </Field>
          <Field id="v-party" label={isExpense ? 'Paid to' : 'Received from'} error={errors.party_name}><input id="v-party" value={v.party_name} maxLength={200} onChange={set('party_name')} /></Field>
          <Field id="v-money" label={isExpense ? 'Paid from' : 'Received in'} required error={errors.money_account}>
            <select id="v-money" value={v.money_account} onChange={set('money_account')}>{options.money_accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
          </Field>
          <Field id="v-amount" label="Amount (before GST)" required error={errors.amount}><input id="v-amount" type="number" min="0" step="0.01" value={v.amount} onChange={set('amount')} /></Field>
          {options.gst_applied && (
            <>
              <Field id="v-tax" label="GST" error={errors.tax_mode} hint={isExpense ? 'Input tax credit you can claim.' : 'Output tax you owe.'}>
                <select id="v-tax" value={v.tax_mode} onChange={set('tax_mode')}><option value="none">No GST</option><option value="intra">Same state (CGST + SGST)</option><option value="inter">Other state (IGST)</option></select>
              </Field>
              {v.tax_mode !== 'none' && (
                <Field id="v-rate" label="GST rate" error={errors.tax_rate}>
                  <select id="v-rate" value={v.tax_rate} onChange={set('tax_rate')}><option value="">Choose…</option>{options.gst_rates.map((r) => <option key={r} value={r}>{num(r)}%</option>)}</select>
                </Field>
              )}
            </>
          )}
          <Field id="v-ref" label={isExpense ? 'Bill or receipt number' : 'Receipt or UTR number'} error={errors.reference}><input id="v-ref" value={v.reference} maxLength={100} onChange={set('reference')} /></Field>
          <ProjectField id="v-project" value={v.project} onChange={(p) => setV((x) => ({ ...x, project: p }))} current={voucher?.project_ref} error={errors.project} />
          <Field id="v-notes" label="Notes" wide error={errors.notes}><input id="v-notes" value={v.notes} maxLength={300} onChange={set('notes')} /></Field>
        </div>
        <p className="hint" role="status">{net > 0 ? `Total ${plain(net + tax)}${tax ? ` (GST ${plain(tax)})` : ''}` : 'Enter the amount.'}{!options.gst_applied ? ' · GST is not switched on for the company, so none can be recorded.' : ''}</p>
        {budgetWarning && <div role="status"><Notice type="warn">{budgetWarning.message} You can still save it.</Notice></div>}
        <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Cancel</button>
          {isExpense && !voucher && <button type="button" className="btn" disabled={busy} onClick={(e) => submit(e, true)}>Keep as draft</button>}
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : voucher ? 'Save changes' : 'Save'}</button></div>
      </form>
    </Dialog>
  );
}

export default function VouchersPage() {
  const { user } = useAuth();
  const notify = useToast();
  const { ask, dialog: askDialog } = useReasonDialog();
  const [params, setParams] = useSearchParams();
  const [kind, setKind] = useState(params.get('new') === 'income' ? 'income' : 'expense');
  const [dialog, setDialog] = useState(!!params.get('new'));
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const [status, setStatus] = useState('');
  const options = useLoad(() => api.get('/accounting/vouchers/options/'), []);
  const { data, error, loading, reload } = useLoad(() => api.get('/accounting/vouchers/', { kind, q: term || undefined, status: status || undefined }), [kind, term, status]);
  const manage = user.permissions.includes('accounting.manage');
  useEffect(() => { if (params.get('new')) setParams({}, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const voidIt = async (v) => {
    const reason = await ask({ title: `Void ${v.number}?`, message: 'The entry it made in the books is reversed. The record is kept.', confirmLabel: 'Void' });
    if (reason === null) return;
    try { await api.post(`/accounting/vouchers/${v.id}/void/`, { change_reason: reason }); notify('Voided.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };

  return (
    <>
      <PageHeader title="Expenses and income" description="Money paid out and received outside invoices. Each one posts to the books by itself; an expense above an approval limit waits for approval first."
        actions={manage && options.data && <button type="button" className="btn btn-primary" onClick={() => setDialog(true)}>{kind === 'expense' ? 'Record expense' : 'Record income'}</button>} />
      <Tabs tabs={[{ key: 'expense', label: 'Expenses' }, { key: 'income', label: 'Other income' }]} value={kind} onChange={(k) => { setKind(k); setStatus(''); }} />
      <section className="card">
        {kind === 'expense' && (
          <div className="chips" role="group" aria-label="Filter by status">
            {EXPENSE_STATUSES.map(([k, l]) => (
              <button key={k} type="button" className="chip" aria-pressed={status === k} onClick={() => setStatus(k)}>
                {l}{data?.status_counts && k && data.status_counts[k] !== undefined ? ` (${data.status_counts[k]})` : ''}
              </button>
            ))}
          </div>
        )}
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
          <Field id="vo-q" label="Search" className="grow"><input id="vo-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Number, name, reference or notes" /></Field>
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && data?.results.length === 0 && <p className="empty">Nothing recorded yet.</p>}
        {data?.results.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Date</th><th>Number</th><th>{kind === 'expense' ? 'Paid to' : 'Received from'}</th><th>Account</th><th>{kind === 'expense' ? 'Paid from' : 'Received in'}</th><th className="amount">Amount</th><th className="amount">GST</th><th className="amount">Total</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {data.results.map((v) => (
                <tr key={v.id} className={v.status === 'void' ? 'row-inactive' : ''}>
                  <td data-label="Date">{fmtDate(v.date)}</td>
                  <td data-label="Number"><Link to={`/accounting/vouchers/${v.id}`}>{v.number ? <code>{v.number}</code> : <em>No number yet</em>}</Link> {v.status !== 'recorded' && <VoucherStatus v={v} />}</td>
                  <td data-label="Name">{v.party_name || '—'}{v.reference && <div className="hint">{v.reference}</div>}</td>
                  <td data-label="Account">{v.category.name}{v.project_ref && <div className="hint">Project {projectLabel(v.project_ref)}</div>}</td><td data-label="Money account">{v.money_account.name}</td>
                  <td className="amount" data-label="Amount">{plain(v.amount)}</td><td className="amount" data-label="GST">{num(v.tax_amount) ? plain(v.tax_amount) : '—'}</td>
                  <td className="amount" data-label="Total"><strong>{plain(v.total)}</strong></td>
                  <td className="row-actions"><Link className="btn btn-small" to={`/accounting/vouchers/${v.id}`}>Open</Link> {manage && v.status === 'recorded' && <button type="button" className="btn btn-small" onClick={() => voidIt(v)}>Void</button>}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
        {data?.results.length > 0 && <div className="list-foot"><span>{data.count} entr{data.count === 1 ? 'y' : 'ies'}</span><span>Recorded total <strong>{money(data.total)}</strong></span></div>}
      </section>
      {dialog && options.data && <VoucherDialog kind={kind} options={options.data} onClose={() => setDialog(false)} onSaved={() => { setDialog(false); reload(); }} />}
      {askDialog}
    </>
  );
}
