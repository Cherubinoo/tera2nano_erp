import { Link } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart } from '../../components/charts';
import { compact, num } from '../../format';
import { useLoad } from '../../hooks';
import { ErrorBox, Loading, Notice, PageHeader } from '../../ui';
import { Section, Table, Tile, money, plain, signTone } from './common';

// Executive dashboard: headline figures for the financial year to date. A tile appears only if the user may see its source.
export default function ExecutiveDashboard() {
  const { data, error, loading, reload } = useLoad(() => api.get('/reports/dashboard/'), []);
  return (
    <>
      <Link to="/reports" className="back-link">← Reports</Link>
      <PageHeader title="Executive dashboard" description={data ? `${data.period.label} to date. Receivables, payables and headcount are as of today.` : 'The business at a glance.'} />
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && (
        <>
          {data.tiles.length === 0 && <Notice>Your role does not include any of the dashboard figures.</Notice>}
          <div className="tiles">
            {data.tiles.map((t) => (
              <Tile key={t.key} label={t.label} to={t.link || undefined} hint={t.hint}
                value={t.value === null || t.value === undefined ? '—' : t.kind === 'money' ? money(t.value) : t.value}
                tone={t.key === 'profit' ? signTone(t.value) : ''} />
            ))}
          </div>
          {data.trend && (
            <Section title="Six-month trend" hint="Revenue and expenses booked each month.">
              <ColumnChart title="Revenue and expenses, last six months" format={compact}
                series={[{ key: 'revenue', label: 'Revenue', colour: 'var(--chart-1)' }, { key: 'expenses', label: 'Expenses', colour: 'var(--chart-2)' }]}
                data={data.trend.map((m) => ({ label: m.label.split(' ')[0], values: { revenue: m.revenue, expenses: m.expenses } }))} />
              <Table caption="Revenue, expenses and profit by month" rowKey={(r) => r.month}
                columns={[{ key: 'label', label: 'Month' }, { key: 'revenue', label: 'Revenue', amount: true }, { key: 'expenses', label: 'Expenses', amount: true },
                  { key: 'profit', label: 'Profit', amount: true, render: (r) => <span className={num(r.profit) < 0 ? 'rpt-neg' : ''}>{plain(r.profit)}</span> }]}
                rows={data.trend} />
            </Section>
          )}
        </>
      )}
    </>
  );
}
