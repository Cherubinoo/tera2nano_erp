# Tera2Nano ERP

A basic ERP for Tera2Nano Solutions LLP (India: GST, TDS, LLP Act, INR): company management, partners, accounting, sales and
quotations, purchases, projects, people and payroll, GST and tax, compliance, workplace, approvals, reports and CRM.
The layout uses familiar ERP and CRM patterns (module launcher, left menu, list pages, detail pages, dashboards) in a Graphite & Gold theme. Module status is in `docs/ROADMAP.md`.

Django REST API + PostgreSQL (`t2n_erp`) in `backend/`, React (Vite) UI in `frontend/`.
Company details live only in PostgreSQL; the UI, PDF templates and business code read them through the Company Management service.

## First-time setup

1. **Database.** Create the `t2n_erp` database (PostgreSQL 18 here) if it does not exist:
   `createdb -U postgres t2n_erp`. The migrations install the `btree_gist` extension, which needs a
   database owner or superuser.
2. **Backend.**
   ```powershell
   cd backend
   python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
   copy .env.example .env      # then fill DB_USER / DB_PASSWORD, DJANGO_SECRET_KEY, FIELD_ENCRYPTION_KEY
   .\.venv\Scripts\python manage.py migrate
   .\.venv\Scripts\python manage.py createsuperuser   # becomes a company administrator
   .\.venv\Scripts\python manage.py runserver
   ```
   `FIELD_ENCRYPTION_KEY` encrypts bank account numbers. **Back it up: without it stored account
   numbers cannot be decrypted.** Generate one with
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
3. **Frontend.** `cd frontend; npm install; npm run dev` (proxies `/api` to `localhost:8000`).
4. Sign in as the administrator; with no company yet, the UI opens the **setup wizard**.

## Using the app

After signing in you land on **Home**: this month's documents and quotation value, a six-month chart, items needing attention
(expiring documents, unverified bank accounts) and your recent documents with downloads. **+ New** (top right) opens the page to
create a quotation, letter, minutes, operations document or user. **Go to a page** (Ctrl+K) finds any page by name. The left menu
groups pages by module (CRM, Sales, Purchases, Accounting, GST & Tax, Projects, People, Partners, Approvals, Compliance, Workplace,
Reports, Documents, settings, Company, Administration) and shows only what your role may open. The bell in the top bar counts
approvals waiting for you; the menu button collapses the sidebar to icons.

## The Guide and the module guide

