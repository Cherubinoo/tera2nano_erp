import { useState } from 'react';
import { Field } from '../../ui';

// Date range controls used by the reports. `onApply` receives {from, to}.
export function PeriodBar({ from, to, onApply, single = false, label = 'As of' }) {
  const [f, setF] = useState(from);
  const [t, setT] = useState(to);
  return (
    <form className="toolbar no-print" onSubmit={(e) => { e.preventDefault(); onApply({ from: f, to: t }); }}>
      {!single && <Field id="rp-from" label="From"><input id="rp-from" type="date" value={f} onChange={(e) => setF(e.target.value)} /></Field>}
      <Field id="rp-to" label={single ? label : 'To'}><input id="rp-to" type="date" value={t} onChange={(e) => setT(e.target.value)} /></Field>
      <button type="submit" className="btn">Show</button>
    </form>
  );
}

export const SOURCE_LABEL = {
  manual: 'Manual journal', invoice: 'Invoice', debit_note: 'Debit note', credit_note: 'Credit note', payment: 'Payment received',
  expense: 'Expense', income: 'Income', transfer: 'Transfer', reversal: 'Reversal', opening: 'Opening balance', vendor_bill: 'Vendor bill',
  vendor_payment: 'Vendor payment', payroll: 'Payroll', partner: 'Partner', depreciation: 'Depreciation', fixed_asset: 'Fixed asset',
};
