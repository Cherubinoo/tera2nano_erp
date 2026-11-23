import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart, Donut, HBars, StackedBar } from '../../components/charts';
import { useLoad } from '../../hooks';
import { ErrorBox, Field, Loading, Notice, PageHeader, fmtDate, useToast } from '../../ui';
import { CATEGORY_COLOUR, STATUS_COLOUR, ItemDialog, StatusBadge, Tile, daysText, useCanManage, useMeta } from './common';
import { ExpiryTable } from './ExpiryWatch';

const SPLIT = [
  ['on_time', 'Filed on time', 'var(--chart-2)'], ['late', 'Filed late', 'var(--chart-3)'],
  ['overdue', 'Overdue', 'var(--chart-4)'], ['pending', 'Still to file', 'var(--chart-1)'],
];

export default function ComplianceDashboard() {
  const canManage = useCanManage();
  const notify = useToast();
  const [fy, setFy] = useState('');
  const [openId, setOpenId] = useState(null);
  const meta = useMeta();
  const { data: d, error, loading, reload } = useLoad(() => api.get('/compliance/dashboard/', { fy: fy || undefined }), [fy]);
  if (loading && !d) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;

  const generate = async () => {
    try {
      const r = await api.post('/compliance/calendar/generate/', { fy: d.fy });
      notify(`Calendar for ${d.fy}: ${r.created} added, ${r.updated} moved.`);
      reload();
    } catch (e) { notify(e.message, 'error'); }
  };
  const score = d.score;
  const scoreTone = score === null ? '' : score >= 90 ? 'good' : score < 60 ? 'bad' : '';

  return (
    <>
      <PageHeader title="Compliance" description="Statutory filings, licences, contracts and legal matters, and what is due next."
        actions={(
          <>
            <Field id="cd-fy" label="Financial year" className="compliance-fy">
              <select id="cd-fy" value={d.fy} onChange={(e) => setFy(e.target.value)}>{d.fys.map((f) => <option key={f} value={f}>{f}</option>)}</select>
            </Field>
            {canManage && <button type="button" className="btn btn-primary" onClick={generate}>{d.generated ? 'Refresh calendar' : 'Generate calendar'}</button>}
            <Link className="btn" to="/compliance/calendar">Open calendar</Link>
          </>
        )} />

      {!d.generated && (
        <Notice type="info">
          No compliance calendar exists for {d.fy} yet. {canManage ? 'Choose "Generate calendar" to create the due dates from the obligations catalogue.' : 'Ask someone who manages compliance to generate it.'}
        </Notice>
      )}

      <div className="tiles">
        <Tile label="Compliance score" value={score === null ? '—' : `${score}%`} tone={scoreTone}
          hint={score === null ? `Nothing has fallen due in ${d.fy} yet` : `Filed on time, of everything due so far in ${d.fy}`} />
        <Tile label="Overdue" value={d.overdue} tone={d.overdue > 0 ? 'bad' : ''} hint={d.overdue ? 'Not filed, past the due date' : 'Nothing overdue'} to="/compliance/filings?status=overdue" />
        <Tile label="Due in 30 days" value={d.due_in_30_days} hint={`${d.due_soon} within ${meta.data?.due_soon_days ?? 15} days`} to="/compliance/filings?status=due_soon" />
        <Tile label="Filed this year" value={`${d.filed} of ${d.total}`} hint="Filings closed in the year" to="/compliance/filings?status=filed" />
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Filings by category">
          <h2>By category</h2>
          <p className="muted">Everything in the {d.fy} calendar, by kind of obligation.</p>
          <Donut centreLabel="items" slices={d.by_category.map((c) => ({ key: c.key, label: c.label, value: c.value, colour: CATEGORY_COLOUR[c.key] }))} />
          {d.by_category.length === 0 && <p className="empty">No items yet.</p>}
        </section>
        <section className="card" aria-label="Filings by status">
          <h2>By status</h2>
          <p className="muted">Where each item stands today.</p>
          <Donut centreLabel="items" slices={d.by_status.map((c) => ({ key: c.key, label: c.label, value: c.value, colour: STATUS_COLOUR[c.key] }))} />
        </section>
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Filings per month">
          <h2>Filings per month</h2>
          <p className="muted">By due month: filed on time, filed late and overdue.</p>
          <ColumnChart title="Filings per month, on time, late and overdue" format={(v) => String(Math.round(v))}
            series={[{ key: 'on_time', label: 'On time', colour: 'var(--chart-2)' }, { key: 'late', label: 'Late', colour: 'var(--chart-3)' }, { key: 'overdue', label: 'Overdue', colour: 'var(--chart-4)' }]}
            data={d.monthly.map((m) => ({ label: m.label.split(' ')[0], values: { on_time: m.on_time, late: m.late, overdue: m.overdue } }))} />
          <h3>On time or late</h3>
          <StackedBar format={(v) => String(v)} parts={SPLIT.map(([key, label, colour]) => ({ key, label, colour, value: d.on_time_split[key] }))} />
        </section>
        <section className="card" aria-label="Workload by assignee">
          <h2>Workload by person</h2>
          <p className="muted">Open items in {d.fy} for each person.</p>
          <HBars rows={d.workload.map((w) => ({ key: w.label, label: w.label, value: w.value }))} format={(v) => String(v)} empty="No open items." />
        </section>
      </div>

      <section className="card" aria-label="Upcoming filings">
        <div className="card-head"><h2>Upcoming filings</h2><Link to="/compliance/filings">View all</Link></div>
        {d.upcoming.length === 0 ? <p className="empty">Nothing is due in the next three months.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Due</th><th>Filing</th><th>Period</th><th>Assigned to</th><th>Status</th></tr></thead>
              <tbody>
                {d.upcoming.map((i) => (
                  <tr key={i.id} className="clickable" onClick={() => setOpenId(i.id)}>
                    <td data-label="Due">{fmtDate(i.due_date)} <span className="hint">{daysText(i.days_to_due)}</span></td>
                    <td data-label="Filing"><button type="button" className="link-button" onClick={(e) => { e.stopPropagation(); setOpenId(i.id); }}><strong>{i.title}</strong></button>{i.form_no && <div className="hint">{i.form_no}</div>}</td>
                    <td data-label="Period">{i.period_label || '—'}</td>
                    <td data-label="Assigned to">{i.assignee?.name || '—'}</td>
                    <td data-label="Status"><StatusBadge item={i} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card" aria-label="Expiry watch">
        <div className="card-head"><h2>Expiry watch</h2><Link to="/compliance/expiry-watch">View all ({d.expiry_watch_total})</Link></div>
        <ExpiryTable rows={d.expiry_watch} />
      </section>
      {openId && meta.data && <ItemDialog id={openId} meta={meta.data} onClose={() => setOpenId(null)} onChanged={reload} />}
    </>
  );
}

