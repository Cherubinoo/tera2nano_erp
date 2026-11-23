# How the ERP modules were built with agents

This document records the multi-agent process used to build the Tera2Nano ERP modules, and the exact prompts given to every agent, so the work can be reviewed, repeated or continued.

## Process

1. **Foundation (lead agent).** Before any module work: role and permission matrix (`apps/accounts/access.py`), document types and numbering for every module, audit helpers (`register_entity`, `log`), project tagging on journal lines, a shared `attachments` app, empty stub apps for each module (installed and routed), a frontend module registry (`src/modules.js`: each `src/modules/<name>/index.jsx` plugs itself into the router and sidebar), and `docs/BUILD_GUIDE.md`. The full backend suite (443 tests) was green before any builder started.
2. **Builders (one agent per module).** Each builder received the common brief plus a module-specific prompt (both below). Rules: edit only its own backend app and frontend folder; soft references between modules (no cross-module foreign keys); post money only through the accounting ledger service; own database name and ports; write hand-computed tests; run its own Playwright e2e in Edge; view screenshots; finish with a report that lists "SHARED CHANGES NEEDED".
3. **Sequencing.** All nine ran in parallel at first. That exhausted the 5-hour usage limit twice, and some agents were stopped mid-way, so the modules are now finished **one agent at a time**. Agents that were stopped cannot be resumed; a fresh agent continues from the files already saved. Order: Projects (owns the project lookup), Approvals, Purchases, HR, Tax, Partners, Compliance, Workplace, CRM.
4. **Integration (lead agent).** After the builders: wire Approvals into purchase orders, leave, payroll and partner withdrawals; reports hub; update old e2e scripts; full backend suite and browser suites; README/ROADMAP; secrets scan; commit and push.

## Status

| Module | State |
|---|---|
| Company, User & Access, Sales, Accounting | Done |
| Projects, Approvals, Purchases, HR, Tax, Partners, Compliance, Workplace, CRM | Done (backend tests and browser checks; see docs/ROADMAP.md) |
| Approvals integration | Done for purchase orders, vendor bills, leave, payroll, project budgets, salary revisions, partner withdrawals |
| Reports hub, project tagging in Sales | Done |
| Interface | Dark-sidebar shell in the Graphite & Gold palette, CRM wired into the sidebar |
| Approvals for expenses, quotations and payments | Done (round 2) |
| Recurring invoices and payment reminders, fixed assets and budgets | Done (round 2) |

---

## Common brief given to every builder

# Common brief for every module builder (Tera2Nano ERP)

Product: a Basic ERP for Tera2Nano Solutions LLP (India: GST, TDS, LLP Act, INR), Django 5.2 + DRF + PostgreSQL backend, React 18 + Vite frontend.
The owner wants a polished, familiar ERP/CRM feel: left module sidebar, list pages with
search/filter chips/status tabs, detail pages with header + action buttons + tabs, dashboards with KPI tiles and charts, "+ New" quick
create. Follow common layout patterns only; never copy another product's text, logos or assets, and never name other products.

READ FIRST, in this order:
1. `C:\projects\t2n_erp\docs\BUILD_GUIDE.md` (rules; follow it exactly).
2. `backend/apps/sales/` (models, services, views, urls, tests) and `backend/apps/accounting/` (ledger, posting) for backend style.
3. `frontend/src/pages/sales/` and `frontend/src/pages/accounting/`, `frontend/src/components/charts.jsx`, `frontend/src/ui.jsx`,
   `frontend/src/modules.js`, `frontend/src/components/Attachments.jsx` for frontend style. Match this quality bar.
4. `backend/apps/accounts/access.py` (permission codes for your module already exist), `backend/apps/company/constants.py` (DocType) and
   `services/numbering.py` (sequences for your types exist).

