import { useRef, useState } from 'react';
import { api } from '../api';
import CrudSection, { ActiveBadge } from '../components/CrudSection';
import { useAuth, useLookups } from '../context';
import { useBlobUrl, useOptions } from '../hooks';
import {
  ADDRESS_FIELDS, CONTACT_FIELDS, FY_FIELDS, PARTNER_FIELDS, REGISTRATION_FIELDS, SIGNATORY_FIELDS, TEMPLATE_FIELDS, contactCrossCheck,
} from '../specs';
import { Badge, Dialog, ErrorBox, Notice, VerificationBadge, fmtDate, useConfirm, useToast } from '../ui';

const labelOf = (list, value) => (list || []).find((o) => o.value === value)?.label || value || '—';

const ADDRESS_DEFAULTS = { address_type: 'registered_office', country: 'India', is_active: true };
const CONTACT_DEFAULTS = { is_active: true };
const PARTNER_DEFAULTS = { partner_type: 'partner', is_active: true };
const SIGNATORY_DEFAULTS = { authorization_type: 'specific_documents', status: 'draft' };
const REGISTRATION_DEFAULTS = { is_active: true };
const FY_DEFAULTS = { is_current: false, irregular_period: false };

export function AddressesSection({ defaults = ADDRESS_DEFAULTS }) {
  const { user } = useAuth();
  const lookups = useLookups();
  return (
    <CrudSection
      title="Addresses" noun="address" endpoint="/company/addresses/" fields={ADDRESS_FIELDS} defaults={defaults}
      canWrite={user.is_company_admin}
      description="Registered office and other business addresses. Choose which address prints on each kind of document."
      columns={[
        { key: 'label', label: 'Label', render: (r) => <strong>{r.label}</strong> },
        { key: 'address_type', label: 'Type', render: (r) => labelOf(lookups.address_types, r.address_type) },
        { key: 'city', label: 'Location', render: (r) => `${r.city}, ${r.state} ${r.postal_code}` },
        { key: 'document_types', label: 'Printed on', render: (r) => (r.document_types?.length ? r.document_types.map((d) => labelOf(lookups.document_types, d)).join(', ') : '—') },
        { key: 'is_primary', label: 'Flags', render: (r) => (<>{r.is_primary && <Badge tone="info">Primary</Badge>} <ActiveBadge active={r.is_active} /></>) },
      ]}
    />
  );
}

export function LetterheadTemplatesSection() {
  const { user } = useAuth();
  return (
    <CrudSection
      title="Letterhead templates" noun="template" endpoint="/company/letterhead-templates/" fields={TEMPLATE_FIELDS}
      defaults={{ is_active: true, sort_order: 100 }} canWrite={user.is_company_admin}
      description="Each letter is issued on a template. Its reference is the template ID, the two-digit year, the date and a unique running number, e.g. LHT-002/26/2710/00001."
      columns={[
        { key: 'code', label: 'Template ID', render: (r) => <code>{r.code}</code> },
        { key: 'name', label: 'Name', render: (r) => <strong>{r.name}</strong> },
        { key: 'default_title', label: 'Heading' },
        { key: 'next_reference_preview', label: 'Next reference', render: (r) => <code>{r.next_reference_preview}</code> },
        { key: 'issued_count', label: 'Issued' },
        { key: 'is_active', label: 'Status', render: (r) => <ActiveBadge active={r.is_active} /> },
      ]}
    />
  );
}

export function ContactsSection() {
  const { user } = useAuth();
  const lookups = useLookups();
  return (
    <CrudSection
      title="Contacts" noun="contact" endpoint="/company/contacts/" fields={CONTACT_FIELDS} defaults={CONTACT_DEFAULTS}
      canWrite={user.is_company_admin} crossCheck={contactCrossCheck} deactivateReason="optional"
      description="The right contact is chosen automatically for each document type, unless you assign one explicitly."
      columns={[
        { key: 'display_name', label: 'Name', render: (r) => <strong>{r.display_name}</strong> },
        { key: 'contact_type', label: 'Type', render: (r) => labelOf(lookups.contact_types, r.contact_type) },
        { key: 'email', label: 'Email' },
        { key: 'phone', label: 'Telephone' },
        { key: 'department', label: 'Department' },
        { key: 'is_primary', label: 'Flags', render: (r) => (<>{r.is_primary && <Badge tone="info">Primary</Badge>} <ActiveBadge active={r.is_active} /></>) },
      ]}
    />
  );
}