The **Guide** tile on the launcher (and the **?** icon in the top bar, and *How this module works* at the foot of each module's menu)
opens the in-app guide: the whole process from lead to reports as a clickable diagram with a *Play the process* tour, a map and table
of how the modules connect (what posts to the books, what uses approvals and attachments), one page per module (what it is used for,
who uses it, key screens, a step-by-step workflow with example figures, statuses, what it sends and receives, what the system does by
itself, and honest limits), a roles table and a glossary.

The same text is written out in [docs/MODULE_GUIDE.md](docs/MODULE_GUIDE.md). It is generated from
`frontend/src/modules/guide/guideData.js` (with `guideModulesA.js`, `guideModulesB.js` and `rolesData.js`), so the screens and the
document cannot drift:

```powershell
cd frontend
node scripts/build-roles-data.mjs      # roles table from backend/apps/accounts/access.py
node scripts/build-guide-docs.mjs      # rewrite docs/MODULE_GUIDE.md (add --check to only compare)
node scripts/check-guide-links.mjs     # every link in the guide and the manual matches a real route
node scripts/build-operate-docs.mjs    # rewrite docs/HOW_TO_OPERATE.md (add --check to only compare)
```

**How to operate.** Guide > *How to operate* (`/guide/operate`) is the operating manual: a *Getting started* chapter (first-week
setup order, finding your way around, roles, daily / month-end / year-end checklists) and one chapter per module with click-by-click
tasks, who may do each one, what happens afterwards, statuses, routines and common problems, plus a task search. The same manual is
written out in [docs/HOW_TO_OPERATE.md](docs/HOW_TO_OPERATE.md), generated from `frontend/src/modules/guide/operate/*.json`.

**Access and invitations.** The Admin, Managing Partner and Partner roles always hold every permission. Partners, HR managers and
admins can invite members from Administration > *Invite members*: the inviter picks the access role, the invitee opens the one-time
link (`/join/...`) and fills in their details (the fields are listed in `INVITE_FIELDS` in `backend/apps/accounts/invitations.py`).
*Email this link* emails the link from the app (see **Email** below); the Sent emails log stores the message with the link hidden.

Modules covered: CRM, Sales, Purchases, Accounting, GST & Tax, Projects, People (HR), Partners, Approvals, Compliance, Workplace,
Reports, Documents, Company, Administration.

## Sales

The **Sales** group (menu) holds everything about billing customers. It needs the `sales.view` permission to open and
`sales.manage` / `sales.issue` to change or issue (Finance Manager, Managing Partner and Admin by default; Partners and Project
Managers can look). All of it reuses the quotation builder, so GST, rounding, numbering and the two PDFs (letterhead and plain
for pre-printed paper) behave exactly as on quotations.

- **Sales Dashboard**: total receivables, overdue, invoiced this month, receivables by age (stacked bar), invoice status
  (donut), invoiced versus received over six months (columns) and who owes the most.
- **Customers**: name, legal name, GST treatment and GSTIN (checked, state taken from it), PAN, contact, address, payment terms
  in days. Each gets an automatic code (`T2N-CUS-26-1211-0001`). A customer page shows what they owe, advances, unused credit
  notes, a statement with a running balance, their documents and details. A customer who owes money cannot be switched off.
- **Invoices and Notes**: invoices, proforma invoices, credit notes and debit notes, with status filters (draft, unpaid,
  overdue, partially paid, paid, void), search and totals. A **draft** can be edited or deleted and uses no number. **Issue**
  allocates the number (`T2N/INV/26/1211/00001`, `T2N/PI/…`, `T2N/CN/…`, `T2N/DN/…`), makes both PDFs and freezes the
  document; after that it can only be voided (reason needed; the number is never reused). Due date comes from the customer's
  payment terms, else the company policy. A proforma can be converted into an invoice draft; an invoice can be cloned.
- **Credit and debit notes** are raised from an invoice (same customer, reason required). A credit note is applied to its
  invoice at once; any unused part can be applied to another invoice later. Credit notes against an invoice cannot together
  exceed it. An invoice with payments, credits or live notes cannot be voided until those are voided.
- **Payments received**: record money against a customer, applied to invoices oldest-due-first or by hand. Whatever is not
  applied stays as the customer's advance. Each payment gets a number (`T2N/PAY/26/1211/00001`); a payment can be voided
  (reason needed) and the invoices go back to unpaid.
- **Receivables Ageing**: unpaid amounts by days past due (current, 1-15, 16-30, 31-45, over 45), per customer and in total.
- **Recurring invoices**: a profile (customer, template items with GST, weekly / monthly / quarterly / half-yearly / yearly or
  every N days or months, start date, optional end date or number of invoices, due days, project, notes and terms) creates a
  normal invoice on each run, as a draft or issued (issuing needs `sales.issue`). The schedule is anchored on the start date, so a
  profile starting on the 31st bills on the last day of shorter months and on 29 February in leap years. Each (profile, run
  date) is made once only; missed runs are caught up, at most 12 per profile per call. Pause, resume (runs missed while paused
  are skipped), stop, edit (affects future invoices only) and "Run now" are on the profile page; its **Child invoices** tab
  lists what it made. Due invoices are made on their own once a day: the first time anyone opens the app that day (switch
  off with `DAILY_JOBS_ON_REQUEST=false`). To run them even on days nobody signs in, schedule
  `.\.venv\Scripts\python manage.py run_daily_jobs` (Windows Task Scheduler or cron; it also runs the other daily jobs such as
  automatic reminders). `generate_recurring_invoices` (optional `--date YYYY-MM-DD`, `--limit N`) still runs just this job; a profile that fails (for example its customer was switched off) shows "last error" and
  does not stop the others.
- **Reminders**: rules say when a reminder falls due relative to the invoice's due date (standard: 3 days before, on the day, 7,
  15 and 30 days after) with editable subject and message using placeholders such as `{customer_name}`, `{invoice_no}`,
  `{amount_due}`, `{due_date}`, `{days_overdue}`, `{company_name}`. The Reminders page lists unpaid invoices that need one
  (never paid, void or draft invoices; each rule once per invoice; only the latest due rule when several have passed). **There is
  email server set up** (see **Email**), Send emails the reminder (editable first) and records it; without one it records the
  rendered text and gives a Copy button and a `mailto:` link. Skip records that it was passed over. With *Email payment reminders
  automatically* on, a daily job emails due reminders to customers with an email address, once per reminder step. History is on the Reminders page and on each invoice; the Sales dashboard counts what is due.

