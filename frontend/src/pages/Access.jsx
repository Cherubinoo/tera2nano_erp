import { Fragment, useMemo, useState } from 'react';
import { api } from '../api';
import { useAuth } from '../context';
import { asList, useLoad } from '../hooks';
import {
  Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, Tabs, fmtDateTime, useConfirm, useReasonDialog, useToast,
} from '../ui';

const can = (user, code) => (user.permissions || []).includes(code);
const OUTCOME_TONE = { success: 'good', failed: 'bad', inactive: 'warn', logout: 'neutral' };

// ----------------------------------------------------------------------------- users

function UserDialog({ record, roles, isSelf, onClose, onSaved }) {
  const notify = useToast();
  const isEdit = !!record;
  const [v, setV] = useState({
    username: record?.username || '', first_name: record?.first_name || '', last_name: record?.last_name || '',
    email: record?.email || '', role: record?.role || 'employee', password: '',
  });
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };

  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setError(null);
    const errs = {};
    if (!isEdit && !v.username.trim()) errs.username = 'Enter a username.';
    if (!isEdit && !v.password) errs.password = 'Enter a password.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      if (isEdit) {
        const body = { first_name: v.first_name, last_name: v.last_name, email: v.email };
        if (!isSelf) body.role = v.role;
        await api.patch(`/auth/users/${record.id}/`, body);
      } else {
        await api.post('/auth/users/', { ...v, username: v.username.trim() });
      }
      notify(isEdit ? 'User updated.' : 'User added.');
      onSaved();
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };

  return (
    <Dialog title={isEdit ? `Edit ${record.username}` : 'Add user'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[]} />
        <div className="form-grid">
          <Field id="u-username" label="Username" required={!isEdit} error={errors.username}>
            <input id="u-username" value={v.username} disabled={isEdit} maxLength={150} autoComplete="off" onChange={set('username')} />
          </Field>
          <Field id="u-role" label="Role" error={errors.role} hint={isSelf ? 'You cannot change your own role.' : undefined}>
            <select id="u-role" value={v.role} disabled={isSelf} onChange={set('role')}>
              {roles.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>
          </Field>
          <Field id="u-first" label="First name" error={errors.first_name}><input id="u-first" value={v.first_name} onChange={set('first_name')} /></Field>
          <Field id="u-last" label="Last name" error={errors.last_name}><input id="u-last" value={v.last_name} onChange={set('last_name')} /></Field>
          <Field id="u-email" label="Email" error={errors.email}><input id="u-email" type="email" value={v.email} onChange={set('email')} /></Field>
          {!isEdit && (
            <Field id="u-password" label="Password" required error={errors.password} hint="At least 10 characters; not a common or easily guessed password.">
              <input id="u-password" type="password" autoComplete="new-password" value={v.password} onChange={set('password')} />
            </Field>
          )}
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </Dialog>
  );
}

function PasswordDialog({ record, onClose }) {
  const notify = useToast();
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setBusy(true); setError(null);
    try {
      await api.post(`/auth/users/${record.id}/set-password/`, { password });
      notify('Password changed. The user must sign in again.');
      onClose();
    } catch (err) { setError(err); }
    setBusy(false);
  };
  return (
    <Dialog title={`Set a new password for ${record.username}`} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={[{ name: 'password' }]} />
        <Field id="pw-new" label="New password" required error={error?.fieldErrors?.password}
          hint="The user is signed out everywhere and must use this password next time.">
          <input id="pw-new" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy || !password}>{busy ? 'Saving…' : 'Set password'}</button>
        </div>
      </form>
    </Dialog>
  );
}

