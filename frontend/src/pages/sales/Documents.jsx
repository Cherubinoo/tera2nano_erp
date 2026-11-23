import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { money, plain } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Field, Loading, PageHeader, Tabs, fmtDate } from '../../ui';
import { KINDS, ProjectFilter, StatusBadge, kindLabel, projectLabel } from './common';

const RECEIVABLE_STATUSES = [['', 'All'], ['draft', 'Draft'], ['unpaid', 'Unpaid'], ['overdue', 'Overdue'], ['partial', 'Partially paid'], ['paid', 'Paid'], ['void', 'Void']];
const OTHER_STATUSES = [['', 'All'], ['draft', 'Draft'], ['open', 'Open'], ['void', 'Void']];

// Invoices, proforma invoices, credit notes and debit notes: one list with a tab for each kind.
export default function DocumentsPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const kind = params.get('kind') || 'invoice';
  const status = params.get('status') || '';
  const term = params.get('q') || '';
  const project = params.get('project') || '';
  const [q, setQ] = useState(term);
  const { data, error, loading, reload } = useLoad(
    () => api.get('/sales/documents/', { kind, status: status || undefined, q: term || undefined, project: project || undefined }), [kind, status, term, project]);
  const canManage = user.permissions.includes('sales.manage');
  const receivable = kind === 'invoice' || kind === 'debit_note';
  const statuses = receivable ? RECEIVABLE_STATUSES : kind === 'proforma_invoice' ? OTHER_STATUSES.filter(([k]) => k !== 'open') : OTHER_STATUSES;
    const cleanParams = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v));

  return (
    <>
      <PageHeader title="Invoices and notes" description="Everything you have billed, and where each document stands."
        actions={canManage && kind !== 'credit_note' && kind !== 'debit_note' && <Link className="btn btn-primary" to={`/sales/documents/new?kind=${kind}`}>New {kindLabel(kind).toLowerCase()}</Link>} />
      <Tabs tabs={KINDS.map((k) => ({ key: k.value, label: k.label }))} value={kind} onChange={(k) => setParams(cleanParams({ kind: k, project }), { replace: true })} />
      {(kind === 'credit_note' || kind === 'debit_note') && canManage && (
        <p className="hint">Credit and debit notes are raised from an invoice: open the invoice and choose "Create credit note" or "Create debit note".</p>
      )}
      <section className="card">
        <div className="chips" role="group" aria-label="Filter by status">
          {statuses.map(([key, label]) => (
            <button key={key} type="button" className="chip" aria-pressed={status === key} onClick={() => setParams(cleanParams({ kind, status: key, q: term, project }), { replace: true })}>{label}</button>
          ))}
        </div>
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setParams(cleanParams({ kind, status, q: q.trim(), project }), { replace: true }); }}>
          <Field id="doc-q" label="Search" className="grow"><input id="doc-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Number or customer name" /></Field>
          <ProjectFilter value={project} onChange={(val) => setParams(cleanParams({ kind, status, q: term, project: val }), { replace: true })} />
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && data?.results.length === 0 && <p className="empty">Nothing here{status || term ? ' matches' : ' yet'}.</p>}
        {data?.results.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr>
                <th>Date</th><th>Number</th><th>Customer</th><th>Project</th>{receivable && <th>Due date</th>}{kind === 'credit_note' || kind === 'debit_note' ? <th>Against</th> : null}
                <th>Status</th><th className="amount">Amount</th>{receivable && <th className="amount">Balance due</th>}{kind === 'credit_note' && <th className="amount">Credit left</th>}
              </tr></thead>
              <tbody>
                {data.results.map((d) => (
                  <tr key={d.id} className="clickable" onClick={() => navigate(`/sales/documents/${d.id}`)}>
                    <td data-label="Date">{fmtDate(d.issue_date)}</td>
                    <td data-label="Number"><Link to={`/sales/documents/${d.id}`} onClick={(e) => e.stopPropagation()}><strong>{d.number || 'Draft'}</strong></Link></td>
                    <td data-label="Customer">{d.customer.name}</td>
                    <td data-label="Project">{d.project_ref ? projectLabel(d.project_ref) : '—'}</td>
                    {receivable && <td data-label="Due date">{fmtDate(d.due_date)}</td>}
                    {kind === 'credit_note' || kind === 'debit_note' ? <td data-label="Against">{d.against?.number || '—'}</td> : null}
                    <td data-label="Status"><StatusBadge doc={d} /></td>
                    <td className="amount" data-label="Amount">{plain(d.grand_total)}</td>
                    {receivable && <td className="amount" data-label="Balance due">{d.balance === null ? '—' : <strong>{plain(d.balance)}</strong>}</td>}
                    {kind === 'credit_note' && <td className="amount" data-label="Credit left">{d.credit_left === null ? '—' : plain(d.credit_left)}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data?.results.length > 0 && (
          <div className="list-foot">
            <span>{data.count} document{data.count === 1 ? '' : 's'}</span>
            <span>Total <strong>{money(data.total_amount)}</strong></span>
            {receivable && <span>Balance due <strong>{money(data.total_balance)}</strong></span>}
          </div>
        )}
      </section>
    </>
  );
}
