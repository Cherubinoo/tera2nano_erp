import { Link } from 'react-router-dom';
import { ColumnChart, HBars, StackedBar } from '../../components/charts';
import { compact, num } from '../../format';
import { Delta, ReportPage, Section, Table, Tile, money, pct, plain, signTone } from './common';

const short = (s, n = 10) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

// 1. Annual revenue --------------------------------------------------------------------------------------------------
export function RevenueReport() {
  return (
    <ReportPage slug="revenue" title="Annual revenue" description="Sales income before GST, net of credit notes, against the same dates a year earlier.">
      {(d) => {
        const k = d.kpis;
        return (
          <>
            <div className="tiles">
              <Tile label="Revenue" value={money(k.revenue)} hint={d.period.label} />
              <Tile label="Previous year" value={money(k.previous_revenue)} hint="Same dates, a year earlier" />
              <Tile label="Change" value={pct(k.change_percent)} tone={signTone(k.change_percent)} hint={k.change_percent === null ? 'No sales a year earlier to compare' : 'Against the previous year'} />
              <Tile label="Monthly average" value={money(k.monthly_average)} hint={k.best_month ? `Best: ${k.best_month.label}, ${money(k.best_month.amount)}` : 'No sales yet'} />
            </div>
            <Section title="Revenue by month">
              <ColumnChart title="Revenue by month against the previous year" format={compact}
                series={[{ key: 'revenue', label: 'This period', colour: 'var(--chart-1)' }, { key: 'previous', label: 'Previous year', colour: 'var(--chart-grey)' }]}
                data={d.months.map((m) => ({ label: m.label.split(' ')[0], values: { revenue: m.revenue, previous: m.previous } }))} />
              <Table caption="Revenue by month" rowKey={(r) => r.month}
                columns={[{ key: 'label', label: 'Month' }, { key: 'revenue', label: 'Revenue', amount: true }, { key: 'previous', label: 'Last year', amount: true },
                  { key: 'change_percent', label: 'Change', amount: true, render: (r) => <Delta value={r.change_percent} /> }]}
                rows={d.months} foot={{ label: 'Total', revenue: plain(k.revenue), previous: plain(k.previous_revenue), change_percent: pct(k.change_percent) }} />
            </Section>
            {d.by_customer && (
              <Section title="Revenue by customer" hint="From issued invoices and debit notes less credit notes, before GST. Entries not made from an invoice are shown on their own row.">
                <HBars rows={d.by_customer.filter((r) => num(r.revenue) > 0).slice(0, 8).map((r) => ({ key: r.customer_id || 'other', label: r.customer, value: r.revenue }))}
                  format={compact} empty="No invoices in this period." />
                <Table caption="Revenue by customer" rowKey={(r) => r.customer_id || 'other'}
                  columns={[{ key: 'customer', label: 'Customer' }, { key: 'revenue', label: 'Revenue', amount: true }, { key: 'previous', label: 'Last year', amount: true },
                    { key: 'change_percent', label: 'Change', amount: true, render: (r) => <Delta value={r.change_percent} /> },
                    { key: 'share_percent', label: 'Share', amount: true, render: (r) => pct(r.share_percent) }]}
                  rows={d.by_customer} empty="No invoices in this period." />
              </Section>
            )}
          </>
        );
      }}
    </ReportPage>
  );
}

