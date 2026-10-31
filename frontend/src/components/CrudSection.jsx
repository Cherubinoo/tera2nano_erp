import { useMemo, useState } from 'react';
import { api } from '../api';
import { asList, useLoad } from '../hooks';
import { useLookups } from '../context';
import { changedLabels } from '../forms';
import {
  Badge, Dialog, ErrorBox, FormErrors, Loading, SpecForm, useConfirm, useReasonDialog, useReasonSubmit, useSpecForm, useToast,
} from '../ui';

function RecordDialog({ title, noun, endpoint, fields, record, defaults, ctx, crossCheck, onClose, onSaved, readOnly, createExtra }) {
  const lookups = useLookups();
  const notify = useToast();
  const { run, dialog: reasonDialog } = useReasonSubmit();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const form = useSpecForm(fields, record, defaults);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const isEdit = !!(record && record.id);
  const fullCtx = { lookups, ...ctx, isEdit, record };

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    if (!form.validate(crossCheck)) return;
    const payload = form.payload(isEdit);
    if (isEdit && Object.keys(payload).length === 0) { notify('No changes to save.', 'info'); onClose(); return; }
    if (isEdit) {
      const ok = await confirm({
        title: `Save changes to this ${noun}?`,
        body: (
          <>
            <p>These fields will be updated:</p>
            <ul>{changedLabels(fields, Object.keys(payload)).map((l) => <li key={l}>{l}</li>)}</ul>
          </>
        ),
        confirmLabel: 'Save changes',
      });
      if (!ok) return;
    }
    setBusy(true);
    try {
      const saved = await run((extra) => (isEdit
        ? api.patch(`${endpoint}${record.id}/`, { ...payload, ...extra })
        : api.post(endpoint, { ...createExtra, ...payload, ...extra })));
      notify(isEdit ? `${cap(noun)} updated.` : `${cap(noun)} added.`);
      onSaved(saved);
    } catch (err) {
      if (err.cancelled) { setBusy(false); return; }
      setError(err);
      form.setErrors(err.fieldErrors || {});
    }
    setBusy(false);
  };

  const close = async () => {
    if (form.dirty.length && !(await confirm({ title: 'Discard changes?', body: 'Your edits have not been saved.', confirmLabel: 'Discard', danger: true }))) return;
    onClose();
  };

  return (
    <Dialog title={title} onClose={close} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={fields} />
        <SpecForm fields={fields} values={form.values} onChange={form.set} errors={form.errors} readOnly={readOnly} ctx={fullCtx} idPrefix="rec" />
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={close}>{readOnly ? 'Close' : 'Cancel'}</button>
          {!readOnly && <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>}
        </div>
      </form>
      {reasonDialog}
      {confirmDialog}
    </Dialog>
  );
}

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// A list of records with add / edit / deactivate, backed by one API endpoint.
export default function CrudSection({
  title, noun, endpoint, fields, columns, canWrite, defaults, rowActions, ctx = {}, params, crossCheck,
  description, toolbar, isActive = (r) => r.is_active, supportsToggle = (r) => 'is_active' in r,
  deactivateReason = 'required', prepareRecord, emptyText, onChanged, sortFn, createExtra,
}) {
  const lookups = useLookups();
  const notify = useToast();
  const { ask, dialog: askDialog } = useReasonDialog();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { data, error, loading, reload } = useLoad(() => api.get(endpoint, params), [endpoint, JSON.stringify(params || {})]);
  const [editing, setEditing] = useState(null); // {record|null}
  const rows = useMemo(() => { const l = asList(data); return sortFn ? [...l].sort(sortFn) : l; }, [data, sortFn]);
  const refresh = () => { reload(); onChanged && onChanged(); };

  const toggle = async (row, active) => {
    const reason = await ask({
      title: `${active ? 'Reactivate' : 'Deactivate'} this ${noun}`,
      label: 'Reason', required: active ? false : deactivateReason === 'required',
      confirmLabel: active ? 'Reactivate' : 'Deactivate',
    });
    if (reason === null) return;
    try {
      await api.post(`${endpoint}${row.id}/${active ? 'activate' : 'deactivate'}/`, { change_reason: reason });
      notify(`${cap(noun)} ${active ? 'reactivated' : 'deactivated'}.`);
      refresh();
    } catch (e) { notify(e.message, 'error'); }
  };

  const actionCtx = { reload: refresh, ask, confirm, notify, canWrite, rows };

  return (
    <section className="card" aria-label={title}>
      <div className="card-head">
        <div>
          <h2>{title}</h2>
          {description && <p className="muted">{description}</p>}
        </div>
        <div className="page-actions">
          {toolbar}
          {canWrite && <button type="button" className="btn btn-primary" onClick={() => setEditing({ record: null })}>Add {noun}</button>}
        </div>
      </div>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {!loading && !error && rows.length === 0 && <p className="empty">{emptyText || `No ${noun} records yet.`}</p>}
      {rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>{columns.map((c) => <th key={c.key} scope="col">{c.label}</th>)}<th scope="col"><span className="sr-only">Actions</span></th></tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className={supportsToggle(row) && !isActive(row) ? 'row-inactive' : ''}>
                  {columns.map((c) => (
                    <td key={c.key} data-label={c.label}>
                      {c.render ? c.render(row, actionCtx, lookups) : (row[c.key] === null || row[c.key] === undefined || row[c.key] === '' ? '—' : String(row[c.key]))}
                    </td>
                  ))}
                  <td className="row-actions">
                    {canWrite && <button type="button" className="btn btn-small" onClick={() => setEditing({ record: prepareRecord ? prepareRecord(row) : row })}>Edit</button>}
                    {!canWrite && <button type="button" className="btn btn-small" onClick={() => setEditing({ record: prepareRecord ? prepareRecord(row) : row, readOnly: true })}>View</button>}
                    {canWrite && supportsToggle(row) && (isActive(row)
                      ? <button type="button" className="btn btn-small" onClick={() => toggle(row, false)}>Deactivate</button>
                      : <button type="button" className="btn btn-small" onClick={() => toggle(row, true)}>Reactivate</button>)}
                    {(rowActions ? rowActions(row, actionCtx) : []).map((a) => (
                      <button key={a.label} type="button" className={`btn btn-small${a.danger ? ' btn-danger-outline' : ''}`} onClick={() => a.onClick(row)}>{a.label}</button>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing && (
        <RecordDialog
          title={editing.readOnly ? `View ${noun}` : editing.record ? `Edit ${noun}` : `Add ${noun}`}
          noun={noun} endpoint={endpoint} fields={fields} record={editing.record} defaults={defaults} ctx={ctx}
          crossCheck={crossCheck} readOnly={editing.readOnly} createExtra={createExtra}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refresh(); }}
        />
      )}
      {askDialog}
      {confirmDialog}
    </section>
  );
}

export const ActiveBadge = ({ active }) => <Badge tone={active ? 'good' : 'neutral'}>{active ? 'Active' : 'Inactive'}</Badge>;
