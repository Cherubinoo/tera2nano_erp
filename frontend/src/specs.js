// Field specs for each record type. Choice lists come from GET /company/lookups/ (ctx.lookups).
import { PATTERNS, dateOrder } from './forms';

const lk = (key) => (ctx) => ctx.lookups[key];
const timezones = () => {
  try { return Intl.supportedValuesOf('timeZone'); } catch { return ['Asia/Kolkata', 'UTC']; }
};

export const PROFILE_FIELDS = {
  legal_name: { name: 'legal_name', label: 'Legal company name', required: true, maxLength: 255 },
  display_name: { name: 'display_name', label: 'Display name', required: true, maxLength: 255, hint: 'Shown on screens and letterheads.' },
  trade_name: { name: 'trade_name', label: 'Trade name', maxLength: 255 },
  short_code: {
    name: 'short_code', label: 'Short company code', upper: true, maxLength: 12, pattern: /^[A-Z0-9]{1,12}$/,
    patternMessage: 'Use letters and digits only.', hint: 'Used to suggest document number prefixes (for example T2N).',
  },
  description: { name: 'description', label: 'Company description', type: 'textarea', wide: true, rows: 4 },
  industry: { name: 'industry', label: 'Industry', maxLength: 120 },
  business_type: { name: 'business_type', label: 'Business type', maxLength: 120 },
  date_of_incorporation: { name: 'date_of_incorporation', label: 'Date of incorporation', type: 'date' },
  website: { name: 'website', label: 'Website', type: 'url', placeholder: 'https://' },
  primary_email: { name: 'primary_email', label: 'Primary email', type: 'email' },
  primary_phone: { name: 'primary_phone', label: 'Primary telephone', type: 'tel', pattern: PATTERNS.phone, patternMessage: 'Enter a valid telephone number.' },
  alternate_phone: { name: 'alternate_phone', label: 'Alternate telephone', type: 'tel', pattern: PATTERNS.phone, patternMessage: 'Enter a valid telephone number.' },
  default_currency: { name: 'default_currency', label: 'Default currency', upper: true, maxLength: 3, required: true, pattern: PATTERNS.currency, patternMessage: 'Use a 3-letter currency code, e.g. INR.' },
  default_timezone: { name: 'default_timezone', label: 'Default time zone', required: true, datalist: timezones, hint: 'For example Asia/Kolkata.' },
  status: { name: 'status', label: 'Company status', type: 'select', required: true, options: lk('company_statuses') },

  legal_entity_type: { name: 'legal_entity_type', label: 'Legal entity type', type: 'select', options: lk('entity_types'), blankLabel: '— not stated —' },
  llpin: { name: 'llpin', label: 'LLP Identification Number (LLPIN)', upper: true, maxLength: 8, pattern: PATTERNS.llpin, patternMessage: 'Format: three letters, a hyphen, four digits (AAA-1234).' },
  pan: { name: 'pan', label: 'PAN', upper: true, maxLength: 10, pattern: PATTERNS.pan, patternMessage: 'Format: ABCDE1234F.' },
  tan: { name: 'tan', label: 'TAN (if applicable)', upper: true, maxLength: 10, pattern: PATTERNS.tan, patternMessage: 'Format: ABCD12345E.' },
  gst_registration_status: { name: 'gst_registration_status', label: 'GST registration status', type: 'select', required: true, options: lk('gst_statuses') },
  gstin: {
    name: 'gstin', label: 'Registered GSTIN', upper: true, maxLength: 15, hint: 'The server also checks the state code, check character and that it contains the company PAN.',
    hidden: (v) => v.gst_registration_status !== 'registered', clearWhenHidden: true,
    validate: (val, v) => {
      if (v.gst_registration_status !== 'registered') return null;
      if (!val) return 'Enter the GSTIN for a registered company.';
      return PATTERNS.gstin.test(val) ? null : 'Enter a valid 15-character GSTIN.';
    },
  },
  incorporation_certificate_reference: { name: 'incorporation_certificate_reference', label: 'Incorporation certificate reference', maxLength: 120 },
  incorporation_certificate: {
    name: 'incorporation_certificate', label: 'Incorporation certificate document', type: 'select', nullable: true,
    options: (ctx) => ctx.incorporationDocs || [], hint: 'Upload it first under Company Documents (type: Incorporation certificate).',
  },
};