// 2. Project profitability ------------------------------------------------------------------------------------------
export function ProjectReport() {
  return (
    <ReportPage slug="project-profitability" allowAll title="Project profitability" description="Revenue, cost and profit of each project, from the entries tagged to it in the books.">
      {(d) => {
        const k = d.kpis;
        const booked = d.projects.filter((r) => num(r.revenue) || num(r.cost));
        return (
          <>
            <div className="tiles">
              <Tile label="Revenue" value={money(k.revenue)} hint="Booked to projects" />
              <Tile label="Cost" value={money(k.cost)} hint="Booked to projects" />
              <Tile label="Profit" value={money(k.profit)} tone={signTone(k.profit)} hint={k.margin_percent === null ? 'No revenue yet' : `${pct(k.margin_percent)} margin`} />
              <Tile label="Not tagged to a project" value={money(k.untagged_revenue)} hint={`${k.loss_making} project${k.loss_making === 1 ? '' : 's'} making a loss`} />
            </div>
            <Section title="Revenue and cost by project">
              {booked.length === 0 ? <p className="empty">Nothing has been booked to a project in this period.</p> : (
                <>
                  <ColumnChart title="Revenue and cost by project" format={compact}
                    series={[{ key: 'revenue', label: 'Revenue', colour: 'var(--chart-2)' }, { key: 'cost', label: 'Cost', colour: 'var(--chart-4)' }]}
                    data={booked.slice(0, 8).map((r) => ({ label: short(r.name), values: { revenue: r.revenue, cost: r.cost } }))} />
                  <p className="hint">The table below lists every project with its figures.</p>
                </>
              )}
              <Table caption="Project profitability" rowKey={(r) => r.id}
                columns={[{ key: 'name', label: 'Project', render: (r) => <><Link to={`/projects/projects/${r.id}`}>{r.name}</Link><span className="rpt-sub">{r.code}{r.customer ? ` · ${r.customer}` : ''}</span></> },
                  { key: 'status_label', label: 'Status' }, { key: 'contract_value', label: 'Contract value', amount: true },
                  { key: 'revenue', label: 'Revenue', amount: true }, { key: 'cost', label: 'Cost', amount: true },
                  { key: 'profit', label: 'Profit', amount: true, render: (r) => <span className={num(r.profit) < 0 ? 'rpt-neg' : ''}>{plain(r.profit)}</span> },
                  { key: 'margin_percent', label: 'Margin', amount: true, render: (r) => pct(r.margin_percent) }]}
                rows={d.projects} empty="No projects yet."
                foot={{ name: 'Total', revenue: plain(k.revenue), cost: plain(k.cost), profit: plain(k.profit), margin_percent: pct(k.margin_percent) }} />
            </Section>
          </>
        );
      }}
    </ReportPage>
  );
}

// 3. Partner capital -------------------------------------------------------------------------------------------------
export function PartnerReport() {
  return (
    <ReportPage slug="partner-capital" allowAll title="Partner capital" description="Each partner's capital, current account and loan at the end of the period, and the drawings made in it.">
      {(d) => {
        const k = d.kpis;
        return (
          <>
            <div className="tiles">
              <Tile label="Total capital" value={money(k.capital)} hint={`${k.partners} active partner${k.partners === 1 ? '' : 's'}, to ${d.as_of}`} />
              <Tile label="Agreed capital" value={money(k.agreed_capital)} hint="As set in each partner account" />
              <Tile label="Current accounts" value={money(k.current)} tone={signTone(k.current)} hint="Profit credited less drawings" />
              <Tile label="Drawings" value={money(k.drawings)} hint="Taken in this period" />
            </div>
            <Section title="Capital by partner">
              <HBars rows={d.partners.filter((r) => num(r.capital) > 0).map((r) => ({ key: r.id, label: r.name, value: r.capital }))} format={compact} empty="No capital recorded yet." />
              <Table caption="Partner capital" rowKey={(r) => r.id}
                columns={[{ key: 'name', label: 'Partner' }, { key: 'agreed_capital', label: 'Agreed capital', amount: true }, { key: 'opening_capital', label: 'Opening capital', amount: true },
                  { key: 'capital', label: 'Capital at end', amount: true }, { key: 'capital_change', label: 'Change', amount: true }, { key: 'current', label: 'Current account', amount: true },
                  { key: 'loan', label: 'Loan', amount: true }, { key: 'drawings', label: 'Drawings', amount: true },
                  { key: 'contribution_percent', label: 'Share of capital', amount: true, render: (r) => pct(r.contribution_percent) }]}
                rows={d.partners} empty="No partners yet."
                foot={{ name: 'Total', agreed_capital: plain(k.agreed_capital), capital: plain(k.capital), current: plain(k.current), loan: plain(k.loan), drawings: plain(k.drawings) }} />
            </Section>
          </>
        );
      }}
    </ReportPage>
  );
}

