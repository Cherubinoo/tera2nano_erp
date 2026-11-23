import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { downloadCsv, money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Dialog, ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, fmtDate, useReasonDialog, useToast } from '../../ui';
import { ApprovalPanel, ApprovalStatus } from '../approvals/ApprovalBadge';
import { PartnerLink, PartnerSelect, StatusBadge, usePartnerLookups } from './common';

const WITHDRAWALS = ['drawing', 'capital_withdrawn'];

const HELP = {
  capital_introduced: 'Money the partner put into the LLP. Debit bank or cash, credit partners\' capital.',
  capital_withdrawn: 'Capital taken back out. It cannot be more than the partner\'s capital.',
  drawing: 'Money the partner took for personal use. Debit the partner\'s current account, credit bank or cash.',
  interest_on_capital: 'Interest the LLP agreement allows on capital. Debit interest expense, credit the partner\'s current account.',
  remuneration: 'Salary or remuneration to the partner. Debit remuneration expense, credit the current account.',
  loan_from_partner: 'A loan to the LLP, not capital. It is repaid, and can earn interest.',
  loan_repayment: 'Repays a loan from the partner. It cannot be more than what is outstanding.',
  profit_share_credit: 'A share of profit credited by hand. Normally use Profit Sharing to allocate for a whole period.',
  profit_share_transfer: 'Moves part of the current account into capital.',
};