export const pick = (...names) => names.map((n) => PROFILE_FIELDS[n]);

export const BASIC_FIELDS = pick(
  'legal_name', 'display_name', 'trade_name', 'short_code', 'description', 'industry', 'business_type',
  'date_of_incorporation', 'website', 'primary_email', 'primary_phone', 'alternate_phone', 'default_currency',
  'default_timezone', 'status',
);
export const LEGAL_FIELDS = pick(
  'legal_entity_type', 'llpin', 'pan', 'tan', 'gst_registration_status', 'gstin',
  'incorporation_certificate_reference', 'incorporation_certificate',
);
export const WIZARD_LEGAL_FIELDS = pick('date_of_incorporation', 'legal_entity_type', 'llpin', 'pan', 'tan', 'incorporation_certificate_reference');
export const WIZARD_GST_FIELDS = pick('gst_registration_status', 'gstin');
export const IDENTITY_FIELDS = pick('legal_name', 'display_name', 'short_code');

export const ADDRESS_FIELDS = [
  { name: 'label', label: 'Address label', required: true, maxLength: 120 },
  { name: 'address_type', label: 'Address type', type: 'select', required: true, options: lk('address_types') },
  { name: 'building_name', label: 'Building or premises name', maxLength: 255 },
  { name: 'line1', label: 'Address line 1', required: true, maxLength: 255 },
  { name: 'line2', label: 'Address line 2', maxLength: 255 },
  { name: 'locality', label: 'Street or locality', maxLength: 255 },
  { name: 'city', label: 'City or town', required: true, maxLength: 120 },
  { name: 'district', label: 'District', maxLength: 120 },
  { name: 'state', label: 'State', required: true, maxLength: 120 },
  { name: 'state_code', label: 'GST state code', type: 'select', options: lk('gst_states'), blankLabel: '— not stated —' },
  { name: 'postal_code', label: 'PIN code', required: true, maxLength: 6, pattern: PATTERNS.pin, patternMessage: 'Enter a 6-digit PIN code.' },
  { name: 'country', label: 'Country', maxLength: 120 },
  { name: 'effective_from', label: 'Effective from', type: 'date' },
  { name: 'effective_to', label: 'Effective to', type: 'date', validate: dateOrder('effective_from', 'effective_to') },
  { name: 'is_primary', label: 'Primary address', type: 'checkbox' },
  { name: 'is_active', label: 'Active', type: 'checkbox' },
  {
    name: 'document_types', label: 'Print this address on', type: 'checkgroup', options: lk('document_types'),
    hint: 'Each document type can use only one active address; leave empty to fall back to the primary address.',
  },
];

export const CONTACT_FIELDS = [
  { name: 'contact_type', label: 'Contact type', type: 'select', required: true, options: lk('contact_types') },
  { name: 'display_name', label: 'Display name', required: true, maxLength: 200 },
  { name: 'email', label: 'Email address', type: 'email' },
  { name: 'phone', label: 'Telephone', type: 'tel', pattern: PATTERNS.phone, patternMessage: 'Enter a valid telephone number.' },
  { name: 'department', label: 'Department', maxLength: 120 },
  { name: 'is_primary', label: 'Primary contact for this type', type: 'checkbox' },
  { name: 'is_active', label: 'Active', type: 'checkbox' },
  { name: 'document_types', label: 'Use on these documents', type: 'checkgroup', options: lk('document_types'), hint: 'Empty means: chosen automatically by contact type.' },
];
export const contactCrossCheck = (v) => (!v.email && !v.phone ? { email: 'Provide an email address or a telephone number.' } : {});

