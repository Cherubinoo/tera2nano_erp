import { Link } from 'react-router-dom';
import { api } from '../../api';
import { useLoad } from '../../hooks';
import { Badge, Field } from '../../ui';

export const STATUS_TONE = { posted: 'good', pending_approval: 'warn', rejected: 'bad', void: 'neutral', draft: 'info', reversed: 'neutral' };
export const STATUS_LABEL = { posted: 'Posted', pending_approval: 'Waiting for approval', rejected: 'Rejected', void: 'Void', draft: 'Draft', reversed: 'Reversed' };

export const StatusBadge = ({ status, label }) => <Badge tone={STATUS_TONE[status] || 'neutral'}>{label || STATUS_LABEL[status] || status}</Badge>;

export const PartnerLink = ({ id, children }) => <Link to={`/partners/accounts/${id}`}>{children}</Link>;

// Partners, transaction kinds, modes, cash and bank accounts and the settings, for the forms and filters.
export function usePartnerLookups() {
  return useLoad(() => api.get('/partners/lookups/'), []);
}

// The current financial year (April to March) containing a date.
export function fyOf(iso) {
  const [y, m] = iso.split('-').map(Number);
  const start = m >= 4 ? y : y - 1;
  return { start: `${start}-04-01`, end: `${start + 1}-03-31`, label: `${start}-${String((start + 1) % 100).padStart(2, '0')}` };
}

export const signed = (v) => Number(v) < 0;

export function PartnerSelect({ id, label = 'Partner', value, onChange, partners, blank, required, error }) {
  return (
    <Field id={id} label={label} required={required} error={error}>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {blank !== undefined && <option value="">{blank}</option>}
        {(partners || []).map((p) => <option key={p.id} value={p.id}>{p.name}{p.is_active ? '' : ' (ceased)'}</option>)}
      </select>
    </Field>
  );
}