Amounts are exact decimals and every action that changes money locks the rows it uses, so two people cannot overpay one
invoice. Sales events are written to the change history without bumping the company record version.

## Accounting

The **Accounting** group holds the books. It is double entry: every entry's debits equal its credits, a posted entry is never
edited (a mistake is corrected by a reversing entry), and every report is a sum of journal lines, so the statements always
agree with each other. Permissions: `accounting.view` to read (Partners included), `accounting.manage` to record
(Finance Manager, Managing Partner, Admin).

- **Sales post themselves.** Issuing an invoice or debit note debits Accounts receivable and credits Sales and the GST payable
  accounts; a credit note does the reverse; a payment debits the bank (or cash) and credits receivable, with any unapplied part
  held as a customer advance. Voiding a sale or payment posts the mirror entry. The posting runs in the same transaction as
  the sale, so the two can never disagree, and each event can post only once.
- **Chart of accounts**: a standard chart for a services LLP (assets, liabilities, equity, income, expenses) is created on
  first use. Accounts the system posts to are marked "System" and cannot be removed; add, rename and switch off the rest. Each
  company bank account gets its own ledger account. **Set up the books** posts any sales made before the books existed.
- **Expenses and income**: record money paid out or received outside invoices, with optional GST (input credit on expenses,
  output tax on income; CGST + SGST or IGST). Each gets a number (`T2N/EXP/…`, `T2N/INC/…`) and can be voided.
- **Journal**: manual entries with an automatic number (`T2N/JV/…`) and a live check that debits equal credits;
  filter by where an entry came from; reverse a manual entry with a reason.
- **Bank and cash**: balances, transfers between accounts, and **reconciliation**: tick the lines on the statement until the
  difference is zero; the most recent reconciliation can be undone.
- **Bank reconciliation from statements**: upload the bank's statement (CSV, Excel .xlsx/.xls, a text-based PDF, or OFX) for a bank account; the
  columns are guessed from the headings (Indian date formats, amounts with commas, Dr/Cr columns, heading and total rows) and
  remembered per account, and re-importing an overlapping statement skips lines already imported. Each line is offered
  matching entries from the books (same amount on a nearby date, then a reference or name in the narration) to accept, can be
  matched by hand (one line to several entries or the reverse), ignored with a reason, or turned into the missing customer
  payment, vendor payment, expense, income, bank charge or transfer through the normal services and approval rules. The
  reconciliation report (CSV and print) explains the difference between the books and the statement, and an account can be
  marked reconciled up to a date. Statement files are stored privately. There is no live bank feed. Code:
  `backend/apps/accounting/services/statement_files.py`, `statements.py`, `bank_matching.py`, `reconcile.py`.
- **Reports**: trial balance, general ledger per account, profit and loss, balance sheet (profit carried between financial
  years without closing entries), and a cash-flow statement (indirect method) that always ends on the real cash and bank
  balance. Every report exports to CSV and prints without the menu.
- **Dashboard**: cash and bank, receivable, payable, profit this year, income versus expenses for six months, and where the
  money went.
- **Fixed assets**: categories carry the depreciation terms (straight line over a useful life, or written-down value at an
  income-tax rate with an optional half-year rule) and the three ledger accounts. Assets are numbered `…-FAS-…`; the purchase can
  be posted (Dr asset, Cr bank, cash or payable) or flagged as already in the books. Depreciation is previewed and run per month
  (one entry per month, once per asset, reversible, never past cost less residual value); a sale, scrap or write-off posts the
  gain or loss. Reports: register as on a date, schedule per asset, depreciation by financial year, disposals. The rules are written
  at the top of `backend/apps/accounting/services/depreciation.py`.
- **Budgets**: a budget per financial year with income and expense lines by account and project (the cost centre), even or
  month-by-month phasing, a saved revision on every change, and **budget vs actual** by account, project and month. An expense that
  would take a line past the active budget shows a warning but is still saved.
- A locked financial year refuses new entries. Opening balances (balances brought forward) are posted from the chart page.

Money in the API is always a string (`"1800.00"`), never a floating-point number.

## Tera2Nano letterhead

The printed letterhead (logo and name top-left, centred address and contact footer, pale page, faint logo
watermark) is the template `Logo and name on top, address and contacts in footer` (`footer_contact`).
Apply the company details and layout from it with:

```powershell
.\.venv\Scripts\python manage.py apply_tera2nano_letterhead --logo path\to\t2n-logo.png
```