function UsersTab({ roles }) {
  const { user: me } = useAuth();
  const notify = useToast();
  const { ask, dialog: askDialog } = useReasonDialog();
  const { data, error, loading, reload } = useLoad(() => api.get('/auth/users/'), []);
  const [editing, setEditing] = useState(null); // {record|null}
  const [pwFor, setPwFor] = useState(null);
  const rows = asList(data);

  const toggle = async (u) => {
    const reason = await ask({
      title: `${u.is_active ? 'Switch off' : 'Switch on'} ${u.username}?`, required: false, label: 'Reason (optional)',
      message: u.is_active ? 'The user is signed out at once and cannot sign in until switched on again.' : undefined,
      confirmLabel: u.is_active ? 'Switch off' : 'Switch on',
    });
    if (reason === null) return;
    try {
      await api.patch(`/auth/users/${u.id}/`, { is_active: !u.is_active, change_reason: reason });
      notify(`${u.username} ${u.is_active ? 'switched off' : 'switched on'}.`);
      reload();
    } catch (e) { notify(e.fieldErrors?.is_active || e.message, 'error'); }
  };

  return (
    <section className="card" aria-label="Users">
      <div className="card-head">
        <div><h2>Users</h2><p className="muted">Everyone who can sign in, and the role that decides what they may do. Switching a user off signs them out at once.</p></div>
        <div className="page-actions"><button type="button" className="btn btn-primary" onClick={() => setEditing({ record: null })}>Add user</button></div>
      </div>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>Username</th><th>Role</th><th>Last sign-in</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((u) => (
                <tr key={u.id} className={u.is_active ? '' : 'row-inactive'}>
                  <td data-label="Name"><strong>{u.full_name}</strong><div className="hint">{u.email}</div></td>
                  <td data-label="Username"><code>{u.username}</code>{u.id === me.id && <> <Badge tone="info">You</Badge></>}</td>
                  <td data-label="Role">{u.role_label}</td>
                  <td data-label="Last sign-in">{fmtDateTime(u.last_login)}</td>
                  <td data-label="Status">{u.is_active ? <Badge tone="good">Active</Badge> : <Badge>Switched off</Badge>}</td>
                  <td className="row-actions">
                    <button type="button" className="btn btn-small" onClick={() => setEditing({ record: u })}>Edit</button>
                    <button type="button" className="btn btn-small" onClick={() => setPwFor(u)}>Set password</button>
                    {u.id !== me.id && <button type="button" className="btn btn-small" onClick={() => toggle(u)}>{u.is_active ? 'Switch off' : 'Switch on'}</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing && <UserDialog record={editing.record} roles={roles} isSelf={editing.record?.id === me.id}
        onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />}
      {pwFor && <PasswordDialog record={pwFor} onClose={() => setPwFor(null)} />}
      {askDialog}
    </section>
  );
}

// ----------------------------------------------------------------------------- permission matrix

function PermissionsTab({ matrixData, reloadMatrix }) {
  const notify = useToast();
  const { ask, dialog: askDialog } = useReasonDialog();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { roles, permissions, matrix, defaults } = matrixData;
  const [draft, setDraft] = useState(() => Object.fromEntries(Object.entries(matrix).map(([r, c]) => [r, new Set(c)])));
  const [busy, setBusy] = useState(false);
  const groups = useMemo(() => [...new Set(permissions.map((p) => p.group))], [permissions]);

  const same = (a, b) => a.size === b.size && [...a].every((x) => b.has(x));
  const changedRoles = roles.filter((r) => !r.locked && !same(draft[r.value], new Set(matrix[r.value])));
  const toggle = (role, code) => setDraft((d) => {
    const next = new Set(d[role]);
    if (next.has(code)) next.delete(code); else next.add(code);
    return { ...d, [role]: next };
  });
  const sync = (m) => setDraft(Object.fromEntries(Object.entries(m).map(([r, c]) => [r, new Set(c)])));

  const save = async () => {
    const reason = await ask({ title: 'Save permission changes?', label: 'Reason', confirmLabel: 'Save permissions',
      message: `Changes apply at once to: ${changedRoles.map((r) => r.label).join(', ')}.` });
    if (reason === null) return;
    setBusy(true);
    try {
      const body = Object.fromEntries(changedRoles.map((r) => [r.value, [...draft[r.value]]]));
      const res = await api.put('/auth/access/', { matrix: body, change_reason: reason });
      sync(res.matrix);
      notify('Permissions saved.');
      reloadMatrix();
    } catch (e) { notify(e.fieldErrors?.matrix || e.message, 'error'); }
    setBusy(false);
  };

  const reset = async () => {
    if (!(await confirm({ title: 'Reset every role to the defaults?', body: <p>All saved permission changes are discarded. This applies at once and is recorded in the history.</p>, confirmLabel: 'Reset', danger: true }))) return;
    const reason = await ask({ title: 'Reason for resetting', confirmLabel: 'Reset permissions' });
    if (reason === null) return;
    setBusy(true);
    try {
      const res = await api.post('/auth/access/reset/', { change_reason: reason });
      sync(res.matrix);
      notify('Permissions reset to the defaults.');
      reloadMatrix();
    } catch (e) { notify(e.message, 'error'); }
    setBusy(false);
  };

  return (
    <section className="card" aria-label="Permissions">
      <div className="card-head">
        <div>
          <h2>Role permissions</h2>
          <p className="muted">Tick what each role may do. Changes apply at once. The Admin role always holds everything, so you cannot lock yourself out.</p>
        </div>
        <div className="page-actions">
          <button type="button" className="btn" disabled={busy} onClick={reset}>Reset to defaults</button>
          <button type="button" className="btn btn-primary" disabled={busy || !changedRoles.length} onClick={save}>Save changes</button>
        </div>
      </div>
      <div className="table-wrap">
        <table className="matrix">
          <thead>
            <tr><th scope="col">Permission</th>{roles.map((r) => <th key={r.value} scope="col" className="num">{r.label}</th>)}</tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <Fragment key={g}>
                <tr className="group-row"><th colSpan={roles.length + 1} scope="colgroup">{g}</th></tr>
                {permissions.filter((p) => p.group === g).map((p) => (
                  <tr key={p.code}>
                    <th scope="row" data-label="Permission"><span>{p.label}</span><div className="hint"><code>{p.code}</code></div></th>
                    {roles.map((r) => {
                      const on = draft[r.value].has(p.code);
                      const isDefault = (defaults[r.value] || []).includes(p.code) === on;
                      return (
                        <td key={r.value} className="num" data-label={r.label}>
                          <input type="checkbox" checked={on} disabled={r.locked || busy}
                            aria-label={`${r.label}: ${p.label}`} title={r.locked ? 'Admin always holds every permission' : isDefault ? '' : 'Differs from the default'}
                            onChange={() => toggle(r.value, p.code)} />
                          {!isDefault && !r.locked && <span className="hint" aria-hidden="true"> ●</span>}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">● marks a setting that differs from the default for that role.</p>
      {askDialog}
      {confirmDialog}
    </section>
  );
}

// ----------------------------------------------------------------------------- history

function HistoryTab() {
  const [q, setQ] = useState('');
  const [outcome, setOutcome] = useState('');
  const [term, setTerm] = useState('');
  const { data, error, loading, reload } = useLoad(() => api.get('/auth/login-history/', { user: term || undefined, outcome: outcome || undefined, limit: 300 }), [term, outcome]);
  const rows = asList(data);
  return (
    <section className="card" aria-label="Login history">
      <div className="card-head"><div><h2>Login history</h2><p className="muted">Every sign-in, failed attempt and sign-out, newest first. A name that is not a real user is never stored.</p></div></div>
      <form className="inline-form" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
        <Field id="lh-user" label="Username"><input id="lh-user" value={q} onChange={(e) => setQ(e.target.value)} /></Field>
        <Field id="lh-outcome" label="Result">
          <select id="lh-outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option value="">All</option><option value="success">Signed in</option><option value="failed">Wrong username or password</option>
            <option value="inactive">Account switched off</option><option value="logout">Signed out</option>
          </select>
        </Field>
        <button type="submit" className="btn">Search</button>
      </form>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {!loading && !rows.length && <p className="empty">Nothing recorded yet.</p>}
      {rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>When</th><th>User</th><th>Result</th><th>IP address</th><th>Browser</th></tr></thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id}>
                  <td data-label="When">{fmtDateTime(e.occurred_at)}</td>
                  <td data-label="User">{e.username}</td>
                  <td data-label="Result"><Badge tone={OUTCOME_TONE[e.outcome]}>{e.outcome_label}</Badge></td>
                  <td data-label="IP address"><code>{e.ip_address || '—'}</code></td>
                  <td data-label="Browser" className="hint">{e.user_agent || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const ACTION_LABEL = { created: 'Added', updated: 'Changed', activated: 'Switched on', deactivated: 'Switched off' };
const summarize = (e) => {
  if (e.entity_type === 'access_matrix') return `Permissions changed for: ${e.changed_fields.join(', ') || '—'}`;
  return e.changed_fields.length
    ? e.changed_fields.map((f) => `${f}: ${e.previous_values[f] ?? '—'} → ${e.new_values[f] ?? '—'}`).join('; ')
    : `${e.new_values.username || ''} ${e.new_values.role ? `(${e.new_values.role})` : ''}`.trim();
};

function ActivityTab() {
  const { data, error, loading, reload } = useLoad(() => api.get('/auth/activity/'), []);
  const rows = asList(data);
  return (
    <section className="card" aria-label="Activity">
      <div className="card-head"><div><h2>User and permission changes</h2><p className="muted">Who added, changed or switched off users, and who changed role permissions. Also part of the company change history.</p></div></div>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {!loading && !rows.length && <p className="empty">No user or permission changes yet.</p>}
      {rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>When</th><th>By</th><th>What</th><th>Details</th><th>Reason</th></tr></thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id}>
                  <td data-label="When">{fmtDateTime(e.occurred_at)}</td>
                  <td data-label="By">{e.actor || '—'}</td>
                  <td data-label="What">{e.entity_type === 'access_matrix' ? 'Permissions' : `User · ${ACTION_LABEL[e.action] || e.action}`}</td>
                  <td data-label="Details">{summarize(e)}</td>
                  <td data-label="Reason">{e.reason || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ----------------------------------------------------------------------------- page

export function AccessManager() {
  const { user } = useAuth();
  const manage = can(user, 'users.manage');
  const watch = can(user, 'users.view_activity');
  const tabs = [
    ...(manage ? [{ key: 'users', label: 'Users' }, { key: 'permissions', label: 'Role permissions' }] : []),
    ...(watch ? [{ key: 'history', label: 'Login history' }, { key: 'activity', label: 'Changes' }] : []),
  ];
  const [tab, setTab] = useState(tabs[0]?.key);
  const { data: matrixData, error, loading, reload } = useLoad(() => (manage ? api.get('/auth/access/') : Promise.resolve(null)), [manage]);

  if (!tabs.length) return <Notice type="warn">Your role does not include user management or login history.</Notice>;
  if (manage && loading && !matrixData) return <Loading />;
  if (manage && error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <>
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {tab === 'users' && <UsersTab roles={matrixData.roles} />}
      {tab === 'permissions' && <PermissionsTab key={JSON.stringify(matrixData.matrix)} matrixData={matrixData} reloadMatrix={reload} />}
      {tab === 'history' && <HistoryTab />}
      {tab === 'activity' && <ActivityTab />}
    </>
  );
}