You are one of nine builders working IN PARALLEL on the same working tree, each on a different module. HARD RULES:
- Edit only `backend/apps/<your module>/` and `frontend/src/modules/<your module>/`. Do NOT edit any other file (settings, urls, App.jsx,
  Layout.jsx, styles.css, ui.jsx, access.py, constants, numbering, other apps). The stub app, its `config/urls.py` include
  (`/api/v1/<module>/`) and INSTALLED_APPS entry exist. If you truly need a shared change (a missing permission code, a CSS class,
  a DocType), do the best you can inside your folders and list it under "SHARED CHANGES NEEDED" in your final report.
  Module-specific CSS: put it in a `<module>.css` inside your frontend folder and `import './<module>.css'` from your `index.jsx`; prefix
  every class with your module name (e.g. `.hr-card`) so nothing clashes; reuse the existing classes from `styles.css` where possible,
  and use its CSS variables so dark mode / the theme keep working.
- Do not `git commit`, `git add`, `git stash`, `git checkout` or otherwise touch git state. Never touch the developer's database or the servers on ports 8000/5173.
  Never delete files you did not create. Do not run `manage.py test apps` (whole suite); run only `apps.<module>` tests. Other builders' apps may be
  mid-edit and temporarily broken; if the shared project fails to import because of someone else's app, wait a minute and retry.
- Cross-module links are SOFT references (a `UUIDField` / ids stored on your records), never a ForeignKey into another module's models, so
  migrations stay independent. Existing FKs you may use: `CompanyProfile`, `CompanyPartner` (apps.company), `Customer` (apps.sales),
  `Account`/journal (via `apps.accounting.services`), the user model.

Cross-module CONTRACT (so parallel modules fit together; build the ones for your module, do not depend on the ones for others being finished):
- `GET /api/v1/projects/lookup/` -> `[{"id","code","name","status","customer"}]`, open to every signed-in user (used for the "Project" dropdown in
  purchases/hr/sales/accounting). Records that belong to a project store `project_id` (UUID, nullable) and post journal lines with `"project": id`.
  In your UI, a Project dropdown is loaded from that URL; if the call fails or is empty the dropdown just shows "No project" (do not crash).
- `GET /api/v1/hr/lookup/` -> `[{"id","code","name","department","designation","user_id"}]` (active employees only, no pay data), open to every
  signed-in user (used to pick an assignee/employee in projects, workplace).
- `GET /api/v1/purchases/vendors/lookup/` -> `[{"id","code","name","gstin"}]` (purchases.view or accounting.view).
- Customers: the existing sales API. Partners: `apps.company.models.CompanyPartner`.
- Approvals: `apps/approvals/api.py` will expose `submit(company, *, kind, source_id, title, amount, requested_by, summary="", url="") -> ApprovalRequest`
  and `register_handler(kind, on_approved=fn(request_obj), on_rejected=fn(request_obj))`. Other modules are wired to it afterwards by the integrator,
  so give your approvable records (purchase order, leave request, expense, payroll run, partner withdrawal) a clean status flow with a state
  such as `pending_approval` and service functions `approve(...)`/`reject(...)` that the approvals module can call, and keep it working
  standalone (a user with the manage permission can approve directly) when no approval rules exist.
- Money posting: only through `apps.accounting.services.ledger.post/reverse_source`, inside the same atomic transaction as your record.
  Never write JournalEntry rows yourself. Idempotent per (source, source_id).

Quality bar (each module is judged as a finished product, not a demo):
- Every action works end to end: create / edit / delete or void / status changes / search / filters / pagination or sensible limits /
  CSV export where a list is financial / PDF where a printable document is specified / attachments where documents are specified.
- Auto-generated reference ids for every business record via `numbering.allocate_number` (contain 2-digit year + DDMM). The user never types a number.
  Optional fields stay optional. Do not hard-code company details (name, GSTIN, address): read the company profile.
  Never expose full bank/PAN/Aadhaar numbers in lists (mask all but the last 4); full value only on the detail page for users who can manage.
