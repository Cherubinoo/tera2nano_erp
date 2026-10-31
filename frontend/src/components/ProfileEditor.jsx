import { useMemo, useState } from 'react';
import { api } from '../api';
import { asList, useLoad } from '../hooks';
import { useAuth, useCompany, useLookups } from '../context';
import { changedLabels } from '../forms';
import { ErrorBox, FormErrors, Loading, SpecForm, useConfirm, useReasonSubmit, useSpecForm, useToast, Notice } from '../ui';

// Edits a group of company-profile fields with PATCH /company/profile/. Only changed fields are sent.
export default function ProfileEditor({ fields, children, saveLabel = 'Save', onSaved, confirmSave = true }) {
  const { user } = useAuth();
  const { profile, loading, error, reload } = useCompany();
  const lookups = useLookups();
  const notify = useToast();
  const { run, dialog: reasonDialog } = useReasonSubmit();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const canWrite = user.is_company_admin;
  const needsDocs = fields.some((f) => f.name === 'incorporation_certificate');
  const docs = useLoad(() => (needsDocs ? api.get('/company/documents/', { document_type: 'incorporation_certificate' }) : Promise.resolve([])), [needsDocs]);
  const incorporationDocs = useMemo(() => asList(docs.data).map((d) => ({ value: d.id, label: `${d.title} (v${d.version})` })), [docs.data]);
  const form = useSpecForm(fields, profile);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState(null);

  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!profile) return null;

  const save = async (e) => {
    e.preventDefault();
    setSaveError(null);
    if (!form.validate()) return;
    const payload = form.payload(true);
    if (!Object.keys(payload).length) { notify('No changes to save.', 'info'); return; }
    if (confirmSave) {
      const ok = await confirm({
        title: 'Save company changes?',
        body: (
          <>
            <p>This updates the company record that every quotation, invoice and letter reads from. Changed fields:</p>
            <ul>{changedLabels(fields, Object.keys(payload)).map((l) => <li key={l}>{l}</li>)}</ul>
          </>
        ),
        confirmLabel: 'Save changes',
      });
      if (!ok) return;
    }
    setBusy(true);
    try {
      await run((extra) => api.patch('/company/profile/', { ...payload, ...extra }));
      await reload();
      notify('Company details saved.');
      onSaved && onSaved();
    } catch (err) {
      if (!err.cancelled) { setSaveError(err); form.setErrors(err.fieldErrors || {}); }
    }
    setBusy(false);
  };

  return (
    <form onSubmit={save} noValidate className="card">
      {!canWrite && <Notice>You can view these details but only company administrators can change them.</Notice>}
      <FormErrors error={saveError} fields={fields} />
      <SpecForm fields={fields} values={form.values} onChange={form.set} errors={form.errors} readOnly={!canWrite}
        ctx={{ lookups, incorporationDocs }} idPrefix="profile" />
      {children}
      {canWrite && (
        <div className="actions">
          <button type="submit" className="btn btn-primary" disabled={busy || !form.dirty.length}>{busy ? 'Saving…' : saveLabel}</button>
          <button type="button" className="btn" onClick={form.reset} disabled={busy || !form.dirty.length}>Cancel</button>
        </div>
      )}
      {reasonDialog}
      {confirmDialog}
    </form>
  );
}
