import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import LogoUploader from '../components/LogoUploader';
import { useAuth, useLookups } from '../context';
import { changedLabels, validateSpec } from '../forms';
import { useDebounced, useLoad } from '../hooks';
import { BRANDING_FIELDS, SIG_KEYS } from '../specs';
import {
  ErrorBox, FormErrors, Loading, Notice, SpecForm, useConfirm, useReasonSubmit, useSpecForm, useToast,
} from '../ui';

const byName = (...names) => names.map((n) => BRANDING_FIELDS.find((f) => f.name === n));
const GROUPS = [
  { title: 'Template and page', fields: byName('default_template', 'default_page_size', 'default_language') },
  { title: 'Header and logo', fields: byName('logo_position', 'logo_width_mm', 'header_alignment', 'show_address_in_header', 'show_contact_in_header', 'header_height_mm') },
  { title: 'Colours and fonts', fields: byName('primary_colour', 'secondary_colour', 'page_background_colour', 'font_family', 'body_font_size', 'heading_font_size') },
  { title: 'Margins', fields: byName('margin_top_mm', 'margin_bottom_mm', 'margin_left_mm', 'margin_right_mm') },
  { title: 'Body spacing', fields: byName('body_top_gap_mm', 'line_spacing', 'paragraph_spacing_pt', 'justify_body') },
  { title: 'Footer and page numbers', fields: byName('phone_label', 'footer_alignment', 'footer_font_size', 'footer_height_mm', 'footer_text_override', 'show_page_numbers', 'page_number_format') },
  { title: 'Watermark', fields: byName('watermark_enabled', 'watermark_type', 'watermark_text', 'watermark_size_mm', 'watermark_opacity') },
  { title: 'Signature block', fields: byName('sig_show', 'sig_label', 'sig_show_name', 'sig_show_designation', 'sig_show_signature_image', 'sig_space_mm') },
];
const SIG_ONLY = new Set(Object.keys(SIG_KEYS));

const flatten = (b) => (b ? { ...b, ...Object.fromEntries(Object.entries(SIG_KEYS).map(([flat, key]) => [flat, b.signature_block?.[key]])) } : null);

function signatureBlock(values, saved) {
  const edited = Object.fromEntries(Object.entries(SIG_KEYS).map(([flat, key]) => [key, flat === 'sig_space_mm' ? Number(values[flat]) : values[flat]]));
  return { ...saved, ...edited };
}