// Upload, view and remove a stored signature image (administrators only; every view is audited).
function SignatureDialog({ endpoint, row, onClose, onChanged }) {
  const notify = useToast();
  const input = useRef(null);
  const [show, setShow] = useState(false);
  const { url } = useBlobUrl(show ? `${endpoint}${row.id}/signature/` : null, []);
  const [error, setError] = useState(null);
  const { confirm, dialog } = useConfirm();

  const upload = async (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!file) return;
    if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 10 * 1024 * 1024) { setError(new Error('Choose a PNG or JPEG image under 10 MB.')); return; }
    try {
      const form = new FormData();
      form.append('file', file);
      await api.upload(`${endpoint}${row.id}/signature/`, form);
      notify('Signature stored.');
      onChanged();
      onClose();
    } catch (err) { setError(err.fieldErrors?.file ? new Error(err.fieldErrors.file) : err); }
  };
  const remove = async () => {
    if (!(await confirm({ title: 'Remove stored signature?', confirmLabel: 'Remove', danger: true }))) return;
    try { await api.del(`${endpoint}${row.id}/signature/`); notify('Signature removed.'); onChanged(); onClose(); } catch (err) { setError(err); }
  };
  return (
    <Dialog title={`Signature – ${row.full_name || row.name}`} onClose={onClose}>
      <Notice>A stored signature image is not proof that this person approved a transaction. Approvals are recorded separately with the approving user and time. Viewing a signature is logged.</Notice>
      <ErrorBox error={error} />
      {show && (url ? <div className="logo-preview"><img src={url} alt="Stored signature" /></div> : <p className="muted">No signature stored.</p>)}
      <div className="actions">
        <input ref={input} type="file" accept="image/png,image/jpeg" hidden onChange={upload} aria-label="Choose signature image" />
        <button type="button" className="btn" onClick={() => input.current.click()}>{row.has_signature ? 'Replace signature' : 'Upload signature'}</button>
        {row.has_signature && <button type="button" className="btn" onClick={() => setShow((s) => !s)}>{show ? 'Hide' : 'View'}</button>}
        {row.has_signature && <button type="button" className="btn btn-danger-outline" onClick={remove}>Remove</button>}
        <button type="button" className="btn" onClick={onClose}>Close</button>
      </div>
      {dialog}
    </Dialog>
  );
}

