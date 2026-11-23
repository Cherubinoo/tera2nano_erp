// Number and money formatting shared by the sales pages. Amounts arrive from the API as strings ("1234.50").

export const num = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};

// 1,23,456.50 (Indian grouping) with the currency symbol.
export function money(value, currency = 'INR') {
  try {
    return num(value).toLocaleString('en-IN', { style: 'currency', currency, minimumFractionDigits: 2, maximumFractionDigits: 2 });
  } catch {
    return `${currency} ${num(value).toFixed(2)}`;
  }
}

// Without the symbol, for table cells where the column heading already says the currency.
export const plain = (value) => num(value).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// 1.2L, 3.4Cr: for chart axes where space is tight.
export const compact = (value) => num(value).toLocaleString('en-IN', { notation: 'compact', maximumFractionDigits: 1 });

export const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

export const addDays = (iso, days) => {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

// Download rows as a CSV file (opens cleanly in Excel). Values are written as given; commas and quotes are escaped.
export function downloadCsv(filename, rows) {
  const esc = (v) => {
    const s = v === null || v === undefined ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const blob = new Blob([`﻿${rows.map((r) => r.map(esc).join(',')).join('\r\n')}`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