export const PARTNER_FIELDS = [
  { name: 'full_name', label: 'Full name', required: true, maxLength: 200 },
  { name: 'designation', label: 'Designation', maxLength: 120 },
  { name: 'partner_type', label: 'Partner type', type: 'select', required: true, options: lk('partner_types') },
  { name: 'is_designated_partner', label: 'Designated partner', type: 'checkbox' },
  { name: 'dpin', label: 'DPIN', maxLength: 8, pattern: PATTERNS.dpin, patternMessage: 'DPIN is 8 digits.' },
  { name: 'official_email', label: 'Official email', type: 'email' },
  { name: 'official_phone', label: 'Official contact number', type: 'tel', pattern: PATTERNS.phone, patternMessage: 'Enter a valid telephone number.' },
  { name: 'appointment_date', label: 'Appointment date', type: 'date' },
  { name: 'cessation_date', label: 'Retirement or cessation date', type: 'date', validate: dateOrder('appointment_date', 'cessation_date', 'Cessation cannot be before appointment.') },
  { name: 'is_active', label: 'Active', type: 'checkbox' },
  { name: 'authorization_scope', label: 'Authorization scope', type: 'textarea', wide: true },
];

export const SIGNATORY_FIELDS = [
  { name: 'name', label: 'Signatory name', required: true, maxLength: 200 },
  { name: 'designation', label: 'Designation', maxLength: 120 },
  { name: 'partner', label: 'Linked partner', type: 'select', nullable: true, options: (ctx) => ctx.partners || [] },
  { name: 'authorization_type', label: 'Authorization type', type: 'select', required: true, options: lk('signatory_authorization_types') },
  { name: 'valid_from', label: 'Authorized from', type: 'date', validate: (v, vals) => (vals.status === 'active' && !v ? 'An active authorization needs a start date.' : null) },
  { name: 'valid_to', label: 'Authorized until', type: 'date', validate: dateOrder('valid_from', 'valid_to') },
  { name: 'status', label: 'Status', type: 'select', required: true, options: lk('signatory_statuses') },
  { name: 'authorization_reference', label: 'Approval or authorization reference', maxLength: 200 },
  { name: 'supporting_document', label: 'Supporting document', type: 'select', nullable: true, options: (ctx) => ctx.documents || [] },
  {
    name: 'authorized_document_types', label: 'Authorized document types', type: 'checkgroup', options: lk('document_types'),
    hint: 'Required when the authorization type is "Specific document types".',
    validate: (v, vals) => (vals.authorization_type === 'specific_documents' && !(v && v.length) ? 'Select at least one document type.' : null),
  },
];

export const REGISTRATION_FIELDS = [
  { name: 'registration_type', label: 'Registration type', type: 'select', required: true, options: lk('registration_types') },
  { name: 'jurisdiction', label: 'Jurisdiction', maxLength: 200 },
  { name: 'state', label: 'State', maxLength: 120 },
  { name: 'state_code', label: 'GST state code', type: 'select', options: lk('gst_states'), blankLabel: '— from GSTIN / none —' },
  { name: 'effective_from', label: 'Effective from', type: 'date' },
  { name: 'effective_to', label: 'Effective to', type: 'date', validate: dateOrder('effective_from', 'effective_to') },
  { name: 'supporting_document', label: 'Supporting document', type: 'select', nullable: true, options: (ctx) => ctx.documents || [] },
  { name: 'is_active', label: 'Active', type: 'checkbox' },
  { name: 'notes', label: 'Notes', type: 'textarea', wide: true },
  {
    name: 'registration_number', label: 'Registration number', required: true, upper: true, maxLength: 60, hint: 'Stored as text so formatting is preserved.',
    validate: (v, vals) => {
      if (vals.registration_type === 'gstin') return PATTERNS.gstin.test(v) ? null : 'Enter a valid 15-character GSTIN.';
      if (vals.registration_type === 'pan') return PATTERNS.pan.test(v) ? null : 'Enter a valid PAN.';
      if (vals.registration_type === 'tan') return PATTERNS.tan.test(v) ? null : 'Enter a valid TAN.';
      if (vals.registration_type === 'llpin') return PATTERNS.llpin.test(v) ? null : 'Enter a valid LLPIN.';
      return null;
    },
  },
];

