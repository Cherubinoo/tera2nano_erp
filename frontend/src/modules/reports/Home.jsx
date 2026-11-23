import { Link } from 'react-router-dom';
import { api } from '../../api';
import { useAuth } from '../../context';
import { useLoad } from '../../hooks';
import { ErrorBox, Loading, PageHeader } from '../../ui';
import { money } from './common';

// Landing page: one card per report the signed-in user may open, each with a live headline number.
export default function ReportsHome() {
  const { data, error, loading, reload } = useLoad(() => api.get('/reports/home/'), []);
  const { user } = useAuth();
  const dashboardLink = user.permissions.includes('reports.view');
  return (
    <>
      <PageHeader title="Reports and analytics" description="Figures drawn from sales, purchases, the books, payroll, projects and partners. Each report shows only if your role may see its source." />
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && (
        <ul className="rpt-cards" aria-label="Reports">
          {dashboardLink && (
            <li>
              <Link to="/reports/dashboard" className="rpt-card rpt-card-featured">
                <h2>Executive dashboard</h2>
                <p>Revenue, expenses, profit, cash, receivables, payables, headcount and open deals, with a six-month trend.</p>
                <span className="rpt-card-link">Open the dashboard</span>
              </Link>
            </li>
          )}
          {data.reports.map((r) => (
            <li key={r.key}>
              <Link to={r.path} className="rpt-card">
                <h2>{r.title}</h2>
                <p>{r.description}</p>
                <div className="rpt-card-figure">
                  <span className="rpt-card-label">{r.headline ? r.headline.label : 'Headline unavailable'}</span>
                  <strong>{r.headline ? money(r.headline.value) : '—'}</strong>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {data && data.reports.length === 0 && <p className="empty">Your role does not include any of the reports yet.</p>}
    </>
  );
}
