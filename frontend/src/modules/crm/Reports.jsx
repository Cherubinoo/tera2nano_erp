import { useState } from 'react';
import { api, downloadFile } from '../../api';
import { ColumnChart, HBars } from '../../components/charts';
import { compact, money, num, plain } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Field, Loading, PageHeader, useToast } from '../../ui';
import { Funnel, query } from './common';

function Exports() {
  const notify = useToast();
  const [kind, setKind] = useState('leads');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  const statuses = { leads: [['', 'Any status'], ['new', 'New'], ['contacted', 'Contacted'], ['qualified', 'Qualified'], ['nurturing', 'Nurturing'], ['unqualified', 'Unqualified'], ['converted', 'Converted']],
    deals: [['', 'Any status'], ['open', 'Open'], ['won', 'Won'], ['lost', 'Lost']], activities: [['', 'Any']] };
  const run = async (e) => {
    e.preventDefault();
    if (from && to && from > to) { notify('The start date is after the end date.', 'error'); return; }
    setBusy(true);
    try { await downloadFile(`/crm/reports/${kind}.csv${query({ from, to, status: kind === 'activities' ? '' : status })}`, `crm-${kind}.csv`); notify('Export downloaded.'); } catch (err) { notify(err.message, 'error'); }
    setBusy(false);
  };
  return (
    <section className="card" aria-label="Export data">
      <h2>Export to CSV</h2>
      <p className="muted">Records created in the date range. Opens in Excel; cells that start like a formula are made safe.</p>
      <form className="toolbar" onSubmit={run}>
        <Field id="ex-kind" label="What"><select id="ex-kind" value={kind} onChange={(e) => { setKind(e.target.value); setStatus(''); }}>
          <option value="leads">Leads</option><option value="deals">Deals</option><option value="activities">Activities</option></select></Field>
        <Field id="ex-status" label="Status"><select id="ex-status" value={status} disabled={kind === 'activities'} onChange={(e) => setStatus(e.target.value)}>{statuses[kind].map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
        <Field id="ex-from" label="Created from"><input id="ex-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field id="ex-to" label="Created to"><input id="ex-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Preparing…' : 'Download CSV'}</button>
      </form>
    </section>
  );
}

export default function Reports() {
  const summary = useLoad(() => api.get('/crm/reports/summary/'), []);
  const dash = useLoad(() => api.get('/crm/dashboard/'), []);
  const loading = summary.loading || dash.loading;
  const error = summary.error || dash.error;
  const s = summary.data, d = dash.data;
  return (
    <>
      <PageHeader title="CRM Reports" description="Conversion, team results, lost deals, ageing and forecast." />
      <Exports />
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={() => { summary.reload(); dash.reload(); }} />
      {s && d && (
        <>
          <div className="grid-2">
            <section className="card" aria-label="Funnel"><h2>Funnel</h2><Funnel steps={s.funnel} /></section>
            <section className="card" aria-label="Lead conversion by source">
              <h2>Lead conversion by source</h2>
              {s.by_source.length === 0 ? <p className="empty">No leads yet.</p> : (
                <div className="table-wrap"><table className="crm-mini">
                  <thead><tr><th>Source</th><th className="amount">Leads</th><th className="amount">Qualified</th><th className="amount">Converted</th><th className="amount">Rate</th></tr></thead>
                  <tbody>{s.by_source.map((r) => <tr key={r.key}><td data-label="Source">{r.label}</td><td className="amount" data-label="Leads">{r.leads}</td><td className="amount" data-label="Qualified">{r.qualified}</td>
                    <td className="amount" data-label="Converted">{r.converted}</td><td className="amount" data-label="Rate">{r.rate}%</td></tr>)}</tbody>
                </table></div>
              )}
            </section>
          </div>
          <section className="card" aria-label="Team results">
            <h2>Sales team</h2>
            {s.team.length === 0 ? <p className="empty">No deals yet.</p> : (
              <div className="table-wrap"><table>
                <thead><tr><th>Owner</th><th className="amount">Open deals</th><th className="amount">Open value</th><th className="amount">Won</th><th className="amount">Lost</th><th className="amount">Won value</th><th className="amount">Win rate</th></tr></thead>
                <tbody>{s.team.map((t) => <tr key={t.key}><td data-label="Owner">{t.label}</td><td className="amount" data-label="Open deals">{t.open}</td><td className="amount" data-label="Open value">{plain(t.open_value)}</td>
                  <td className="amount" data-label="Won">{t.won}</td><td className="amount" data-label="Lost">{t.lost}</td><td className="amount" data-label="Won value">{plain(t.won_value)}</td>
                  <td className="amount" data-label="Win rate">{t.win_rate === null ? '—' : `${t.win_rate}%`}</td></tr>)}</tbody>
              </table></div>
            )}
          </section>
          <div className="grid-2">
            <section className="card" aria-label="Why deals are lost">
              <h2>Why deals are lost</h2>
              <HBars rows={s.lost_reasons.map((r) => ({ key: r.reason, label: `${r.reason} (${r.count})`, value: r.value, colour: 'var(--chart-4)' }))} format={(v) => money(v)} empty="No deal has been lost." />
            </section>
            <section className="card" aria-label="Ageing of open deals">
              <h2>Ageing of open deals</h2>
              <p className="muted">Days since each open deal was created.</p>
              <ColumnChart title="Open deals by age" format={compact} series={[{ key: 'value', label: 'Value', colour: 'var(--chart-3)' }]}
                data={d.aging.map((a) => ({ label: a.label, values: { value: a.value } }))} />
              <ul className="crm-plain">{d.aging.map((a) => <li key={a.key}><span>{a.label}</span><strong>{a.count} deal{a.count === 1 ? '' : 's'} · {money(a.value)}</strong></li>)}</ul>
            </section>
          </div>
          <section className="card" aria-label="Forecast">
            <h2>Forecast by expected close month</h2>
            <div className="table-wrap"><table>
              <thead><tr><th>Month</th><th className="amount">Deals</th><th className="amount">Value</th><th className="amount">Weighted</th></tr></thead>
              <tbody>
                {d.forecast.map((m) => <tr key={m.key}><td data-label="Month">{m.label}</td><td className="amount" data-label="Deals">{m.count}</td><td className="amount" data-label="Value">{plain(m.value)}</td><td className="amount" data-label="Weighted">{plain(m.weighted)}</td></tr>)}
                {[['past_due', 'Past the expected date'], ['undated', 'No expected date'], ['later', 'Later']].filter(([k]) => num(d.forecast_other[k].count) > 0).map(([k, l]) => (
                  <tr key={k}><td data-label="Month">{l}</td><td className="amount" data-label="Deals">{d.forecast_other[k].count}</td><td className="amount" data-label="Value">{plain(d.forecast_other[k].value)}</td><td className="amount" data-label="Weighted">{plain(d.forecast_other[k].weighted)}</td></tr>))}
              </tbody>
            </table></div>
          </section>
        </>
      )}
    </>
  );
}