export function BrandingDesigner() {
  const { user } = useAuth();
  const lookups = useLookups();
  const notify = useToast();
  const canWrite = user.is_company_admin;
  const { data, error, loading, reload } = useLoad(() => api.get('/company/branding/'), []);
  const flat = useMemo(() => flatten(data), [data]);
  const form = useSpecForm(BRANDING_FIELDS, flat);
  const { run, dialog: reasonDialog } = useReasonSubmit();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [docType, setDocType] = useState('business_letter');
  const [variant, setVariant] = useState('letterhead'); // 'plain' = without letterhead, for pre-printed paper
  const [preview, setPreview] = useState({ url: null, loading: false, error: null, stale: false });
  const seq = useRef(0);
  const urlRef = useRef(null);

  const refreshPreview = useCallback(async (values) => {
    if (!canWrite || !data) return;
    const errs = validateSpec(BRANDING_FIELDS, values);
    if (Object.keys(errs).length) {
      setPreview((p) => ({ ...p, error: new Error('Fix the highlighted fields to refresh the preview.'), loading: false }));
      return;
    }
    const mine = ++seq.current;
    setPreview((p) => ({ ...p, loading: true, error: null }));
    try {
      const body = {};
      BRANDING_FIELDS.forEach((f) => {
        if (SIG_ONLY.has(f.name)) return;
        const v = values[f.name];
        body[f.name] = f.type === 'number' ? Number(v) : v;
      });
      body.signature_block = signatureBlock(values, data.signature_block);
      body.document_type = docType;
      body.variant = variant;
      const { blob } = await api.blob('/company/branding/preview/', { method: 'POST', body, fallbackName: 'preview.pdf' });
      if (mine !== seq.current) return;
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = URL.createObjectURL(blob);
      setPreview({ url: urlRef.current, loading: false, error: null });
    } catch (e) {
      if (mine === seq.current) setPreview((p) => ({ ...p, loading: false, error: e }));
    }
  }, [canWrite, data, docType, variant]);

  useEffect(() => () => { if (urlRef.current) URL.revokeObjectURL(urlRef.current); }, []);

  // Debounced live preview as the form changes.
  const snapshotKey = useDebounced(JSON.stringify(form.values), 1500);
  useEffect(() => {
    if (!data) return;
    refreshPreview(JSON.parse(snapshotKey));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshotKey, data, docType, variant]);

  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;

  const sigDirty = form.dirty.some((n) => SIG_ONLY.has(n));
  const save = async (e) => {
    e.preventDefault();
    setSaveError(null);
    if (!form.validate()) return;
    const payload = form.payload(true);
    if (sigDirty) payload.signature_block = signatureBlock(form.values, data.signature_block);
    if (!Object.keys(payload).length) { notify('No changes to save.', 'info'); return; }
    const labels = changedLabels(BRANDING_FIELDS, Object.keys(payload).filter((n) => n !== 'signature_block'));
    const ok = await confirm({
      title: 'Save letterhead changes?',
      body: (
        <>
          <p>New quotations, invoices and letters will use this letterhead. Documents already issued keep their original files.</p>
          <ul>{labels.map((l) => <li key={l}>{l}</li>)}{sigDirty && <li>Signature block</li>}</ul>
        </>
      ),
      confirmLabel: 'Save letterhead',
    });
    if (!ok) return;
    setBusy(true);
    try {
      await run((extra) => api.patch('/company/branding/', { ...payload, ...extra }));
      notify('Letterhead saved.');
      reload();
    } catch (err) {
      if (!err.cancelled) { setSaveError(err); form.setErrors(err.fieldErrors || {}); }
    }
    setBusy(false);
  };

  const allFields = BRANDING_FIELDS;
  return (
    <div className="designer">
      <form onSubmit={save} noValidate className="designer-form">
        {!canWrite && <Notice>You can view the letterhead settings; only administrators can change them.</Notice>}
        <section className="card">
          <h2>Logos</h2>
          <div className="logo-grid">
            <LogoUploader title="Company logo" path="/company/profile/logo/" canWrite={canWrite} removable
              note="The company logo is used on the letterhead unless you upload a letterhead override." onChanged={() => refreshPreview(form.values)} />
            <LogoUploader title="Letterhead logo override" path="/company/branding/assets/primary_logo/" canWrite={canWrite}
              note="Optional. Falls back to the company logo when empty." onChanged={() => { reload(); }} />
            <LogoUploader title="Watermark image" path="/company/branding/assets/watermark_image/" canWrite={canWrite}
              note="Used when the watermark type is 'Company logo'. Falls back to the letterhead logo when empty. A transparent PNG works best."
              onChanged={() => { reload(); refreshPreview(form.values); }} />
            <LogoUploader title="Watermark image" path="/company/branding/assets/watermark_image/" canWrite={canWrite}
              note="Used when the watermark type is 'Company logo'. Typically a colour version of the logo on a transparent background. Falls back to the letterhead logo."
              onChanged={() => { reload(); refreshPreview(form.values); }} />
            <LogoUploader title="Alternate / monochrome logo" path="/company/branding/assets/alternate_logo/" canWrite={canWrite}
              onChanged={() => { reload(); }} />
          </div>
        </section>
        <FormErrors error={saveError} fields={allFields} />
        {GROUPS.map((g) => (
          <section className="card" key={g.title}>
            <h2>{g.title}</h2>
            <SpecForm fields={g.fields} values={form.values} onChange={form.set} errors={form.errors} readOnly={!canWrite}
              ctx={{ lookups }} idPrefix="brand" />
          </section>
        ))}
        {canWrite && (
          <div className="actions sticky-actions">
            <button type="submit" className="btn btn-primary" disabled={busy || !form.dirty.length}>{busy ? 'Saving…' : 'Save letterhead'}</button>
            <button type="button" className="btn" onClick={form.reset} disabled={busy || !form.dirty.length}>Cancel</button>
            {data && <span className="muted">Letterhead version {data.version}</span>}
          </div>
        )}
        {reasonDialog}
        {confirmDialog}
      </form>

      {canWrite && (
        <aside className="designer-preview" aria-label="Letterhead preview">
          <div className="card sticky">
            <div className="card-head">
              <h2>Preview</h2>
              <button type="button" className="btn btn-small" onClick={() => refreshPreview(form.values)} disabled={preview.loading}>Refresh preview</button>
            </div>
            <div className="field">
              <label htmlFor="preview-doc">Sample document</label>
              <select id="preview-doc" value={docType} onChange={(e) => setDocType(e.target.value)}>
                {lookups.printable_document_types.map((t) => <option key={t} value={t}>{lookups.document_types.find((d) => d.value === t)?.label || t}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="preview-variant">Version</label>
              <select id="preview-variant" value={variant} onChange={(e) => setVariant(e.target.value)}>
                <option value="letterhead">With letterhead</option>
                <option value="plain">Plain, for pre-printed letterhead paper</option>
              </select>
            </div>
            <p className="hint">Shows your unsaved changes. Samples are marked "PREVIEW", use no document number and are not stored.</p>
            <ErrorBox error={preview.error} />
            {preview.loading && <Loading label="Rendering preview…" />}
            {preview.url
              ? <iframe title="Letterhead preview" src={preview.url} className="pdf-frame" />
              : !preview.loading && <p className="muted">No preview yet.</p>}
          </div>
        </aside>
      )}
    </div>
  );
}