It only fills blank fields, never touches PAN/GSTIN/LLPIN/bank/tax data, leaves customised branding alone
(use `--force` to reset it) and does not mark setup complete. The logo must be uploaded from your original
artwork (transparent PNG is best); it is used in the header and as the watermark. Everything stays editable
under Branding and Letterhead Designer.

## Letterhead templates and letter references

Letters are issued on a template from the catalogue (page **Letterhead Templates**): LHT-001 General Company
Letterhead, LHT-002 Client & Business Correspondence, LHT-003 Employee & HR Letters, LHT-004 Bank & Government
Correspondence, LHT-005 Legal & Compliance Letters, LHT-006 Internal Office Orders, LHT-007 Vendor & Partnership
Letters, LHT-008 Authorization Letters. They are created when setup starts; add more or deactivate any.

A letter's reference is **template ID / 2-digit year / date (DDMM) / running number**, for example
`LHT-002/26/2710/00001`. Each template has its own counter, which counts up from 00001 and restarts at 00001 every
calendar year (the 2-digit year is part of the number, so a restart never repeats a reference). A letter dated in an
earlier year continues that year's numbers. The date part can be changed to DDMMYYYY or removed under Document Numbering (the numbering
changes apply only to numbers issued afterwards). Issue on a template with
`POST /api/v1/company/issued-documents/` and `"letterhead_template": "LHT-002"`.

## Printing letters

Page **Letters and Documents**: choose a template, write the letter, and issue it. Each issued letter is saved
in two versions that use identical margins and text positions:

- **Letterhead PDF**: with the full letterhead (logo, name, footer, watermark). For sending or printing on plain paper.
- **Print PDF**: no letterhead artwork, for printing on your pre-printed official letterhead paper. Use "Open to print".

Spacing is under **Branding and Letterhead Designer, Body spacing**: space below the header, line spacing,
paragraph spacing and optional justified text. The preview has a "Plain, for pre-printed letterhead paper" toggle.
If your paper's header or footer is taller or shorter, adjust Header height, Footer height and the margins until the
preview text clears your artwork. Issued letters keep the spacing they were issued with.

## Reference IDs

Every reference ID contains the last two digits of the year and the day-month of its date, then a running number:
`T2N/INV/26/2710/00001`, `T2N-EMP-26-2710-0001`, `T2N-MOM-26-2710-0001`, `LHT-002/26/2710/00001`. Each document type
(and each letterhead template) has its own counter. Invoices, quotations and standard letters restart at 00001 each
financial year; letterhead templates restart each calendar year; employees, projects, meetings and minutes keep
counting. Every bank account also gets its own reference ID when it is added (`T2N-BNK-26-2710-0001`, shown as "Ref ID" on the
Bank Accounts page; it cannot be edited and is never reused). Accounts that existed before get one, oldest first, the first time
the list is opened. Because the date is in the number, a restart never repeats a reference. GST rules cap a tax invoice number at
16 characters: the Document Numbering page warns when an invoice format is longer (for a GST-registered company).

## Operations

Page **Operations**: every operation has its own ID (OPS-001 Work Order, OPS-002 Job Card / Task Assignment, OPS-003 Site
Visit / Inspection Report, OPS-004 Material Requisition, OPS-005 Delivery & Dispatch Note, OPS-006 Service / Maintenance
Report, OPS-007 Standard Operating Procedure, OPS-008 Incident / Non-conformance Report, OPS-009 Project Status Report,
OPS-010 Daily / Weekly Operations Report). These ten are a starting list created on first visit: administrators can add,
rename or switch off operations under "Manage operations". Choose an operation, paste the content, optionally add
details (label and value, printed under the reference) and headed sections, and issue. The reference is
`OPS-001/26/2710/00001` (operation ID / 2-digit year / DDMM / running number); each operation has its own counter that
restarts every calendar year. Like letters, you get a letterhead PDF and a plain Print PDF. Via the API:
`POST /api/v1/company/issued-documents/` with `"document_type": "operation_document"`, `"letterhead_template": "OPS-001"`
and `content` holding `paragraphs`, `sections` (`[{"heading", "body"}]`) and `meta` (`[[label, value]]`).

## Quotations

Page **Quotations**: enter the customer (name, address, optional GSTIN and state), the items (description, HSN/SAC, quantity,
unit, rate, discount %, GST %), an optional introduction, notes and terms, then issue. The ID is the standard quotation
number (`T2N/QUO/26/2710/00001`); you get a letterhead PDF and a plain Print PDF. Prices, discount, GST and totals are worked
out on the server (`services/quotations.py`), so the totals shown on the page are the ones that print; nothing is saved
until you issue.

