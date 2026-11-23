import { useState } from 'react';
import { api } from '../../api';
import { useAuth } from '../../context';
import { plain } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, useConfirm, useToast } from '../../ui';
import { useKinds } from './common';

const blankStep = () => ({ approver_type: 'role', role: 'finance_manager', user: '', mode: 'any', escalate_after_days: '' });

function conditionText(r) {
  const parts = [];
  if (r.amount_from && r.amount_to) parts.push(`${plain(r.amount_from)} to ${plain(r.amount_to)}`);
  else if (r.amount_from) parts.push(`from ${plain(r.amount_from)}`);
  else if (r.amount_to) parts.push(`up to ${plain(r.amount_to)}`);
  if (r.project_text) parts.push(`project has "${r.project_text}"`);
  if (r.department_text) parts.push(`department has "${r.department_text}"`);
  return parts.length ? parts.join(', ') : 'Any amount';
}

function RuleDialog({ rule, kinds, roles, onClose, onSaved }) {
  const notify = useToast();
  const users = useLoad(() => api.get('/approvals/users/'), []);
  const [v, setV] = useState(() => (rule ? {
    kind: rule.kind, name: rule.name, description: rule.description, amount_from: rule.amount_from ?? '', amount_to: rule.amount_to ?? '',
    project_text: rule.project_text, department_text: rule.department_text, is_active: rule.is_active,
    steps: rule.steps.map((s) => ({ approver_type: s.approver_type, role: s.role || 'finance_manager', user: s.user || '', mode: s.mode, escalate_after_days: s.escalate_after_days ?? '' })),
  } : { kind: kinds[0]?.value || '', name: '', description: '', amount_from: '', amount_to: '', project_text: '', department_text: '', is_active: true, steps: [blankStep()] }));
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const setStep = (i, k, val) => { setV((x) => ({ ...x, steps: x.steps.map((s, j) => (j === i ? { ...s, [k]: val } : s)) })); setErrors((x) => ({ ...x, steps: undefined })); };
  const move = (i, d) => setV((x) => { const s = [...x.steps]; const j = i + d; if (j < 0 || j >= s.length) return x; [s[i], s[j]] = [s[j], s[i]]; return { ...x, steps: s }; });

  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setError(null);
    const errs = {};
    if (!v.name.trim()) errs.name = 'Enter a name for the rule.';
    if (!v.kind) errs.kind = 'Choose what the rule applies to.';
    if (v.amount_from !== '' && v.amount_to !== '' && Number(v.amount_to) < Number(v.amount_from)) errs.amount_to = 'The upper amount must not be below the lower amount.';
    if (v.steps.some((s) => s.approver_type === 'user' && !s.user)) errs.steps = 'Choose a user for every step that names one.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const body = {
        ...v, amount_from: v.amount_from === '' ? null : v.amount_from, amount_to: v.amount_to === '' ? null : v.amount_to,
        steps: v.steps.map((s) => ({ approver_type: s.approver_type, role: s.approver_type === 'role' ? s.role : '', user: s.approver_type === 'user' ? s.user : null, mode: s.mode, escalate_after_days: s.escalate_after_days === '' ? null : Number(s.escalate_after_days) })),
      };
      const saved = rule ? await api.patch(`/approvals/rules/${rule.id}/`, body) : await api.post('/approvals/rules/', body);
      notify(rule ? 'Rule saved.' : 'Rule added.');
      onSaved(saved);
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };

  return (
    <Dialog title={rule ? `Edit rule: ${rule.name}` : 'New approval rule'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[{ name: 'name' }, { name: 'kind' }, { name: 'amount_from' }, { name: 'amount_to' }, { name: 'steps' }]} />
        <div className="form-grid">
          <Field id="ar-name" label="Rule name" required error={errors.name}><input id="ar-name" value={v.name} maxLength={150} onChange={set('name')} /></Field>
          <Field id="ar-kind" label="Applies to" required error={errors.kind}>
            <select id="ar-kind" value={v.kind} onChange={set('kind')}>{kinds.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}</select>
          </Field>
          <Field id="ar-desc" label="Description" wide error={errors.description}><input id="ar-desc" value={v.description} maxLength={300} onChange={set('description')} /></Field>
          <Field id="ar-from" label="Amount from" error={errors.amount_from} hint="Included. Leave empty for no lower limit."><input id="ar-from" type="number" min="0" step="0.01" value={v.amount_from} onChange={set('amount_from')} /></Field>
          <Field id="ar-to" label="Amount to" error={errors.amount_to} hint="Included. Leave empty for no upper limit."><input id="ar-to" type="number" min="0" step="0.01" value={v.amount_to} onChange={set('amount_to')} /></Field>
          <Field id="ar-project" label="Project contains (optional)" error={errors.project_text}><input id="ar-project" value={v.project_text} maxLength={150} onChange={set('project_text')} /></Field>
          <Field id="ar-dept" label="Department contains (optional)" error={errors.department_text}><input id="ar-dept" value={v.department_text} maxLength={150} onChange={set('department_text')} /></Field>
          <div className="field field-check field-wide">
            <label className="check"><input type="checkbox" checked={v.is_active} onChange={(e) => setV((x) => ({ ...x, is_active: e.target.checked }))} /> Rule is switched on</label>
          </div>
        </div>
        <fieldset className="subcard">
          <legend>Approval steps, in order</legend>
          {errors.steps && <div className="error-text" role="alert">{errors.steps}</div>}
          {v.steps.map((s, i) => (
            <div key={i} className="apr-step-row">
              <strong className="apr-step-no">{i + 1}</strong>
              <Field id={`ar-t-${i}`} label="Approver is">
                <select id={`ar-t-${i}`} value={s.approver_type} onChange={(e) => setStep(i, 'approver_type', e.target.value)}>
                  <option value="role">Anyone with a role</option><option value="user">A specific user</option>
                </select>
              </Field>
              {s.approver_type === 'role' ? (
                <Field id={`ar-r-${i}`} label="Role">
                  <select id={`ar-r-${i}`} value={s.role} onChange={(e) => setStep(i, 'role', e.target.value)}>{roles.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}</select>
                </Field>
              ) : (
                <Field id={`ar-u-${i}`} label="User">
                  <select id={`ar-u-${i}`} value={s.user} onChange={(e) => setStep(i, 'user', e.target.value)}>
                    <option value="">Choose…</option>
                    {(users.data || []).map((u) => <option key={u.id} value={u.id}>{u.name} ({u.role_label})</option>)}
                  </select>
                </Field>
              )}
              <Field id={`ar-m-${i}`} label="Needs">
                <select id={`ar-m-${i}`} value={s.approver_type === 'user' ? 'any' : s.mode} disabled={s.approver_type === 'user'} onChange={(e) => setStep(i, 'mode', e.target.value)}>
                  <option value="any">Any one of them</option><option value="all">All of them</option>
                </select>
              </Field>
              <Field id={`ar-e-${i}`} label="Escalate after (days)">
                <input id={`ar-e-${i}`} type="number" min="1" max="90" value={s.escalate_after_days} placeholder="Never" onChange={(e) => setStep(i, 'escalate_after_days', e.target.value)} />
              </Field>
              <div className="apr-step-tools">
                <button type="button" className="btn btn-small" onClick={() => move(i, -1)} disabled={i === 0} aria-label={`Move step ${i + 1} up`}>Up</button>
                <button type="button" className="btn btn-small" onClick={() => move(i, 1)} disabled={i === v.steps.length - 1} aria-label={`Move step ${i + 1} down`}>Down</button>
                <button type="button" className="btn btn-small btn-danger-outline" disabled={v.steps.length === 1} onClick={() => setV((x) => ({ ...x, steps: x.steps.filter((_, j) => j !== i) }))} aria-label={`Remove step ${i + 1}`}>Remove</button>
              </div>
            </div>
          ))}
          <button type="button" className="btn btn-small" onClick={() => setV((x) => ({ ...x, steps: [...x.steps, blankStep()] }))}>Add step</button>
        </fieldset>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{rule ? 'Save rule' : 'Add rule'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export default function Rules() {
  const { user } = useAuth();
  const canManage = (user.permissions || []).includes('approvals.manage');
  const notify = useToast();
  const { kinds, roles } = useKinds();
  const { data, error, loading, reload } = useLoad(() => api.get('/approvals/rules/'), []);
  const [edit, setEdit] = useState(null); // null | 'new' | rule
  const { confirm, dialog: confirmDialog } = useConfirm();

  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const rules = data;
  const swap = async (a, b) => {
    try { await api.post('/approvals/rules/reorder/', { ids: [b.id, a.id] }); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const toggle = async (r) => {
    try { await api.patch(`/approvals/rules/${r.id}/`, { is_active: !r.is_active }); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const remove = async (r) => {
    if (!(await confirm({ title: `Delete "${r.name}"?`, body: r.open_requests ? `${r.open_requests} open request(s) already use this rule and will carry on unchanged.` : 'Records submitted later will use the next matching rule.', confirmLabel: 'Delete rule', danger: true }))) return;
    try { await api.del(`/approvals/rules/${r.id}/`); notify('Rule deleted.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const kindsInOrder = [...new Set(rules.map((r) => r.kind))];

  return (
    <>
      <PageHeader title="Approval rules" description="Who has to approve what. The first matching rule (lowest in the list, top first) is used."
        actions={canManage && <button type="button" className="btn btn-primary" onClick={() => setEdit('new')}>New rule</button>} />
      <Notice type="info">
        When no rule matches, the request goes to the Managing Partner or an administrator, unless the requester may manage that kind of record, in which case it is approved at once.
        Nobody approves their own request. The only exception is an administrator when no other approver exists at all. A step that waits past its escalation days can also be approved by the Managing Partners and administrators.
      </Notice>
      {rules.length === 0 && <p className="empty">There are no rules. Requests go to the Managing Partner or an administrator.</p>}
      {kindsInOrder.map((kind) => {
        const group = rules.filter((r) => r.kind === kind);
        return (
          <section className="card" key={kind} aria-label={`${group[0].kind_label} rules`}>
            <h2>{group[0].kind_label}</h2>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Order</th><th>Rule</th><th>When</th><th>Steps</th><th>Status</th>{canManage && <th><span className="sr-only">Actions</span></th>}</tr></thead>
                <tbody>
                  {group.map((r, i) => (
                    <tr key={r.id}>
                      <td data-label="Order">
                        {i + 1}
                        {canManage && group.length > 1 && (
                          <span className="apr-order-tools">
                            <button type="button" className="btn btn-small" disabled={i === 0} onClick={() => swap(group[i - 1], r)} aria-label={`Move ${r.name} up`}>Up</button>
                            <button type="button" className="btn btn-small" disabled={i === group.length - 1} onClick={() => swap(r, group[i + 1])} aria-label={`Move ${r.name} down`}>Down</button>
                          </span>
                        )}
                      </td>
                      <td data-label="Rule"><strong>{r.name}</strong>{r.description && <div className="muted">{r.description}</div>}</td>
                      <td data-label="When">{conditionText(r)}</td>
                      <td data-label="Steps">
                        <ol className="apr-rule-steps">
                          {r.steps.map((s) => <li key={s.order}>{s.who}{s.mode === 'all' ? ' (all)' : ''}{s.escalate_after_days ? `, escalates after ${s.escalate_after_days} d` : ''}</li>)}
                        </ol>
                      </td>
                      <td data-label="Status"><Badge tone={r.is_active ? 'good' : 'neutral'}>{r.is_active ? 'On' : 'Off'}</Badge></td>
                      {canManage && (
                        <td data-label="Actions">
                          <div className="apr-row-actions">
                            <button type="button" className="btn btn-small" onClick={() => setEdit(r)}>Edit</button>
                            <button type="button" className="btn btn-small" onClick={() => toggle(r)}>{r.is_active ? 'Switch off' : 'Switch on'}</button>
                            <button type="button" className="btn btn-small btn-danger-outline" onClick={() => remove(r)}>Delete</button>
                          </div>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
      {edit && <RuleDialog rule={edit === 'new' ? null : edit} kinds={kinds} roles={roles} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); }} />}
      {confirmDialog}
    </>
  );
}
