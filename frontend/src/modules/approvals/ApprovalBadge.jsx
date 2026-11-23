import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { useLoad } from '../../hooks';
import { Badge, useReasonDialog, useToast } from '../../ui';
import { STATUS_LABEL, STATUS_TONE } from './common';

// Small status for any record that goes through approval. Other modules import it:
//   import { ApprovalStatus } from '../approvals/ApprovalBadge';
//   <ApprovalStatus kind="purchase_order" sourceId={po.id} />
// Renders nothing when the record was never submitted (pass showNone to show "Not submitted"). `refreshKey` reloads it.
export function ApprovalStatus({ kind, sourceId, showNone = false, refreshKey = 0 }) {
  const { data } = useLoad(() => (kind && sourceId ? api.get('/approvals/status/', { kind, source_id: sourceId }) : Promise.resolve(null)),
    [kind, sourceId, refreshKey]);
  if (!data) return null;
  if (data.status === 'none') return showNone ? <Badge tone="neutral">Not submitted</Badge> : null;
  const badge = <Badge tone={STATUS_TONE[data.status] || 'neutral'}>Approval: {STATUS_LABEL[data.status] || data.status}</Badge>;
  if (!data.can_view || !data.id) return badge;
  return (
    <Link to={`/approvals/requests/${data.id}`} className="apr-badge-link" title={`Open ${data.reference_number}`}>{badge}</Link>
  );
}

export const ApprovalBadge = ApprovalStatus;

// The approval state of a record plus, when the record is waiting for approval but has no open request (it was closed
// elsewhere), a "Request approval" button that sends it again. `waiting`: the record itself says it is pending approval.
//   <ApprovalPanel kind="vendor_bill" sourceId={b.id} waiting={b.status === 'pending_approval'} canSubmit={canManage} onChange={reload} />
export function ApprovalPanel({ kind, sourceId, waiting = false, canSubmit = false, onChange, refreshKey = 0 }) {
  const [tick, setTick] = useState(0);
  const { data } = useLoad(() => (kind && sourceId ? api.get('/approvals/status/', { kind, source_id: sourceId }) : Promise.resolve(null)),
    [kind, sourceId, refreshKey, tick]);
  if (!data) return null;
  const open = data.status === 'pending';
  const badge = data.status !== 'none' && (
    <Badge tone={STATUS_TONE[data.status] || 'neutral'}>Approval: {STATUS_LABEL[data.status] || data.status}</Badge>
  );
  return (
    <span className="apr-panel">
      {badge && (data.can_view && data.id
        ? <Link to={`/approvals/requests/${data.id}`} className="apr-badge-link" title={`Open ${data.reference_number}`}>{badge}</Link> : badge)}
      {waiting && canSubmit && !open && (
        <ApprovalButton kind={kind} sourceId={sourceId} className="btn btn-small" onSubmitted={() => { setTick((t) => t + 1); onChange && onChange(); }} />
      )}
    </span>
  );
}

// "Request approval" button. Works only for kinds whose module registered a describe function on the server.
export function ApprovalButton({ kind, sourceId, label = 'Request approval', className = 'btn', disabled = false, onSubmitted }) {
  const notify = useToast();
  const [busy, setBusy] = useState(false);
  const click = async () => {
    setBusy(true);
    try {
      const req = await api.post('/approvals/submit/', { kind, source_id: String(sourceId) });
      notify(req.status === 'approved' ? `Approved automatically (${req.reference_number}).` : `Sent for approval: ${req.reference_number}`);
      onSubmitted && onSubmitted(req);
    } catch (err) {
      notify(err.message || 'Could not send for approval.', 'error');
    }
    setBusy(false);
  };
  return <button type="button" className={className} disabled={disabled || busy} onClick={click}>{busy ? 'Sending…' : label}</button>;
}

// The buttons of a record that is waiting for approval: "Take back" (always, for the person who may change the record) and,
// only when the approvals module is not managing the record, local Approve / Reject. `base` is the record's API path, which
// answers POST base/withdraw/, base/approve/ and base/reject/.
//   <ApprovalActions base={`/sales/quotes/${id}`} canAct={canManage} canDecide={canDecide} managed={q.approval_managed} noun="quotation" onChange={reload} />
export function ApprovalActions({ base, canAct = false, canDecide = false, managed = true, noun = 'record', takeBack = 'Take back', onChange }) {
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const [busy, setBusy] = useState(false);
  const run = async (path, body, message) => {
    setBusy(true);
    try { await api.post(`${base}/${path}/`, body); notify(message); onChange && onChange(); } catch (e) { notify(e.message, 'error'); }
    setBusy(false);
  };
  const reject = async () => {
    const reason = await ask({ title: `Reject ${noun}`, confirmLabel: 'Reject', message: `The ${noun} goes back to the person who prepared it, with your reason.` });
    if (reason !== null) run('reject', { change_reason: reason }, `The ${noun} was rejected.`);
  };
  return (
    <>
      {canAct && <button type="button" className="btn" disabled={busy} onClick={() => run('withdraw', {}, `The ${noun} was taken back.`)}>{takeBack}</button>}
      {canDecide && !managed && (
        <>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => run('approve', {}, `The ${noun} was approved.`)}>Approve</button>
          <button type="button" className="btn" disabled={busy} onClick={reject}>Reject</button>
        </>
      )}
      {dialog}
    </>
  );
}

export default ApprovalStatus;