// Record a partner transaction. `preset`: {partner, kind}. Large withdrawals wait for approval (the server decides).
export function TransactionDialog({ preset = {}, onClose, onSaved }) {
  const notify = useToast();
  const { data: lk } = usePartnerLookups();
  const [v, setV] = useState({ partner: preset.partner || '', kind: preset.kind || 'capital_introduced', date: today(), amount: '', mode: 'bank_transfer',
    money_account: '', reference: '', narration: '', rate: '', period_from: '', period_to: '' });
  const [errors, setErrors] = useState({});
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [calc, setCalc] = useState(null);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  if (!lk) return <Dialog title="Record partner transaction" onClose={onClose}><Loading /></Dialog>;
  const kind = lk.kinds.find((k) => k.value === v.kind);
  const threshold = num(lk.settings.withdrawal_approval_threshold);
  const waits = WITHDRAWALS.includes(v.kind) && threshold > 0 && num(v.amount) > threshold;
  const isInterest = v.kind === 'interest_on_capital';
  const isLoan = v.kind === 'loan_from_partner';

  const work = async () => {
    setCalc(null);
    const errs = {};
    if (!v.partner) errs.partner = 'Choose the partner.';
    if (!v.period_from) errs.period_from = 'Enter the start of the period.';
    if (!v.period_to) errs.period_to = 'Enter the end of the period.';
    if (v.rate === '') errs.rate = 'Enter the rate.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    try {
      const r = await api.get('/partners/interest-preview/', { partner: v.partner, rate: v.rate, from: v.period_from, to: v.period_to });
      setCalc(r);
      setV((x) => ({ ...x, amount: r.interest }));
    } catch (e) { setErrors(e.fieldErrors || {}); setError(e); }
  };

  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setError(null);
    const errs = {};
    if (!v.partner) errs.partner = 'Choose the partner.';
    if (!v.date) errs.date = 'Choose the date.';
    if (!(num(v.amount) > 0)) errs.amount = 'Enter the amount.';
    if (v.rate !== '' && (num(v.rate) < 0 || num(v.rate) > 100)) errs.rate = 'The rate must be between 0 and 100.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const body = { ...v };
      if (!kind.money) { delete body.mode; delete body.money_account; }
      if (!isInterest && !isLoan) { delete body.rate; }
      if (!isInterest) { delete body.period_from; delete body.period_to; }
      Object.keys(body).forEach((k) => { if (body[k] === '') delete body[k]; });
      const t = await api.post('/partners/transactions/', body);
      notify(t.status === 'pending_approval' ? `${t.number} recorded and waiting for approval.` : `${t.number} recorded.`);
      onSaved(t);
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };

  return (
    <Dialog title="Record partner transaction" onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[]} />
        <div className="form-grid">
          <PartnerSelect id="tx-partner" value={v.partner} onChange={(x) => { setV((s) => ({ ...s, partner: x })); setErrors((e) => ({ ...e, partner: undefined })); }}
            partners={lk.partners} blank="Choose…" required error={errors.partner} />
          <Field id="tx-kind" label="Type" required error={errors.kind} hint={HELP[v.kind]}>
            <select id="tx-kind" value={v.kind} onChange={set('kind')}>{lk.kinds.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}</select>
          </Field>
          <Field id="tx-date" label="Date" required error={errors.date}><input id="tx-date" type="date" value={v.date} onChange={set('date')} /></Field>
          <Field id="tx-amount" label="Amount" required error={errors.amount}>
            <input id="tx-amount" type="number" min="0" step="0.01" value={v.amount} onChange={set('amount')} />
          </Field>
          {kind.money && (
            <>
              <Field id="tx-mode" label="Paid by" error={errors.mode}>
                <select id="tx-mode" value={v.mode} onChange={set('mode')}>{lk.modes.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}</select>
              </Field>
              <Field id="tx-acct" label={v.kind === 'capital_introduced' || v.kind === 'loan_from_partner' ? 'Received in' : 'Paid from'} error={errors.money_account}
                hint="Leave as default to use the main bank account (or cash when paid by cash).">
                <select id="tx-acct" value={v.money_account} onChange={set('money_account')}>
                  <option value="">Default</option>
                  {lk.money_accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </Field>
            </>
          )}
          {(isInterest || isLoan) && (
            <Field id="tx-rate" label="Rate (% a year)" error={errors.rate} hint={isLoan ? 'Optional. Used for the interest estimate on the Loans page.' : 'Annual simple interest.'}>
              <input id="tx-rate" type="number" min="0" max="100" step="0.01" value={v.rate} onChange={set('rate')} />
            </Field>
          )}
          {isInterest && (
            <>
              <Field id="tx-from" label="Interest period from" error={errors.period_from}><input id="tx-from" type="date" value={v.period_from} onChange={set('period_from')} /></Field>
              <Field id="tx-to" label="Interest period to" error={errors.period_to}><input id="tx-to" type="date" value={v.period_to} onChange={set('period_to')} /></Field>
              <div className="field field-wide">
                <button type="button" className="btn btn-small" onClick={work}>Work out interest on opening capital</button>
                {calc && <p className="hint" role="status">{plain(calc.opening_capital)} capital at {calc.rate}% for {calc.days} days = {plain(calc.interest)}</p>}
              </div>
            </>
          )}
          <Field id="tx-ref" label="Reference (cheque no., UTR ...)" error={errors.reference}><input id="tx-ref" value={v.reference} maxLength={100} onChange={set('reference')} /></Field>
          <Field id="tx-narr" label="Narration" wide error={errors.narration}><input id="tx-narr" value={v.narration} maxLength={300} onChange={set('narration')} /></Field>
        </div>
        {waits && <Notice type="warn">This is more than {money(threshold)}, so it will wait for approval before it is posted to the books.</Notice>}
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Record transaction'}</button>
        </div>
      </form>
    </Dialog>
  );
}

