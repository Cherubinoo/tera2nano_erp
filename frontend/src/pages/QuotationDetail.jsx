import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../context';
import { useLoad } from '../hooks';
import { ApprovalActions, ApprovalPanel } from '../modules/approvals/ApprovalBadge';
import { Badge, ErrorBox, Loading, Notice, PageHeader, fmtDate, useToast } from '../ui';
import { Downloads } from './Letters';
import { QUOTE_TONE, inr } from './Quotations';

// A quotation that is waiting for approval (not issued yet: no ID, no PDF) or was sent back to a draft. Once approved it is an
// ordinary issued quotation and this page links to it.
export default function QuotationDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const notify = useToast();
  const { data: q, error, loading, reload } = useLoad(() => api.get(`/sales/quotes/${id}/`), [id]);
  const doc = useLoad(() => (q?.issued ? api.get(`/company/issued-documents/${q.issued.id}/`) : Promise.resolve(null)), [q?.issued?.id]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const decide = user.permissions.includes('sales.manage');
  const canAct = decide || (q.created_by_id && String(q.created_by_id) === String(user.id));
  const items = q.content.items || [];

  const send = async () => {
    try {
      const r = await api.post(`/sales/quotes/${id}/issue/`);
      notify(r.status === 'pending_approval' ? 'Quotation sent for approval. It is issued once approved.' : `Quotation issued: ${r.issued.reference_number}`);
      reload();
    } catch (e) { notify(e.message, 'error'); }
  };
  const remove = async () => {
    if (!window.confirm('Delete this draft quotation?')) return;
    try { await api.del(`/sales/quotes/${id}/`); navigate('/company/quotations'); } catch (e) { notify(e.message, 'error'); }
  };

  return (
    <>
      <Link to="/company/quotations" className="back-link">← Quotations</Link>
      <PageHeader title={q.issued ? q.issued.reference_number : `Quotation for ${q.customer_name}`} description={`${q.customer_name} · dated ${fmtDate(q.issue_date)}${q.subject ? ` · ${q.subject}` : ''}`}
        actions={(
          <>
            <Badge tone={QUOTE_TONE[q.status] || 'neutral'}>{q.status_label}</Badge>
            <ApprovalPanel kind="quotation" sourceId={q.id} waiting={q.status === 'pending_approval'} canSubmit={canAct} onChange={reload} refreshKey={q.status} />
            {q.status === 'pending_approval' && (
              <ApprovalActions base={`/sales/quotes/${id}`} canAct={canAct} canDecide={decide} managed={q.approval_managed} noun="quotation" onChange={reload} />
            )}
            {q.status === 'issued' && q.issued && decide && (
              <Link className="btn btn-primary" to={`/sales/documents/new?kind=invoice&quotation=${q.issued.id}`}>Create invoice</Link>
            )}
            {canAct && q.status === 'draft' && (
              <>
                <Link className="btn" to={`/company/quotations?edit=${id}`}>Edit</Link>
                <button type="button" className="btn btn-primary" onClick={send}>Send quotation</button>
                <button type="button" className="btn" onClick={remove}>Delete</button>
              </>
            )}
          </>
        )} />
      {q.status === 'pending_approval' && <Notice>This quotation is above an approval limit. It has no ID and no PDF until it is approved; approving issues it exactly as if it had been issued directly.</Notice>}
      {q.status === 'draft' && q.decision_note && <Notice type="warn">{q.decision_note}</Notice>}
      {q.status === 'draft' && !q.decision_note && <Notice>This quotation has not been issued.</Notice>}
      {q.status === 'issued' && q.issued && (
        <Notice type="success">
          <p><strong>Issued.</strong> ID: <code>{q.issued.reference_number}</code>{q.decision_note ? ` · Approval note: ${q.decision_note}` : ''}</p>
          {doc.data && <div className="row-actions"><Downloads doc={doc.data} onError={(e) => notify(e.message, 'error')} /></div>}
        </Notice>
      )}
      <div className="tiles">
        <div className="tile"><div className="tile-label">Total</div><div className="tile-value">{q.currency} {inr(q.total)}</div></div>
        <div className="tile"><div className="tile-label">Items</div><div className="tile-value">{items.length}</div></div>
        <div className="tile"><div className="tile-label">Prepared by</div><div className="tile-value">{q.created_by || '—'}</div></div>
      </div>
      <section className="card">
        <h2>Items</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Description</th><th className="amount">Qty</th><th className="amount">Rate</th><th className="amount">Discount %</th><th className="amount">GST %</th></tr></thead>
            <tbody>
              {items.map((i, n) => (
                <tr key={n}>
                  <td data-label="Description">{i.description}{i.hsn_sac && <div className="hint">HSN/SAC {i.hsn_sac}</div>}</td>
                  <td className="amount" data-label="Qty">{i.quantity}{i.unit ? ` ${i.unit}` : ''}</td>
                  <td className="amount" data-label="Rate">{inr(i.rate)}</td>
                  <td className="amount" data-label="Discount %">{i.discount_percent || '—'}</td>
                  <td className="amount" data-label="GST %">{i.gst_rate ?? 'Default'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
