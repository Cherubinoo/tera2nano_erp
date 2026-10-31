import { useMemo, useState } from 'react';
import { api } from '../api';
import CrudSection, { ActiveBadge } from '../components/CrudSection';
import { useAuth } from '../context';
import { localToday } from '../forms';
import { asList, useLoad } from '../hooks';
import { OPERATION_FIELDS } from '../specs';
import { ErrorBox, Field, FormErrors, Loading, Notice, useConfirm, useToast } from '../ui';
import { Downloads, IssuedList } from './Letters';
import { Rows } from './Minutes';

const blankDetail = () => ({ label: '', value: '' });
const blankSection = () => ({ heading: '', body: '' });
const paragraphs = (text) => text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);

// Pick an operation, paste or write the content, add sections, issue it with the operation's own ID, download.
export function OperationsManager() {
  const { user } = useAuth();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [catalogueKey, setCatalogueKey] = useState(0);
  const { data, error, loading, reload } = useLoad(
    () => api.get('/company/letterhead-templates/', { kind: 'operation' }), [catalogueKey]);
  const operations = useMemo(() => asList(data), [data]);
  const active = useMemo(() => operations.filter((o) => o.is_active), [operations]);
  const [selected, setSelected] = useState('');
  const chosen = active.find((o) => o.code === selected) || null;

  const [values, setValues] = useState({ issue_date: localToday(), title: '', body: '' });
  const [details, setDetails] = useState([blankDetail()]);
  const [sections, setSections] = useState([blankSection()]);
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const set = (name) => (e) => { setValues((v) => ({ ...v, [name]: e.target.value })); setErrors((x) => ({ ...x, [name]: undefined })); };

  const pick = (code) => {
    setSelected(code);
    setIssued(null);
    setFailure(null);
    setErrors({});
  };

  const submit = async (e) => {
    e.preventDefault();
    setFailure(null);
    const content = {};
    const meta = details.filter((d) => d.label.trim() && d.value.trim()).map((d) => [d.label.trim(), d.value.trim()]);
    const paras = paragraphs(values.body);
    const secs = sections.filter((s) => s.heading.trim() || s.body.trim())
      .map((s) => ({ heading: s.heading.trim(), body: s.body.trim() }));
    if (meta.length) content.meta = meta;
    if (paras.length) content.paragraphs = paras;
    if (secs.length) content.sections = secs;
    const errs = {};
    if (!values.issue_date) errs.issue_date = 'Choose the document date.';
    if (!paras.length && !secs.length) errs.body = 'Paste the content, or add at least one section.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    const ok = await confirm({
      title: 'Issue this document?',
      body: (
        <>
          <p>A unique ID is allocated now from <strong>{chosen.code}</strong> and the document is saved exactly as it appears. An issued document cannot be edited; if it is wrong, void it and issue a new one.</p>
          <p>Operation: <strong>{chosen.name}</strong></p>
        </>
      ),
      confirmLabel: 'Issue document',
    });
    if (!ok) return;
    setBusy(true);
    try {
      const doc = await api.post('/company/issued-documents/', {
        document_type: 'operation_document', letterhead_template: chosen.code, issue_date: values.issue_date,
        title: values.title.trim(), content,
      });
      setIssued(doc);
      setRefreshKey((k) => k + 1);
      setCatalogueKey((k) => k + 1); // the operation's next reference has moved on
      setValues((v) => ({ ...v, title: '', body: '' }));
      setDetails([blankDetail()]);
      setSections([blankSection()]);
      notify(`Document issued: ${doc.reference_number}`);
    } catch (err) {
      setFailure(err);
      setErrors(err.fieldErrors || {});
    }
    setBusy(false);
  };

  return (
    <>
      <section className="card" aria-label="Choose an operation">
        <div className="card-head">
          <div>
            <h2>1. Choose the operation</h2>
            <p className="muted">Every operation has its own ID. A document's reference is that ID, the two-digit year, the date and a running number, e.g. OPS-001/26/2710/00001.</p>
          </div>
        </div>
        {loading && !data && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && !active.length && <p className="empty">No operations are active. Add one under "Manage operations" below.</p>}
        <div className="op-grid" role="group" aria-label="Operations">
          {active.map((o) => (
            <button key={o.id} type="button" className={`op-card${o.code === selected ? ' selected' : ''}`}
              aria-pressed={o.code === selected} onClick={() => pick(o.code)}>
              <code>{o.code}</code>
              <strong>{o.name}</strong>
              <span className="hint">Next: {o.next_reference_preview}</span>
            </button>
          ))}
        </div>
      </section>

      {chosen && (
        <form className="card" onSubmit={submit} noValidate>
          <div className="card-head">
            <div>
              <h2>2. {chosen.name}</h2>
              <p className="muted">Paste the content, add sections and details, then issue. You get the ID, a PDF with the letterhead and a plain PDF for your pre-printed paper.</p>
            </div>
          </div>
          <FormErrors error={failure} fields={[]} />
          <div className="form-grid">
            <Field id="op-title" label="Heading" error={errors.title} hint={chosen.default_title ? `Leave empty to print "${chosen.default_title}".` : 'Leave empty to print the operation name.'}>
              <input id="op-title" value={values.title} maxLength={200} onChange={set('title')} placeholder={chosen.default_title || chosen.name} />
            </Field>
            <Field id="op-date" label="Document date" required error={errors.issue_date}>
              <input id="op-date" type="date" value={values.issue_date} onChange={set('issue_date')} />
            </Field>
          </div>

          <Rows title="Details (optional)" rows={details} setRows={setDetails} blank={blankDetail} noun="detail" minRows={1}
            hint="Shown under the reference number, e.g. Project: Alpha, Location: Chennai. Rows without both parts are ignored."
            columns={[{ key: 'label', label: 'Detail' }, { key: 'value', label: 'Value' }]} />

          <Field id="op-body" label="Content" error={errors.body} wide hint="Paste your text here. A blank line starts a new paragraph; single line breaks are kept.">
            <textarea id="op-body" rows={10} value={values.body} onChange={set('body')} />
          </Field>

          <Rows title="Sections (optional)" rows={sections} setRows={setSections} blank={blankSection} noun="section" minRows={1}
            hint="Each section prints with its heading, after the content above."
            columns={[
              { key: 'heading', label: 'Section heading', wide: true },
              { key: 'body', label: 'Section text', type: 'textarea', rows: 5, wide: true },
            ]} />

          <div className="actions">
            <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Issuing…' : 'Issue document'}</button>
          </div>
        </form>
      )}

      {issued && (
        <Notice type="success">
          <p><strong>Document issued.</strong> ID: <code>{issued.reference_number}</code></p>
          <div className="row-actions"><Downloads doc={issued} onError={(e) => notify(e.message, 'error')} /></div>
          <p className="hint">Use "Print PDF" on pre-printed letterhead paper, or "Letterhead PDF" to print or send on plain paper.</p>
        </Notice>
      )}

      <IssuedList refreshKey={refreshKey} types={['operation_document']} title="Issued operations documents"
        emptyText="No operations documents issued yet." templateKind="operation" templateLabel="Operation" searchHint="OPS-001/26/…"
        templateOptions={operations.map((o) => ({ value: o.code, label: `${o.code} ${o.name}` }))} />

      <CrudSection
        title="Manage operations" noun="operation" endpoint="/company/letterhead-templates/" fields={OPERATION_FIELDS}
        params={{ kind: 'operation', _r: catalogueKey }} createExtra={{ kind: 'operation' }}
        defaults={{ is_active: true, sort_order: 100 }} canWrite={user.is_company_admin}
        onChanged={() => setCatalogueKey((k) => k + 1)}
        description="Add, rename or switch off operations. An operation's ID cannot change once documents have been issued on it."
        columns={[
          { key: 'code', label: 'Operation ID', render: (r) => <code>{r.code}</code> },
          { key: 'name', label: 'Name', render: (r) => <strong>{r.name}</strong> },
          { key: 'default_title', label: 'Heading' },
          { key: 'next_reference_preview', label: 'Next reference', render: (r) => <code>{r.next_reference_preview}</code> },
          { key: 'issued_count', label: 'Issued' },
          { key: 'is_active', label: 'Status', render: (r) => <ActiveBadge active={r.is_active} /> },
        ]}
      />
      {confirmDialog}
    </>
  );
}
