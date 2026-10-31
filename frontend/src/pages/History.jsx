import { Fragment, useState } from 'react';
import { api } from '../api';
import { useLoad } from '../hooks';
import { Badge, ErrorBox, Field, Loading, fmtDateTime } from '../ui';

// Suggestions only: the API accepts any value for these filters.
const ENTITIES = ['profile', 'address', 'partner', 'signatory', 'contact', 'tax_registration', 'tax_settings', 'bank_account', 'branding', 'numbering', 'financial_year', 'policy', 'document', 'issued_document'];
const CATEGORIES = ['legal_name', 'registration', 'gst', 'bank', 'signatory', 'address', 'branding', 'numbering', 'payment_terms', 'approval_thresholds', 'tax', 'financial_year', 'policy', 'document'];
const ACTIONS = ['created', 'updated', 'deactivated', 'activated', 'deleted', 'verified', 'revealed', 'downloaded', 'issued'];
const PAGE_SIZE = 25;

const show = (v) => (v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v));

function Detail({ ev }) {
  return (
    <div className="event-detail">
      {ev.changed_fields.length > 0 ? (
        <table>
          <thead><tr><th>Field</th><th>Previous</th><th>New</th></tr></thead>
          <tbody>
            {ev.changed_fields.map((f) => (
              <tr key={f}><th scope="row">{f.replace(/_/g, ' ')}</th><td>{show(ev.previous_values[f])}</td><td>{show(ev.new_values[f])}</td></tr>
            ))}
          </tbody>
        </table>
      ) : <p className="muted">No field values recorded for this event.</p>}
      <dl className="meta">
        <dt>Reason</dt><dd>{ev.reason || '—'}</dd>
        <dt>Request ID</dt><dd><code>{ev.request_id || '—'}</code></dd>
        <dt>Record ID</dt><dd><code>{ev.entity_id || '—'}</code></dd>
        <dt>Event ID</dt><dd><code>{ev.id}</code></dd>
        <dt>Company version after</dt><dd>{ev.profile_version ?? '—'}</dd>
      </dl>
      <p className="hint">Sensitive values such as full account numbers, DPINs and personal contact details are never copied into the history.</p>
    </div>
  );
}

export default function ChangeHistory() {
  const [filters, setFilters] = useState({ entity_type: '', category: '', action: '', from: '', to: '' });
  const [applied, setApplied] = useState(filters);
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState(null);
  const { data, error, loading, reload } = useLoad(() => api.get('/company/audit-history/', { ...applied, page, page_size: PAGE_SIZE }), [JSON.stringify(applied), page]);
  const [rangeError, setRangeError] = useState('');

  const apply = (e) => {
    e.preventDefault();
    if (filters.from && filters.to && filters.to < filters.from) { setRangeError('"To" date cannot be before "From" date.'); return; }
    setRangeError('');
    setPage(1);
    setApplied(filters);
  };
  const clear = () => { const empty = { entity_type: '', category: '', action: '', from: '', to: '' }; setFilters(empty); setApplied(empty); setPage(1); setRangeError(''); };
  const set = (k) => (e) => setFilters({ ...filters, [k]: e.target.value });
  const rows = data?.results || [];
  const pages = data ? Math.max(1, Math.ceil(data.count / PAGE_SIZE)) : 1;

  return (
    <>
      <form className="card filters" onSubmit={apply} noValidate>
        <div className="form-grid">
          <Field id="h-entity" label="Record type"><input id="h-entity" list="h-entities" value={filters.entity_type} onChange={set('entity_type')} /></Field>
          <Field id="h-cat" label="Category"><input id="h-cat" list="h-cats" value={filters.category} onChange={set('category')} /></Field>
          <Field id="h-action" label="Action"><input id="h-action" list="h-actions" value={filters.action} onChange={set('action')} /></Field>
          <Field id="h-from" label="From date"><input id="h-from" type="date" value={filters.from} onChange={set('from')} /></Field>
          <Field id="h-to" label="To date" error={rangeError}><input id="h-to" type="date" value={filters.to} onChange={set('to')} aria-describedby={rangeError ? 'h-to-err' : undefined} /></Field>
        </div>
        <datalist id="h-entities">{ENTITIES.map((x) => <option key={x} value={x} />)}</datalist>
        <datalist id="h-cats">{CATEGORIES.map((x) => <option key={x} value={x} />)}</datalist>
        <datalist id="h-actions">{ACTIONS.map((x) => <option key={x} value={x} />)}</datalist>
        <div className="actions">
          <button type="submit" className="btn btn-primary">Apply filters</button>
          <button type="button" className="btn" onClick={clear}>Clear</button>
        </div>
      </form>

      <section className="card" aria-label="Change history">
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && !rows.length && <p className="empty">No changes match these filters.</p>}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>When</th><th>Who</th><th>Record</th><th>Action</th><th>Category</th><th>Changed</th><th><span className="sr-only">Details</span></th></tr></thead>
              <tbody>
                {rows.map((ev) => (
                  <Fragment key={ev.id}>
                    <tr>
                      <td data-label="When">{fmtDateTime(ev.occurred_at)}</td>
                      <td data-label="Who">{ev.actor_label || '—'}</td>
                      <td data-label="Record">{ev.entity_type.replace(/_/g, ' ')}</td>
                      <td data-label="Action"><Badge tone={ev.action === 'revealed' ? 'warn' : 'neutral'}>{ev.action}</Badge></td>
                      <td data-label="Category">{ev.category || '—'}</td>
                      <td data-label="Changed">{ev.changed_fields.length ? ev.changed_fields.map((f) => f.replace(/_/g, ' ')).join(', ') : '—'}</td>
                      <td className="row-actions">
                        <button type="button" className="btn btn-small" aria-expanded={open === ev.id} onClick={() => setOpen(open === ev.id ? null : ev.id)}>{open === ev.id ? 'Hide' : 'Details'}</button>
                      </td>
                    </tr>
                    {open === ev.id && <tr className="detail-row"><td colSpan={7}><Detail ev={ev} /></td></tr>}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.count > 0 && (
          <nav className="pager" aria-label="Pagination">
            <button type="button" className="btn btn-small" disabled={!data.previous || loading} onClick={() => setPage((p) => Math.max(1, p - 1))}>Previous</button>
            <span>Page {page} of {pages} · {data.count} events</span>
            <button type="button" className="btn btn-small" disabled={!data.next || loading} onClick={() => setPage((p) => p + 1)}>Next</button>
          </nav>
        )}
      </section>
    </>
  );
}
