import { useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, downloadFile } from '../../api';
import { useAuth } from '../../context';
import { downloadCsv, money, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, fmtDate, useConfirm, useToast } from '../../ui';
import { LeadStatus, OwnerSelect, Pager, RatingBadge, query, useLookups } from './common';

const BLANK = { company_name: '', contact_name: '', email: '', phone: '', source: '', industry: '', product_interest: '', estimated_value: '', status: 'new',
  rating: 'warm', city: '', state: '', notes: '', owner: '' };

export function DuplicateList({ duplicates }) {
  const path = { lead: 'leads', contact: 'contacts', account: 'accounts' };
  return (
    <ul className="crm-plain">
      {duplicates.map((d) => (
        <li key={`${d.type}${d.id}`}>
          <span><Badge>{d.type}</Badge> <Link to={`/crm/${path[d.type]}/${d.id}`} target="_blank" rel="noreferrer">{d.name}</Link>{d.detail && <span className="muted"> · {d.detail}</span>}</span>
          <span className="muted">same {d.matched_on.join(', ')}</span>
        </li>
      ))}
    </ul>
  );
}

export function LeadDialog({ lead, lookups, onClose, onSaved }) {
  const notify = useToast();
  const [v, setV] = useState(() => (lead ? Object.fromEntries(Object.keys(BLANK).map((k) => [k, k === 'owner' ? lead.owner?.id || '' : lead[k] ?? ''])) : { ...BLANK, source: 'website', owner: '' }));
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [duplicates, setDuplicates] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };
  const text = (k, label, props = {}) => (
    <Field id={`lead-${k}`} label={label} error={errors[k]} required={props.required}><input id={`lead-${k}`} value={v[k]} onChange={set(k)} {...props} /></Field>
  );
  const save = async (allowDuplicates) => {
    setFailure(null); setDuplicates(null);
    const e = {};
    if (!v.company_name.trim() && !v.contact_name.trim()) e.company_name = 'Enter the company or the contact person.';
    if (v.email && !/^\S+@\S+\.\S+$/.test(v.email)) e.email = 'Enter a valid email address.';
    if (v.estimated_value !== '' && !(num(v.estimated_value) >= 0)) e.estimated_value = 'Enter an amount of 0 or more.';
    setErrors(e);
    if (Object.keys(e).length) return;
    setBusy(true);
    const body = { ...v, estimated_value: v.estimated_value === '' ? null : v.estimated_value, owner: v.owner || null, rating: v.rating || 'warm' };
    if (allowDuplicates) body.allow_duplicates = true;
    try {
      const saved = lead ? await api.patch(`/crm/leads/${lead.id}/`, body) : await api.post('/crm/leads/', body);
      notify(lead ? 'Lead saved.' : `Lead ${saved.reference_code} created.`);
      onSaved(saved);
    } catch (err) {
      if (err.status === 409 && err.data?.duplicates) setDuplicates(err.data.duplicates);
      else { setFailure(err); setErrors(err.fieldErrors || {}); }
    }
    setBusy(false);
  };
  const statuses = (lookups.lead_statuses || []).filter((s) => s.value !== 'converted');
  return (
    <Dialog title={lead ? `Edit ${lead.display_name}` : 'New lead'} onClose={onClose} wide>
      <form onSubmit={(ev) => { ev.preventDefault(); ev.stopPropagation(); save(false); }} noValidate>
        <FormErrors error={failure} fields={Object.keys(BLANK).map((name) => ({ name }))} />
        {duplicates && (
          <div className="notice notice-warn" role="alert">
            <div><strong>This looks like a record you already have.</strong> Open it instead, or save this one anyway.
              <DuplicateList duplicates={duplicates} /></div>
            <button type="button" className="btn btn-small" onClick={() => save(true)} disabled={busy}>Save anyway</button>
          </div>
        )}
        <div className="form-grid">
          {text('company_name', 'Company', { maxLength: 200 })}
          {text('contact_name', 'Contact person', { maxLength: 150 })}
          {text('email', 'Email', { type: 'email' })}
          {text('phone', 'Phone', { maxLength: 30 })}
          <Field id="lead-source" label="Source" error={errors.source}>
            <select id="lead-source" value={v.source} onChange={set('source')}>
              {(lookups.sources || []).map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
              {v.source && !(lookups.sources || []).some((s) => s.key === v.source) && <option value={v.source}>{v.source}</option>}
            </select>
          </Field>
          <Field id="lead-product_interest" label="Interested in" error={errors.product_interest}>
            <input id="lead-product_interest" list="crm-interests" value={v.product_interest} onChange={set('product_interest')} maxLength={150} />
            <datalist id="crm-interests">{(lookups.product_interests || []).map((p) => <option key={p} value={p} />)}</datalist>
          </Field>
          {text('estimated_value', 'Estimated value (INR)', { type: 'number', min: 0, step: '0.01' })}
          {text('industry', 'Industry', { maxLength: 100 })}
          <Field id="lead-status" label="Status" error={errors.status}>
            <select id="lead-status" value={v.status} onChange={set('status')}>{statuses.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</select>
          </Field>
          <Field id="lead-rating" label="Rating" error={errors.rating} hint={lead ? `Suggested from the score: ${lead.suggested_rating}.` : undefined}>
            <select id="lead-rating" value={v.rating} onChange={set('rating')}>{(lookups.ratings || []).map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</select>
          </Field>
          {text('city', 'City', { maxLength: 100 })}
          {text('state', 'State', { maxLength: 100 })}
          <OwnerSelect id="lead-owner" label="Owner" value={v.owner} onChange={(x) => setV((s) => ({ ...s, owner: x }))} lookups={lookups} error={errors.owner} />
        </div>
        <Field id="lead-notes" label="Notes" wide error={errors.notes}><textarea id="lead-notes" rows={3} value={v.notes} onChange={set('notes')} /></Field>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save lead'}</button>
        </div>
      </form>
    </Dialog>
  );
}

const TEMPLATE = [['company_name', 'contact_name', 'email', 'phone', 'source', 'industry', 'product_interest', 'estimated_value', 'status', 'rating', 'city', 'state', 'notes'],
  ['Sunrise Packaging', 'Meera Nair', 'meera@sunrise.example', '9876500011', 'Referral', 'Manufacturing', 'IT solutions', '250000', 'new', 'warm', 'Pune', 'Maharashtra', 'Asked for a demo']];

export function ImportDialog({ lookups, onClose, onDone }) {
  const notify = useToast();
  const input = useRef(null);
  const [text, setText] = useState('');
  const [fileName, setFileName] = useState('');
  const [preview, setPreview] = useState(null);
  const [withDuplicates, setWithDuplicates] = useState(false);
  const [owner, setOwner] = useState('');
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);

  const run = async (csv, commit, dup = withDuplicates) => {
    setBusy(true); setFailure(null);
    try {
      const r = await api.post('/crm/leads/import/', { csv, commit, import_duplicates: dup, owner: owner || undefined });
      if (commit) { notify(`${r.created} lead${r.created === 1 ? '' : 's'} imported.`); onDone(); return; }
      setPreview(r);
    } catch (err) { setFailure(err); setPreview(null); }
    setBusy(false);
  };
  const pick = async (e) => {
    const f = e.target.files?.[0];
    setPreview(null);
    if (!f) return;
    if (f.size > 1024 * 1024) { setFailure({ message: 'The file is larger than 1 MB. Split it into smaller files.', fieldErrors: {} }); return; }
    const body = await f.text();
    setText(body); setFileName(f.name);
    run(body, false);
  };
  const importable = preview ? (withDuplicates ? preview.total - preview.errors : preview.total - preview.errors - preview.duplicates) : 0;
  return (
    <Dialog title="Import leads from CSV" onClose={onClose} wide>
      <p className="muted">The first row must hold column headings such as company_name, contact_name, email, phone, source, estimated_value. Each row is checked on its own; nothing is saved until you confirm.</p>
      <FormErrors error={failure} fields={[{ name: 'csv' }]} />
      <div className="toolbar">
        <Field id="imp-file" label="CSV file" className="grow"><input id="imp-file" type="file" accept=".csv,text/csv" ref={input} onChange={pick} /></Field>
        <button type="button" className="btn" onClick={() => downloadCsv('lead-import-template.csv', TEMPLATE)}>Download template</button>
      </div>
      <OwnerSelect id="imp-owner" label="Assign imported leads to" value={owner} onChange={(x) => { setOwner(x); setPreview(null); }} lookups={lookups} anyLabel="Me" any />
      {busy && !preview && <Loading label="Checking the file…" />}
      {preview && (
        <>
          <div className="tiles crm-tiles-small">
            <div className="tile"><div className="tile-label">Rows</div><div className="tile-value">{preview.total}</div></div>
            <div className="tile good"><div className="tile-label">Ready</div><div className="tile-value">{importable}</div></div>
            <div className={`tile ${preview.duplicates ? 'warn' : ''}`}><div className="tile-label">Duplicates</div><div className="tile-value">{preview.duplicates}</div></div>
            <div className={`tile ${preview.errors ? 'bad' : ''}`}><div className="tile-label">Errors</div><div className="tile-value">{preview.errors}</div></div>
          </div>
          {preview.duplicates > 0 && (
            <label className="check"><input type="checkbox" checked={withDuplicates} onChange={(e) => setWithDuplicates(e.target.checked)} /> Import the {preview.duplicates} duplicate row{preview.duplicates === 1 ? '' : 's'} as well</label>
          )}
          <div className="table-wrap crm-import-rows">
            <table>
              <caption className="sr-only">Rows of {fileName}</caption>
              <thead><tr><th>Row</th><th>Company</th><th>Contact</th><th>Email</th><th>Result</th></tr></thead>
              <tbody>
                {preview.rows.map((r) => (
                  <tr key={r.row} className={r.status === 'error' ? 'crm-row-bad' : ''}>
                    <td data-label="Row">{r.row}</td><td data-label="Company">{r.data.company_name || '—'}</td><td data-label="Contact">{r.data.contact_name || '—'}</td>
                    <td data-label="Email">{r.data.email || '—'}</td>
                    <td data-label="Result">
                      {r.status === 'ok' && <Badge tone="good">Ready</Badge>}
                      {r.status === 'duplicate' && <><Badge tone="warn">Duplicate</Badge><span className="crm-sub">{r.duplicates.join('; ')}</span></>}
                      {r.status === 'error' && <><Badge tone="bad">Error</Badge><span className="crm-sub">{Object.entries(r.errors).map(([k, m]) => `${k.replace(/_/g, ' ')}: ${m}`).join('; ')}</span></>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <div className="dialog-foot-inline">
        <button type="button" className="btn" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={busy || !preview || importable === 0} onClick={() => run(text, true)}>
          {busy && preview ? 'Importing…' : `Import ${preview ? importable : ''} lead${importable === 1 ? '' : 's'}`}
        </button>
      </div>
    </Dialog>
  );
}

const STATUS_CHIPS = [['', 'All'], ['open', 'Open']];

export default function Leads() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const notify = useToast();
  const lookups = useLookups();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = useState(params.get('new') === '1');
  const [importing, setImporting] = useState(false);
  const [selected, setSelected] = useState([]);
  const [bulkOwner, setBulkOwner] = useState('');
  const [bulkStatus, setBulkStatus] = useState('');
  const get = (k) => params.get(k) || '';
  const status = get('status'), q = get('q'), source = get('source'), rating = get('rating'), owner = get('owner'), page = Number(get('page') || 1);
  const [term, setTerm] = useState(q);
  const canManage = user.permissions.includes('crm.manage');
  const { data, error, loading, reload } = useLoad(() => api.get('/crm/leads/', { status, q, source, rating, owner, page, page_size: 25 }), [status, q, source, rating, owner, page]);
  const setParam = (changes) => {
    const next = { status, q, source, rating, owner, ...changes, page: changes.page || '' };
    setParams(Object.fromEntries(Object.entries(next).filter(([, v]) => v)), { replace: true });
    setSelected([]);
  };
  const rows = data?.results || [];
  const counts = data?.status_counts || {};
  const total = Object.values(counts).reduce((a, n) => a + n, 0);
  const openCount = total - (counts.converted || 0) - (counts.unqualified || 0);
  const chips = [...STATUS_CHIPS, ...(lookups.lead_statuses || []).map((s) => [s.value, s.label])];
  const chipCount = (k) => (k === '' ? total : k === 'open' ? openCount : counts[k] || 0);
  const allOn = rows.length > 0 && rows.every((r) => selected.includes(r.id));
  const toggle = (id) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  const bulk = async (action, extra, label) => {
    try {
      const r = await api.post('/crm/leads/bulk/', { ids: selected, action, ...extra });
      notify(`${label}: ${r.done} lead${r.done === 1 ? '' : 's'}${r.skipped_converted ? ` (${r.skipped_converted} converted lead${r.skipped_converted === 1 ? '' : 's'} left alone)` : ''}.`);
      setSelected([]); setBulkOwner(''); setBulkStatus(''); reload();
    } catch (e) { notify(e.message, 'error'); }
  };
  const bulkDelete = async () => {
    if (!(await confirm({ title: `Delete ${selected.length} lead${selected.length === 1 ? '' : 's'}?`, body: <p>Deleted leads cannot be brought back. Converted leads are never deleted.</p>, confirmLabel: 'Delete', danger: true }))) return;
    bulk('delete', {}, 'Deleted');
  };
  const exportCsv = async () => {
    try { await downloadFile(`/crm/reports/leads.csv${query({ status: status === 'open' ? '' : status, owner })}`, 'crm-leads.csv'); } catch (e) { notify(e.message, 'error'); }
  };

  return (
    <>
      <PageHeader title="Leads" description="People and companies that might become customers."
        actions={<>
          <button type="button" className="btn" onClick={exportCsv}>Export CSV</button>
          {canManage && <button type="button" className="btn" onClick={() => setImporting(true)}>Import CSV</button>}
          {canManage && <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>New lead</button>}
        </>} />
      <section className="card">
        <div className="chips" role="group" aria-label="Filter leads by status">
          {chips.map(([k, l]) => <button key={k || 'all'} type="button" className="chip" aria-pressed={status === k} onClick={() => setParam({ status: k })}>{l} ({chipCount(k)})</button>)}
        </div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setParam({ q: term.trim() }); }}>
          <Field id="lead-q" label="Search" className="grow"><input id="lead-q" value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Company, contact, email, phone or ID" /></Field>
          <Field id="lead-f-source" label="Source">
            <select id="lead-f-source" value={source} onChange={(e) => setParam({ source: e.target.value })}><option value="">Any</option>{(lookups.sources || []).map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}</select>
          </Field>
          <Field id="lead-f-rating" label="Rating">
            <select id="lead-f-rating" value={rating} onChange={(e) => setParam({ rating: e.target.value })}><option value="">Any</option>{(lookups.ratings || []).map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</select>
          </Field>
          <OwnerSelect id="lead-f-owner" label="Owner" value={owner} onChange={(x) => setParam({ owner: x })} lookups={lookups} any extra={[['me', 'Me'], ['none', 'Unassigned']]} />
          <button type="submit" className="btn">Search</button>
        </form>

        {canManage && selected.length > 0 && (
          <div className="crm-bulk" role="region" aria-label="Bulk actions">
            <strong>{selected.length} selected</strong>
            <select aria-label="Assign selected leads to" value={bulkOwner} onChange={(e) => setBulkOwner(e.target.value)}>
              <option value="">Assign to…</option>{(lookups.owners || []).map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
            <button type="button" className="btn btn-small" disabled={!bulkOwner} onClick={() => bulk('assign', { owner: bulkOwner }, 'Assigned')}>Assign</button>
            <select aria-label="Set status of selected leads" value={bulkStatus} onChange={(e) => setBulkStatus(e.target.value)}>
              <option value="">Set status…</option>{(lookups.lead_statuses || []).filter((s) => s.value !== 'converted').map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
            <button type="button" className="btn btn-small" disabled={!bulkStatus} onClick={() => bulk('status', { status: bulkStatus }, 'Updated')}>Set status</button>
            <button type="button" className="btn btn-small btn-danger-outline" onClick={bulkDelete}>Delete</button>
            <button type="button" className="btn btn-small" onClick={() => setSelected([])}>Clear</button>
          </div>
        )}

        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {data && rows.length === 0 && <p className="empty">{q || source || rating || owner || status ? 'No lead matches these filters.' : 'No leads yet. Add the first one or import a CSV.'}</p>}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr>
                {canManage && <th><input type="checkbox" aria-label="Select all leads on this page" checked={allOn} onChange={() => setSelected(allOn ? [] : rows.map((r) => r.id))} /></th>}
                <th>Lead</th><th>Source</th><th>Status</th><th>Rating</th><th className="amount">Score</th><th className="amount">Value</th><th>Owner</th><th>Created</th></tr></thead>
              <tbody>
                {rows.map((l) => (
                  <tr key={l.id} className="clickable" onClick={() => navigate(`/crm/leads/${l.id}`)}>
                    {canManage && <td data-label="Select" onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label={`Select ${l.display_name}`} checked={selected.includes(l.id)} onChange={() => toggle(l.id)} /></td>}
                    <td data-label="Lead"><Link to={`/crm/leads/${l.id}`} onClick={(e) => e.stopPropagation()}><strong>{l.display_name}</strong></Link>
                      <span className="crm-sub">{l.reference_code}{l.contact_name && l.company_name ? ` · ${l.contact_name}` : ''}</span></td>
                    <td data-label="Source">{l.source_label}</td>
                    <td data-label="Status"><LeadStatus lead={l} /></td>
                    <td data-label="Rating"><RatingBadge lead={l} /></td>
                    <td className="amount" data-label="Score">{l.score}</td>
                    <td className="amount" data-label="Value">{num(l.estimated_value) ? plain(l.estimated_value) : '—'}</td>
                    <td data-label="Owner">{l.owner ? l.owner.name : <span className="muted">Unassigned</span>}</td>
                    <td data-label="Created">{fmtDate(l.created_at?.slice(0, 10))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && <Pager page={data.page} count={data.count} size={data.page_size} onPage={(p) => setParam({ page: String(p) })} />}
        {rows.length > 0 && <div className="list-foot"><span>{data.count} lead{data.count === 1 ? '' : 's'}</span><span>Estimated value <strong>{money(data.total_value)}</strong></span></div>}
      </section>
      {creating && lookups.ready && <LeadDialog lookups={lookups} onClose={() => { setCreating(false); if (params.get('new')) setParam({}); }} onSaved={(l) => { setCreating(false); navigate(`/crm/leads/${l.id}`); }} />}
      {importing && <ImportDialog lookups={lookups} onClose={() => setImporting(false)} onDone={() => { setImporting(false); reload(); }} />}
      {confirmDialog}
      {!canManage && <Notice type="info">You can view leads. Ask a partner or administrator to change them.</Notice>}
    </>
  );
}