function TransactionView({ id, onClose }) {
  const { data: t, error } = useLoad(() => api.get(`/partners/transactions/${id}/`), [id]);
  return (
    <Dialog title={t ? t.number : 'Transaction'} onClose={onClose}>
      {!t && !error && <Loading />}
      <ErrorBox error={error} />
      {t && (
        <>
          <dl className="partners-kv">
            <dt>Partner</dt><dd><PartnerLink id={t.partner.id}>{t.partner.name}</PartnerLink></dd>
            <dt>Type</dt><dd>{t.kind_label}</dd>
            <dt>Date</dt><dd>{fmtDate(t.date)}</dd>
            <dt>Amount</dt><dd><strong>{money(t.amount)}</strong></dd>
            <dt>Status</dt><dd><StatusBadge status={t.status} label={t.status_label} />{t.status === 'pending_approval' && <> <ApprovalStatus kind="partner_withdrawal" sourceId={t.id} /></>}</dd>
            {t.mode_label && (<><dt>Paid by</dt><dd>{t.mode_label}</dd></>)}
            {t.rate !== null && (<><dt>Rate</dt><dd>{t.rate}% a year{t.period_from ? `, ${fmtDate(t.period_from)} to ${fmtDate(t.period_to)}` : ''}</dd></>)}
            {t.reference && (<><dt>Reference</dt><dd>{t.reference}</dd></>)}
            {t.narration && (<><dt>Narration</dt><dd>{t.narration}</dd></>)}
            <dt>Requested by</dt><dd>{t.requested_by || '—'}</dd>
            {t.decided_by && (<><dt>Decided by</dt><dd>{t.decided_by} on {fmtDate((t.decided_at || '').slice(0, 10))}</dd></>)}
            {t.decision_reason && (<><dt>Reason</dt><dd>{t.decision_reason}</dd></>)}
          </dl>
          {t.journal ? (
            <>
              <h3>Posted to the books</h3>
              <div className="table-wrap"><table>
                <thead><tr><th>Account</th><th className="amount">Debit</th><th className="amount">Credit</th></tr></thead>
                <tbody>{t.journal.lines.map((l, i) => (
                  <tr key={i}><td data-label="Account">{l.account}</td><td className="amount" data-label="Debit">{num(l.debit) ? plain(l.debit) : ''}</td><td className="amount" data-label="Credit">{num(l.credit) ? plain(l.credit) : ''}</td></tr>
                ))}</tbody>
              </table></div>
              <p className="hint">Journal {t.journal.reference} dated {fmtDate(t.journal.date)}{t.journal.status === 'reversed' ? ' (reversed)' : ''}.</p>
            </>
          ) : <p className="muted">Nothing is posted to the books for this transaction.</p>}
        </>
      )}
    </Dialog>
  );
}

const STATUS_TABS = [['', 'All'], ['posted', 'Posted'], ['pending_approval', 'Waiting for approval'], ['rejected', 'Rejected'], ['void', 'Void']];