// 4. Salary ----------------------------------------------------------------------------------------------------------
export function SalaryReport() {
  return (
    <ReportPage slug="salary" title="Salary report" description="Salary cost by month and department from approved, posted or paid payroll runs. Totals only: no one person's pay is shown.">
      {(d) => {
        const k = d.kpis;
        return (
          <>
            <div className="tiles">
              <Tile label="Cost to company" value={money(k.employer_cost)} hint={`${k.months_paid} month${k.months_paid === 1 ? '' : 's'} paid`} />
              <Tile label="Gross pay" value={money(k.gross)} />
              <Tile label="Net pay" value={money(k.net)} hint={`After ${money(k.deductions)} deductions`} />
              <Tile label="Employees paid" value={k.headcount} hint="In the latest month paid" />
            </div>
            <Section title="Pay by month">
              <ColumnChart title="Gross and net pay by month" format={compact}
                series={[{ key: 'gross', label: 'Gross', colour: 'var(--chart-1)' }, { key: 'net', label: 'Net', colour: 'var(--chart-2)' }]}
                data={d.months.map((m) => ({ label: m.label.split(' ')[0], values: { gross: m.gross, net: m.net } }))} />
              <Table caption="Salary by month" rowKey={(r) => r.month}
                columns={[{ key: 'label', label: 'Month' }, { key: 'employees', label: 'Employees', amount: true, render: (r) => r.employees }, { key: 'gross', label: 'Gross pay', amount: true },
                  { key: 'deductions', label: 'Deductions', amount: true }, { key: 'net', label: 'Net pay', amount: true }, { key: 'employer_cost', label: 'Cost to company', amount: true }]}
                rows={d.months} foot={{ label: 'Total', gross: plain(k.gross), deductions: plain(k.deductions), net: plain(k.net), employer_cost: plain(k.employer_cost) }} />
            </Section>
            <Section title="Cost by department">
              <HBars rows={d.departments.map((r) => ({ key: r.department, label: r.department, value: r.employer_cost }))} format={compact} empty="No payroll has been run in this period." />
              <Table caption="Salary by department" rowKey={(r) => r.department}
                columns={[{ key: 'department', label: 'Department' }, { key: 'employees', label: 'Employees', amount: true, render: (r) => r.employees }, { key: 'gross', label: 'Gross pay', amount: true },
                  { key: 'net', label: 'Net pay', amount: true }, { key: 'employer_cost', label: 'Cost to company', amount: true }]}
                rows={d.departments} empty="No payroll has been run in this period." />
            </Section>
          </>
        );
      }}
    </ReportPage>
  );
}

