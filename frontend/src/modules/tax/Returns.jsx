import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { useLoad } from '../../hooks';
import { Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, fmtDate, useToast } from '../../ui';
import { StatusBadge, fyNow, has, useTaxOptions } from './common';

const STATUS_CHIPS = [['', 'All'], ['pending', 'Pending'], ['prepared', 'Prepared'], ['filed', 'Filed'], ['late', 'Late']];
const GROUPS = [['', 'All returns'], ['gst', 'GST'], ['tds', 'TDS'], ['income_tax', 'Income tax and audit']];

export function NewReturnDialog({ opts, defaults = {}, onClose, onSaved }) {
  const notify = useToast();
  const [v, setV] = useState({ return_type: defaults.return_type || 'gstr3b', financial_year: defaults.financial_year || fyNow(), period: defaults.period || '', quarter: defaults.quarter || '1', audit_applicable: false, due_date: '', notes: '' });
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const kind = opts.return_types.find((t) => t.value === v.return_type)?.kind;
  const set = (k, val) => setV((x) => ({ ...x, [k]: val }));
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const errs = {};
    if (kind === 'monthly' && !v.period) errs.period = 'Choose the month.';
    if (Object.keys(errs).length) { setErr({ message: 'Please correct the highlighted fields.', fieldErrors: errs }); return; }
    setBusy(true);
    setErr(null);
    try {
      const body = { return_type: v.return_type, financial_year: v.financial_year, notes: v.notes, due_date: v.due_date || undefined };
      if (kind === 'monthly') body.period = v.period;
      if (kind === 'quarterly') body.quarter = v.quarter;
      if (v.return_type === 'itr5') body.audit_applicable = v.audit_applicable;
      const f = await api.post('/tax/filings/', body);
      notify(`Return record created: ${f.reference}`);
      onSaved(f);
    } catch (x) { setErr(x); }
    setBusy(false);
  };
  const fe = err?.fieldErrors || {};
  return (
    <Dialog title="New return record" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <FormErrors error={err} fields={[{ name: 'return_type' }, { name: 'financial_year' }, { name: 'period' }, { name: 'quarter' }, { name: 'due_date' }, { name: 'notes' }]} />
        <div className="form-grid">
          <Field id="nr-type" label="Return" required error={fe.return_type}>
            <select id="nr-type" value={v.return_type} onChange={(e) => set('return_type', e.target.value)}>
              {opts.return_types.map((t) => <option key={t.value} value={t.value}>{t.long}</option>)}
            </select>
          </Field>
          <Field id="nr-fy" label="Financial year" required error={fe.financial_year}>
            <select id="nr-fy" value={v.financial_year} onChange={(e) => set('financial_year', e.target.value)}>{opts.financial_years.map((y) => <option key={y}>{y}</option>)}</select>
          </Field>
          {kind === 'monthly' && <Field id="nr-period" label="Month" required error={fe.period}><input id="nr-period" type="month" value={v.period} onChange={(e) => set('period', e.target.value)} /></Field>}
          {kind === 'quarterly' && (
            <Field id="nr-quarter" label="Quarter" required error={fe.quarter}>
              <select id="nr-quarter" value={v.quarter} onChange={(e) => set('quarter', e.target.value)}>
                <option value="1">Q1 (Apr to Jun)</option><option value="2">Q2 (Jul to Sep)</option><option value="3">Q3 (Oct to Dec)</option><option value="4">Q4 (Jan to Mar)</option>
              </select>
            </Field>
          )}
          {v.return_type === 'itr5' && (
            <div className="field field-check"><label className="check"><input type="checkbox" checked={v.audit_applicable} onChange={(e) => set('audit_applicable', e.target.checked)} /> Accounts are audited (later due date)</label></div>
          )}
          <Field id="nr-due" label="Due date" hint="Leave empty to use the statutory date." error={fe.due_date}><input id="nr-due" type="date" value={v.due_date} onChange={(e) => set('due_date', e.target.value)} /></Field>
          <Field id="nr-notes" label="Notes" wide error={fe.notes}><input id="nr-notes" value={v.notes} maxLength={500} onChange={(e) => set('notes', e.target.value)} /></Field>
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Creating…' : 'Create record'}</button>
        </div>
      </form>
    </Dialog>
  );
}

