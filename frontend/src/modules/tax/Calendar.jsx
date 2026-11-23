import { useState } from 'react';
import { api } from '../../api';
import { Donut, HBars } from '../../components/charts';
import { useLoad } from '../../hooks';
import { ErrorBox, Field, Loading, PageHeader } from '../../ui';
import { EventList } from './Dashboard';
import { KIND_COLOUR, KIND_LABEL, Tile, monthLabel } from './common';

export default function Calendar() {
  const [days, setDays] = useState('90');
  const [kind, setKind] = useState('');
  const { data, error, loading, reload } = useLoad(() => api.get('/tax/calendar/', { days }), [days]);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const events = data.events.filter((e) => !kind || e.kind === kind);
  const byMonth = {};
  events.forEach((e) => { const k = e.due_date.slice(0, 7); (byMonth[k] = byMonth[k] || []).push(e); });
  const byKind = Object.keys(KIND_LABEL).map((k) => ({ key: k, label: KIND_LABEL[k], colour: KIND_COLOUR[k], value: data.events.filter((e) => e.kind === k).length }));
  return (
    <>
      <PageHeader title="Tax Calendar" description="GST returns, TDS returns, advance tax and income tax filings that are due, with the last 30 days' missed ones." />
      <div className="tiles">
        <Tile label="Overdue" value={data.counts.overdue} tone={data.counts.overdue ? 'bad' : 'good'} hint="Not filed or paid after the due date" />
        <Tile label="Due in 7 days" value={data.counts.next_7} />
        <Tile label="Due in 30 days" value={data.counts.next_30} />
        <Tile label={`Due in ${days} days`} value={data.counts.total - data.counts.overdue} hint="Everything coming up" />
      </div>
      <div className="grid-2">
        <section className="card" aria-label="Due dates by kind">
          <h2>By kind</h2>
          <Donut slices={byKind} centreLabel="due dates" />
        </section>
        <section className="card" aria-label="Due dates by month">
          <h2>By month</h2>
          <HBars rows={Object.entries(
            data.events.reduce((a, e) => { const k = e.due_date.slice(0, 7); a[k] = (a[k] || 0) + 1; return a; }, {})).map(([k, n]) => ({ key: k, label: monthLabel(k), value: n }))}
            format={(v) => `${v} due`} empty="Nothing due." />
        </section>
      </div>
      <section className="card">
        <div className="toolbar">
          <Field id="cal-days" label="Look ahead">
            <select id="cal-days" value={days} onChange={(e) => setDays(e.target.value)}><option value="30">30 days</option><option value="60">60 days</option><option value="90">90 days</option><option value="180">180 days</option><option value="365">1 year</option></select>
          </Field>
          <div className="chips" role="group" aria-label="Filter by kind">
            {[['', 'All'], ...Object.entries(KIND_LABEL)].map(([k, l]) => <button key={k} type="button" className="chip" aria-pressed={kind === k} onClick={() => setKind(k)}>{l}</button>)}
          </div>
        </div>
        {events.length === 0 && <p className="empty">Nothing due in this window.</p>}
        {Object.entries(byMonth).map(([k, list]) => (
          <div key={k}><h3>{monthLabel(k)}</h3><EventList events={list} /></div>
        ))}
      </section>
    </>
  );
}