- Validation on the server for every field with clear messages; the UI also validates and shows server field errors.
- Permissions: server enforces on every endpoint, UI hides what the role cannot do. Add tests for every role.
- Audit every create/update/void/delete with `audit.log` (register entities in `AppConfig.ready()`).
- Visualisations: a module Dashboard page with KPI tiles and at least 3 meaningful charts using `components/charts.jsx`, plus charts inside
  relevant list/detail pages (ageing, trend, split, funnel/pipeline). Charts need text equivalents (tables or aria-labels).
- Mobile: no horizontal scroll at 390 px; tables use `data-label` cells.
- Backend tests with hand-computed numbers; keep each test method independent; a whole-module test run should take well under 3 minutes.
- Browser end-to-end: write a Playwright script (model it on `<scratchpad>/e2e/sales.mjs`) that drives your module's main flows in Edge
  (create records, post, download a PDF if any, charts render, no console errors, mobile width has no horizontal scroll). Run it against your own
  database and ports using a temporary `frontend/vite.e2e.<module>.config.js` (delete it afterwards). Fix every defect it finds in the app.
  Take a screenshot of the dashboard and one detail page and LOOK at them (Read the png) and fix ugly layout.

Your environment details (scratchpad = `C:\Users\Delight\AppData\Local\Temp\claude\940cd5affa333b1a\e592b0a4-8472-4eff-8ce9-934d95e9a0f2\scratchpad`):
- PostgreSQL on port 5433, user `t2n_dev`, no password. Use database `t2n_<module>` (tests create `test_t2n_<module>`), `DB_PORT=5433 DB_USER=t2n_dev DB_PASSWORD=`.
- `PYTHONPATH=<scratchpad>\pylib` (PyMuPDF). `bash <scratchpad>/e2e/freshdb.sh t2n_e2e_<module>` makes a migrated DB with admin `e2eadmin` / `E2e-Test-Pass-77!`
  (the script drops and recreates that DB name each time).
- Backend dev server for e2e: your port (given below). Frontend Vite dev server: your port (given below) with a proxy to your backend port (copy
  `<scratchpad>/e2e/` vite config pattern from the sales/acct scripts or the existing `frontend/vite.config.js`).
- Playwright-core is installed in `<scratchpad>/e2e` (node_modules there). Edge: `C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`.
- Shell: Git Bash and PowerShell. Long heredocs get mangled in Bash: create files with the Write tool, then run them.
- Build check: `cd frontend && npm run build -- --outDir <scratchpad>/dist-<module> --emptyOutDir`.
- Migrations: `manage.py makemigrations <module>` (generate once at the end of your model work; regenerate/delete-and-recreate your own initial
  migration freely while iterating since nothing is deployed, but ship one clean `0001_initial` (plus a later one only if truly needed)).

FINAL REPORT (keep it under 400 words): what you built (endpoints, screens), test counts, e2e results, anything not done and why, the permission
codes / attachment entity types / audit entities / DocTypes you used, how other modules should hook in, and "SHARED CHANGES NEEDED".

---

## Per-module prompts

Each prompt below was sent as the task for one builder agent. Every one begins with: *"First read `brief_common.md` (the common brief above) and follow it exactly."* Database names, ports and permissions are per module.

### 1. Purchases & Vendors (`apps.purchases`, DB `t2n_purchases`, backend 8011, Vite 5191, perms `purchases.view/manage`, order 40)

