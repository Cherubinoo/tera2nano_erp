import { useState } from 'react';
import { api } from '../../api';
import { useLoad } from '../../hooks';
import { Badge, Dialog, Field, FormErrors, useToast } from '../../ui';

export const STATUS_TONE = { pending: 'info', approved: 'good', rejected: 'bad', cancelled: 'neutral', escalated: 'warn' };
export const STATUS_LABEL = { pending: 'Pending', approved: 'Approved', rejected: 'Rejected', cancelled: 'Cancelled', escalated: 'Escalated' };
export const STATUS_COLOUR = { pending: 'var(--chart-1)', approved: 'var(--chart-2)', rejected: 'var(--chart-4)', cancelled: 'var(--chart-grey)' };

export function StatusBadge({ status, label }) {
  return <Badge tone={STATUS_TONE[status] || 'neutral'}>{label || STATUS_LABEL[status] || status}</Badge>;
}

// "3 h", "2 d": how long a request has waited (or took).
export function ageText(hours) {
  const h = Number(hours) || 0;
  if (h < 1) return 'under 1 h';
  if (h < 48) return `${Math.round(h)} h`;
  return `${Math.round(h / 24)} d`;
}

export function useKinds() {
  const { data } = useLoad(() => api.get('/approvals/kinds/'), []);
  return { kinds: data?.kinds || [], roles: data?.roles || [] };
}

export function useSummary() {
  const { data, reload } = useLoad(() => api.get('/approvals/summary/'), []);
  return { summary: data || { pending_for_me: 0, my_open_requests: 0, overdue_for_me: 0 }, reload };
}

export const canSeeAll = (user) => (user.permissions || []).some((c) => c === 'approvals.view' || c === 'approvals.manage');

// Source links are in-app paths ("/purchases/orders/12") or absolute URLs.
export const isInternal = (url) => typeof url === 'string' && url.startsWith('/');

// Approve or reject one request, or approve several at once. `requests` is an array of request rows.
export function DecisionDialog({ mode, requests, onClose, onDone }) {
  const notify = useToast();
  const [comment, setComment] = useState('');
  const [error, setError] = useState(null);
  const [fieldError, setFieldError] = useState('');
  const [failed, setFailed] = useState([]);
  const [busy, setBusy] = useState(false);
  const reject = mode === 'reject';
  const many = requests.length > 1;

  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setError(null);
    if (reject && !comment.trim()) { setFieldError('Give a reason for rejecting.'); return; }
    setBusy(true);
    try {
      if (many) {
        const res = await api.post('/approvals/requests/bulk-approve/', { ids: requests.map((r) => r.id), comment });
        if (res.failed.length) {
          setFailed(res.failed);
          notify(`${res.approved} approved, ${res.failed.length} could not be approved.`, res.approved ? 'success' : 'error');
          if (res.approved) onDone(res, false);
        } else { notify(`${res.approved} request${res.approved === 1 ? '' : 's'} approved.`); onDone(res, true); }
      } else {
        const res = await api.post(`/approvals/requests/${requests[0].id}/${reject ? 'reject' : 'approve'}/`, { comment });
        notify(reject ? 'Request rejected.' : res.status === 'approved' ? 'Request approved.' : 'Your approval is recorded; the request moves on.');
        onDone(res, true);
      }
    } catch (err) {
      setError(err);
      if (err.fieldErrors?.comment) setFieldError(err.fieldErrors.comment);
    }
    setBusy(false);
  };

  const title = many ? `Approve ${requests.length} requests` : `${reject ? 'Reject' : 'Approve'} ${requests[0].reference_number}`;
  return (
    <Dialog title={title} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[{ name: 'comment' }]} />
        {!many && <p className="muted">{requests[0].title}</p>}
        {many && <ul className="apr-bulk-list">{requests.map((r) => <li key={r.id}>{r.reference_number}: {r.title}</li>)}</ul>}
        <Field id="apr-comment" label={reject ? 'Reason for rejecting' : 'Comment (optional)'} required={reject} error={fieldError}>
          <textarea id="apr-comment" rows={3} value={comment} onChange={(e) => { setComment(e.target.value); setFieldError(''); }} />
        </Field>
        {failed.length > 0 && (
          <div className="notice notice-error" role="alert">
            <strong>Not approved:</strong>
            <ul>{failed.map((f) => <li key={f.id}>{f.error}</li>)}</ul>
          </div>
        )}
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>{failed.length ? 'Close' : 'Cancel'}</button>
          {!failed.length && <button type="submit" className={`btn ${reject ? 'btn-danger' : 'btn-primary'}`} disabled={busy}>{reject ? 'Reject' : 'Approve'}</button>}
        </div>
      </form>
    </Dialog>
  );
}
