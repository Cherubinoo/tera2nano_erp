import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, downloadFile } from '../../api';
import { useAuth } from '../../context';
import { plain } from '../../format';
import { asList, useLoad } from '../../hooks';
import { Badge, ErrorBox, Field, Loading, Notice, PageHeader, fmtDate, fmtDateTime, useConfirm, useToast } from '../../ui';
import { Problem } from './bankCommon';

const FIELD_HELP = {
  date: 'The day the money moved.',
  description: 'What the bank wrote about it (narration, particulars, remarks).',
  reference: 'Cheque number, UTR or reference, if the bank gives one.',
  debit: 'Money that left the account.',
  credit: 'Money that came in.',
  amount: 'Use this only when the statement has one amount column instead of separate withdrawal and deposit columns.',
  drcr: 'A column that says Dr or Cr next to a single amount.',
  balance: 'The running balance after each line. Used to check the reconciliation.',
};

// Pick the bank account (when not opened from one), upload the file, confirm the columns, check, import.
export default function BankImportPage() {
  const { id: routeId } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const manage = user.permissions.includes('accounting.manage');
  const accounts = useLoad(() => api.get('/accounting/bank-rec/'), []);
  const [accountId, setAccountId] = useState(routeId || '');
  const [file, setFile] = useState(null);
  const [draft, setDraft] = useState(null); // upload response: statement, headers, sample, mapping ...
  const [mapping, setMapping] = useState({});
  const [check, setCheck] = useState(null);
  const [done, setDone] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const notify = useToast();

  const rows = asList(accounts.data);
  useEffect(() => { if (!accountId && rows.length) setAccountId(routeId || rows[0].id); }, [rows.length, routeId]); // eslint-disable-line react-hooks/exhaustive-deps
  const account = rows.find((a) => a.id === accountId);

  const reset = () => { setDraft(null); setCheck(null); setDone(null); setError(null); setFile(null); };

  const upload = async (e) => {
    e.preventDefault();
    if (!file) { setError({ message: 'Choose the statement file first.', fieldErrors: { file: 'Choose the statement file first.' } }); return; }
    setBusy(true); setError(null); setCheck(null); setDone(null);
    const form = new FormData();
    form.append('file', file);
    try {
      const r = await api.upload(`/accounting/bank/${accountId}/statements/`, form);
      setDraft(r); setMapping(r.mapping || {});
    } catch (err) { setError(err); }
    setBusy(false);
  };

  const changeHeaderRow = async (value) => {
    setBusy(true); setError(null); setCheck(null);
    try {
      const r = await api.get(`/accounting/statements/${draft.statement.id}/`, { header_row: value });
      setDraft((d) => ({ ...d, ...r })); setMapping(r.mapping || {});
    } catch (err) { setError(err); }
    setBusy(false);
  };

  const run = async (dryRun) => {
    setBusy(true); setError(null);
    try {
      const r = await api.post(`/accounting/statements/${draft.statement.id}/import/`, { mapping, dry_run: dryRun });
      if (dryRun) setCheck(r.summary);
      else { setDone(r.summary); setDraft(null); setCheck(null); notify('Statement imported.'); }
    } catch (err) { setError(err); }
    setBusy(false);
  };

  const discard = async () => {
    try { await api.del(`/accounting/statements/${draft.statement.id}/`); } catch { /* already gone */ }
    reset();
  };

  if (accounts.loading && !accounts.data) return <Loading />;
  if (accounts.error) return <ErrorBox error={accounts.error} onRetry={accounts.reload} />;

  return (
    <>
      <Link to="/accounting/bank-reconciliation" className="back-link">← Bank reconciliation</Link>
      <PageHeader title="Import a bank statement"
        description="Download the statement from your bank's website as CSV, Excel (.xlsx or .xls), PDF or OFX and upload it here. Lines already imported are skipped, so an overlapping statement is safe." />
      {!manage && <Notice type="warn">You can see imported statements, but your role does not allow importing.</Notice>}
      {rows.length === 0 && <Notice>There is no bank account yet. Add one in Company &gt; Bank Accounts.</Notice>}

      {manage && rows.length > 0 && !draft && (
        <section className="card" aria-label="Upload">
          <h2>1. Choose the account and the file</h2>
          <Problem error={error} />
          <form className="form-grid" onSubmit={upload}>
            <Field id="bi-account" label="Bank account" required>
              <select id="bi-account" value={accountId} onChange={(e) => { setAccountId(e.target.value); reset(); }} disabled={!!routeId}>
                {rows.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </Field>
            <Field id="bi-file" label="Statement file" required hint="CSV, Excel (.xlsx or .xls), PDF or OFX, up to 10 MB.">
              <input id="bi-file" type="file" accept=".csv,.txt,.xlsx,.xls,.pdf,.ofx,.qfx" onChange={(e) => setFile(e.target.files[0] || null)} />
            </Field>
            <div className="field field-wide"><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Reading the file…' : 'Upload and read'}</button></div>
          </form>
        </section>
      )}

      {draft && (
        <MappingStep draft={draft} mapping={mapping} setMapping={(m) => { setMapping(m); setCheck(null); }} busy={busy} error={error}
          onHeaderRow={changeHeaderRow} onCheck={() => run(true)} onImport={() => run(false)} onDiscard={discard} check={check} />
      )}

      {done && (
        <section className="card" aria-label="Import summary">
          <h2>Import finished</h2>
          <Summary s={done} />
          <div className="actions">
            <button type="button" className="btn btn-primary" onClick={() => navigate(`/accounting/bank/${accountId}/match`)}>Match the lines</button>
            <button type="button" className="btn" onClick={reset}>Import another file</button>
          </div>
        </section>
      )}

      {account && <EarlierImports accountId={account.id} manage={manage} key={`${account.id}-${done ? 'd' : ''}`} />}
    </>
  );
}

function Summary({ s }) {
  return (
    <>
      <div className="balance-strip" role="status">
        <div><span>Rows read</span><strong>{s.rows_read}</strong></div>
        <div><span>Imported</span><strong>{s.imported}</strong></div>
        <div><span>Already imported (skipped)</span><strong>{s.duplicates}</strong></div>
        <div><span>Headings and totals (skipped)</span><strong>{s.skipped}</strong></div>
        <div className={s.problems.length ? 'due' : ''}><span>Rows with problems</span><strong>{s.problems.length}</strong></div>
      </div>
      {s.period_start && <p className="muted">Lines from {fmtDate(s.period_start)} to {fmtDate(s.period_end)}.</p>}
      {s.problems.length > 0 && (
        <details className="subcard" open={s.problems.length <= 10}>
          <summary>Rows that were not imported, and why</summary>
          <ul className="problem-list">{s.problems.map((p) => <li key={`${p.row}-${p.reason}`}><strong>Row {p.row}:</strong> {p.reason}</li>)}</ul>
        </details>
      )}
    </>
  );
}

function MappingStep({ draft, mapping, setMapping, busy, error, onHeaderRow, onCheck, onImport, onDiscard, check }) {
  const fields = draft.fields || [
    { key: 'date', label: 'Date' }, { key: 'description', label: 'Description / narration' }, { key: 'reference', label: 'Reference / cheque no.' },
    { key: 'debit', label: 'Withdrawal (money out)' }, { key: 'credit', label: 'Deposit (money in)' }, { key: 'amount', label: 'Single amount column' },
    { key: 'drcr', label: 'Dr / Cr column' }, { key: 'balance', label: 'Balance' },
  ];
  const set = (k, v) => setMapping({ ...mapping, [k]: v === '' ? null : Number(v) });
  const headers = draft.headers || [];
  const colName = (i) => headers[i] || `Column ${i + 1}`;
  const single = mapping.amount != null;
  return (
    <section className="card" aria-label="Columns">
      <h2>2. Check the columns</h2>
      <p className="muted">
        File <strong>{draft.statement.file_name}</strong> ({draft.statement.format_label}).
        {draft.needs_mapping && (draft.mapping_source === 'saved'
          ? ' The columns were set the way you set them for this account last time.'
          : ' The columns were guessed from the headings. Change any that are wrong.')}
      </p>
      <Problem error={error} />
      {draft.needs_mapping ? (
        <>
          <div className="form-grid">
            <Field id="map-header" label="Row with the column names" hint="Statements often start with account details; pick the row that has Date, Narration and so on.">
              <select id="map-header" value={mapping.header_row ?? 0} onChange={(e) => onHeaderRow(e.target.value)} disabled={busy}>
                {(draft.top_rows || []).map((r, i) => <option key={i} value={i}>Row {i + 1}: {r.filter(Boolean).slice(0, 4).join(' | ').slice(0, 60) || '(empty)'}</option>)}
              </select>
            </Field>
            {fields.map((f) => ((f.key === 'drcr' && !single) ? null : (
              <Field key={f.key} id={`map-${f.key}`} label={f.label} required={f.key === 'date'} hint={FIELD_HELP[f.key]}>
                <select id={`map-${f.key}`} value={mapping[f.key] ?? ''} onChange={(e) => set(f.key, e.target.value)}>
                  <option value="">Not in this file</option>
                  {headers.map((h, i) => <option key={i} value={i}>{colName(i)}</option>)}
                </select>
              </Field>
            )))}
            <Field id="map-order" label="Dates are written" hint="Indian statements put the day first.">
              <select id="map-order" value={mapping.date_order || 'dmy'} onChange={(e) => setMapping({ ...mapping, date_order: e.target.value })}>
                <option value="dmy">Day first (05/08/2026 is 5 August)</option>
                <option value="mdy">Month first (08/05/2026 is 5 August)</option>
              </select>
            </Field>
            {single && mapping.drcr == null && (
              <Field id="map-sign" label="A plus amount means">
                <select id="map-sign" value={mapping.amount_sign || 'deposit_positive'} onChange={(e) => setMapping({ ...mapping, amount_sign: e.target.value })}>
                  <option value="deposit_positive">Money in (minus is money out)</option>
                  <option value="withdrawal_positive">Money out (minus is money in)</option>
                </select>
              </Field>
            )}
          </div>
          <h3>First rows of the file</h3>
          <div className="table-wrap sample-table"><table>
            <thead><tr>{headers.map((h, i) => <th key={i} scope="col">{colName(i)}</th>)}</tr></thead>
            <tbody>{(draft.sample || []).map((r, i) => <tr key={i}>{headers.map((_, j) => <td key={j} data-label={colName(j)}>{r[j]}</td>)}</tr>)}</tbody>
          </table></div>
        </>
      ) : <Notice type="info">OFX files describe their own columns, so there is nothing to set. {draft.total_rows} transactions found.</Notice>}

      {check && (
        <>
          <h3>What will be imported</h3>
          <Summary s={check} />
          {check.preview.length > 0 && (
            <div className="table-wrap"><table>
              <thead><tr><th>Date</th><th>Description</th><th>Reference</th><th className="amount">Money in</th><th className="amount">Money out</th><th className="amount">Balance</th></tr></thead>
              <tbody>{check.preview.map((p) => (
                <tr key={p.row}><td data-label="Date">{fmtDate(p.date)}</td><td data-label="Description">{p.description}</td><td data-label="Reference">{p.reference || '—'}</td>
                  <td className="amount" data-label="Money in">{Number(p.amount) > 0 ? plain(p.amount) : ''}</td>
                  <td className="amount" data-label="Money out">{Number(p.amount) < 0 ? plain(-Number(p.amount)) : ''}</td>
                  <td className="amount" data-label="Balance">{p.balance != null ? plain(p.balance) : ''}</td></tr>
              ))}</tbody>
            </table></div>
          )}
        </>
      )}
      <div className="actions">
        <button type="button" className="btn" onClick={onCheck} disabled={busy}>{busy ? 'Reading…' : 'Check'}</button>
        <button type="button" className="btn btn-primary" onClick={onImport} disabled={busy || !check}>Import {check ? `${check.imported} line${check.imported === 1 ? '' : 's'}` : ''}</button>
        <button type="button" className="btn" onClick={onDiscard} disabled={busy}>Cancel</button>
        {!check && <span className="hint">Click Check to see what will be imported before importing.</span>}
      </div>
    </section>
  );
}

function EarlierImports({ accountId, manage }) {
  const { data, reload } = useLoad(() => api.get(`/accounting/bank/${accountId}/statements/`), [accountId]);
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const rows = asList(data);
  const remove = async (s) => {
    if (!(await confirm({ title: 'Remove this import?', body: <p>Its {s.imported} lines are taken out. This is only possible while none of them is matched or ignored.</p>, confirmLabel: 'Remove', danger: true }))) return;
    try { await api.del(`/accounting/statements/${s.id}/`); notify('Import removed.'); reload(); } catch (err) { notify(err.message, 'error'); }
  };
  if (!rows.length) return null;
  return (
    <section className="card" aria-label="Earlier imports">
      <h2>Statements imported for this account</h2>
      <div className="table-wrap"><table>
        <thead><tr><th>File</th><th>Period</th><th className="amount">Lines</th><th className="amount">Matched or ignored</th><th>Imported</th><th><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>{rows.map((s) => (
          <tr key={s.id}>
            <td data-label="File">{s.file_name} <Badge>{s.format_label}</Badge></td>
            <td data-label="Period">{s.period_start ? `${fmtDate(s.period_start)} to ${fmtDate(s.period_end)}` : '—'}</td>
            <td className="amount" data-label="Lines">{s.imported}{s.duplicates ? ` (+${s.duplicates} already there)` : ''}</td>
            <td className="amount" data-label="Matched or ignored">{s.matched ?? 0}</td>
            <td data-label="Imported">{fmtDateTime(s.imported_at)}{s.created_by ? ` by ${s.created_by}` : ''}</td>
            <td className="row-actions">
              <button type="button" className="btn btn-small" onClick={() => downloadFile(`/accounting/statements/${s.id}/file/`, s.file_name)}>Download</button>
              {manage && !s.matched && <button type="button" className="btn btn-small btn-danger-outline" onClick={() => remove(s)}>Remove</button>}
            </td>
          </tr>
        ))}</tbody>
      </table></div>
      {dialog}
    </section>
  );
}
