import './assets.css';
import { api } from '../../api';
import { asList, useLoad } from '../../hooks';
import { Badge } from '../../ui';

export const STATUS_TONE = { in_use: 'good', disposed: 'neutral', sold: 'info', written_off: 'bad' };
export const AssetStatus = ({ status, label }) => <Badge tone={STATUS_TONE[status] || 'neutral'}>{label}</Badge>;

export const BOOKS_LABEL = { pending: 'Not in the books', posted: 'Posted', existing: 'Already in books' };

// Projects (the cost centres) come from the projects module; if that call fails the dropdown just offers "none".
export function useProjects() {
  const { data } = useLoad(() => api.get('/projects/lookup/').catch(() => []), []);
  return asList(data);
}

export const projectLabel = (projects, id) => {
  const p = projects.find((x) => x.id === id);
  return p ? `${p.code ? `${p.code} ` : ''}${p.name}` : '';
};

// "12 yrs", "40% WDV": the depreciation terms in a few words.
export const termsText = (t) => {
  const salvage = Number(t.salvage_percent) ? `, ${Number(t.salvage_percent)}% residual` : '';
  if (t.method === 'slm') return `Straight line, ${Number(t.useful_life_years)} yr${Number(t.useful_life_years) === 1 ? '' : 's'}${salvage}`;
  return `Written-down value, ${Number(t.rate_percent)}%${t.half_year_rule ? ', half-year rule' : ''}${salvage}`;
};

// The rules, in plain words, shown on the categories page and next to the depreciation run.
export function DepreciationRules() {
  return (
    <details className="fa-rules">
      <summary>How depreciation is worked out</summary>
      <ul>
        <li>Financial years run April to March. Depreciation starts on the date the asset is put to use, not the purchase date.</li>
        <li>The month it starts in is charged for the days used (days from the put-to-use date to month end, both counted, divided by the days in the month). Later months count in full. On a disposal, the last month is charged up to and including the disposal date.</li>
        <li><strong>Straight line (Companies Act):</strong> yearly charge = (cost less residual value) / useful life in years. Monthly charge = yearly charge / 12.</li>
        <li><strong>Written-down value (income-tax block rate):</strong> yearly charge = rate % of the book value at the start of the year (the cost in the first year), spread over the months of use.</li>
        <li><strong>Half-year rule (optional, written-down value only):</strong> in the year of acquisition, an asset used for fewer than 180 days up to 31 March gets half of the yearly rate; used for 180 days or more it gets the full rate. With the rule off, the first year is pro-rata by months used.</li>
        <li>Residual value is a % of cost (Companies Act allows up to 5%). Depreciation never goes beyond cost less residual value.</li>
        <li>Each month is posted once per asset: running a period again charges nothing twice. A run can be reversed, which frees the month.</li>
      </ul>
    </details>
  );
}

// The twelve month labels of a financial year that starts on `startIso` (YYYY-MM-DD).
export function monthLabels(startIso) {
  const [y, m] = (startIso || '2026-04-01').split('-').map(Number);
  return Array.from({ length: 12 }, (_, i) => new Date(y, m - 1 + i, 1).toLocaleDateString('en-IN', { month: 'short' }));
}

// Same rule as the server: eleven months of yearly / 12 rounded to the paisa, and the last month takes the remainder.
export function evenSplit(annual) {
  const cents = Math.round(Number(annual || 0) * 100);
  const per = Math.round(cents / 12);
  return [...Array(11).fill(per), cents - per * 11].map((c) => (c / 100).toFixed(2));
}
