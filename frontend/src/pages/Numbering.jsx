import { useMemo, useState } from 'react';
import { api } from '../api';
import { useCompany, useAuth, useLookups } from '../context';
import { changedLabels } from '../forms';
import { useLoad, asList } from '../hooks';
import { SEQUENCE_FIELDS } from '../specs';
import {
  Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, SpecForm, fmtDateTime, useConfirm, useReasonSubmit, useSpecForm, useToast,
} from '../ui';

const SEQ_DEFAULTS = {
  separator: '/', min_length: 5, reset_frequency: 'never', next_value: 1, is_active: true, include_financial_year: false,
  include_short_year: true, date_format: 'ddmm', // every ID carries the 2-digit year and the date
};

function SequenceDialog({ record, unconfigured, lookups, onClose, onSaved }) {
  const notify = useToast();
  const { run, dialog: reasonDialog } = useReasonSubmit();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const isEdit = !!record;
  const options = useMemo(() => (isEdit ? lookups.document_types : unconfigured), [isEdit, lookups, unconfigured]);
  const form = useSpecForm(SEQUENCE_FIELDS, record, SEQ_DEFAULTS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const minNext = record?.minimum_next_value || 1;
  const ctx = { lookups, isEdit, sequenceTypeOptions: options, minNext };

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    const cross = (v) => (isEdit && Number(v.next_value) < minNext
      ? { next_value: `Cannot be lower than ${minNext}; that would reuse an issued number.` } : {});
    if (!form.validate(cross)) return;
    const payload = form.payload(isEdit);
    const docType = isEdit ? record.document_type : form.values.document_type;
    if (isEdit && !Object.keys(payload).length) { notify('No changes to save.', 'info'); onClose(); return; }
    if (isEdit) {
      const ok = await confirm({
        title: 'Change this numbering rule?',
        body: (
          <>
            <p>Numbers already issued are never changed or reused. The rule changes for numbers issued from now on:</p>
            <ul>{changedLabels(SEQUENCE_FIELDS, Object.keys(payload)).map((l) => <li key={l}>{l}</li>)}</ul>
          </>
        ),
        confirmLabel: 'Save rule',
      });
      if (!ok) return;
    }
    setBusy(true);
    try {
      await run((extra) => api.patch('/company/document-numbering/', { sequences: [{ ...payload, document_type: docType }], ...extra }));
      notify('Numbering rule saved.');
      onSaved();
    } catch (err) {
      if (!err.cancelled) {
        setError(err);
        const mapped = {};
        Object.entries(err.fieldErrors || {}).forEach(([k, v]) => { mapped[k.startsWith(`${docType}.`) ? k.slice(docType.length + 1) : k] = v; });
        form.setErrors(mapped);
      }
    }
    setBusy(false);
  };

  return (
    <Dialog title={isEdit ? `Edit numbering – ${record.document_type_label}` : 'Add numbering sequence'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={error} fields={SEQUENCE_FIELDS} />
        {isEdit && <Notice>Next value cannot go below {minNext} (the lowest value that cannot clash with an issued number).</Notice>}
        <SpecForm fields={SEQUENCE_FIELDS} values={form.values} onChange={form.set} errors={form.errors} ctx={ctx} idPrefix="seq" />
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
      {reasonDialog}
      {confirmDialog}
    </Dialog>
  );
}

function IssuedDialog({ seq, onClose }) {
  const { data, error, loading } = useLoad(() => api.get(`/company/document-numbering/${seq.id}/issued/`), [seq.id]);
  const rows = asList(data);
  return (
    <Dialog title={`Recent numbers – ${seq.document_type_label}`} onClose={onClose} wide>
      {loading && <Loading />}
      <ErrorBox error={error} />
      {!loading && !rows.length && <p className="empty">No numbers have been issued from this sequence yet.</p>}
      {rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Reference number</th><th>Status</th><th>Issued</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td><code>{r.reference_number}</code></td>
                  <td>{r.status === 'void' ? <Badge tone="bad">Void</Badge> : <Badge tone="good">Issued</Badge>}{r.void_reason ? ` – ${r.void_reason}` : ''}</td>
                  <td>{fmtDateTime(r.allocated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="dialog-foot-inline"><button type="button" className="btn" onClick={onClose}>Close</button></div>
    </Dialog>
  );
}

export function NumberingManager() {
  const { user } = useAuth();
  const lookups = useLookups();
  const company = useCompany();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const canWrite = user.is_company_admin;
  const { data, error, loading, reload } = useLoad(() => api.get('/company/document-numbering/'), []);
  const [editing, setEditing] = useState(null); // {record|null}
  const [issuedFor, setIssuedFor] = useState(null);
  const [shortCode, setShortCode] = useState(company.profile?.short_code || '');
  const [codeError, setCodeError] = useState('');
  const [seeding, setSeeding] = useState(false);

  const rows = data?.results || [];
  const unconfigured = data?.unconfigured || [];
  const letterRows = data?.letterhead_sequences || [];

  const seed = async () => {
    const code = shortCode.trim().toUpperCase();
    if (!/^[A-Z0-9]{1,12}$/.test(code)) { setCodeError('Enter a short code of letters and digits, e.g. T2N.'); return; }
    setCodeError('');
    const ok = await confirm({
      title: 'Create default numbering sequences?',
      body: `A sequence will be created for each of the ${unconfigured.length} document types that has none, using the prefix "${code}". Existing sequences are not touched.`,
      confirmLabel: 'Create sequences',
    });
    if (!ok) return;
    setSeeding(true);
    try {
      const res = await api.post('/company/document-numbering/seed/', { short_code: code });
      notify(`${res.created.length} sequence(s) created.`);
      reload();
      company.reload();
    } catch (e) {
      setCodeError(e.fieldErrors?.short_code || e.message);
    }
    setSeeding(false);
  };

  return (
    <section className="card" aria-label="Document numbering">
      <div className="card-head">
        <div>
          <h2>Numbering sequences</h2>
          <p className="muted">Numbers are allocated by the server and never reused. Changes affect only numbers issued from now on.</p>
        </div>
        {canWrite && unconfigured.length > 0 && (
          <button type="button" className="btn btn-primary" onClick={() => setEditing({ record: null })}>Add sequence</button>
        )}
      </div>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Document</th><th>Next number</th><th>Prefix</th><th>FY segment</th><th>Reset</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className={r.is_active ? '' : 'row-inactive'}>
                  <td data-label="Document"><strong>{r.document_type_label}</strong></td>
                  <td data-label="Next number">
                    <code>{r.next_reference_preview}</code>
                    {r.warning && <div className="hint" role="note"><Badge tone="warn">Check</Badge> {r.warning}</div>}
                  </td>
                  <td data-label="Prefix">{r.prefix}</td>
                  <td data-label="FY segment">{r.include_financial_year ? 'Yes' : 'No'}</td>
                  <td data-label="Reset">{lookups.reset_frequencies.find((x) => x.value === r.reset_frequency)?.label}</td>
                  <td data-label="Status">{r.is_active ? <Badge tone="good">Active</Badge> : <Badge>Off</Badge>}</td>
                  <td className="row-actions">
                    {canWrite && <button type="button" className="btn btn-small" onClick={() => setEditing({ record: r })}>Edit</button>}
                    <button type="button" className="btn btn-small" onClick={() => setIssuedFor(r)}>Recent numbers</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!loading && !rows.length && <p className="empty">No numbering sequences yet.</p>}
      {letterRows.length > 0 && (
        <>
          <h3>Letterhead template sequences</h3>
          <p className="muted">Reference = template ID / two-digit year / date / running number. Each template has its own running number, which restarts at 00001 every year.</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Template</th><th>Next reference</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {letterRows.map((r) => (
                  <tr key={r.id} className={r.is_active ? '' : 'row-inactive'}>
                    <td data-label="Template"><strong>{r.letterhead_template_code}</strong></td>
                    <td data-label="Next reference"><code>{r.next_reference_preview}</code></td>
                    <td data-label="Status">{r.is_active ? <Badge tone="good">Active</Badge> : <Badge>Off</Badge>}</td>
                    <td className="row-actions">
                      {canWrite && <button type="button" className="btn btn-small" onClick={() => setEditing({ record: r })}>Edit format</button>}
                      <button type="button" className="btn btn-small" onClick={() => setIssuedFor(r)}>Recent numbers</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {unconfigured.length > 0 && (
        <p className="hint">Not configured: {unconfigured.map((u) => u.label).join(', ')}.</p>
      )}

      {canWrite && unconfigured.length > 0 && (
        <div className="subcard">
          <h3>Create the missing sequences with sensible defaults</h3>
          <p className="muted">Prefixes are built from your short company code, for example T2N/INV/2026-27/00001. You can edit each rule afterwards.</p>
          <div className="inline-form">
            <Field id="seed-code" label="Short company code" error={codeError}>
              <input id="seed-code" value={shortCode} maxLength={12} onChange={(e) => setShortCode(e.target.value.toUpperCase())}
                aria-describedby={codeError ? 'seed-code-err' : undefined} aria-invalid={!!codeError || undefined} />
            </Field>
            <button type="button" className="btn" onClick={seed} disabled={seeding}>{seeding ? 'Creating…' : 'Create missing sequences'}</button>
          </div>
        </div>
      )}

      {editing && (
        <SequenceDialog record={editing.record} unconfigured={unconfigured} lookups={lookups}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />
      )}
      {issuedFor && <IssuedDialog seq={issuedFor} onClose={() => setIssuedFor(null)} />}
      {confirmDialog}
    </section>
  );
}
