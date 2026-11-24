import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, downloadFile } from '../api';
import { EmailButton } from '../components/Email';
import { useAuth, useLookups } from '../context';
import { localToday } from '../forms';
import { asList, useLoad } from '../hooks';
import {
  Badge, ErrorBox, Field, FormErrors, Loading, Notice, fmtDate, useConfirm, useReasonDialog, useToast,
} from '../ui';

// Documents written as free text. Invoices and quotations are produced by their own modules.
const LETTER_TYPES = [
  'business_letter', 'offer_letter', 'appointment_letter', 'experience_letter', 'handover_letter',
  'completion_certificate',
];

const today = localToday;
const lines = (text) => text.split('\n').map((l) => l.trim()).filter(Boolean);
const paragraphs = (text) => text.split(/\n\s*\n/).map((p) => p.replace(/\s*\n\s*/g, ' ').trim()).filter(Boolean);

export function Downloads({ doc, onError }) {
  const stem = (doc.reference_number || doc.id).replace(/\//g, '-');
  const get = (suffix, qs) => downloadFile(`/company/issued-documents/${doc.id}/download/${qs}`, `${stem}${suffix}.pdf`).catch(onError);
  const open = async (qs) => {
    try {
      const { blob } = await api.blob(`/company/issued-documents/${doc.id}/download/${qs}`, { fallbackName: 'letter.pdf' });
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank', 'noopener');
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) { onError(e); }
  };
  return (
    <>
      <button type="button" className="btn btn-small" onClick={() => get('', '')}>Letterhead PDF</button>
      {doc.has_print_file && (
        <>
          <button type="button" className="btn btn-small" onClick={() => get('-print', '?variant=print')}
            title="Without the letterhead artwork, for printing on pre-printed official letterhead paper">Print PDF</button>
          <button type="button" className="btn btn-small" onClick={() => open('?variant=print&inline=true')}>Open to print</button>
        </>
      )}
      {EMAILABLE.includes(doc.document_type) && doc.status !== 'void' && (
        <EmailButton kind="issued_document" id={doc.id} perm="documents.issue" small
          title={`Email ${doc.document_type === 'quotation' ? 'quotation' : 'document'} ${doc.reference_number || ''}`.trim()} />
      )}
    </>
  );
}

// Issued documents that can be emailed from their list (with the letterhead PDF attached). Invoices, purchase orders and
// payslips are emailed from their own pages.
const EMAILABLE = ['quotation', ...LETTER_TYPES, 'meeting_minutes', 'operation_document', 'circular'];

export function IssuedList({
  refreshKey, onVoided, types = LETTER_TYPES, title = 'Issued letters', emptyText = 'No letters issued yet.', showMeeting = false,
  templateKind = 'letter', templateLabel = 'Template', searchHint = 'LHT-002/26/…', templateOptions, showTemplates = true,
}) {
  const { user } = useAuth();
  const notify = useToast();
  const lookups = useLookups();
  const { ask, dialog } = useReasonDialog();
  const [q, setQ] = useState('');
  const [template, setTemplate] = useState('');
  const [term, setTerm] = useState('');
  const { data, error, loading, reload } = useLoad(
    () => api.get('/company/issued-documents/', { q: term || undefined, template: template || undefined, document_type: types.length === 1 ? types[0] : undefined }),
    [term, template, refreshKey, types.join(',')],
  );
  const rows = asList(data).filter((d) => types.includes(d.document_type));
  const onError = (e) => notify(e.message, 'error');

  const voidDoc = async (d) => {
    const reason = await ask({ title: `Void ${d.reference_number}?`, message: 'The document and its ID are kept but marked void. The ID is never reused.', required: true, confirmLabel: 'Void document' });
    if (reason === null) return;
    try {
      await api.post(`/company/issued-documents/${d.id}/void/`, { change_reason: reason });
      notify(`${d.reference_number} marked void.`);
      reload();
      onVoided && onVoided();
    } catch (e) { onError(e); }
  };

  return (
    <section className="card" aria-label={title}>
      <div className="card-head"><h2>{title}</h2></div>
      <form className="inline-form" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
        <Field id="lt-search" label="Search by ID or heading">
          <input id="lt-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={searchHint} />
        </Field>
        {showTemplates && (
          <Field id="lt-filter" label={templateLabel}>
            <select id="lt-filter" value={template} onChange={(e) => setTemplate(e.target.value)}>
              <option value="">{templateKind === 'operation' ? 'All operations' : 'All templates'}</option>
              {(templateOptions || (lookups.letterhead_templates || []).filter((t) => (t.kind || 'letter') === templateKind))
                .map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </Field>
        )}
        <button type="submit" className="btn">Search</button>
      </form>
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {!loading && !rows.length && <p className="empty">{emptyText}</p>}
      {rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>ID</th>{showMeeting && <th>Meeting ID</th>}<th>Heading</th>{showTemplates && <th>Template</th>}<th>Date</th><th>Issued by</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.id} className={d.status === 'void' ? 'row-inactive' : ''}>
                  <td data-label="ID"><code>{d.reference_number}</code></td>
                  {showMeeting && <td data-label="Meeting ID"><code>{d.source_id || '—'}</code></td>}
                  <td data-label="Heading"><strong>{d.title}</strong></td>
                  {showTemplates && <td data-label="Template">{d.letterhead_template_code || '—'}</td>}
                  <td data-label="Date">{fmtDate(d.issue_date)}</td>
                  <td data-label="Issued by">{d.generated_by_name}</td>
                  <td data-label="Status">{d.status === 'issued' ? <Badge tone="good">Issued</Badge> : <Badge tone="bad">{d.status}</Badge>}</td>
                  <td className="row-actions">
                    <Downloads doc={d} onError={onError} />
                    {d.document_type === 'quotation' && d.status === 'issued' && user.permissions.includes('sales.manage') && (
                      <Link className="btn btn-small" to={`/sales/documents/new?kind=invoice&quotation=${d.id}`}>Create invoice</Link>
                    )}
                    {user.is_company_admin && d.status === 'issued' && (
                      <button type="button" className="btn btn-small btn-danger" onClick={() => voidDoc(d)}>Void</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {dialog}
    </section>
  );
}

export function LettersManager() {
  const lookups = useLookups();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const templates = useMemo(() => (lookups.letterhead_templates || []).filter((t) => t.is_active && (t.kind || 'letter') === 'letter'), [lookups]);
  const [values, setValues] = useState({
    document_type: 'business_letter', letterhead_template: '', issue_date: today(), title: '', recipient: '', subject: '', body: '',
  });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const chosen = values.letterhead_template;
  const template = chosen === 'none' ? '' : (chosen || templates[0]?.value || '');
  const set = (name) => (e) => { setValues((v) => ({ ...v, [name]: e.target.value })); setErrors((x) => ({ ...x, [name]: undefined })); };

  const submit = async (e) => {
    e.preventDefault();
    setFailure(null);
    const errs = {};
    if (!values.issue_date) errs.issue_date = 'Choose the letter date.';
    if (!paragraphs(values.body).length) errs.body = 'Write the body of the letter.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    const ok = await confirm({
      title: 'Issue this letter?',
      body: (
        <>
          <p>A unique ID is allocated now and the letter is saved exactly as it appears. An issued letter cannot be edited; if it is wrong, void it and issue a new one.</p>
          <p>Template: <strong>{templates.find((t) => t.value === template)?.label || 'standard numbering'}</strong></p>
        </>
      ),
      confirmLabel: 'Issue letter',
    });
    if (!ok) return;
    setBusy(true);
    try {
      const content = { paragraphs: paragraphs(values.body) };
      if (lines(values.recipient).length) content.recipient = lines(values.recipient);
      if (values.subject.trim()) content.subject = values.subject.trim();
      const doc = await api.post('/company/issued-documents/', {
        document_type: values.document_type, issue_date: values.issue_date, title: values.title.trim(), content,
        ...(template ? { letterhead_template: template } : {}),
      });
      setIssued(doc);
      setRefreshKey((k) => k + 1);
      setValues((v) => ({ ...v, title: '', recipient: '', subject: '', body: '' }));
      notify(`Letter issued: ${doc.reference_number}`);
    } catch (err) {
      setFailure(err);
      setErrors(err.fieldErrors || {});
    }
    setBusy(false);
  };

  return (
    <>
      <form className="card" onSubmit={submit} noValidate>
        <div className="card-head">
          <div>
            <h2>New letter</h2>
            <p className="muted">Pick a template, write the letter and issue it. You get an ID, a PDF with the letterhead, and a plain PDF for printing on your pre-printed official letterhead paper.</p>
          </div>
        </div>
        <FormErrors error={failure} fields={[]} />
        <div className="form-grid">
          <Field id="lt-template" label="Letterhead template" error={errors.letterhead_template} hint="The ID starts with the template ID, e.g. LHT-002/26/2710/00001.">
            <select id="lt-template" value={chosen === 'none' ? 'none' : template} onChange={set('letterhead_template')}>
              {templates.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              <option value="none">No template (standard letter numbering)</option>
            </select>
          </Field>
          <Field id="lt-type" label="Document type" error={errors.document_type}>
            <select id="lt-type" value={values.document_type} onChange={set('document_type')}>
              {lookups.document_types.filter((t) => LETTER_TYPES.includes(t.value)).map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </Field>
          <Field id="lt-date" label="Letter date" required error={errors.issue_date}>
            <input id="lt-date" type="date" value={values.issue_date} onChange={set('issue_date')} />
          </Field>
          <Field id="lt-title" label="Heading (optional)" error={errors.title} hint="Defaults to the template's heading, if it has one.">
            <input id="lt-title" value={values.title} maxLength={200} onChange={set('title')} />
          </Field>
          <Field id="lt-recipient" label="To (one line per row)" error={errors.recipient} wide>
            <textarea id="lt-recipient" rows={3} value={values.recipient} onChange={set('recipient')} placeholder={'The Manager\nAcme Industries\nChennai'} />
          </Field>
          <Field id="lt-subject" label="Subject" error={errors.subject} wide>
            <input id="lt-subject" value={values.subject} onChange={set('subject')} />
          </Field>
          <Field id="lt-body" label="Letter" required error={errors.body} wide hint="Leave a blank line between paragraphs.">
            <textarea id="lt-body" rows={12} value={values.body} onChange={set('body')} />
          </Field>
        </div>
        <div className="actions">
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Issuing…' : 'Issue letter'}</button>
        </div>
      </form>

      {issued && (
        <Notice type="success">
          <p><strong>Letter issued.</strong> ID: <code>{issued.reference_number}</code></p>
          <div className="row-actions"><Downloads doc={issued} onError={(e) => notify(e.message, 'error')} /></div>
          <p className="hint">Use "Print PDF" on pre-printed letterhead paper, or "Letterhead PDF" to print or send on plain paper.</p>
        </Notice>
      )}

      <IssuedList refreshKey={refreshKey} />
      {confirmDialog}
    </>
  );
}
