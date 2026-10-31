import { useEffect, useState } from 'react';
import { api } from '../api';
import CrudSection, { ActiveBadge } from '../components/CrudSection';
import { useAuth } from '../context';
import { useOptions } from '../hooks';
import { BANK_FIELDS } from '../specs';
import { Badge, Notice, VerificationBadge, fmtDate } from '../ui';

const DEFAULTS = { account_type: 'current', currency: 'INR' };
const REVEAL_MS = 30000;

// Bank accounts: masked everywhere; full number only via an audited, auto-hiding reveal.
export function BankAccounts() {
  const { user } = useAuth();
  const canWrite = user.is_finance;
  const documents = useOptions('/company/documents/', (d) => `${d.title} (v${d.version})`);
  const [revealed, setRevealed] = useState(null); // {name, number} - memory only, never stored

  useEffect(() => {
    if (!revealed) return undefined;
    const t = setTimeout(() => setRevealed(null), REVEAL_MS);
    return () => clearTimeout(t);
  }, [revealed]);
  useEffect(() => () => setRevealed(null), []);

  const reveal = (row, ctx) => async () => {
    const why = await ctx.ask({ title: `Reveal account number – ${row.display_name}`, label: 'Why do you need the full number? (optional)', required: false, confirmLabel: 'Reveal' });
    if (why === null) return;
    try {
      const res = await api.post(`/company/bank-accounts/${row.id}/reveal/`, { change_reason: why });
      setRevealed({ name: row.display_name, number: res.account_number });
    } catch (e) { ctx.notify(e.message, 'error'); }
  };

  const verify = (row, ctx, decision) => async () => {
    if (decision === 'verified' && !row.supporting_document) { ctx.notify('Attach the bank verification document (Edit) before verifying.', 'error'); return; }
    const ok = await ctx.confirm({
      title: decision === 'verified' ? 'Verify these payment details?' : 'Reject these payment details?',
      body: decision === 'verified'
        ? 'Verified details may be printed on invoices and quotations. Someone other than the person who entered the details must verify them.'
        : 'Rejected details will be removed from invoices and quotations.',
      confirmLabel: decision === 'verified' ? 'Verify' : 'Reject', danger: decision !== 'verified',
    });
    if (!ok) return;
    const note = await ctx.ask({ title: 'Verification note', label: 'Note', required: false, confirmLabel: 'Record' });
    if (note === null) return;
    try {
      await api.post(`/company/bank-accounts/${row.id}/verify/`, { decision, change_reason: note });
      ctx.notify(decision === 'verified' ? 'Bank account verified.' : 'Bank account rejected.');
      ctx.reload();
    } catch (e) { ctx.notify(e.message, 'error'); }
  };

  return (
    <>
      {!canWrite && <Notice>Bank details are masked. Only finance users and administrators can see or change payment details.</Notice>}
      {revealed && (
        <div className="notice notice-warn reveal" role="alert">
          <div>
            <strong>{revealed.name}</strong>: <code className="reveal-number">{revealed.number}</code>
            <div className="hint">Hides automatically in 30 seconds. This view has been recorded in the change history.</div>
          </div>
          <button type="button" className="btn btn-small" onClick={() => setRevealed(null)}>Hide now</button>
        </div>
      )}
      <CrudSection
        title="Bank accounts" noun="bank account" endpoint="/company/bank-accounts/" fields={BANK_FIELDS} defaults={DEFAULTS}
        canWrite={canWrite} ctx={{ documents, isAdmin: user.is_company_admin }}
        description="Every account gets its own reference ID (e.g. T2N-BNK-26-2710-0001) when it is added. Changing the account number, IFSC, holder or bank name of a verified account resets it to pending and removes it from documents until it is verified again."
        emptyText="No bank accounts yet. A bank account is optional; add one when it is available."
        columns={[
          { key: 'reference_code', label: 'Ref ID', render: (r) => <code>{r.reference_code || '—'}</code> },
          { key: 'display_name', label: 'Account', render: (r) => <strong>{r.display_name}</strong> },
          { key: 'bank_name', label: 'Bank', render: (r) => `${r.bank_name}${r.branch ? `, ${r.branch}` : ''}` },
          { key: 'account_number_masked', label: 'Account number', render: (r) => <code>{r.account_number_masked}</code> },
          { key: 'ifsc_code', label: 'IFSC' },
          { key: 'verification_status', label: 'Verification', render: (r) => <VerificationBadge status={r.verification_status} /> },
          { key: 'show_on_invoices', label: 'On documents', render: (r) => (
            <>
              {r.show_on_invoices && <Badge tone="info">Invoices</Badge>} {r.show_on_quotations && <Badge tone="info">Quotations</Badge>}
              {!r.show_on_invoices && !r.show_on_quotations && '—'}
            </>
          ) },
          { key: 'is_primary', label: 'Flags', render: (r) => (<>{r.is_primary && <Badge tone="info">Primary</Badge>} <ActiveBadge active={r.is_active} /></>) },
          { key: 'effective_from', label: 'Effective', render: (r) => (r.effective_from ? `${fmtDate(r.effective_from)} – ${r.effective_to ? fmtDate(r.effective_to) : 'open'}` : '—') },
        ]}
        rowActions={(row, ctx) => (canWrite ? [
          { label: 'Reveal number', onClick: reveal(row, ctx) },
          ...(row.verification_status !== 'verified' ? [{ label: 'Verify', onClick: verify(row, ctx, 'verified') }] : []),
          ...(row.verification_status !== 'rejected' ? [{ label: 'Reject', danger: true, onClick: verify(row, ctx, 'rejected') }] : []),
        ] : [])}
      />
    </>
  );
}