export function PartnersSection() {
  const { user } = useAuth();
  const lookups = useLookups();
  const [sigRow, setSigRow] = useState(null);
  const [refresh, setRefresh] = useState(0);
  return (
    <>
      {!user.is_company_admin && <Notice>Partners' personal details (DPIN, email, telephone) are visible to administrators only.</Notice>}
      <CrudSection
        key={refresh}
        title="Partners" noun="partner" endpoint="/company/partners/" fields={PARTNER_FIELDS} defaults={PARTNER_DEFAULTS}
        canWrite={user.is_company_admin}
        columns={[
          { key: 'full_name', label: 'Name', render: (r) => <strong>{r.full_name}</strong> },
          { key: 'designation', label: 'Designation' },
          { key: 'partner_type', label: 'Type', render: (r) => (<>{labelOf(lookups.partner_types, r.partner_type)} {r.is_designated_partner && <Badge tone="info">Designated</Badge>}</>) },
          { key: 'appointment_date', label: 'Appointed', render: (r) => fmtDate(r.appointment_date) },
          { key: 'is_active', label: 'Status', render: (r) => <ActiveBadge active={r.is_active} /> },
        ]}
        rowActions={(row) => (user.is_company_admin ? [{ label: 'Signature', onClick: () => setSigRow(row) }] : [])}
      />
      {sigRow && <SignatureDialog endpoint="/company/partners/" row={sigRow} onClose={() => setSigRow(null)} onChanged={() => setRefresh((n) => n + 1)} />}
    </>
  );
}

export function SignatoriesSection() {
  const { user } = useAuth();
  const lookups = useLookups();
  const partners = useOptions('/company/partners/', (p) => p.full_name);
  const documents = useOptions('/company/documents/', (d) => `${d.title} (v${d.version})`);
  const [sigRow, setSigRow] = useState(null);
  const [refresh, setRefresh] = useState(0);
  return (
    <>
      <CrudSection
        key={refresh}
        title="Authorized signatories" noun="signatory" endpoint="/company/signatories/" fields={SIGNATORY_FIELDS}
        defaults={SIGNATORY_DEFAULTS} canWrite={user.is_company_admin} ctx={{ partners, documents }}
        isActive={(r) => r.status === 'active'} supportsToggle={() => true}
        description="Who may sign which documents, and for how long. Non-administrators only see active signatories."
        columns={[
          { key: 'name', label: 'Name', render: (r) => <strong>{r.name}</strong> },
          { key: 'designation', label: 'Designation' },
          { key: 'authorization_type', label: 'Authorization', render: (r) => labelOf(lookups.signatory_authorization_types, r.authorization_type) },
          { key: 'valid_from', label: 'Valid', render: (r) => `${fmtDate(r.valid_from)} – ${r.valid_to ? fmtDate(r.valid_to) : 'open'}` },
          { key: 'status', label: 'Status', render: (r) => <Badge tone={r.status === 'active' ? 'good' : r.status === 'revoked' ? 'bad' : 'neutral'}>{labelOf(lookups.signatory_statuses, r.status)}</Badge> },
        ]}
        rowActions={(row) => (user.is_company_admin ? [{ label: 'Signature', onClick: () => setSigRow({ ...row, full_name: row.name }) }] : [])}
      />
      {sigRow && <SignatureDialog endpoint="/company/signatories/" row={sigRow} onClose={() => setSigRow(null)} onChanged={() => setRefresh((n) => n + 1)} />}
    </>
  );
}

export function RegistrationsSection() {
  const { user } = useAuth();
  const lookups = useLookups();
  const documents = useOptions('/company/documents/', (d) => `${d.title} (v${d.version})`);
  const verify = (row, ctx, decision) => async () => {
    if (decision === 'verified' && !row.supporting_document) { ctx.notify('Attach a supporting document (Edit) before verifying.', 'error'); return; }
    const note = await ctx.ask({ title: decision === 'verified' ? 'Verify registration' : 'Reject registration', label: 'Note', required: false, confirmLabel: decision === 'verified' ? 'Verify' : 'Reject' });
    if (note === null) return;
    try {
      await api.post(`/company/tax-registrations/${row.id}/verify/`, { decision, change_reason: note });
      ctx.notify(decision === 'verified' ? 'Registration verified.' : 'Registration rejected.');
      ctx.reload();
    } catch (e) { ctx.notify(e.message, 'error'); }
  };
  return (
    <CrudSection
      title="Registrations" noun="registration" endpoint="/company/tax-registrations/" fields={REGISTRATION_FIELDS}
      defaults={REGISTRATION_DEFAULTS} canWrite={user.is_company_admin} ctx={{ documents }}
      description="GSTIN, PAN, TAN and other registrations. Only record registrations that actually apply."
      columns={[
        { key: 'registration_type', label: 'Type', render: (r) => labelOf(lookups.registration_types, r.registration_type) },
        { key: 'registration_number', label: 'Number', render: (r) => <code>{r.registration_number}</code> },
        { key: 'state', label: 'State' },
        { key: 'effective_from', label: 'Effective', render: (r) => `${fmtDate(r.effective_from)} – ${r.effective_to ? fmtDate(r.effective_to) : 'open'}` },
        { key: 'verification_status', label: 'Verification', render: (r) => <VerificationBadge status={r.verification_status} /> },
        { key: 'is_active', label: 'Status', render: (r) => <ActiveBadge active={r.is_active} /> },
      ]}
      rowActions={(row, ctx) => (user.is_company_admin ? [
        ...(row.verification_status !== 'verified' ? [{ label: 'Verify', onClick: verify(row, ctx, 'verified') }] : []),
        ...(row.verification_status !== 'rejected' ? [{ label: 'Reject', onClick: verify(row, ctx, 'rejected'), danger: true }] : []),
      ] : [])}
    />
  );
}

const daysBetween = (a, b) => Math.round((new Date(b) - new Date(a)) / 86400000);

export function FinancialYearsManager({ onChanged }) {
  const { user } = useAuth();
  const change = (row, ctx, action, title) => async () => {
    const reason = await ctx.ask({ title: `${title}: ${row.label}`, required: true, confirmLabel: title });
    if (reason === null) return;
    try {
      await api.post(`/company/financial-years/${row.id}/${action}/`, { change_reason: reason });
      ctx.notify(`${row.label}: ${title.toLowerCase()} done.`);
      ctx.reload();
    } catch (e) { ctx.notify(e.message, 'error'); }
  };
  return (
    <CrudSection
      title="Financial years" noun="financial year" endpoint="/company/financial-years/" fields={FY_FIELDS}
      defaults={FY_DEFAULTS} canWrite={user.is_company_admin} supportsToggle={() => false} onChanged={onChanged}
      prepareRecord={(r) => ({ ...r, irregular_period: ![364, 365].includes(daysBetween(r.start_date, r.end_date)) })}
      description="Years cannot overlap. Locking a year stops new document numbers being issued in it."
      columns={[
        { key: 'label', label: 'Year', render: (r) => <strong>{r.label}</strong> },
        { key: 'start_date', label: 'Start', render: (r) => fmtDate(r.start_date) },
        { key: 'end_date', label: 'End', render: (r) => fmtDate(r.end_date) },
        { key: 'is_current', label: 'Current', render: (r) => (r.is_current ? <Badge tone="info">Current</Badge> : '—') },
        { key: 'status', label: 'Status', render: (r) => <Badge tone={r.status === 'open' ? 'good' : 'neutral'}>{r.status === 'open' ? 'Open' : 'Closed'}</Badge> },
        { key: 'is_locked', label: 'Lock', render: (r) => (r.is_locked ? <Badge tone="warn">Locked</Badge> : '—') },
      ]}
      rowActions={(row, ctx) => (user.is_company_admin ? [
        ...(!row.is_current && row.status === 'open' ? [{ label: 'Set current', onClick: change(row, ctx, 'set-current', 'Set as current year') }] : []),
        row.is_locked ? { label: 'Unlock', onClick: change(row, ctx, 'unlock', 'Unlock') } : { label: 'Lock', onClick: change(row, ctx, 'lock', 'Lock') },
        ...(row.status === 'open' ? [{ label: 'Close', danger: true, onClick: change(row, ctx, 'close', 'Close year') }] : []),
      ] : [])}
    />
  );
}