export const FY_FIELDS = [
  { name: 'label', label: 'Financial year label', required: true, maxLength: 20, placeholder: '2026-27' },
  { name: 'start_date', label: 'Start date', type: 'date', required: true },
  { name: 'end_date', label: 'End date', type: 'date', required: true, validate: (v, vals) => (v && vals.start_date && v <= vals.start_date ? 'End date must be after the start date.' : null) },
  { name: 'is_current', label: 'Current financial year', type: 'checkbox' },
  { name: 'irregular_period', label: 'This first period is shorter or longer than 12 months', type: 'checkbox', alwaysSend: true, wide: true },
];

export const POLICY_FIELDS = [
  { name: 'change_summary', label: 'Summary of this version', maxLength: 300, wide: true },
  { name: 'effective_from', label: 'Effective from', type: 'date', hint: 'Defaults to the activation date.' },
  { name: 'payment_terms', label: 'Standard payment terms', type: 'textarea', wide: true },
  { name: 'payment_terms_days', label: 'Payment term (days)', type: 'number', min: 0, max: 3650, integer: true },
  { name: 'quotation_validity_days', label: 'Default quotation validity (days)', type: 'number', min: 0, max: 3650, integer: true },
  { name: 'invoice_due_days', label: 'Default invoice due period (days)', type: 'number', min: 0, max: 3650, integer: true },
  { name: 'quotation_terms', label: 'Default quotation terms and conditions', type: 'textarea', wide: true, rows: 5 },
  { name: 'invoice_terms', label: 'Default invoice terms and conditions', type: 'textarea', wide: true, rows: 5 },
  { name: 'project_terms', label: 'Standard project terms', type: 'textarea', wide: true, rows: 4 },
  { name: 'confidentiality_notice', label: 'Confidentiality notice', type: 'textarea', wide: true },
  { name: 'document_footer_text', label: 'Document footer text', maxLength: 300, wide: true },
  { name: 'refund_cancellation_terms', label: 'Refund and cancellation terms', type: 'textarea', wide: true },
  { name: 'email_signature', label: 'Default email signature', type: 'textarea', wide: true },
  { name: 'correspondence_wording', label: 'Standard business correspondence wording', type: 'textarea', wide: true },
];

export const SEQUENCE_FIELDS = [
  { name: 'document_type', label: 'Document type', type: 'select', required: true, options: (ctx) => ctx.sequenceTypeOptions || [], disabledWhen: (v, ctx) => ctx.isEdit },
  { name: 'prefix', label: 'Prefix', required: true, maxLength: 40, hint: 'For example T2N/QUO or T2N-EMP.' },
  { name: 'separator', label: 'Separator', maxLength: 3 },
  { name: 'include_financial_year', label: 'Include financial-year segment', type: 'checkbox' },
  { name: 'include_short_year', label: 'Include two-digit year (e.g. 26)', type: 'checkbox' },
  { name: 'date_format', label: 'Include issue date', type: 'select', options: lk('date_formats'), blankLabel: 'No date' },
  { name: 'min_length', label: 'Minimum sequence length', type: 'number', required: true, min: 1, max: 10, integer: true },
  { name: 'reset_frequency', label: 'Reset frequency', type: 'select', required: true, options: lk('reset_frequencies'),
    validate: (v, vals) => (v === 'financial_year' && !vals.include_financial_year && !(vals.include_short_year && vals.date_format)
      ? 'Resetting every financial year needs the financial-year segment, or the year and date, in the number.' : null) },
  { name: 'next_value', label: 'Next sequence value', type: 'number', required: true, integer: true,
    min: (ctx) => ctx.minNext || 1 },
  { name: 'is_active', label: 'Active', type: 'checkbox' },
];