function GenerateDialog({ opts, onClose, onDone }) {
  const notify = useToast();
  const [fy, setFy] = useState(fyNow());
  const [audit, setAudit] = useState(false);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const go = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setBusy(true);
    setErr(null);
    try {
      const r = await api.post('/tax/filings/generate/', { financial_year: fy, audit_applicable: audit });
      notify(r.created ? `${r.created} return records created for ${fy}.` : `Everything for ${fy} already exists.`);
      onDone();
    } catch (x) { setErr(x); }
    setBusy(false);
  };
  return (
    <Dialog title="Create the year's returns" onClose={onClose}>
      <form onSubmit={go}>
        <FormErrors error={err} fields={[{ name: 'financial_year' }]} />
        <p>Creates every return the year needs, with its due date: {opts.gst_registered ? 'GSTR-1 and GSTR-3B for each month, GSTR-9, ' : ''}TDS 24Q and 26Q for each quarter, ITR-5 and the tax audit report. Records that already exist are left alone.</p>
        {!opts.gst_registered && <p className="notice notice-info">GST returns are left out because the company is not registered for GST.</p>}
        <div className="form-grid">
          <Field id="gen-fy" label="Financial year" required error={err?.fieldErrors?.financial_year}>
            <select id="gen-fy" value={fy} onChange={(e) => setFy(e.target.value)}>{opts.financial_years.map((y) => <option key={y}>{y}</option>)}</select>
          </Field>
          <div className="field field-check"><label className="check"><input type="checkbox" checked={audit} onChange={(e) => setAudit(e.target.checked)} /> Accounts are audited (ITR due 31 October)</label></div>
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Creating…' : 'Create records'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export function FilingTable({ rows, onOpen }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Return</th><th>Period</th><th>Due</th><th>Status</th><th>Reference</th><th>Acknowledgement</th><th>Filed on</th></tr></thead>
        <tbody>
          {rows.map((f) => (
            <tr key={f.id} className="clickable" onClick={() => onOpen(f)}>
              <td data-label="Return"><strong>{f.return_label}</strong></td>
              <td data-label="Period">{f.period_label}</td>
              <td data-label="Due">{fmtDate(f.due_date)}{f.overdue && <div className="hint bad-text">{-f.days_left} days late</div>}</td>
              <td data-label="Status"><StatusBadge status={f.status} />{f.filed_late && <div className="hint">Filed late</div>}</td>
              <td data-label="Reference">{f.reference}</td>
              <td data-label="Acknowledgement">{f.arn || '—'}</td>
              <td data-label="Filed on">{fmtDate(f.filed_date)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Returns() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const canManage = has(user, 'tax.manage');
  const opts = useTaxOptions();
  const [params, setParams] = useSearchParams();
  const status = params.get('status') || '';
  const group = params.get('group') || '';
  const fy = params.get('fy') || '';
  const [dialog, setDialog] = useState(params.get('generate') ? 'generate' : params.get('new') ? 'new' : null);
  const { data, error, loading, reload } = useLoad(() => api.get('/tax/filings/', { status, group, fy }), [status, group, fy]);
  useEffect(() => {
    if (params.get('new') || params.get('generate')) { const p = new URLSearchParams(params); p.delete('new'); p.delete('generate'); setParams(p, { replace: true }); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const setParam = (k, val) => { const p = new URLSearchParams(params); if (val) p.set(k, val); else p.delete(k); setParams(p, { replace: true }); };
  return (
    <>
      <PageHeader title="Returns" description="GST, TDS, income tax and audit returns: when each is due, what was filed and the acknowledgement."
        actions={canManage && (<>
          <button type="button" className="btn btn-primary" onClick={() => setDialog('new')}>New return record</button>
          <button type="button" className="btn" onClick={() => setDialog('generate')}>Create the year's returns</button>
        </>)} />
      <section className="card">
        <div className="chips" role="group" aria-label="Filter by status">
          {STATUS_CHIPS.map(([k, l]) => (
            <button key={k} type="button" className="chip" aria-pressed={status === k} onClick={() => setParam('status', k)}>
              {l}{data && ` (${k === '' ? data.counts.all : data.counts[k]})`}
            </button>
          ))}
        </div>
        <div className="toolbar">
          <Field id="rt-group" label="Kind"><select id="rt-group" value={group} onChange={(e) => setParam('group', e.target.value)}>{GROUPS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
          <Field id="rt-fy" label="Financial year"><select id="rt-fy" value={fy} onChange={(e) => setParam('fy', e.target.value)}><option value="">All years</option>{(opts.data?.financial_years || []).map((y) => <option key={y}>{y}</option>)}</select></Field>
        </div>
        {loading && !data && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {data && data.results.length === 0 && (
          <p className="empty">No return records{status || group || fy ? ' match' : ' yet'}. {canManage && !status && <button type="button" className="tax-link-button" onClick={() => setDialog('generate')}>Create the year's returns</button>}</p>
        )}
        {data && data.results.length > 0 && <FilingTable rows={data.results} onOpen={(f) => navigate(`/tax/returns/${f.id}`)} />}
        {data && <div className="list-foot"><span>{data.count} return{data.count === 1 ? '' : 's'}</span></div>}
      </section>
      {dialog === 'new' && opts.data && <NewReturnDialog opts={opts.data} onClose={() => setDialog(null)} onSaved={(f) => { setDialog(null); navigate(`/tax/returns/${f.id}`); }} />}
      {dialog === 'generate' && opts.data && <GenerateDialog opts={opts.data} onClose={() => setDialog(null)} onDone={() => { setDialog(null); reload(); }} />}
    </>
  );
}