- GST is charged only when it is switched on under Tax Configuration for a regular taxpayer; otherwise the quotation has no
  tax. A line's rate is chosen on the line or taken from the default tax code, and must be a currently applicable GST rate.
  Same state as the company: CGST + SGST (UTGST in some union territories); another state: IGST. The place of supply is the
  customer state, else the state in the customer GSTIN, else the default place of supply, else the company's state.
- The quotation ID is never typed: the form shows the ID the quotation will get (it follows the quotation date) and the server
  assigns the real one when you issue (`GET /api/v1/company/document-numbering/next/` previews it without using a number).
  "Customer's own reference" is only for their RFQ number.
- **Pasted formatting is kept.** Text pasted from documents or AI tools (headings `#`/`##`/`###`, `**bold**`, `-` and `1.` lists,
  `| tables |`, `---` rules) is printed as real headings, lists and tables, in letters, operations documents, quotation
  sections, notes and terms (`services/richtext.py`). Anything else prints as plain text. The rupee sign prints as "Rs." because
  the built-in PDF fonts have no rupee glyph.
- **Key details** (project, client, completion period ...) print as a label/value table under the totals.
- **No signature block by default.** Quotations do not print "For <company> / Authorised Signatory" (the company seal is stamped
  on the page); tick the option on the form to print it. Minutes never print it. Letters follow the Branding setting.
- For IT solutions the form offers unit and SAC suggestions (confirm the right code with your tax adviser) and optional headed
  sections printed under the totals: scope of work, deliverables, timeline, payment schedule, support and warranty,
  assumptions and exclusions. Sections with no text are left out.
- "Valid until" defaults to the policy's quotation validity days. Terms default to the company's quotation terms; payment terms
  and the verified bank account (if enabled for quotations) print under the totals.
- API: `POST /api/v1/company/issued-documents/` with `"document_type": "quotation"` and `content.quotation`
  (`customer`, `items`, `valid_until`, `subject`, `intro`, `notes`, `terms`, `include_bank`);
  `POST /api/v1/company/quotations/working/` returns the worked-out lines and totals without using a number.

## Meeting minutes

Page **Meeting Minutes**: enter the meeting (title, date and time, venue and mode, minutes taker),
the attendees with attendance, agenda, discussion items with decisions, action items (owner and due date) and the
next meeting. Issuing gives a **minutes ID** (standard numbering, e.g. `T2N-MOM-26-2710-0001`, or a letterhead template ID
such as `LHT-006/26/2710/00001`) and a **meeting ID** (e.g. `T2N-MTG-26-2710-0001`). Fill in an existing meeting ID to record
another set of minutes for the same meeting. As with letters, you get a letterhead PDF and a plain PDF for pre-printed
paper. There are no signature lines: the minutes keep blank space at the bottom right for your company seal. Issuing does
not record any approval.

Numbering for meetings and minutes comes from the default sequences, which `apply_tera2nano_letterhead` creates when
missing (or use Document Numbering, "Create the missing sequences").

## Email

The app emails invoices and credit notes, quotations and letters, purchase orders, payslips, payment reminders and invitation links
(each from a dialog prefilled with to, cc, subject and message, with the PDF attached). Set the mail server in `backend/.env`:
`EMAIL_HOST`, `EMAIL_PORT` (587), `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` (true) or `EMAIL_USE_SSL` (port 465),
`DEFAULT_FROM_EMAIL`, optionally `EMAIL_TIMEOUT` (seconds) and `APP_BASE_URL` (the address used in invitation links). Without
`EMAIL_HOST` (development) messages are written as files to `EMAIL_FILE_PATH` (default `backend/sent_emails/`) and the app says
"Email is not set up on the server; messages are saved but not delivered". The password is read from the environment only. Sender
name, reply-to, signature and automatic reminders are per company under Administration > *Email settings* (with a test email);
Administration > *Sent emails* logs every message with its result, the reason for failures and *Send again* (`email.manage`). A
failed send never undoes the business action. Code: `backend/apps/emails/`.

## Where files are stored

Private files live under `backend/private_media/`, in one folder per document type:
`company/issued/<document_type>/` (generated PDFs, with `logos/` copies used for that document) and
`company/documents/<document_type>/` (uploaded repository documents). Other folders: `company/logo/`,
`company/branding/`, `company/signatures/`, `bank-statements/` (uploaded bank statements). File names are random; originals are kept only in the database.

## Users, roles and permissions

