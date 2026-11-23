# Tera2Nano Solutions LLP: ERP roadmap

The 14 modules requested, plus CRM, and what each one holds today. Status is as of 15 Nov 2026.
Done = works end to end, has tests and a browser check; Partial = some listed items are not built.

| # | Module | Status | What exists / what is still open |
|---|---|---|---|
| 1 | Company Management | **Done** | Profile, LLPIN/PAN/TAN/GSTIN, registered office, bank accounts (encrypted, reference IDs), documents, financial years, letterhead and logo, numbering, policies, change history. |
| 2 | Partner Management | **Done** | Partner capital, drawings, interest, remuneration, loans, profit-sharing ratios with effective dates and FY allocation, capital statements, partner documents, withdrawals through approvals. |
| 3 | Finance & Accounting | **Done** | Double-entry books, chart of accounts, automatic posting from sales, purchases, payroll and partners, expenses and income with GST (expenses above an approval limit wait for approval), journal, bank reconciliation, trial balance, ledger, P&L, balance sheet, cash flow. Fixed-asset register: categories with straight-line (Companies Act) or written-down-value (income-tax rate, optional half-year rule) depreciation, numbered assets with documents, purchase posting (or flagged as already in the books), monthly depreciation preview and run (once per asset and month, reversible), sale, scrap and write-off with gain or loss, register as on a date, depreciation by financial year, disposals report. Budgets by financial year with account and project (cost-centre) lines, even or month-by-month phasing, saved revisions, budget vs actual by account, project and month, and a warning (never a block) on an expense that goes past the active budget. |
| 4 | Invoice & Quotation | **Done** | Quotations, customers, invoices, proforma, credit and debit notes (GST), payments applied to invoices, receivables ageing, project tagging. Recurring invoice profiles (weekly to yearly or every N days or months, draft or issued, catch-up, a daily `generate_recurring_invoices` command) and payment reminders (configurable rules and wording, a Reminders page, history on each invoice). Reminders are recorded in the system only: there is no email delivery. |
| 5 | Project Management | **Done** | Projects, milestones, tasks (Kanban), timesheets, budgets, budget revisions through approvals, project profit and loss from tagged journal lines, portfolio profitability. |
| 6 | Employee Management | **Done** | Employees, departments, attendance, leave with approvals, payroll with PF/ESI/professional tax, payslips, performance, project assignments, self-service pages, salary revisions through approvals. |
| 7 | Purchase & Vendor | **Done** | Vendors, purchase requests, vendor quotations, purchase orders with approvals, vendor bills with approvals and ITC, vendor payments, payables ageing. |
| 8 | GST & Tax | **Done** | GST summary, sales and purchase registers, return filing records, GST challans, TDS, advance tax, income-tax records, tax calendar. Filing itself happens on the government portals; this records and prepares figures. |
| 9 | Legal & Compliance | **Done** | Compliance master and calendar (LLP Forms 8, 11 and others), reminders, filings with acknowledgements, legal events, contracts, notices and cases, policies, expiry watch. |
| 10 | Meeting & Administration | **Done** | Meetings with agenda, decisions and action items, tasks (Kanban), announcements, circulars with acknowledgements, assets, visitors, minutes of meeting. |
| 11 | Approval Management | **Done** | Rules, multi-step chains, inbox, delegation, escalation, history. Wired into purchase orders, vendor bills, leave, payroll runs, project budgets, salary revisions, partner withdrawals, expenses, quotations and payments (vendor payments wait before posting; voiding a payment made or received above a rule waits; customer receipts themselves are never held). |
| 12 | Document Management | **Done** | Private repository with versioning and checksums, issued-document archive, attachments on employees, vendors, partners, projects, bills, filings and CRM records. |
| 13 | Reports & Analytics | **Done** | Executive dashboard and eight reports (annual revenue, project profitability, partner capital, salary, pending customer payments, vendor outstanding, GST summary, cash flow) with period filters and CSV export. |
| 14 | User & Access | **Done** | Seven roles, user management, a configurable permission matrix, login history, activity log. |
| + | CRM | **Done** | Leads (scoring, duplicate check, CSV import, conversion), contacts, accounts, deals pipeline board with drag and drop, activities, products, reports, settings. A won deal creates the sales customer and can start a quotation. |

## How the system is built

Each module is a Django app under `backend/apps/` and a folder under `frontend/src/modules/` that plugs itself into the sidebar
(`frontend/src/modules.js`). The interface uses familiar ERP and CRM layout patterns (a module launcher, dark left menu, top bar with
search and quick create, list pages with status tabs, detail pages with action buttons, dashboards with tiles and charts) in the
Graphite & Gold palette. Colours are CSS variables in `frontend/src/styles.css`. See `docs/BUILD_GUIDE.md`.

Rules for every module: reference IDs are generated automatically (year and date in each), nothing company-specific is
hard-coded, money uses decimals with rounding rules written in code, every change is audited, and each module ships with tests.

## Accounting and tax correctness

The books, GST, TDS and payroll figures need a chartered accountant's review of the chart of accounts, posting rules,
GST treatment, statutory rates and return formats before they are relied on for filing.

## Open items

- Fixed assets: no automatic GST on the sale of an asset (the proceeds are posted net; record GST with a journal entry), no asset revaluation or impairment, and no import of assets bought before the register was started (enter them with their put-to-use date; the first run charges the months back to it). Budgets: cost centres are projects only, with no separate cost-centre master.
- Bank feeds, payment-gateway integration, email delivery of reminders (the system is in-app only today).
- Inventory and warehouses, only if physical goods are sold.