Scope (full purchases cycle):
1. **Vendors**: code (auto, VEN), name, contact person, email/phone, GSTIN (validate format and state code), PAN, address, state (place of supply), payment terms days, default TDS section/rate, optional opening balance (posted only if supplied), bank details (masked in lists), status, notes. `GET vendors/lookup/` per the contract. Detail page: summary tiles (billed, paid, outstanding, overdue); tabs Overview, Bills, Payments, Orders, Quotations, Documents (Attachments entity `vendor`), Statement (running balance).
2. **Purchase requests** (title, items, requested by, needed-by, status draft/submitted/approved/rejected/converted) convertible to a PO. If no DocType exists, list by date and report "SHARED CHANGES NEEDED: DocType PURCHASE_REQUEST".
3. **Vendor quotations received** with compare view per request; convert accepted quotation to PO.
4. **Purchase orders**: draft → issued (PDF on letterhead via `issue_document`) → partially received → received → closed/cancelled; lines (description, HSN/SAC, qty, rate, discount, GST rate); CGST/SGST vs IGST by place of supply; amount in words; terms; delivery date; project soft ref; approval-ready status flow (`pending_approval`, `approve()/reject()`); receive goods; convert to bill.
5. **Vendor bills**: from PO or standalone; vendor bill no. + our reference; dates; GST lines; ITC eligibility (eligible/ineligible/blocked); optional TDS; project tag; scanned bill attachment (`vendor_bill`). Posting: Dr expense + Dr input GST (only if ITC eligible, else added to expense) / Cr payable / Cr tds_payable; reverse on void.
6. **Vendor payments**: amount, mode, date, reference, allocate to bills (partial, multiple), advance (unapplied) handling; Dr payable Cr bank/cash; void with reason. Bill status draft/open/partially paid/paid/overdue/void.
7. **Debit notes / vendor credits** if time permits.
8. **Reports**: payables ageing (current, 1-30, 31-60, 61-90, 90+), vendor outstanding, purchases by vendor/month/category, top vendors, 30-day due calendar. Dashboard: KPI tiles, ColumnChart monthly purchases, HBars top vendors, StackedBar ageing, Donut categories; CSV exports.
Nav: Purchases Dashboard, Vendors, Purchase Requests, Quotations, Purchase Orders, Bills, Payments, Payables Ageing. Quick create: Purchase order, Vendor bill, Vendor payment, Vendor.
Tests: hand-computed GST (intra vs inter state), posting and trial balance, payment allocation (partial/multiple/advance), void/reverse, TDS, ageing buckets with fixed dates, numbering format, every role, 404s, audit, PO PDF.

### 2. GST & Tax (`apps.tax`, DB `t2n_tax`, backend 8012, Vite 5192, perms `tax.view/manage`, order 50)

Data comes from the books (system accounts `gst_*_out`, `gst_*_in`, `tds_payable`, `tds_receivable`) and sales documents. The purchase register is read from the journal (not from purchases models); registers tolerate absent tables (empty section, never a 500). DocTypes: TAX_FILING, TAX_PAYMENT.
1. **GST summary dashboard** by month/FY: output tax, ITC, net payable, per head; trend ColumnChart, ITC vs output StackedBar, Donut by rate; KPI tiles.
2. **Sales register (GSTR-1 style)**: B2B, B2C, credit/debit notes, HSN summary, CSV in GSTR-1-friendly layout. **Purchase register (GSTR-2B/ITC style)**. Books-vs-filed reconciliation.
3. **Return filing records**: GSTR-1, GSTR-3B, GSTR-9; configurable due-date rules (1: 11th, 3B: 20th); status pending/prepared/filed/late; ARN; filed date; figures snapshot computed at preparation; late fee/interest; acknowledgement attachment (`tax_filing`); TAX_FILING numbering; prepare 3B from register.
4. **GST payments (challan)**: head-wise cash payment with CIN, posting to books, ITC set-off entry, void with reason; liability ledger per head.
5. **TDS**: payable ledger by section, challan 281 records, quarterly returns 24Q/26Q (31 Jul/31 Oct/31 Jan/31 May), TDS receivable tracker (26AS style).
6. **Advance tax & income tax**: schedule 15 Jun 15% / 15 Sep 45% / 15 Dec 75% / 15 Mar 100%; ITR-5 and tax-audit records; computation worksheet from the P&L service.
7. **Tax calendar**: `GET calendar/` upcoming due dates for 90 days (consumed later by Compliance).
Nav: Tax Dashboard, GST Summary, Sales Register, Purchase Register, Returns, GST Payments, TDS, Advance Tax & Income Tax, Tax Calendar.
Tests: hand-computed registers, liability ledger, set-off, payment postings with balanced trial balance, due-date rules, advance-tax math, permission matrix, 404s, audit, period validation.

