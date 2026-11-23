import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { downloadCsv, money } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Field, Loading, PageHeader } from '../../ui';

// A list page: status chips, search, a table whose rows open the record, a totals footer and CSV export.
export default function DocList({ title, description, url, statuses, columns, rowTo, actions, totals, csv, searchHint, extraParams, emptyText, reloadKey }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const status = params.get('status') || '';
  const term = params.get('q') || '';
  const [q, setQ] = useState(term);
  const { data, error, loading, reload } = useLoad(() => api.get(url, { status: status || undefined, q: term || undefined, ...(extraParams || {}) }),
    [url, status, term, reloadKey, JSON.stringify(extraParams || {})]);
  const rows = data?.results || [];
  const set = (changes) => {
    const next = { status, q: term, ...changes };
    setParams(Object.fromEntries(Object.entries(next).filter(([, v]) => v)), { replace: true });
  };
  return (
    <>
      <PageHeader title={title} description={description}
        actions={<>{csv && rows.length > 0 && <button type="button" className="btn" onClick={() => downloadCsv(csv.file, [csv.head, ...rows.map(csv.row)])}>Export CSV</button>}{actions}</>} />
      <section className="card">
        {statuses && (
          <div className="chips" role="group" aria-label="Filter by status">
            {statuses.map(([k, l]) => <button key={k} type="button" className="chip" aria-pressed={status === k} onClick={() => set({ status: k })}>{l}{data?.status_counts && k && data.status_counts[k] !== undefined ? ` (${data.status_counts[k]})` : ''}</button>)}
          </div>
        )}
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); set({ q: q.trim() }); }}>
          <Field id="list-q" label="Search" className="grow"><input id="list-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder={searchHint} /></Field>
          <button type="submit" className="btn">Search</button>
        </form>
        {loading && <Loading />}
        <ErrorBox error={error} onRetry={reload} />
        {!loading && !error && rows.length === 0 && <p className="empty">{status || term ? 'Nothing matches.' : emptyText}</p>}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr>{columns.map((c) => <th key={c.h} className={c.amount ? 'amount' : ''}>{c.h}</th>)}</tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className={rowTo ? 'clickable' : ''} onClick={rowTo ? () => navigate(rowTo(r)) : undefined}>
                    {columns.map((c) => <td key={c.h} data-label={c.h} className={c.amount ? 'amount' : ''}>{c.v(r)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rows.length > 0 && (
          <div className="list-foot"><span>{data.count} record{data.count === 1 ? '' : 's'}</span>
            {(totals || []).map(([label, key]) => data[key] !== undefined && <span key={key}>{label} <strong>{money(data[key])}</strong></span>)}</div>
        )}
      </section>
    </>
  );
}