export const TEMPLATE_FIELDS = [
  { name: 'code', label: 'Template ID', required: true, upper: true, maxLength: 7, pattern: /^LHT-\d{3}$/,
    patternMessage: 'Format: LHT-001.', hint: 'Becomes the start of every reference issued on this template.',
    disabledWhen: (v, ctx) => !!(ctx.isEdit && ctx.record && ctx.record.issued_count > 0) },
  { name: 'name', label: 'Template name', required: true, maxLength: 120 },
  { name: 'default_title', label: 'Heading printed on the letter', maxLength: 120, hint: 'Optional. Can be overridden for each letter.' },
  { name: 'description', label: 'Description', type: 'textarea', wide: true },
  { name: 'sort_order', label: 'Display order', type: 'number', min: 0, max: 32000, integer: true },
  { name: 'is_active', label: 'Active', type: 'checkbox' },
];

export const OPERATION_FIELDS = [
  { name: 'code', label: 'Operation ID', required: true, upper: true, maxLength: 7, pattern: /^OPS-\d{3}$/,
    patternMessage: 'Format: OPS-001.', hint: 'Becomes the start of every reference issued for this operation.',
    disabledWhen: (v, ctx) => !!(ctx.isEdit && ctx.record && ctx.record.issued_count > 0) },
  { name: 'name', label: 'Operation name', required: true, maxLength: 120 },
  { name: 'default_title', label: 'Heading printed on the document', maxLength: 120, hint: 'Optional. Can be changed for each document.' },
  { name: 'description', label: 'Description', type: 'textarea', wide: true },
  { name: 'sort_order', label: 'Display order', type: 'number', min: 0, max: 32000, integer: true },
  { name: 'is_active', label: 'Active', type: 'checkbox' },
];

export const BANK_FIELDS = [
  { name: 'display_name', label: 'Account display name', required: true, maxLength: 120 },
  { name: 'bank_name', label: 'Bank name', required: true, maxLength: 150 },
  { name: 'branch', label: 'Branch', maxLength: 150 },
  { name: 'account_holder_name', label: 'Account holder name', required: true, maxLength: 200 },
  {
    name: 'account_number', label: 'Account number', omitIfEmpty: true, autoComplete: 'off', maxLength: 20,
    pattern: PATTERNS.account, patternMessage: 'Account number must be 6 to 20 digits.',
    placeholder: (ctx) => (ctx.isEdit ? 'Leave blank to keep the stored number' : ''),
    hint: 'Write-only. The full number is never shown again; only the last four digits are displayed.',
  },
  { name: 'ifsc_code', label: 'IFSC code', required: true, upper: true, maxLength: 11, pattern: PATTERNS.ifsc, patternMessage: 'Format: four letters, 0, six letters or digits (HDFC0001234).' },
  { name: 'account_type', label: 'Account type', type: 'select', required: true, options: lk('bank_account_types') },
  { name: 'currency', label: 'Currency', upper: true, maxLength: 3, pattern: PATTERNS.currency, patternMessage: '3-letter currency code.' },
  { name: 'effective_from', label: 'Effective from', type: 'date' },
  { name: 'effective_to', label: 'Effective to', type: 'date', validate: dateOrder('effective_from', 'effective_to') },
  { name: 'supporting_document', label: 'Bank verification document', type: 'select', nullable: true, options: (ctx) => ctx.documents || [] },
  { name: 'is_primary', label: 'Primary account', type: 'checkbox' },
  { name: 'show_on_invoices', label: 'Show verified bank details on invoices', type: 'checkbox', disabledWhen: (v, ctx) => ctx.record?.verification_status !== 'verified' },
  { name: 'show_on_quotations', label: 'Show verified bank details on quotations', type: 'checkbox', disabledWhen: (v, ctx) => ctx.record?.verification_status !== 'verified' },
];