### 3. Employee Management & Payroll (`apps.hr`, DB `t2n_hr`, backend 8013, Vite 5193, perms `hr.view/manage/payroll`, order 60, module `perm: null`)

Employee.user is an optional OneToOne to the user model; a signed-in employee sees only their own profile, leave and payslips. DocTypes: PAYSLIP (printable), PAYROLL_RUN, LEAVE_REQUEST, employee code `T2N-EMP-26-1211-0001`.
1. **Employees**: auto code, personal and job details, department, designation, reporting manager, status (active, on notice, exited), PAN/Aadhaar and bank (masked in lists, full only on detail for `hr.manage`), UAN/ESI. Departments and designations CRUD. Detail tabs: Profile, Job & Salary, Documents (`employee`), Leave, Attendance, Performance, Payslips, Projects. `GET lookup/` per contract (no pay data). Salary visible only with `hr.payroll` or to the employee.
2. **Attendance**: daily marking, bulk mark, monthly grid, optional self check-in/out, holiday calendar.
3. **Leave**: configurable types, balances, requests (LEAVE_REQUEST) with `pending_approval` flow and `approve()/reject()/cancel()`, overlap and balance validation, unpaid leave to payroll, team calendar.
4. **Payroll**: salary structures with effective dates; PF 12% on basic capped at 15,000 wage ceiling; ESI if gross ≤ 21,000 (0.75%/3.25%); professional tax slabs; manual TDS; LOP; payroll run per month → payslips → lock → approve → post (Dr salaries + employer PF/ESI, Cr salaries_payable and statutory liabilities) → mark paid (Dr salaries_payable Cr bank/cash) → reverse via `reverse_source`. Payslip PDF (no full bank number). Bank-transfer CSV (full details, `hr.payroll`, audited). Reports: monthly cost, department cost, statutory summary, salary register, YTD.
5. **Performance**: review cycles, goals/KRAs, ratings (1-5), appraisal chart.
6. **Project assignments** (soft `project_id`, role, allocation %, dates); `GET assignments/?project=<id>`; utilisation chart.
7. **Self-service**: My Profile (limited edits), My Attendance, My Leave, My Payslips.
8. **Dashboard**: headcount, joiners/exits, on leave, payroll cost, department donut, birthdays/anniversaries.
Tests: salary/PF/ESI/PT/LOP math, payroll posting and trial balance, void/reverse, leave rules, self-service isolation, permission matrix, masking, audit, 404s, payslip PDF with no full bank number.

### 4. Project Management (`apps.projects`, DB `t2n_projects`, backend 8014, Vite 5194, perms `projects.view/manage`, order 30)

Owns `GET /api/v1/projects/lookup/` (open to every signed-in user). Projects (auto code, client = sales Customer optional, type, manager, dates, status, priority, contract value, budget + lines by category, billing method, agreements `project`); detail tabs Overview, Milestones & Tasks, Team, Budget vs Actual, Financials (P&L from journal lines tagged `project_id`), Invoices, Bills & Expenses, Documents, Activity. Milestones; tasks with Kanban; timesheets (billable hours × rate, weekly totals); documented progress and health rules; portfolio profitability, status donut, Gantt-style bars; invoice-from-milestone; dashboard KPIs and charts.
Nav: Projects Dashboard, All Projects, Tasks, Timesheets, Milestones, Project Profitability.
Tests: lookup shape and permission, CRUD, health/progress with fixed dates, financials from tagged journal lines, budget vs actual, timesheet math, roles, audit, 404s, attachments, invoices by project.

### 5. Approval Management (`apps.approvals`, DB `t2n_approvals`, backend 8015, Vite 5195, perms `approvals.view/manage`, order 70, module `perm: null`)

