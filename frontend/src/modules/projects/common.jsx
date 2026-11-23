import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { ColumnChart } from '../../components/charts';
import { compact, num } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, fmtDate } from '../../ui';

export const STATUS_TONE = { planned: 'info', active: 'good', on_hold: 'warn', completed: 'neutral', cancelled: 'bad' };
export const STATUS_LABEL = { planned: 'Planned', active: 'Active', on_hold: 'On hold', completed: 'Completed', cancelled: 'Cancelled' };
export const HEALTH = { on_track: ['good', 'On track'], at_risk: ['warn', 'At risk'], delayed: ['bad', 'Delayed'] };
export const BUDGET_HEALTH = { on_track: ['good', 'On budget'], at_risk: ['warn', 'Nearing budget'], over_budget: ['bad', 'Over budget'], none: ['neutral', 'No budget'] };
export const PRIORITY_TONE = { low: 'neutral', medium: 'info', high: 'warn', urgent: 'bad' };
export const MILESTONE_TONE = { pending: 'neutral', in_progress: 'info', done: 'good', overdue: 'bad' };
export const CATEGORIES = [['labour', 'Labour'], ['materials', 'Materials'], ['subcontract', 'Subcontract'], ['travel', 'Travel'], ['other', 'Other']];
export const TYPES = [['it_solution', 'IT solution'], ['nano_rnd', 'Nano-technology / R&D'], ['consulting', 'Consulting'], ['internal', 'Internal'], ['other', 'Other']];
export const PRIORITIES = [['low', 'Low'], ['medium', 'Medium'], ['high', 'High'], ['urgent', 'Urgent']];
export const TASK_STATUSES = [['todo', 'To do'], ['in_progress', 'In progress'], ['review', 'In review'], ['done', 'Done']];
export const SALES_STATUS_TONE = { draft: 'neutral', unpaid: 'info', overdue: 'bad', partial: 'warn', paid: 'good', void: 'neutral', open: 'info', closed: 'neutral', issued: 'info' };
export const SOURCE_LABELS = {};

export const StatusBadge = ({ status }) => <Badge tone={STATUS_TONE[status] || 'neutral'}>{STATUS_LABEL[status] || status}</Badge>;
export const HealthBadge = ({ value }) => { const [tone, label] = HEALTH[value] || ['neutral', '—']; return <Badge tone={tone}>{label}</Badge>; };
export const BudgetBadge = ({ value }) => { const [tone, label] = BUDGET_HEALTH[value] || ['neutral', '—']; return <Badge tone={tone}>{label}</Badge>; };
export const PriorityBadge = ({ value }) => <Badge tone={PRIORITY_TONE[value] || 'neutral'}>{(PRIORITIES.find((p) => p[0] === value) || [0, value])[1]}</Badge>;

export function Tile({ label, value, hint, tone, to }) {
  const inner = (<><div className="tile-label">{label}</div><div className="tile-value">{value}</div>{hint && <div className="hint">{hint}</div>}</>);
  return to ? <Link className={`tile ${tone || ''}`} to={to}>{inner}</Link> : <div className={`tile ${tone || ''}`}>{inner}</div>;
}

export function Progress({ value, tone }) {
  const v = Math.max(0, Math.min(100, num(value)));
  return (
    <div className="projects-progress" role="img" aria-label={`${v}% complete`}>
      <span className="projects-progress-track"><span className={`projects-progress-fill ${tone || ''}`} style={{ width: `${v}%` }} /></span>
      <span className="projects-progress-num">{v}%</span>
    </div>
  );
}

export const signClass = (v) => (num(v) < 0 ? 'projects-neg' : '');

// The "Project" dropdown data shared with every other module; an empty list when the call fails.
export function useProjectLookup() {
  const { data } = useLoad(() => api.get('/projects/lookup/').catch(() => []), []);
  return useMemo(() => (Array.isArray(data) ? data : []), [data]);
}

// Active employees from the HR module (when it is installed); an empty list otherwise, and people can be typed in.
export function useEmployees() {
  const { data } = useLoad(() => api.get('/hr/lookup/').catch(() => []), []);
  return useMemo(() => (Array.isArray(data) ? data : []), [data]);
}

export const clientErrors = (e) => (e && e.fieldErrors) || {};

// Timeline bars from start to planned end, with a progress fill and a marker for today. A text table of the same dates follows.
export function Timeline({ rows, today }) {
  const dated = rows.filter((r) => r.start_date && r.planned_end_date);
  if (!dated.length) return <p className="empty">No projects with a start and planned end date yet.</p>;
  const t0 = Math.min(...dated.map((r) => new Date(`${r.start_date}T00:00:00`).getTime()));
  const t1 = Math.max(...dated.map((r) => new Date(`${r.planned_end_date}T00:00:00`).getTime()), t0 + 86400000);
  const span = t1 - t0;
  const pos = (d) => ((new Date(`${d}T00:00:00`).getTime() - t0) / span) * 100;
  const now = today ? pos(today) : null;
  const tone = { on_track: 'good', at_risk: 'warn', delayed: 'bad' };
  return (
    <div>
      <div className="projects-gantt-axis" aria-hidden="true"><span>{fmtDate(new Date(t0).toISOString().slice(0, 10))}</span><span>{fmtDate(new Date(t1).toISOString().slice(0, 10))}</span></div>
      <ul className="projects-gantt" aria-label="Project timeline">
        {dated.map((r) => {
          const left = Math.max(pos(r.start_date), 0);
          const width = Math.max(pos(r.planned_end_date) - left, 1.5);
          return (
            <li key={r.id}>
              <span className="projects-gantt-name"><Link to={`/projects/projects/${r.id}`}>{r.name}</Link>
                <span className="projects-sub">{fmtDate(r.start_date)} to {fmtDate(r.planned_end_date)} · {num(r.progress)}% done</span></span>
              <span className="projects-gantt-track" title={`${r.name}: ${fmtDate(r.start_date)} to ${fmtDate(r.planned_end_date)}`}>
                <span className={`projects-gantt-bar ${tone[r.health] || ''}`} style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }}>
                  <span className="projects-gantt-done" style={{ width: `${Math.min(num(r.progress), 100)}%` }} />
                </span>
                {now !== null && now >= 0 && now <= 100 && <span className="projects-gantt-today" style={{ left: `${now}%` }} aria-hidden="true" />}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Revenue and cost per month; a plain sentence instead of an empty chart when nothing has been booked. */
export function MonthlyChart({ title, monthly }) {
  if (!monthly.some((m) => num(m.revenue) !== 0 || num(m.cost) !== 0)) return <p className="muted">Nothing has been booked to {title.toLowerCase().includes('project revenue') ? 'a project' : 'this project'} in the last six months.</p>;
  return (
    <ColumnChart title={title} format={compact}
      series={[{ key: 'revenue', label: 'Revenue', colour: 'var(--chart-2)' }, { key: 'cost', label: 'Cost', colour: 'var(--chart-4)' }]}
      data={monthly.map((m) => ({ label: m.label, values: { revenue: m.revenue, cost: m.cost } }))} />
  );
}
