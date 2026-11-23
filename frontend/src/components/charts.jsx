import { useId } from 'react';
import { compact, num } from '../format';

// Small SVG/CSS charts with no dependencies. Every chart has a text equivalent (aria-label and a hidden table) and uses
// the --chart-* colour tokens so it reads in light and dark mode. Values are labelled on the marks instead of in a legend
// where there is room.

export const SERIES_COLOURS = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)', 'var(--chart-grey)'];

function niceMax(max) {
  if (max <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(max));
  const f = max / pow;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * pow;
}

// Vertical grouped columns. series: [{key, label, colour?}], data: [{label, values: {key: number}}].
export function ColumnChart({ series, data, format = compact, title }) {
  const id = useId();
  const W = 480, H = 230, left = 44, right = 8, top = 18, bottom = 28;
  const plotW = W - left - right, plotH = H - top - bottom;
  const max = niceMax(Math.max(0, ...data.flatMap((d) => series.map((s) => num(d.values[s.key])))));
  const ticks = [0, 0.5, 1].map((t) => t * max);
  const groupW = plotW / Math.max(data.length, 1);
  const barW = Math.min(30, (groupW * 0.7) / series.length);
  const summary = data.map((d) => `${d.label}: ${series.map((s) => `${s.label} ${format(d.values[s.key])}`).join(', ')}`).join('; ');
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={`${id}-t`} preserveAspectRatio="xMidYMid meet">
        <title id={`${id}-t`}>{`${title || 'Chart'}. ${summary}`}</title>
        {ticks.map((t) => {
          const y = top + plotH - (t / max) * plotH;
          return (
            <g key={t}>
              <line x1={left} x2={W - right} y1={y} y2={y} className="chart-grid" />
              <text x={left - 6} y={y + 4} textAnchor="end" className="chart-axis">{format(t)}</text>
            </g>
          );
        })}
        {data.map((d, i) => {
          const cx = left + groupW * i + groupW / 2;
          const x0 = cx - (barW * series.length) / 2;
          return (
            <g key={d.label}>
              {series.map((s, j) => {
                const v = num(d.values[s.key]);
                const h = (v / max) * plotH;
                return (
                  <g key={s.key}>
                    <rect x={x0 + j * barW} y={top + plotH - h} width={barW - 2} height={Math.max(h, v > 0 ? 1.5 : 0)} rx="2"
                      fill={s.colour || SERIES_COLOURS[j]}><title>{`${d.label}, ${s.label}: ${format(v)}`}</title></rect>
                    {v > 0 && h > 14 && series.length === 1 && (
                      <text x={x0 + j * barW + (barW - 2) / 2} y={top + plotH - h - 4} textAnchor="middle" className="chart-value">{format(v)}</text>
                    )}
                  </g>
                );
              })}
              <text x={cx} y={H - 8} textAnchor="middle" className="chart-axis">{d.label}</text>
            </g>
          );
        })}
      </svg>
      {series.length > 1 && (
        <figcaption className="chart-legend">
          {series.map((s, j) => <span key={s.key}><i style={{ background: s.colour || SERIES_COLOURS[j] }} aria-hidden="true" />{s.label}</span>)}
        </figcaption>
      )}
    </figure>
  );
}

// Horizontal bars, one row per item, label on the left and the value at the end of the bar.
export function HBars({ rows, format = compact, colour = 'var(--chart-1)', empty = 'Nothing to show yet.' }) {
  if (!rows.length) return <p className="muted">{empty}</p>;
  const max = Math.max(...rows.map((r) => num(r.value)), 1);
  return (
    <ul className="hbars" aria-label="Bar chart">
      {rows.map((r) => (
        <li key={r.key || r.label}>
          <span className="hbar-label" title={r.label}>{r.label}</span>
          <span className="hbar-track"><span className="hbar" style={{ width: `${Math.max((num(r.value) / max) * 100, 1.5)}%`, background: r.colour || colour }} /></span>
          <span className="hbar-value">{format(r.value)}</span>
        </li>
      ))}
    </ul>
  );
}

// A single bar split into parts (for example receivables by age), with the parts listed beneath.
export function StackedBar({ parts, format = compact, total }) {
  const sum = total ?? parts.reduce((a, p) => a + num(p.value), 0);
  return (
    <div>
      <div className="stack" role="img" aria-label={parts.map((p) => `${p.label}: ${format(p.value)}`).join(', ')}>
        {parts.map((p, i) => num(p.value) > 0 && (
          <span key={p.key} style={{ width: `${(num(p.value) / (sum || 1)) * 100}%`, background: p.colour || SERIES_COLOURS[i] }} title={`${p.label}: ${format(p.value)}`} />
        ))}
        {sum === 0 && <span className="stack-empty" />}
      </div>
      <ul className="legend-list">
        {parts.map((p, i) => (
          <li key={p.key}><i style={{ background: p.colour || SERIES_COLOURS[i] }} aria-hidden="true" /><span>{p.label}</span><strong>{format(p.value)}</strong></li>
        ))}
      </ul>
    </div>
  );
}

// Donut of counts with the total in the middle and a labelled legend.
export function Donut({ slices, centreLabel = 'total', format = (v) => v, showTotal = true }) {
  const total = slices.reduce((a, s) => a + num(s.value), 0);
  const R = 52, C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div className="donut-wrap">
      <svg viewBox="0 0 140 140" width="150" height="150" role="img" aria-label={`${slices.map((s) => `${s.label}: ${format(s.value)}`).join(', ')}`}>
        <circle cx="70" cy="70" r={R} fill="none" stroke="var(--border)" strokeWidth="18" />
        {total > 0 && slices.map((s) => {
          const len = (num(s.value) / total) * C;
          const el = <circle key={s.key} cx="70" cy="70" r={R} fill="none" stroke={s.colour} strokeWidth="18"
            strokeDasharray={`${len} ${C - len}`} strokeDashoffset={-offset} transform="rotate(-90 70 70)"><title>{`${s.label}: ${format(s.value)}`}</title></circle>;
          offset += len;
          return num(s.value) > 0 ? el : null;
        })}
        <text x="70" y={showTotal ? 68 : 74} textAnchor="middle" className="donut-total">{showTotal ? total : slices.filter((s) => num(s.value) > 0).length}</text>
        <text x="70" y="86" textAnchor="middle" className="chart-axis">{centreLabel}</text>
      </svg>
      <ul className="legend-list">
        {slices.map((s) => <li key={s.key}><i style={{ background: s.colour }} aria-hidden="true" /><span>{s.label}</span><strong>{format(s.value)}</strong></li>)}
      </ul>
    </div>
  );
}