Page **Users and Access** (menu, for users who may manage users or view activity) has four tabs:

- **Users**: add users, change names, email and role, set a password, switch users off and on. Switching a user off signs them
  out at once. You cannot change your own role or switch yourself off, and the last administrator cannot be demoted or switched
  off, so nobody can lock the company out.
- **Role permissions**: a matrix of what each role may do. Changes apply at once, need a reason and are recorded; "Reset to
  defaults" restores the standard set. The Admin role always holds every permission.
- **Login history**: every sign-in, failed attempt (wrong password, account switched off) and sign-out, with time, IP address
  and browser. A name typed at login is stored only when it belongs to a real user, so a password typed into the name box is
  never kept.
- **Changes**: who added or changed users and who changed permissions. These also appear in the Company Change History.

Roles (the seven requested): Admin, Managing Partner, Partner, Finance Manager, HR Manager, Project Manager, Employee. The first
version's roles carry over: `finance` became Finance Manager and `staff` became Employee, with the same powers.

Default permissions (change them on the matrix; each module adds its own codes in `apps/accounts/access.py`):

| Permission | Admin | Managing Partner | Partner | Finance Manager | HR / Project Manager | Employee |
|---|---|---|---|---|---|---|
| View company details | yes | yes | yes | yes | yes | yes |
| Change company details | yes | yes | | | | |
| Bank accounts, invoices, credit notes | yes | yes | | yes | | |
| Issue letters, quotations, minutes | yes | yes | yes | yes | yes | yes |
| View confidential documents | yes | yes | yes | yes | | |
| View restricted documents | yes | yes | | | | |
| View company change history | yes | yes | | | | |
| Manage users and permissions | yes | | | | | |
| View login history and user changes | yes | yes | | | | |

Set a user's role on the Users tab, or in Django admin (`/admin/`).

## How the modules were built (agents and prompts)

The remaining modules (CRM, Purchases, GST & Tax, People & Payroll, Projects, Approvals, Compliance, Partners, Workplace,
Reports) were built with one agent per module under a shared brief, run one at a time. The process, current status and the exact prompt given to
every agent are in [docs/AGENT_PROCESS.md](docs/AGENT_PROCESS.md); module conventions are in
[docs/BUILD_GUIDE.md](docs/BUILD_GUIDE.md).

## Tests

```powershell
cd backend
$env:DB_USER="..."; $env:DB_PASSWORD="..."   # a role allowed to CREATE DATABASE (tests use test_t2n_erp)
.\.venv\Scripts\python manage.py test apps
```
443 tests cover migrations, API behaviour, permissions, persistence, audit immutability,
numbering uniqueness (including 36 concurrent allocations) and PDF issuing.

## Inspecting the database in pgAdmin 4

Register a server in pgAdmin pointing at `t2n_erp`. For a read-only login use
`docs/pgadmin_readonly_role.sql`. Bank account numbers appear as ciphertext
(`account_number_encrypted`); the audit table rejects UPDATE/DELETE by trigger.

## Key design points

- **Single source of truth:** `apps/company/services/company.py` (`resolve_for_document`, `build_snapshot`).
  Other modules should call it, and `services/documents.issue_document` for branded PDFs. Never hardcode company data.
- **Issued documents are frozen:** the exact PDF, a JSON snapshot of every printed company detail, a logo
  copy, and the profile/branding/policy/template versions are stored. Referenced addresses, contacts, banks,
  registrations and policies are `PROTECT`ed against deletion.
- **Numbering:** row-locked sequence + `IssuedReferenceNumber` with a unique constraint; numbers are never
  reused, only voided. Yearly/monthly resets add the period to the number so a restart cannot collide.
- **Policies are versioned:** active versions are immutable; a change is a new version with an effective date.
- **Audit:** append-only (model + Postgres trigger), secrets redacted, request ID recorded, reasons required
  for important changes once setup is complete.
- **Files** live in `backend/private_media/` (never served directly) and download via authenticated endpoints.

## Things to confirm before relying on the output

- GST rates in `GST_CURRENT_RATES` (settings) and the CGST/SGST/UTGST/IGST rules in `services/tax.py` reflect
  the author's understanding of current rules; have your tax adviser confirm them and update when notifications change.
- PDFs use built-in Latin-1 fonts: currency prints as "INR"/"Rs." (no ₹ glyph) and unsupported characters become "?".
- `signature_image` is optional and off by default on documents; it is never treated as proof of approval.
  Approvals must be recorded separately by the module that owns the transaction.
