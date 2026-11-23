import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../../api';
import { downloadCsv } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, ErrorBox, Loading, PageHeader, fmtDate } from '../../ui';
import { Pick, ToneBadge, daysText } from './common';

const SOURCE = { company: 'Company records', contract: 'Contracts' };

export function ExpiryTable({ rows }) {
  if (!rows.length) return <p className="empty">Nothing is expiring soon.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Expires</th><th>What</th><th>Kind</th><th>Detail</th><th>Status</th><th>Where</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.source}-${i}`}>
              <td data-label="Expires">{fmtDate(r.date)}<div className="hint">{daysText(r.days_left).replace('overdue', 'ago')}</div></td>
              <td data-label="What"><strong>{r.title}</strong></td>
              <td data-label="Kind">{r.kind}</td>
              <td data-label="Detail">{r.detail || '—'}</td>
              <td data-label="Status"><ToneBadge tone={r.tone}>{r.expired ? 'Expired' : r.tone === 'due_soon' ? 'Expiring soon' : 'Valid'}</ToneBadge></td>
              <td data-label="Where">
                <Link to={r.link}>{SOURCE[r.source]}</Link>{r.read_only && <> <Badge tone="neutral">Read only</Badge></>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const HORIZONS = [{ value: '30', label: 'Next 30 days' }, { value: '60', label: 'Next 60 days' }, { value: '90', label: 'Next 90 days' }, { value: '180', label: 'Next 6 months' }, { value: '365', label: 'Next 12 months' }];
const KIND = { filing: 'Filing', contract: 'Contract', licence: 'Licence', hearing: 'Hearing', policy: 'Policy review', tax: 'Tax' };

export default function ExpiryWatchPage() {
  const navigate = useNavigate();
  const [days, setDays] = useState('90');
  const [kind, setKind] = useState('');
  const watch = useLoad(() => api.get('/compliance/expiry-watch/', { days }), [days]);
  const rem = useLoad(() => api.get('/compliance/reminders/', { days, kind: kind || undefined }), [days, kind]);
  const exportCsv = () => downloadCsv('compliance-reminders.csv', [['Due', 'Days left', 'Kind', 'What', 'Detail', 'Assigned to', 'Source'],
    ...(rem.data?.results || []).map((r) => [r.due_date, r.days_left, KIND[r.kind], r.title, r.subtitle, r.assignee, r.source])]);

  return (
    <>
      <PageHeader title="Expiry Watch" description="Licences and registrations from the company records, contract expiries, and everything else that is coming due."
        actions={<button type="button" className="btn" onClick={exportCsv} disabled={!rem.data?.results.length}>Export CSV</button>} />
      <section className="card">
        <div className="toolbar">
          <Pick id="ew-days" label="Look ahead" value={days} onChange={setDays} options={HORIZONS} blank="Next 90 days" />
        </div>
        <h2>Licences, registrations and contracts</h2>
        <p className="muted">Company licences and registrations are read from the company profile and cannot be changed here.</p>
        {watch.loading && <Loading />}
        <ErrorBox error={watch.error} onRetry={watch.reload} />
        {watch.data && <ExpiryTable rows={watch.data.results} />}
      </section>
      <section className="card" aria-label="Everything coming due">
        <h2>Everything coming due</h2>
        <p className="muted">Filings, contract and licence expiries, hearings and policy reviews, overdue items first.</p>
        <div className="chips" role="group" aria-label="Filter by kind">
          <button type="button" className="chip" aria-pressed={kind === ''} onClick={() => setKind('')}>All</button>
          {Object.entries(KIND).map(([k, label]) => <button key={k} type="button" className="chip" aria-pressed={kind === k} onClick={() => setKind(k)}>{label}</button>)}
        </div>
        {rem.loading && <Loading />}
        <ErrorBox error={rem.error} onRetry={rem.reload} />
        {rem.data && rem.data.results.length === 0 && <p className="empty">Nothing is due in this period.</p>}
        {rem.data && rem.data.results.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Due</th><th>What</th><th>Kind</th><th>Assigned to</th><th>Source</th></tr></thead>
              <tbody>
                {rem.data.results.map((r, i) => (
                  <tr key={`${r.kind}-${r.id}-${i}`} className="clickable" onClick={() => r.link && navigate(r.link)}>
                    <td data-label="Due">{fmtDate(r.due_date)} <ToneBadge tone={r.tone}>{daysText(r.days_left)}</ToneBadge></td>
                    <td data-label="What"><Link to={r.link} onClick={(e) => e.stopPropagation()}><strong>{r.title}</strong></Link>{r.subtitle && <div className="hint">{r.subtitle}</div>}</td>
                    <td data-label="Kind">{KIND[r.kind]}{r.reminder_today && <> <Badge tone="info">Reminder today</Badge></>}</td>
                    <td data-label="Assigned to">{r.assignee || '—'}</td>
                    <td data-label="Source">{r.source}{r.read_only && <> <Badge tone="neutral">Read only</Badge></>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rem.data && <div className="list-foot"><span>{rem.data.count} item{rem.data.count === 1 ? '' : 's'}</span></div>}
      </section>
    </>
  );
}