export const TAX_SETTINGS_FIELDS = [
  { name: 'gst_applicable', label: 'GST applies to this company', type: 'checkbox', wide: true },
  { name: 'gst_treatment', label: 'GST treatment', type: 'select', required: true, options: lk('gst_treatments'),
    validate: (v, vals) => {
      if (vals.gst_applicable && v === 'not_applicable') return 'Choose a GST treatment when GST is applicable.';
      if (!vals.gst_applicable && v !== 'not_applicable') return 'Treatment must be "not applicable" while GST is switched off.';
      return null;
    } },
  { name: 'default_tax_code', label: 'Default tax code', type: 'select', nullable: false, options: (ctx) => (ctx.codes || []).filter((c) => c.code).map((c) => ({ value: c.code, label: `${c.code} (${c.rate}%)` })) },
  { name: 'default_place_of_supply_state_code', label: 'Default place of supply', type: 'select', options: lk('gst_states'), blankLabel: '— none —' },
  { name: 'invoice_title', label: 'Invoice title', maxLength: 60, required: true, hint: 'For example "Tax Invoice" for registered taxpayers.' },
  { name: 'effective_from', label: 'Effective from', type: 'date' },
  { name: 'show_gstin_on_documents', label: 'Show GSTIN on documents', type: 'checkbox' },
  { name: 'show_hsn_sac', label: 'Show HSN/SAC codes', type: 'checkbox' },
  { name: 'show_place_of_supply', label: 'Show place of supply', type: 'checkbox' },
  { name: 'show_tax_breakup', label: 'Show CGST/SGST/IGST breakup', type: 'checkbox' },
];

