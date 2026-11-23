import { Link } from 'react-router-dom';
import { api } from '../../api';
import { asList, useLoad } from '../../hooks';
import { Badge, Field } from '../../ui';

export const STATUS_TONE = {
  draft: 'neutral', unpaid: 'info', overdue: 'bad', partial: 'warn', paid: 'good', void: 'neutral', open: 'info', closed: 'neutral', issued: 'info',
};

export const StatusBadge = ({ doc }) => <Badge tone={STATUS_TONE[doc.status_key] || 'neutral'}>{doc.status_label}</Badge>;

export const KINDS = [
  { value: 'invoice', label: 'Invoices', one: 'Invoice' },
  { value: 'proforma_invoice', label: 'Proforma invoices', one: 'Proforma invoice' },
  { value: 'credit_note', label: 'Credit notes', one: 'Credit note' },
  { value: 'debit_note', label: 'Debit notes', one: 'Debit note' },
];
export const kindLabel = (k) => KINDS.find((x) => x.value === k)?.one || k;

// Link to a document's page, showing its number (or "Draft" before it has one).
export const DocLink = ({ doc }) => (
  <Link to={`/sales/documents/${doc.id}`} onClick={(e) => e.stopPropagation()}>{doc.number || <em>Draft</em>}</Link>
);

export const PAYMENT_MODES = [
  ['bank_transfer', 'Bank transfer (NEFT/RTGS/IMPS)'], ['upi', 'UPI'], ['cheque', 'Cheque'], ['cash', 'Cash'], ['card', 'Card'], ['other', 'Other'],
];

// The "Project" dropdown: projects come from GET /api/v1/projects/lookup/. If that fails or is empty, only "No project" is offered.
// `current` is the project already on the record ({id, code, name}), kept in the list even if it was cancelled since.
export function ProjectField({ id, value, onChange, current, error, wide }) {
  const projects = useLoad(() => api.get('/projects/lookup/').catch(() => []), []);
  const list = asList(projects.data);
  const extra = current && !list.some((p) => p.id === current.id) ? current : null;
  return (
    <Field id={id} label="Project" error={error} wide={wide} hint="Optional. Tags the books entries of this record to the project.">
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">No project</option>
        {extra && <option value={extra.id}>{extra.code} · {extra.name}</option>}
        {list.map((p) => <option key={p.id} value={p.id}>{p.code} · {p.name}</option>)}
      </select>
    </Field>
  );
}

// Project code and name as a link-free label for lists and detail pages.
export const projectLabel = (ref) => (ref ? `${ref.code} · ${ref.name}` : '');

// "Project" filter for lists (?project=<id>): all projects, or one.
export function ProjectFilter({ value, onChange }) {
  const projects = useLoad(() => api.get('/projects/lookup/').catch(() => []), []);
  const list = asList(projects.data);
  if (!list.length && !value) return null;
  return (
    <Field id="project-filter" label="Project">
      <select id="project-filter" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">All projects</option>
        {list.map((p) => <option key={p.id} value={p.id}>{p.code} · {p.name}</option>)}
      </select>
    </Field>
  );
}
