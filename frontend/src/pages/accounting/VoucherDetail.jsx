import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { money, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { ApprovalActions, ApprovalPanel } from '../../modules/approvals/ApprovalBadge';
import { ErrorBox, Loading, Notice, PageHeader, fmtDate, useReasonDialog, useToast } from '../../ui';
import { VoucherDialog, VoucherStatus } from './Vouchers';
import { projectLabel } from '../sales/common';

// One expense or income entry. An expense above an approval limit waits here (not numbered, not in the books) until approved;
// a rejected one is a draft again with the approver's note, ready to correct and record once more.
export default function VoucherDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const [editing, setEditing] = useState(false);
  const { data: v, error, loading, reload } = useLoad(() => api.get(`/accounting/vouchers/${id}/`), [id]);
  const options = useLoad(() => api.get('/accounting/vouchers/options/'), []);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const manage = user.permissions.includes('accounting.manage');
  const isExpense = v.kind === 'expense';
  const noun = isExpense ? 'expense' : 'income';

  const record = async () => {
    try {
      const r = await api.post(`/accounting/vouchers/${id}/record/`);
      notify(r.status === 'pending_approval' ? 'Expense sent for approval. It is posted to the books once approved.' : `Expense recorded: ${r.number}`);
      reload();
    } catch (e) { notify(e.message, 'error'); }
  };
  const voidIt = async () => {
    const reason = await ask({ title: `Void ${v.number}?`, message: 'The entry it made in the books is reversed. The record is kept.', confirmLabel: 'Void' });
    if (reason === null) return;
    try { await api.post(`/accounting/vouchers/${id}/void/`, { change_reason: reason }); notify('Voided.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const remove = async () => {
    if (!window.confirm('Delete this draft expense?')) return;
    try { await api.del(`/accounting/vouchers/${id}/`); navigate('/accounting/vouchers'); } catch (e) { notify(e.message, 'error'); }
  };

  return (
    <>
      <Link to="/accounting/vouchers" className="back-link">← Expenses and income</Link>
      <PageHeader title={v.number || (v.status === 'pending_approval' ? 'Expense waiting for approval' : `Draft ${noun}`)} description={`${v.party_name || v.category.name} · ${fmtDate(v.date)}`}
        actions={(
          <>
            <VoucherStatus v={v} />
            {isExpense && <ApprovalPanel kind="expense" sourceId={v.id} waiting={v.status === 'pending_approval'} canSubmit={manage} onChange={reload} refreshKey={v.status} />}
            {isExpense && v.status === 'pending_approval' && (
              <ApprovalActions base={`/accounting/vouchers/${id}`} canAct={manage} canDecide={manage} managed={v.approval_managed} noun="expense" onChange={reload} />
            )}
            {manage && v.status === 'draft' && (
              <>
                <button type="button" className="btn" onClick={() => setEditing(true)} disabled={!options.data}>Edit</button>
                <button type="button" className="btn btn-primary" onClick={record}>Record expense</button>
                <button type="button" className="btn" onClick={remove}>Delete</button>
              </>
            )}
            {manage && v.status === 'recorded' && <button type="button" className="btn" onClick={voidIt}>Void</button>}
          </>
        )} />
      {v.status === 'pending_approval' && <Notice>This expense is above an approval limit. It is not numbered or posted to the books until it is approved.</Notice>}
      {v.status === 'draft' && v.decision_note && <Notice type="warn">{v.decision_note}</Notice>}
      {v.status === 'draft' && !v.decision_note && <Notice>This is a draft. Nothing is in the books until you record it.</Notice>}
      {v.status === 'void' && <Notice type="warn">Voided{v.void_reason ? `: ${v.void_reason}` : ''}</Notice>}
      <div className="tiles">
        <div className="tile"><div className="tile-label">Amount before GST</div><div className="tile-value">{money(v.amount)}</div></div>
        <div className="tile"><div className="tile-label">GST</div><div className="tile-value">{num(v.tax_amount) ? money(v.tax_amount) : '—'}</div>{v.gst_split.length > 0 && <div className="hint">{v.gst_split.map((g) => `${g.label} ${plain(g.amount)}`).join(' + ')}</div>}</div>
        <div className="tile"><div className="tile-label">Total</div><div className="tile-value">{money(v.total)}</div></div>
      </div>
      <section className="card">
        <dl className="pur-facts">
          <div><dt>Account</dt><dd>{v.category.code} {v.category.name}</dd></div>
          <div><dt>{isExpense ? 'Paid from' : 'Received in'}</dt><dd>{v.money_account.name}</dd></div>
          <div><dt>{isExpense ? 'Paid to' : 'Received from'}</dt><dd>{v.party_name || '—'}</dd></div>
          <div><dt>Project</dt><dd>{v.project_ref ? projectLabel(v.project_ref) : 'None'}</dd></div>
          <div><dt>Bill or receipt number</dt><dd>{v.reference || '—'}</dd></div>
          <div><dt>Notes</dt><dd>{v.notes || '—'}</dd></div>
          <div><dt>Entered by</dt><dd>{v.created_by || '—'}</dd></div>
          {v.entry_id && <div><dt>Journal entry</dt><dd><Link to={`/accounting/journal/${v.entry_id}`}>View the entry in the books</Link></dd></div>}
        </dl>
      </section>
      {editing && options.data && <VoucherDialog kind={v.kind} options={options.data} voucher={v} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} />}
      {dialog}
    </>
  );
}