export const BRANDING_FIELDS = [
  { name: 'default_template', label: 'Default document template', type: 'select', required: true, options: lk('templates') },
  { name: 'default_page_size', label: 'Default PDF page size', type: 'select', required: true, options: lk('page_sizes') },
  { name: 'default_language', label: 'Default document language', maxLength: 8, required: true },
  { name: 'logo_position', label: 'Logo position', type: 'select', required: true, options: lk('alignments') },
  { name: 'logo_width_mm', label: 'Logo width (mm)', type: 'number', required: true, min: 10, max: 80, integer: true },
  { name: 'header_alignment', label: 'Header alignment', type: 'select', required: true, options: lk('alignments') },
  { name: 'footer_alignment', label: 'Footer alignment', type: 'select', required: true, options: lk('alignments') },
  { name: 'show_address_in_header', label: 'Show address in header', type: 'checkbox' },
  { name: 'show_contact_in_header', label: 'Show contact details in header', type: 'checkbox' },
  { name: 'primary_colour', label: 'Primary brand colour', type: 'color', required: true, pattern: PATTERNS.hex, patternMessage: 'Use #RRGGBB.' },
  { name: 'secondary_colour', label: 'Secondary brand colour', type: 'color', required: true, pattern: PATTERNS.hex, patternMessage: 'Use #RRGGBB.' },
  { name: 'page_background_colour', label: 'Page background colour', maxLength: 7, placeholder: '#F5F5F3', pattern: /^(#[0-9A-Fa-f]{6})?$/,
    patternMessage: 'Use #RRGGBB, or leave empty for a white page.', hint: 'Leave empty for white.' },
  { name: 'phone_label', label: 'Label before the phone number', maxLength: 12, required: true, hint: 'For example Tel or Mob.' },
  { name: 'font_family', label: 'Font family', type: 'select', required: true, options: lk('fonts') },
  { name: 'body_font_size', label: 'Body font size (pt)', type: 'number', required: true, min: 7, max: 16, integer: true },
  { name: 'heading_font_size', label: 'Heading font size (pt)', type: 'number', required: true, min: 9, max: 28, integer: true },
  { name: 'footer_font_size', label: 'Footer font size (pt)', type: 'number', required: true, min: 6, max: 12, integer: true },
  { name: 'margin_top_mm', label: 'Top margin (mm)', type: 'number', required: true, min: 0, max: 60, integer: true },
  { name: 'margin_bottom_mm', label: 'Bottom margin (mm)', type: 'number', required: true, min: 0, max: 60, integer: true },
  { name: 'margin_left_mm', label: 'Left margin (mm)', type: 'number', required: true, min: 10, max: 50, integer: true },
  { name: 'margin_right_mm', label: 'Right margin (mm)', type: 'number', required: true, min: 10, max: 50, integer: true },
  { name: 'body_top_gap_mm', label: 'Space below the header (mm)', type: 'number', required: true, min: 0, max: 60, integer: true,
    hint: 'Gap between the letterhead header and the first line of the letter.' },
  { name: 'line_spacing', label: 'Line spacing (1.0 – 2.5)', type: 'number', required: true, step: 0.05, min: 1, max: 2.5 },
  { name: 'paragraph_spacing_pt', label: 'Space between paragraphs (pt)', type: 'number', required: true, min: 0, max: 40, integer: true },
  { name: 'justify_body', label: 'Justify paragraphs (straight left and right edges)', type: 'checkbox', wide: true },
  { name: 'header_height_mm', label: 'Header height (mm)', type: 'number', required: true, min: 10, max: 70, integer: true },
  { name: 'footer_height_mm', label: 'Footer height (mm)', type: 'number', required: true, min: 8, max: 40, integer: true },
  { name: 'footer_text_override', label: 'Footer text (overrides the policy footer)', maxLength: 300, wide: true },
  { name: 'show_page_numbers', label: 'Show page numbers', type: 'checkbox' },
  { name: 'page_number_format', label: 'Page number format', maxLength: 40, hint: 'Use {page} and {pages}.' },
  { name: 'watermark_enabled', label: 'Show a watermark', type: 'checkbox' },
  { name: 'watermark_type', label: 'Watermark type', type: 'select', required: true,
    options: [{ value: 'text', label: 'Text' }, { value: 'logo', label: 'Company logo' }] },
  { name: 'watermark_text', label: 'Watermark text', maxLength: 40,
    validate: (v, vals) => (vals.watermark_enabled && vals.watermark_type === 'text' && !String(v || '').trim() ? 'Enter the watermark text or switch it off.' : null) },
  { name: 'watermark_size_mm', label: 'Logo watermark width (mm)', type: 'number', min: 30, max: 180, integer: true },
  { name: 'watermark_opacity', label: 'Watermark opacity (0.02 – 0.50)', type: 'number', step: 0.01, min: 0.02, max: 0.5 },
  // Signature block is a JSON object on the server; these are flattened for editing.
  { name: 'sig_show', label: 'Show signature block', type: 'checkbox', noSubmit: true },
  { name: 'sig_label', label: 'Signature label', maxLength: 60, noSubmit: true },
  { name: 'sig_show_name', label: 'Print signatory name', type: 'checkbox', noSubmit: true },
  { name: 'sig_show_designation', label: 'Print signatory designation', type: 'checkbox', noSubmit: true },
  { name: 'sig_show_signature_image', label: 'Print stored signature image', type: 'checkbox', noSubmit: true,
    hint: 'A stored image is not proof of approval; approvals are recorded separately.' },
  { name: 'sig_space_mm', label: 'Signature space (mm)', type: 'number', min: 5, max: 60, integer: true, noSubmit: true },
];

export const SIG_KEYS = { sig_show: 'show', sig_label: 'label', sig_show_name: 'show_name', sig_show_designation: 'show_designation', sig_show_signature_image: 'show_signature_image', sig_space_mm: 'space_mm' };

export const DOCUMENT_FIELDS = [
  { name: 'document_type', label: 'Document type', type: 'select', required: true, options: lk('document_kinds') },
  { name: 'title', label: 'Title', required: true, maxLength: 200 },
  { name: 'access_classification', label: 'Access classification', type: 'select', required: true,
    options: (ctx) => ctx.lookups.access_classes.map((o) => ({ ...o, label: o.label })).filter((o) => ctx.isAdmin || o.value !== 'restricted') },
  { name: 'expiry_date', label: 'Expiry date (if applicable)', type: 'date' },
  { name: 'partner', label: 'Related partner', type: 'select', nullable: true, options: (ctx) => ctx.partners || [] },
  { name: 'tax_registration', label: 'Related tax registration', type: 'select', nullable: true, options: (ctx) => ctx.registrations || [] },
  { name: 'bank_account', label: 'Related bank account', type: 'select', nullable: true, options: (ctx) => ctx.bankAccounts || [] },
  { name: 'address', label: 'Related address', type: 'select', nullable: true, options: (ctx) => ctx.addresses || [] },
  { name: 'notes', label: 'Notes', type: 'textarea', wide: true },
];
