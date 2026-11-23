import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../api';
import { money } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, fmtDateTime, useConfirm, useReasonDialog, useToast } from '../../ui';
import { DecisionDialog, StatusBadge, ageText, isInternal } from './common';

const STEP_TONE = { approved: 'good', rejected: 'bad', active: 'info', waiting: 'neutral' };
const STEP_LABEL = { approved: 'Approved', rejected: 'Rejected', active: 'Waiting for decision', waiting: 'Not started' };
const EVENT_TONE = { approved: 'good', auto_approved: 'good', step_approved: 'good', rejected: 'bad', withdrawn: 'neutral', cancelled: 'neutral', escalated: 'warn', delegated: 'info', submitted: 'info', resubmitted: 'info' };

function ReassignDialog({ req, onClose, onDone }) {
  const notify = useToast();
  const users = useLoad(() => api.get('/approvals/users/'), []);
  const open = req.steps.filter((s) => s.state === 'active' || s.state === 'waiting');
  const [v, setV] = useState({ step: String(req.current_step), user: '', comment: '' });
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!v.user) { setErrors({ user: 'Choose who should approve instead.' }); return; }
    setBusy(true);
    try {
      const res = await api.post(`/approvals/requests/${req.id}/reassign/`, { step: Number(v.step), user: v.user, comment: v.comment });
      notify('Step reassigned.');
      onDone(res);
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title="Reassign approval" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[{ name: 'user' }, { name: 'step' }, { name: 'comment' }]} />
        <Field id="ra-step" label="Step" error={errors.step}>
          <select id="ra-step" value={v.step} onChange={set('step')}>{open.map((s) => <option key={s.order} value={s.order}>Step {s.order}: {s.who}</option>)}</select>
        </Field>
        <Field id="ra-user" label="New approver" required error={errors.user} hint="This person alone approves the step. The person who asked for approval cannot be chosen.">
          <select id="ra-user" value={v.user} onChange={set('user')}>
            <option value="">Choose…</option>
            {(users.data || []).filter((u) => u.id !== req.requested_by.id).map((u) => <option key={u.id} value={u.id}>{u.name} ({u.role_label})</option>)}
          </select>
        </Field>
        <Field id="ra-comment" label="Note (optional)" error={errors.comment}><input id="ra-comment" value={v.comment} onChange={set('comment')} /></Field>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>Reassign</button>
        </div>
      </form>
    </Dialog>
  );
}