Owns `apps/approvals/api.py` (built first, exact signatures):
```
submit(company, *, kind, source_id, title, amount=None, requested_by, summary="", url="", project_id=None) -> ApprovalRequest
register_handler(kind, *, on_approved, on_rejected, label="", required_codes=(), describe=None)
status_of(kind, source_id) -> "none"|"pending"|"approved"|"rejected"|"cancelled"
cancel_for_source(kind, source_id, actor)
```
Kinds: purchase_order, vendor_bill, leave_request, expense, payroll_run, budget, salary_revision, payment, partner_withdrawal, quotation. Handlers run in the decision's transaction; a failure rolls the decision back.
Scope: configurable rules per kind (amount band, ordered multi-step chain, role/user approver, any-one/all-of, escalation days, priority, self-approval blocked except admin when no other approver; lazily seeded defaults; auto-approve when no rule matches and requester holds the manage code); requests with APPROVAL numbers, per-step decisions, comment required on reject, resubmit, withdraw, delegate, immutable history; inbox (pending for me with bulk approve, filters, my requests, all approvals + CSV); `GET summary/` (`pending_for_me`, `my_open_requests`); dashboard KPIs and charts; `ApprovalBadge` and `ApprovalButton`. In-app only.
Tests: rule matching, chains, any/all, self-approval, reject needs comment, handler once + rollback, withdraw, delegate, escalation, per-role inbox, summary counts, audit, 404s, double-decision guard.

### 6. Legal & Compliance (`apps.compliance`, DB `t2n_compliance`, backend 8016, Vite 5196, perms `compliance.view/manage`, order 80)

Reuses the company module read-only (profile, partners, signatories, licences with expiry, audit).
1. **Compliance master**: editable catalogue (LLP Form 11 due 30 May, Form 8 due 30 Oct, Forms 3/4/5/15/17, DIR-3 KYC 30 Sep, ITR-5, TDS returns, GSTR-1/3B/9, advance tax, PF/ESI, PT, MSME, trade licence, Shops & Establishment) seeded lazily per company, with category, enabled flag, assignee role.
2. **Calendar** per FY: idempotent generation that never overwrites filed items; statuses upcoming/due soon/overdue/filed/not applicable; assignee, priority, reminders (15/7/1); filing details (date, SRN/ARN, fee, remarks); acknowledgement attachment (`compliance_filing`, COMPLIANCE_FILING numbering); month grid and agenda; mark filed; not-applicable with reason; bulk actions; CSV; data-driven penalty estimator (LLP ₹100/day/form; ITR 234F).
3. **Legal events** timeline (`legal_event`).
4. **Legal repository**: contracts (`contract`) with renewal alerts, notices & cases with hearing dates, policies with versions; combined Expiry Watch with company licences.
5. **Reminders**: `GET reminders/`, `GET summary/`; optional read-only merge of `/api/v1/tax/calendar/` (try/except).
6. **Dashboard**: compliance score, overdue, due in 30 days, Donut by category/status, filings StackedBar on-time vs late, HBars workload.
Nav: Compliance Dashboard, Calendar, Filings, Obligations, Legal Events, Contracts, Notices & Cases, Policies, Expiry Watch.
Tests: due-date generation for a fixed FY, idempotent regenerate, status with injected clock, penalty math, contract renewal, roles, audit, 404s, summary shapes.

### 7. Partner Management (`apps.partners`, DB `t2n_partners`, backend 8017, Vite 5197, perms `partners.view/manage`, order 35)