// 5 and 6. Ageing (customers and vendors) ---------------------------------------------------------------------------
function AgeingSections({ d, who, rowsKey, countKey, countLabel, link }) {
  const k = d.kpis;
  const rows = d[rowsKey];
  const name = who;
  return (
    <>
      <div className="tiles">
        <Tile label="Outstanding" value={money(k.outstanding)} hint={`As of ${d.as_of}`} />
        <Tile label="Overdue" value={money(k.overdue)} tone={num(k.overdue) > 0 ? 'bad' : ''} hint={k.overdue_percent === null ? 'Nothing outstanding' : `${pct(k.overdue_percent)} of the total`} />
        <Tile label={name === 'customer' ? 'Customers owing' : 'Vendors owed'} value={k[countKey]} hint={`${k[countLabel]} open ${name === 'customer' ? 'invoices' : 'bills'}`} />
        <Tile label="Largest" value={k.largest ? money(k.largest.amount) : '—'} hint={k.largest ? k.largest[name] : 'None'} />
      </div>
      <Section title="By age" hint="Days past the due date; not yet due counts as current.">
        <StackedBar parts={d.buckets.map((b) => ({ key: b.key, label: b.label, value: b.amount }))} format={compact} />
      </Section>
      <Section title={name === 'customer' ? 'By customer' : 'By vendor'}>
        <HBars rows={rows.slice(0, 8).map((r) => ({ key: r[`${name}_id`], label: r[name], value: r.total }))} format={compact} empty="Nothing outstanding." />
        <Table caption={`Outstanding by ${name}`} rowKey={(r) => r[`${name}_id`]}
          columns={[{ key: name, label: name === 'customer' ? 'Customer' : 'Vendor', render: (r) => (link ? <Link to={link(r)}>{r[name]}</Link> : r[name]) },
            ...d.buckets.map((b) => ({ key: b.key, label: b.label, amount: true })), { key: 'total', label: 'Total', amount: true },
            { key: 'oldest_overdue_days', label: 'Oldest overdue', amount: true, render: (r) => (r.oldest_overdue_days ? `${r.oldest_overdue_days} days` : '—') }]}
          rows={rows} empty="Nothing outstanding."
          foot={{ [name]: 'Total', ...Object.fromEntries(d.buckets.map((b) => [b.key, plain(b.amount)])), total: plain(k.outstanding) }} />
      </Section>
    </>
  );
}

export function ReceivablesReport() {
  return (
    <ReportPage slug="receivables" allowAll title="Pending customer payments" description="What customers still owe on issued invoices and debit notes, by customer and age. A period limits it to invoices raised in it.">
      {(d) => <AgeingSections d={d} who="customer" rowsKey="customers" countKey="customers_owing" countLabel="invoices" link={(r) => `/sales/customers/${r.customer_id}`} />}
    </ReportPage>
  );
}

export function VendorReport() {
  return (
    <ReportPage slug="vendor-outstanding" allowAll title="Vendor outstanding" description="What we still owe vendors on recorded bills and opening balances, by vendor and age. A period limits it to bills dated in it.">
      {(d) => <AgeingSections d={d} who="vendor" rowsKey="vendors" countKey="vendors_owed" countLabel="bills" link={(r) => `/purchases/vendors/${r.vendor_id}`} />}
    </ReportPage>
  );
}

// 7. GST ----------------------------------------------------------------------------------------------------------
export function GstReport() {
  return (
    <ReportPage slug="gst" title="GST summary and tax liability" description="GST raised on sales, input credit earned on purchases, what was paid, and what is still owed.">
      {(d) => {
        const k = d.kpis;
        return (
          <>
            <div className="tiles">
              <Tile label="GST raised" value={money(k.output)} hint={`On ${money(k.taxable)} of sales`} />
              <Tile label="Input tax credit" value={money(k.itc)} hint={`${money(k.itc_balance)} still unused`} />
              <Tile label="Net for the period" value={money(k.net_payable)} hint="Raised less credit earned" />
              <Tile label="Still owed" value={money(k.closing_liability)} tone={num(k.closing_liability) > 0 ? 'bad' : ''} hint={`${money(k.paid)} paid in cash`} />
            </div>
            <Section title="GST by month">
              <ColumnChart title="GST raised and input credit by month" format={compact}
                series={[{ key: 'output', label: 'GST raised', colour: 'var(--chart-1)' }, { key: 'itc', label: 'Input credit', colour: 'var(--chart-3)' }]}
                data={d.months.map((m) => ({ label: m.label.split(' ')[0], values: { output: m.output, itc: m.itc } }))} />
              <Table caption="GST by month" rowKey={(r) => r.month}
                columns={[{ key: 'label', label: 'Month' }, { key: 'taxable', label: 'Taxable sales', amount: true }, { key: 'output', label: 'GST raised', amount: true },
                  { key: 'itc', label: 'Input credit', amount: true }, { key: 'net', label: 'Net', amount: true }, { key: 'paid', label: 'Paid in cash', amount: true }, { key: 'closing', label: 'Owed at month end', amount: true }]}
                rows={d.months} foot={{ label: 'Total', taxable: plain(k.taxable), output: plain(k.output), itc: plain(k.itc), net: plain(k.net_payable), paid: plain(k.paid), closing: plain(k.closing_liability) }} />
            </Section>
            <Section title="By tax head">
              <Table caption="GST by head" rowKey={(r) => r.head}
                columns={[{ key: 'label', label: 'Head' }, { key: 'opening', label: 'Owed at start', amount: true }, { key: 'output', label: 'GST raised', amount: true }, { key: 'itc', label: 'Credit earned', amount: true },
                  { key: 'itc_utilised', label: 'Credit used', amount: true }, { key: 'paid', label: 'Paid in cash', amount: true }, { key: 'closing', label: 'Owed at end', amount: true }]}
                rows={d.heads} />
            </Section>
          </>
        );
      }}
    </ReportPage>
  );
}