export default function RequestDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const notify = useToast();
  const { data: req, error, loading, reload } = useLoad(() => api.get(`/approvals/requests/${id}/`), [id]);
  const [dialog, setDialog] = useState(null);
  const { ask, dialog: reasonDialog } = useReasonDialog();
  const { confirm, dialog: confirmDialog } = useConfirm();

  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;

  const withdraw = async () => {
    const comment = await ask({ title: 'Withdraw this request', message: 'The record will go back to its owner. You can send it again later.', label: 'Reason (optional)', required: false, confirmLabel: 'Withdraw' });
    if (comment === null) return;
    try { await api.post(`/approvals/requests/${req.id}/withdraw/`, { comment }); notify('Request withdrawn.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const resubmit = async () => {
    if (!(await confirm({ title: 'Send for approval again?', body: 'A new request is created with a new reference. This one stays in the history.', confirmLabel: 'Send again' }))) return;
    try { const n = await api.post(`/approvals/requests/${req.id}/resubmit/`); notify(`Sent again: ${n.reference_number}`); navigate(`/approvals/requests/${n.id}`); } catch (e) { notify(e.message, 'error'); }
  };

  return (
    <>
      <PageHeader
        title={<>{req.reference_number} <StatusBadge status={req.status} label={req.status_label} /></>}
        description={`${req.kind_label}: ${req.title}`}
        actions={(<>
          {req.can_act && <button type="button" className="btn btn-primary" onClick={() => setDialog('approve')}>Approve</button>}
          {req.can_act && <button type="button" className="btn btn-danger-outline" onClick={() => setDialog('reject')}>Reject</button>}
          {req.can_reassign && <button type="button" className="btn" onClick={() => setDialog('reassign')}>Reassign</button>}
          {req.can_withdraw && <button type="button" className="btn" onClick={withdraw}>Withdraw</button>}
          {req.can_resubmit && <button type="button" className="btn btn-primary" onClick={resubmit}>Send again</button>}
        </>)} />
      <p><Link to="/approvals">Back to approvals</Link></p>

      <div className="grid-2">
        <section className="card" aria-label="Request details">
          <h2>Details</h2>
          <dl className="summary">
            <div><dt>Type</dt><dd>{req.kind_label}</dd></div>
            <div><dt>Amount</dt><dd>{req.amount === null ? '—' : money(req.amount)}</dd></div>
            <div><dt>Requested by</dt><dd>{req.requested_by?.name}</dd></div>
            <div><dt>Submitted</dt><dd>{fmtDateTime(req.submitted_at)}</dd></div>
            <div><dt>{req.decided_at ? 'Decided' : 'Waiting for'}</dt><dd>{req.decided_at ? fmtDateTime(req.decided_at) : (req.waiting_on.length ? req.waiting_on.join(', ') : req.waiting_on_role || '—')}</dd></div>
            <div><dt>{req.decided_at ? 'Took' : 'Age'}</dt><dd>{ageText(req.age_hours)}{req.overdue && <> <Badge tone="warn">Overdue</Badge></>}</dd></div>
            <div><dt>Rule applied</dt><dd>{req.rule_name || '—'}</dd></div>
            <div><dt>Record</dt><dd>{req.url ? (isInternal(req.url) ? <Link to={req.url}>Open the record</Link> : <a href={req.url}>Open the record</a>) : '—'}</dd></div>
          </dl>
          {req.summary && <p className="apr-summary">{req.summary}</p>}
          {req.previous && <p className="hint">Sent again after {req.previous}.</p>}
          {req.resubmissions.length > 0 && <p className="hint">Sent again as {req.resubmissions.join(', ')}.</p>}
        </section>

        <section className="card" aria-label="Approval steps">
          <h2>Approval steps</h2>
          <ol className="apr-steps">
            {req.steps.map((s) => (
              <li key={s.order} className={`apr-step apr-step-${s.state}`}>
                <div className="apr-step-head">
                  <strong>Step {s.order}: {s.who}</strong>
                  <Badge tone={STEP_TONE[s.state]}>{req.status === 'approved' && s.state === 'waiting' ? 'Skipped' : STEP_LABEL[s.state]}</Badge>
                  {s.mode === 'all' && <span className="muted"> everyone must approve</span>}
                  {s.delegated && <span className="muted"> reassigned</span>}
                </div>
                {s.state === 'active' && (
                  <div className="muted">
                    {s.approvers.length ? `Can be approved by: ${s.approvers.join(', ')}` : 'No approver available.'}
                    {s.due_at && <> Due {fmtDateTime(s.due_at)}{s.overdue ? ' (overdue)' : ''}.</>}
                  </div>
                )}
                {s.decisions.map((d, i) => (
                  <div key={i} className="apr-decision">
                    <Badge tone={d.decision === 'approved' ? 'good' : 'bad'}>{d.decision === 'approved' ? 'Approved' : 'Rejected'}</Badge>{' '}
                    {d.approver}, {fmtDateTime(d.decided_at)}{d.comment && <q> {d.comment}</q>}
                  </div>
                ))}
              </li>
            ))}
          </ol>
        </section>
      </div>

      <section className="card" aria-label="History">
        <h2>History</h2>
        <ol className="apr-timeline">
          {req.events.map((e) => (
            <li key={e.id}>
              <span className={`apr-dot apr-dot-${EVENT_TONE[e.action] || 'neutral'}`} aria-hidden="true" />
              <div><strong>{e.action_label}</strong>{e.step_order ? ` (step ${e.step_order})` : ''} by {e.actor || 'System'}</div>
              <div className="muted">{fmtDateTime(e.at)}</div>
              {e.comment && <div className="apr-event-comment">{e.comment}</div>}
            </li>
          ))}
        </ol>
      </section>

      {(dialog === 'approve' || dialog === 'reject') && (
        <DecisionDialog mode={dialog} requests={[req]} onClose={() => setDialog(null)} onDone={() => { setDialog(null); reload(); }} />
      )}
      {dialog === 'reassign' && <ReassignDialog req={req} onClose={() => setDialog(null)} onDone={() => { setDialog(null); reload(); }} />}
      {reasonDialog}
      {confirmDialog}
    </>
  );
}
