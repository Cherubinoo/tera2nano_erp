import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { HBars, StackedBar } from '../../components/charts';
import { useAuth } from '../../context';
import { money, num, plain, today } from '../../format';
import { useLoad } from '../../hooks';
import { Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, Tabs, fmtDate, useConfirm, useReasonDialog, useToast } from '../../ui';
import { Tile, fyBounds, fyNow, has, RangeBar, StatusBadge, useTaxOptions } from './common';
import { PaymentDialog, PaymentTable } from './Payments';
import { FilingTable, NewReturnDialog } from './Returns';

function Ledger() {
  const [range, setRange] = useState({ from: fyBounds(fyNow())[0], to: today() });
  const { data: l, error, loading, reload } = useLoad(() => api.get('/tax/tds/ledger/', range), [range.from, range.to]);
  return (
    <>
      <RangeBar from={range.from} to={range.to} onApply={setRange} />
      {loading && !l && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {l && (
        <>
          <div className="tiles">
            <Tile label="Opening balance" value={money(l.opening)} hint="Owed at the start of the period" />
            <Tile label="TDS deducted" value={money(l.deducted)} hint="Withheld from payments" />
            <Tile label="Paid to the government" value={money(l.paid)} hint="Challan 281 payments" tone="good" />
            <Tile label="TDS payable" value={money(l.closing)} tone={num(l.closing) > 0 ? 'bad' : ''} hint="Still to be deposited" />
          </div>
          <div className="grid-2">
            <section className="card" aria-label="TDS payable by section">
              <h2>By section</h2>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Section</th><th className="amount">Opening</th><th className="amount">Deducted</th><th className="amount">Paid</th><th className="amount">Payable</th></tr></thead>
                  <tbody>
                    {l.sections.map((s) => (
                      <tr key={s.section}><td data-label="Section"><strong>{s.section}</strong></td><td className="amount" data-label="Opening">{plain(s.opening)}</td><td className="amount" data-label="Deducted">{plain(s.deducted)}</td>
                        <td className="amount" data-label="Paid">{plain(s.paid)}</td><td className="amount" data-label="Payable"><strong>{plain(s.closing)}</strong></td></tr>
                    ))}
                    {l.sections.length === 0 && <tr><td colSpan={5} className="empty">No TDS postings in the books for this period.</td></tr>}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="card" aria-label="Payable by section chart">
              <h2>Payable by section</h2>
              <HBars rows={l.sections.filter((s) => num(s.closing) > 0).map((s) => ({ key: s.section, label: `Section ${s.section}`, value: s.closing }))} format={money} empty="Nothing payable." />
              <h3>Deducted and paid</h3>
              <StackedBar format={money} parts={[{ key: 'p', label: 'Paid', value: l.paid, colour: 'var(--chart-2)' }, { key: 'u', label: 'Not yet paid', value: Math.max(num(l.deducted) - num(l.paid), 0), colour: 'var(--chart-4)' }]} />
            </section>
          </div>
          <section className="card" aria-label="TDS postings">
            <h2>Postings</h2>
            {l.rows.length === 0 ? <p className="empty">No postings in this period.</p> : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Date</th><th>Reference</th><th>Section</th><th>Details</th><th className="amount">Deducted</th><th className="amount">Paid</th><th className="amount">Balance</th></tr></thead>
                  <tbody>
                    {l.rows.map((r) => (
                      <tr key={r.id}><td data-label="Date">{fmtDate(r.date)}</td><td data-label="Reference">{r.reference || '—'}</td><td data-label="Section">{r.section}</td>
                        <td data-label="Details">{r.memo || r.narration}</td><td className="amount" data-label="Deducted">{num(r.deducted) ? plain(r.deducted) : '—'}</td>
                        <td className="amount" data-label="Paid">{num(r.paid) ? plain(r.paid) : '—'}</td><td className="amount" data-label="Balance">{plain(r.balance)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}

function Challans({ canManage }) {
  const notify = useToast();
  const opts = useTaxOptions();
  const { data, error, loading, reload } = useLoad(() => api.get('/tax/payments/', { type: 'tds' }), []);
  const [dialog, setDialog] = useState(false);
  const { ask, dialog: reasonDialog } = useReasonDialog();
  const voidIt = async (p) => {
    const reason = await ask({ title: `Void challan ${p.reference}`, label: 'Reason', confirmLabel: 'Void challan' });
    if (reason === null) return;
    try { await api.post(`/tax/payments/${p.id}/void/`, { reason }); notify('Challan voided.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  return (
    <section className="card" aria-label="TDS challans">
      <div className="card-head"><h2>TDS challans (281)</h2>{canManage && <button type="button" className="btn btn-primary" onClick={() => setDialog(true)}>Record challan</button>}</div>
      {loading && !data && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && data.results.length === 0 && <p className="empty">No TDS challans recorded yet.</p>}
      {data && data.results.length > 0 && <PaymentTable rows={data.results} opts={opts.data} canManage={canManage} onVoid={voidIt} onOpen={() => {}} />}
      {data && <div className="list-foot"><span>{data.count} challan{data.count === 1 ? '' : 's'}</span><span>Total paid <strong>{money(data.total)}</strong></span></div>}
      {dialog && opts.data && <PaymentDialog opts={opts.data} defaults={{ tax_type: 'tds' }} onClose={() => setDialog(false)} onSaved={() => { setDialog(false); reload(); }} />}
      {reasonDialog}
    </section>
  );
}

function QuarterlyReturns({ canManage }) {
  const navigate = useNavigate();
  const opts = useTaxOptions();
  const { data, error, loading, reload } = useLoad(() => api.get('/tax/filings/', { group: 'tds' }), []);
  const [dialog, setDialog] = useState(false);
  return (
    <section className="card" aria-label="TDS returns">
      <div className="card-head"><h2>Quarterly TDS returns (24Q and 26Q)</h2>
        {canManage && <button type="button" className="btn btn-primary" onClick={() => setDialog(true)}>New TDS return</button>}</div>
      <p className="muted">Due 31 July, 31 October, 31 January and 31 May. Open a return to prepare its figures from the TDS ledger and record the acknowledgement.</p>
      {loading && !data && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && data.results.length === 0 && <p className="empty">No TDS returns yet. Use "Create the year's returns" on the Returns page to add all quarters.</p>}
      {data && data.results.length > 0 && <FilingTable rows={data.results} onOpen={(f) => navigate(`/tax/returns/${f.id}`)} />}
      {dialog && opts.data && <NewReturnDialog opts={opts.data} defaults={{ return_type: 'tds_26q' }} onClose={() => setDialog(false)} onSaved={(f) => navigate(`/tax/returns/${f.id}`)} />}
    </section>
  );
}

const STATUS_OPTIONS = [['expected', 'Certificate awaited'], ['received', 'Certificate received'], ['claimed', 'Matched in Form 26AS']];

function CreditDialog({ credit, onClose, onSaved }) {
  const notify = useToast();
  const blank = { deductor_name: '', deductor_tan: '', section: '194J', financial_year: fyNow(), date: today(), gross_amount: '', tds_amount: '', certificate_no: '', certificate_date: '', status: 'expected', notes: '' };
  const [v, setV] = useState(credit ? { ...blank, ...Object.fromEntries(Object.entries(credit).map(([k, x]) => [k, x ?? ''])) } : blank);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: k === 'deductor_tan' || k === 'section' ? e.target.value.toUpperCase() : e.target.value })); setErr((x) => (x ? { ...x, fieldErrors: { ...x.fieldErrors, [k]: undefined } } : x)); };
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const errs = {};
    if (!v.deductor_name.trim()) errs.deductor_name = "Enter the deductor's name.";
    if (!v.section.trim()) errs.section = 'Enter the section.';
    if (!(num(v.tds_amount) > 0)) errs.tds_amount = 'Enter the TDS amount.';
    if (!v.date) errs.date = 'Enter the date.';
    if (v.status !== 'expected' && !v.certificate_no.trim()) errs.certificate_no = 'Enter the certificate number once it is received.';
    if (Object.keys(errs).length) { setErr({ message: 'Please correct the highlighted fields.', fieldErrors: errs }); return; }
    setBusy(true);
    setErr(null);
    try {
      const body = { deductor_name: v.deductor_name, deductor_tan: v.deductor_tan, section: v.section, financial_year: v.financial_year, date: v.date, gross_amount: v.gross_amount,
        tds_amount: v.tds_amount, certificate_no: v.certificate_no, certificate_date: v.certificate_date || null, status: v.status, notes: v.notes };
      if (credit) await api.patch(`/tax/tds/credits/${credit.id}/`, body); else await api.post('/tax/tds/credits/', body);
      notify(credit ? 'TDS credit updated.' : 'TDS credit added.');
      onSaved();
    } catch (x) { setErr(x); }
    setBusy(false);
  };
  const fe = err?.fieldErrors || {};
  return (
    <Dialog title={credit ? 'Edit TDS credit' : 'Add TDS credit'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={err} fields={Object.keys(v).map((name) => ({ name }))} />
        <div className="form-grid">
          <Field id="cr-name" label="Deductor (customer)" required error={fe.deductor_name}><input id="cr-name" value={v.deductor_name} maxLength={200} onChange={set('deductor_name')} /></Field>
          <Field id="cr-tan" label="Deductor's TAN" hint="Optional, for example MUMA12345B" error={fe.deductor_tan}><input id="cr-tan" value={v.deductor_tan} maxLength={10} onChange={set('deductor_tan')} /></Field>
          <Field id="cr-section" label="Section" required error={fe.section}><input id="cr-section" value={v.section} maxLength={10} onChange={set('section')} /></Field>
          <Field id="cr-date" label="Date of deduction" required error={fe.date}><input id="cr-date" type="date" value={v.date} onChange={set('date')} /></Field>
          <Field id="cr-gross" label="Amount paid or credited" hint="Before TDS, optional" error={fe.gross_amount}><input id="cr-gross" inputMode="decimal" value={v.gross_amount} onChange={set('gross_amount')} /></Field>
          <Field id="cr-tds" label="TDS amount" required error={fe.tds_amount}><input id="cr-tds" inputMode="decimal" value={v.tds_amount} onChange={set('tds_amount')} /></Field>
          <Field id="cr-fy" label="Financial year" error={fe.financial_year}><input id="cr-fy" value={v.financial_year} maxLength={7} onChange={set('financial_year')} /></Field>
          <Field id="cr-status" label="Status" error={fe.status}><select id="cr-status" value={v.status} onChange={set('status')}>{STATUS_OPTIONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
          <Field id="cr-cert" label="Certificate number (Form 16A)" error={fe.certificate_no} required={v.status !== 'expected'}><input id="cr-cert" value={v.certificate_no} maxLength={60} onChange={set('certificate_no')} /></Field>
          <Field id="cr-certdate" label="Certificate date" error={fe.certificate_date}><input id="cr-certdate" type="date" value={v.certificate_date} onChange={set('certificate_date')} /></Field>
          <Field id="cr-notes" label="Notes" wide error={fe.notes}><input id="cr-notes" value={v.notes} maxLength={300} onChange={set('notes')} /></Field>
        </div>
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </Dialog>
  );
}

function Receivable({ canManage }) {
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const opts = useTaxOptions();
  const [fy, setFy] = useState('');
  const [status, setStatus] = useState('');
  const { data, error, loading, reload } = useLoad(() => api.get('/tax/tds/credits/', { fy, status }), [fy, status]);
  const [dialog, setDialog] = useState(null);
  const remove = async (c) => {
    if (!(await confirm({ title: 'Delete this TDS credit?', danger: true, confirmLabel: 'Delete', body: <p>{c.deductor_name}, {plain(c.tds_amount)} under section {c.section}.</p> }))) return;
    try { await api.del(`/tax/tds/credits/${c.id}/`); notify('TDS credit deleted.'); reload(); } catch (e) { notify(e.message, 'error'); }
  };
  const s = data?.summary;
  return (
    <>
      {s && (
        <div className="tiles">
          <Tile label="TDS to claim (tracked)" value={money(s.total)} hint={`${s.count} entr${s.count === 1 ? 'y' : 'ies'}`} />
          <Tile label="Certificate awaited" value={money(s.by_status.expected)} tone={num(s.by_status.expected) > 0 ? 'bad' : ''} />
          <Tile label="Certificate received" value={money(s.by_status.received)} />
          <Tile label="Matched in 26AS" value={money(s.by_status.claimed)} tone="good" hint={`Books show ${money(s.books_balance)} in TDS receivable`} />
        </div>
      )}
      <section className="card" aria-label="TDS receivable tracker">
        <div className="card-head"><h2>TDS receivable (Form 26AS tracker)</h2>{canManage && <button type="button" className="btn btn-primary" onClick={() => setDialog({})}>Add TDS credit</button>}</div>
        <p className="muted">TDS your customers deducted from payments to you. Add each certificate, then tick it off when it shows in Form 26AS.</p>
        <div className="toolbar">
          <Field id="cr-f-fy" label="Financial year"><select id="cr-f-fy" value={fy} onChange={(e) => setFy(e.target.value)}><option value="">All years</option>{(opts.data?.financial_years || []).map((y) => <option key={y}>{y}</option>)}</select></Field>
          <div className="chips" role="group" aria-label="Filter by status">
            {[['', 'All'], ...STATUS_OPTIONS].map(([k, l]) => <button key={k} type="button" className="chip" aria-pressed={status === k} onClick={() => setStatus(k)}>{l}</button>)}
          </div>
        </div>
        {loading && !data && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {data && data.results.length === 0 && <p className="empty">No TDS credits tracked{fy || status ? ' for this filter' : ' yet'}.</p>}
        {data && data.results.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Deductor</th><th>Section</th><th>Certificate</th><th className="amount">Gross</th><th className="amount">TDS</th><th>Status</th>{canManage && <th><span className="sr-only">Actions</span></th>}</tr></thead>
              <tbody>
                {data.results.map((c) => (
                  <tr key={c.id}>
                    <td data-label="Date">{fmtDate(c.date)}<div className="hint">{c.financial_year}{c.quarter ? ` Q${c.quarter}` : ''}</div></td>
                    <td data-label="Deductor"><strong>{c.deductor_name}</strong><div className="hint">{c.deductor_tan}</div></td>
                    <td data-label="Section">{c.section}</td>
                    <td data-label="Certificate">{c.certificate_no || '—'}<div className="hint">{c.certificate_date ? fmtDate(c.certificate_date) : ''}</div></td>
                    <td className="amount" data-label="Gross">{c.gross_amount ? plain(c.gross_amount) : '—'}</td>
                    <td className="amount" data-label="TDS"><strong>{plain(c.tds_amount)}</strong></td>
                    <td data-label="Status"><StatusBadge status={c.status} /></td>
                    {canManage && <td className="row-actions"><button type="button" className="btn btn-small" onClick={() => setDialog(c)}>Edit</button><button type="button" className="btn btn-small" onClick={() => remove(c)}>Delete</button></td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {s && s.by_section.length > 0 && (<><h3>By section</h3><HBars rows={s.by_section.map((x) => ({ key: x.section, label: `Section ${x.section}`, value: x.amount }))} format={money} /></>)}
      </section>
      {dialog && <CreditDialog credit={dialog.id ? dialog : null} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); reload(); }} />}
      {confirmDialog}
    </>
  );
}

export default function Tds() {
  const { user } = useAuth();
  const canManage = has(user, 'tax.manage');
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') || 'ledger';
  return (
    <>
      <PageHeader title="TDS" description="TDS withheld and owed, challans paid, quarterly returns, and TDS your customers deducted from you." />
      <Tabs tabs={[{ key: 'ledger', label: 'Payable ledger' }, { key: 'challans', label: 'Challans' }, { key: 'returns', label: 'Quarterly returns' }, { key: 'receivable', label: 'Receivable tracker' }]}
        value={tab} onChange={(k) => setParams(k === 'ledger' ? {} : { tab: k }, { replace: true })} />
      {tab === 'ledger' && <Ledger />}
      {tab === 'challans' && <Challans canManage={canManage} />}
      {tab === 'returns' && <QuarterlyReturns canManage={canManage} />}
      {tab === 'receivable' && <Receivable canManage={canManage} />}
    </>
  );
}