// 8. Cash flow ----------------------------------------------------------------------------------------------------
export function CashFlowReport() {
  return (
    <ReportPage slug="cash-flow" title="Cash-flow analysis" description="Where cash came from and where it went, from the books. Moves between your own bank and cash accounts are left out of money in and out.">
      {(d) => {
        const k = d.kpis;
        const s = d.statement;
        const lines = [['Net profit', s.net_profit], ...s.operating.map((i) => [i.name, i.amount]), ['Net cash from operating activities', k.operating, true],
          ...s.investing.map((i) => [i.name, i.amount]), ['Net cash from investing activities', k.investing, true],
          ...s.financing.map((i) => [i.name, i.amount]), ['Net cash from financing activities', k.financing, true],
          ['Net change in cash and bank', k.net_change, true], ['Cash and bank at start', k.opening_cash], ['Cash and bank at end', k.closing_cash, true]].map(([label, amount, strong], i) => ({ i, label, amount, strong }));
        return (
          <>
            <div className="tiles">
              <Tile label="Cash and bank at end" value={money(k.closing_cash)} hint={`Started at ${money(k.opening_cash)}`} />
              <Tile label="Money in" value={money(k.inflow)} />
              <Tile label="Money out" value={money(k.outflow)} />
              <Tile label="Net change" value={money(k.net_change)} tone={signTone(k.net_change)} hint={`Operating ${money(k.operating)}`} />
            </div>
            <Section title="Money in and out by month">
              <ColumnChart title="Money in and out by month" format={compact}
                series={[{ key: 'inflow', label: 'Money in', colour: 'var(--chart-3)' }, { key: 'outflow', label: 'Money out', colour: 'var(--chart-4)' }]}
                data={d.months.map((m) => ({ label: m.label.split(' ')[0], values: { inflow: m.inflow, outflow: m.outflow } }))} />
              <Table caption="Cash and bank by month" rowKey={(r) => r.month}
                columns={[{ key: 'label', label: 'Month' }, { key: 'inflow', label: 'Money in', amount: true }, { key: 'outflow', label: 'Money out', amount: true },
                  { key: 'net', label: 'Net', amount: true }, { key: 'closing', label: 'Balance at month end', amount: true }]}
                rows={d.months} />
            </Section>
            <Section title="Cash-flow statement" hint={s.ties ? 'Ends on the real change in cash and bank.' : 'The statement does not tie to the cash and bank balances: check the journal.'}>
              <Table caption="Cash-flow statement" rowKey={(r) => r.i}
                columns={[{ key: 'label', label: 'Line', render: (r) => (r.strong ? <strong>{r.label}</strong> : r.label) }, { key: 'amount', label: 'Amount', amount: true }]} rows={lines} />
            </Section>
          </>
        );
      }}
    </ReportPage>
  );
}
