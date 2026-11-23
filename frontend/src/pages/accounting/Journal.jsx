import { useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { money, num, plain, today } from '../../format';
import { asList, useLoad } from '../../hooks';
import { Badge, ErrorBox, Field, FormErrors, Loading, Notice, PageHeader, fmtDate, useReasonDialog, useToast } from '../../ui';
import { SOURCE_LABEL } from './common';

export function JournalPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const [source, setSource] = useState('');
  const { data, error, loading, reload } = useLoad(() => api.get('/accounting/journal/', { q: term || undefined, source: source || undefined }), [term, source]);
  const manage = user.permissions.includes('accounting.manage');
  return (
    <>
      <PageHeader title="Journal" description="Every entry in the books, newest first. Invoices, payments, expenses and transfers post here by themselves."
        actions={manage && <Link className="btn btn-primary" to="/accounting/journal/new">New journal entry</Link>} />
      <section className="card">
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
          <Field id="j-q" label="Search" className="grow"><input id="j-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Reference or description" /></Field>
          <Field id="j-source" label="From">
            <select id="j-source" value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="">Everything</option>{Object.entries(SOURCE_LABEL).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select>
          </Field>
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && data?.results.length === 0 && <p className="empty">No entries.</p>}
        {data?.results.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Date</th><th>Reference</th><th>Description</th><th>From</th><th>Status</th><th className="amount">Amount</th></tr></thead>
            <tbody>
              {data.results.map((e) => (
                <tr key={e.id} className={`clickable${e.status === 'reversed' ? ' row-inactive' : ''}`} onClick={() => navigate(`/accounting/journal/${e.id}`)}>
                  <td data-label="Date">{fmtDate(e.date)}</td>
                  <td data-label="Reference"><Link to={`/accounting/journal/${e.id}`} onClick={(ev) => ev.stopPropagation()}><code>{e.reference || '—'}</code></Link></td>
                  <td data-label="Description">{e.narration}</td>
                  <td data-label="From"><Badge tone="info">{e.source_label}</Badge></td>
                  <td data-label="Status">{e.status === 'reversed' ? <Badge>Reversed</Badge> : e.source === 'reversal' ? <Badge>Reversal</Badge> : <Badge tone="good">Posted</Badge>}</td>
                  <td className="amount" data-label="Amount">{money(e.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
        {data && <div className="list-foot"><span>{data.count} entr{data.count === 1 ? 'y' : 'ies'}</span></div>}
      </section>
    </>
  );
}

export function JournalDetailPage() {
  const { id } = useParams();
  const { user } = useAuth();
  const notify = useToast();
  const { ask, dialog } = useReasonDialog();
  const { data: e, error, loading, reload } = useLoad(() => api.get(`/accounting/journal/${id}/`), [id]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const manual = ['manual', 'transfer', 'opening'].includes(e.source);
  const canReverse = user.permissions.includes('accounting.manage') && manual && e.status === 'posted' && !e.reverses;
  const reverse = async () => {
    const reason = await ask({ title: 'Reverse this entry?', message: 'A mirror entry is posted today. The original stays in the books, marked reversed.', confirmLabel: 'Reverse' });
    if (reason === null) return;
    try { await api.post(`/accounting/journal/${id}/reverse/`, { change_reason: reason }); notify('Entry reversed.'); reload(); } catch (err) { notify(err.message, 'error'); }
  };
  const dr = e.lines.reduce((a, l) => a + num(l.debit), 0);
  const cr = e.lines.reduce((a, l) => a + num(l.credit), 0);
  return (
    <>
      <Link to="/accounting/journal" className="back-link">← Journal</Link>
      <header className="doc-head">
        <div><h1>Journal entry <code>{e.reference || '—'}</code> {e.status === 'reversed' ? <Badge>Reversed</Badge> : <Badge tone="good">Posted</Badge>}</h1>
          <p className="muted">{fmtDate(e.date)} · {e.source_label}{e.financial_year ? ` · ${e.financial_year}` : ''}</p></div>
        {canReverse && <div className="page-actions"><button type="button" className="btn" onClick={reverse}>Reverse</button></div>}
      </header>
      {e.reversed_by && <Notice type="warn">This entry was reversed by <Link to={`/accounting/journal/${e.reversed_by}`}>another entry</Link>.</Notice>}
      {e.reverses && <Notice>This entry reverses <Link to={`/accounting/journal/${e.reverses}`}>an earlier one</Link>.</Notice>}
      {!manual && e.source !== 'reversal' && <Notice>Posted automatically. To change it, void the {e.source_label.toLowerCase()} itself; that reverses this entry.</Notice>}
      <section className="card">
        <p>{e.narration}</p>
        <div className="table-wrap"><table>
          <thead><tr><th>Account</th><th>Description</th><th className="amount">Debit</th><th className="amount">Credit</th></tr></thead>
          <tbody>
            {e.lines.map((l) => (
              <tr key={l.id}><td data-label="Account"><Link to={`/accounting/accounts/${l.account.id}`}>{l.account.code} {l.account.name}</Link></td><td data-label="Description">{l.memo}</td>
                <td className="amount" data-label="Debit">{num(l.debit) ? plain(l.debit) : ''}</td><td className="amount" data-label="Credit">{num(l.credit) ? plain(l.credit) : ''}</td></tr>
            ))}
          </tbody>
          <tfoot><tr><th colSpan={2} scope="row">Total</th><td className="amount"><strong>{plain(dr)}</strong></td><td className="amount"><strong>{plain(cr)}</strong></td></tr></tfoot>
        </table></div>
      </section>
      {dialog}
    </>
  );
}

const blank = () => ({ account: '', debit: '', credit: '', memo: '' });

export function JournalNewPage() {
  const navigate = useNavigate();
  const notify = useToast();
  const accounts = useLoad(() => api.get('/accounting/accounts/', { active: 'true' }), []);
  const [date, setDate] = useState(today());
  const [narration, setNarration] = useState('');
  const [lines, setLines] = useState([blank(), blank()]);
  const [error, setError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const list = asList(accounts.data);
  const dr = useMemo(() => lines.reduce((a, l) => a + Math.round(num(l.debit) * 100), 0), [lines]);
  const cr = useMemo(() => lines.reduce((a, l) => a + Math.round(num(l.credit) * 100), 0), [lines]);
  const diff = dr - cr;
  const set = (i, k) => (e) => setLines((ls) => ls.map((l, n) => (n === i ? { ...l, [k]: e.target.value } : l)));

  const save = async (e) => {
    e.preventDefault();
    setError(null);
    const errs = {};
    if (!narration.trim()) errs.narration = 'Say what the entry is for.';
    const used = lines.filter((l) => l.account || num(l.debit) || num(l.credit));
    if (used.length < 2) errs.lines = 'Enter at least two lines.';
    if (used.some((l) => !l.account)) errs.lines = 'Choose an account on every line.';
    if (used.some((l) => num(l.debit) > 0 && num(l.credit) > 0)) errs.lines = 'A line is either a debit or a credit, not both.';
    if (!errs.lines && dr !== cr) errs.lines = `The entry does not balance: debits ${plain(dr / 100)}, credits ${plain(cr / 100)}.`;
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const r = await api.post('/accounting/journal/', { date, narration: narration.trim(), lines: used.map((l) => ({ account: l.account, debit: l.debit || 0, credit: l.credit || 0, memo: l.memo })) });
      notify(`Journal entry posted: ${r.reference}`);
      navigate(`/accounting/journal/${r.id}`);
    } catch (err) { setError(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };

  return (
    <>
      <Link to="/accounting/journal" className="back-link">← Journal</Link>
      <PageHeader title="New journal entry" description="Debits must equal credits. A posted entry is never edited; if it is wrong, reverse it." />
      <form className="card" onSubmit={save} noValidate>
        <FormErrors error={error} fields={[]} />
        <div className="form-grid">
          <Field id="jn-date" label="Date" required error={errors.date}><input id="jn-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
          <Field id="jn-narr" label="What is it for?" required error={errors.narration} wide><input id="jn-narr" value={narration} maxLength={500} onChange={(e) => setNarration(e.target.value)} /></Field>
        </div>
        {errors.lines && <p className="error-text" role="alert">{errors.lines}</p>}
        {loadingNote(accounts)}
        <div className="table-wrap"><table className="items-table alloc-table">
          <thead><tr><th>Account</th><th>Description</th><th className="amount">Debit</th><th className="amount">Credit</th><th><span className="sr-only">Remove</span></th></tr></thead>
          <tbody>
            {lines.map((l, i) => (
              <tr key={i}>
                <td><select aria-label={`Account ${i + 1}`} value={l.account} onChange={set(i, 'account')}><option value="">Choose…</option>{list.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</select></td>
                <td><input aria-label={`Description ${i + 1}`} value={l.memo} maxLength={300} onChange={set(i, 'memo')} /></td>
                <td className="amount"><input type="number" min="0" step="0.01" aria-label={`Debit ${i + 1}`} value={l.debit} onChange={set(i, 'debit')} /></td>
                <td className="amount"><input type="number" min="0" step="0.01" aria-label={`Credit ${i + 1}`} value={l.credit} onChange={set(i, 'credit')} /></td>
                <td>{lines.length > 2 && <button type="button" className="btn btn-small" onClick={() => setLines((ls) => ls.filter((_, n) => n !== i))}>Remove</button>}</td>
              </tr>
            ))}
          </tbody>
          <tfoot><tr><th colSpan={2} scope="row">Total</th><td className="amount"><strong>{plain(dr / 100)}</strong></td><td className="amount"><strong>{plain(cr / 100)}</strong></td><td /></tr></tfoot>
        </table></div>
        <div className="toolbar"><button type="button" className="btn btn-small" onClick={() => setLines((ls) => [...ls, blank()])}>Add line</button>
          <span role="status" className={diff === 0 && dr > 0 ? 'hint' : 'error-text'}>{dr === 0 && cr === 0 ? 'Enter the amounts.' : diff === 0 ? 'Balanced.' : `Difference ${plain(Math.abs(diff) / 100)}: ${diff > 0 ? 'credits are short' : 'debits are short'}.`}</span></div>
        <div className="actions"><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Posting…' : 'Post entry'}</button><Link className="btn" to="/accounting/journal">Cancel</Link></div>
      </form>
    </>
  );
}

function loadingNote(l) { return l.loading ? <Loading /> : null; }