export function TransactionsPage() {
  const { user } = useAuth();
  const notify = useToast();
  const { ask, dialog: askDialog } = useReasonDialog();
  const [params, setParams] = useSearchParams();
  const { data: lk } = usePartnerLookups();
  const [filters, setFilters] = useState({ q: '', partner: params.get('partner') || '', kind: params.get('kind') || '', status: params.get('status') || '', from: '', to: '' });
  const [term, setTerm] = useState(filters);
  const [dialog, setDialog] = useState(params.get('new') === '1' ? {} : null);
  const [view, setView] = useState(null);
  const { data, error, loading, reload } = useLoad(() => api.get('/partners/transactions/', term), [JSON.stringify(term)]);
  const canManage = user.permissions.includes('partners.manage');
  useEffect(() => { if (params.toString()) setParams({}, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const setF = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }));
  const apply = (next) => { setFilters(next); setTerm(next); };

  const act = async (t, what) => {
    try {
      if (what === 'approve') {
        await api.post(`/partners/transactions/${t.id}/approve/`);
        notify(`${t.number} approved and posted.`);
      } else {
        const reason = await ask({
          title: `${what === 'reject' ? 'Reject' : 'Void'} ${t.number}?`,
          message: what === 'reject' ? 'Nothing is posted to the books.' : t.status === 'posted' ? 'The posting to the books is reversed. The record is kept.' : 'The record is kept.',
          confirmLabel: what === 'reject' ? 'Reject' : 'Void transaction',
        });
        if (reason === null) return;
        await api.post(`/partners/transactions/${t.id}/${what}/`, { reason });
        notify(`${t.number} ${what === 'reject' ? 'rejected' : 'voided'}.`);
      }
      reload();
    } catch (e) { notify(e.message, 'error'); }
  };

  const exportCsv = () => downloadCsv('partner-transactions.csv', [['Number', 'Date', 'Partner', 'Type', 'Status', 'Paid by', 'Reference', 'Narration', 'Amount'],
    ...data.results.map((t) => [t.number, t.date, t.partner.name, t.kind_label, t.status_label, t.mode_label, t.reference, t.narration, t.amount])]);

  return (
    <>
      <PageHeader title="Partner transactions" description="Capital, drawings, remuneration, interest and loans. Each one is posted to the books."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setDialog({})}>Record transaction</button>} />
      <section className="card">
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); apply(filters); }}>
          <Field id="tx-q" label="Search" className="grow"><input id="tx-q" value={filters.q} onChange={setF('q')} placeholder="Number, partner, reference or narration" /></Field>
          <PartnerSelect id="tx-fp" label="Partner" value={filters.partner} onChange={(x) => setFilters((f) => ({ ...f, partner: x }))} partners={lk?.partners} blank="All partners" />
          <Field id="tx-fk" label="Type">
            <select id="tx-fk" value={filters.kind} onChange={setF('kind')}><option value="">All types</option>{lk?.kinds.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}</select>
          </Field>
          <Field id="tx-ff" label="From"><input id="tx-ff" type="date" value={filters.from} onChange={setF('from')} /></Field>
          <Field id="tx-ft" label="To"><input id="tx-ft" type="date" value={filters.to} onChange={setF('to')} /></Field>
          <button type="submit" className="btn">Search</button>
        </form>
        <div className="chips" role="group" aria-label="Status">
          {STATUS_TABS.map(([val, label]) => <button key={val} type="button" className="chip" aria-pressed={term.status === val} onClick={() => apply({ ...filters, status: val })}>{label}</button>)}
        </div>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && data?.results.length === 0 && <p className="empty">No transactions match.</p>}
        {data?.results.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Number</th><th>Partner</th><th>Type</th><th>Status</th><th>Reference</th><th className="amount">Amount</th><th><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {data.results.map((t) => (
                  <tr key={t.id} className={t.status === 'void' || t.status === 'rejected' ? 'row-inactive' : ''}>
                    <td data-label="Date">{fmtDate(t.date)}</td>
                    <td data-label="Number"><button type="button" className="partners-linkbtn" onClick={() => setView(t.id)}><code>{t.number}</code></button></td>
                    <td data-label="Partner"><PartnerLink id={t.partner.id}>{t.partner.name}</PartnerLink></td>
                    <td data-label="Type">{t.kind_label}</td>
                    <td data-label="Status"><StatusBadge status={t.status} label={t.status_label} />{t.status === 'pending_approval' && <> <ApprovalPanel kind="partner_withdrawal" sourceId={t.id} waiting canSubmit={canManage} onChange={reload} /></>}</td>
                    <td data-label="Reference">{t.reference || '—'}</td>
                    <td className="amount" data-label="Amount"><strong>{plain(t.amount)}</strong></td>
                    <td className="row-actions">
                      {canManage && t.status === 'pending_approval' && (<>
                        <button type="button" className="btn btn-small btn-primary" onClick={() => act(t, 'approve')}>Approve</button>
                        <button type="button" className="btn btn-small" onClick={() => act(t, 'reject')}>Reject</button>
                      </>)}
                      {canManage && (t.status === 'posted' || t.status === 'pending_approval') && <button type="button" className="btn btn-small" onClick={() => act(t, 'void')}>Void</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data?.results.length > 0 && (
          <div className="list-foot">
            <span>{data.count} transaction{data.count === 1 ? '' : 's'}{data.truncated ? ' (first 500 shown; narrow the search)' : ''}</span>
            <span>Posted total <strong>{money(data.posted_total)}</strong></span>
            <button type="button" className="btn btn-small" onClick={exportCsv}>Export CSV</button>
          </div>
        )}
      </section>
      {dialog && <TransactionDialog preset={dialog} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); reload(); }} />}
      {view && <TransactionView id={view} onClose={() => setView(null)} />}
      {askDialog}
    </>
  );
}