Partners are `apps.company.models.CompanyPartner`; extend with related tables only. DocType PARTNER_TRANSACTION.
1. **Dashboard**: total capital, ratio donut, capital by partner, drawings by month, loans, profit share YTD.
2. **Transactions** (capital introduced/withdrawn, drawing, interest on capital, remuneration, loan from/repayment, profit share credit/transfer) posting to the books with `JournalEntry.Source.PARTNER` and party = partner; void/reverse; `pending_approval` + `approve()/reject()`; self-approval of own withdrawal blocked for non-admins.
3. **Profit sharing**: ratios with effective dates (must total 100%); FY allocation from the P&L service, minus remuneration/interest as configured, remainder to the largest share; preview, post, reverse.
4. **Capital account statement** per partner (opening, introduced, withdrawn, profit share, drawings, interest, remuneration, closing), PDF or CSV, history chart, loan ledger.
5. **Partner documents** (`partner`).
Nav: Partner Dashboard, Partners, Transactions, Profit Sharing, Loans, Statements.
Tests: postings, remainder rounding, ratio validation, statement balances, reverse, balanced trial balance, self-approval rule, roles including a partner-user seeing only their own account, audit, 404s.

### 8. Meeting & Administration / Workplace (`apps.workplace`, DB `t2n_workplace`, backend 8018, Vite 5198, perms `workplace.view/manage`, order 90, module `perm: null`)

Reuses the Company module's Minutes of Meeting (no chairperson line; seal space).
1. **Meetings**: schedule, agenda, attendees, status, recurring meetings, calendar and agenda views, link to the existing minutes flow, decisions and action items.
2. **Tasks** (TASK numbering): assignee, priority, status, labels, source soft refs, comments, subtasks; Kanban and list; My Tasks; analytics.
3. **Announcements and circulars**: announcements with audience, pin, dates; CIRCULAR documents (printable on letterhead) with per-recipient acknowledgement; attachments (`circular`).
4. **Admin utilities**: assets register (`asset`), visitor log.
5. **Dashboard**: upcoming meetings, my tasks, overdue, announcements, pending acknowledgements.
Nav: Workplace Home, Meetings, Tasks, My Tasks, Announcements, Circulars, Assets, Visitors.
Tests: recurrence with fixed dates, task flow, my-tasks isolation, circular PDF + acknowledgements, announcement audience per role, minutes link, roles, audit, 404s.

### 9. CRM (`apps.crm`, DB `t2n_crm`, backend 8019, Vite 5199, perms `crm.view/manage`, order 20)

Pipeline-style CRM. DocTypes LEAD, DEAL; customers are `apps.sales.models.Customer`.
1. **Leads**: source, industry, value, status, owner, rating, scoring (documented), duplicate detection, CSV import with preview and row errors, bulk actions; timeline; convert to Contact + Account + Deal (a Customer is created only when the deal is won).
2. **Accounts and Contacts** with soft `customer_id`.
3. **Deals pipeline**: configurable stages with probability; Kanban with drag-and-drop and a "Move to" menu; stage history; weighted forecast; won → create/link Customer and offer "Create quotation".
4. **Activities**: tasks, calls, meetings, notes, email log; today/overdue/upcoming; My activities.
5. **Products/services** catalogue (optional) as deal line items.
6. **Analytics**: KPI tiles, SVG funnel, pipeline by stage, lead sources donut, win/loss trend, top owners, deal ageing, forecast by month; CSV exports.
7. **Settings**: stages, sources, lost reasons.
Nav: CRM Dashboard, Leads, Contacts, Accounts, Deals, Activities, Reports, CRM Settings.
Tests: scoring, duplicates, conversion, stage move and probability, weighted forecast, win-creates-customer, CSV validation, activity due logic, roles (partner has `crm.manage`; employee none), audit, 404s.

### 10. Reports & Analytics hub (planned, built after the modules above)

Cross-module analytics: annual revenue, project profitability, partner capital, salary report, pending customer payments, vendor outstanding, GST summary, tax liability, cash-flow analysis; permission `reports.view`.

### 11. Integration (done by the lead agent after the module builders)

Wire Approvals into purchase orders, leave requests, payroll runs and partner withdrawals; tag project ids across Sales, Purchases and HR; update the old browser scripts now that the "Coming next" sidebar group is gone; run the full backend suite and every browser suite; update the README and ROADMAP; rescan for secrets; commit and push.
