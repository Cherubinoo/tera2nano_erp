# Building a module for the Tera2Nano ERP

How the system is put together and the rules every module follows. Read `backend/apps/sales` and `backend/apps/accounting`
(and `frontend/src/pages/sales`, `frontend/src/pages/accounting`) first: they are the reference for style and quality.

## Layout

| What | Where |
|---|---|
| Your backend app | `backend/apps/<module>/` (models, services, views, urls, tests). The app, its URL include (`/api/v1/<module>/`) and `INSTALLED_APPS` entry already exist as stubs. |
| Your frontend | `frontend/src/modules/<module>/` (`index.jsx` is the module definition, see `frontend/src/modules.js`). |
| Permissions | Codes already exist in `backend/apps/accounts/access.py` (e.g. `purchases.view`, `purchases.manage`) with role defaults. Check them with `request.user.has_code("...")`. |
| Document types and numbering | Already added in `backend/apps/company/constants.py` (`DocType`) and `services/numbering.py` (`DEFAULT_SEQUENCES`). |
| Audit | Register your record types in your `AppConfig.ready()` with `audit.register_entity(name, EntityAudit(...))`; write events with `audit.log(...)`. |
| Attachments | `apps.attachments.registry.register("employee", view="hr.view", manage="hr.manage")` in `ready()`, then use `<Attachments entityType="employee" entityId={id} canManage />` in the UI. |

**Only edit files inside your own two folders.** Everything else is shared and other people are editing in parallel. If you
need a shared change, do not make it: describe it at the end of your report under "SHARED CHANGES NEEDED".

## Adding a module

1. Backend: `backend/apps/<module>/` (models, services, views, urls, tests), installed and routed as described under Layout.
2. Frontend: `frontend/src/modules/<module>/index.jsx` default-exports the module definition (`frontend/src/modules.js`); the sidebar,
   routes and launcher pick it up by themselves.
3. Launcher: add one entry to `frontend/src/moduleMeta.js` keyed by the module's `key` (display name, icon, one-line purpose, order).
4. **Guide: add a guide entry.** Add an object with the module's `key` to `frontend/src/modules/guide/guideModulesA.js` or
   `guideModulesB.js` (copy an existing one: `usedFor`, `who`, `codes`, `screens` with deep links, `steps` with examples, `statuses`,
   `automatic`, `reports`, `limits`, `ledger`, `approvals`, `attachments`). Add the flows that connect it to other modules to `FLOWS` in
   `guideData.js`, and its steps to `FLOW_STEPS` if it belongs in the end-to-end process. Write only what the code does, and say plainly
   what is not connected. Then, in `frontend/`:
   `node scripts/check-guide-links.mjs` (every link must match a real route, every module must have a guide entry) and
   `node scripts/build-guide-docs.mjs` (rewrites `docs/MODULE_GUIDE.md`; commit it).
5. If you add permission codes to `access.py`, run `node scripts/build-roles-data.mjs` so the Guide's roles table follows.

## Backend rules

- One company. Get it with `from apps.company.api import current_company`. Your models point at `CompanyProfile` like the others.
- Models extend `apps.core.models.UUIDModel`. Money is `DecimalField(max_digits=16, decimal_places=2)`; never use floats.
  Round with `Decimal.quantize(Decimal("0.01"), ROUND_HALF_UP)`. The JSON renderer already writes Decimals as strings.
- Views are DRF `APIView`s returning plain dicts (see `apps/sales/views.py`). Permission check first (`need(request, "code")`),
  then validate, then work inside `transaction.atomic`. Validation errors: `rest_framework.exceptions.ValidationError({"field": "message"})`
  or Django's `ValidationError` from services (both become 400 with field messages). Unknown ids return 404, never 500.
- Lock rows you change money or state on (`select_for_update(of=("self",))`; PostgreSQL refuses `FOR UPDATE` across a nullable join).
- Numbers: `numbering.allocate_number(company, DocType.X, actor=user, on_date=date).reference_number` gives
  `T2N/PO/26/1211/00001`; formats include the 2-digit year and DDMM. Never invent your own counters.
- Printable documents (PO, payslip, circular): `apps.company.services.documents.issue_document(DocType.X, actor=..., content={...},
  issue_date=..., source_module="<module>", source_id=str(id))`. `content` takes `recipient` (lines), `subject`, `paragraphs`, `meta`
  `[[label, value]]`, `table {columns, rows, numeric_columns, column_widths_mm}`, `totals`, `details`, `closing_sections`, `terms`,
  `show_signature`; pasted Markdown in text is rendered. Look at `services/quotations.py` for a worked example. It returns an
  `IssuedDocument` (PDF and plain print PDF); download with the existing `/api/v1/company/issued-documents/<id>/download/`.
- **Post to the books** when money moves: `apps.accounting.services.ledger.post(company, date=, lines=[{"account": "payable" or an Account,
  "debit"/"credit": Decimal, "memo": ..., "party": {"type": "vendor", "id": id}, "project": project_uuid}], source=JournalEntry.Source.X,
  source_id=str(your_id), reference=..., narration=..., actor=user)`. System accounts by key: `cash bank receivable payable sales
  sales_returns gst_cgst_in gst_sgst_in gst_igst_in gst_cgst_out gst_sgst_out gst_igst_out tds_receivable tds_payable
  customer_advances salaries salaries_payable capital partner_current retained depreciation other_income round_off`
  (`chart.system_account(company, key)`); ordinary accounts are looked up by code or created in the chart. Debits must equal credits.
  Post inside the same transaction as your own record. One posting per `(source, source_id)`; to undo, `ledger.reverse_source(company,
  source, source_id, actor, reason)`. Tag lines with `"project": <uuid>` when the money belongs to a project.
  `JournalEntry.Source` already has `vendor_bill vendor_payment payroll partner depreciation`.
- Registers and reports read from `JournalLine` / your own tables with `Sum`; never loop over thousands of rows when a query will do.
- Audit every create / change / void / delete (`audit.log`), with a reason where the person must give one.
- Tests live in `backend/apps/<module>/tests/` and extend `apps.company.tests.base.CompanyTestMixin` (see `apps/sales/tests/test_sales.py`:
  `self.full_company()`, `self.api` (admin), `self.api_finance`, `self.api_staff` (employee), `self.anon`, `client_for(make_user(name, Role.X))`).
  Write tests with hand-computed numbers, the permission matrix (every role), 404s for unknown ids, validation failures, void/undo paths,
  audit events, the journal entries your actions post (and that the trial balance still balances), and every business rule you write.
  Avoid tests that depend on today's date (use fixed dates, or dates relative to `datetime.date.today()` when the rule is about "overdue").

## Frontend rules

- `frontend/src/modules/<module>/index.jsx` default-exports the module definition (read `frontend/src/modules.js`). Put pages in the same folder.
- Use the shared pieces: `ui.jsx` (`PageHeader`, `Field`, `Dialog`, `Badge`, `Tabs`, `Loading`, `ErrorBox`, `FormErrors`, `Notice`, `fmtDate`,
  `useToast`, `useConfirm`, `useReasonDialog`), `hooks.js` (`useLoad`, `asList`), `api.js`, `format.js` (`money`, `plain`, `num`, `compact`,
  `today`, `addDays`, `downloadCsv`), `components/charts.jsx` (`ColumnChart`, `HBars`, `StackedBar`, `Donut`), `components/Attachments.jsx`,
  `pages/Letters.jsx` (`Downloads` for an issued document's PDFs), and `context.jsx` (`useAuth`: `user.permissions`, `useLookups`).
- Look and feel follow the Sales and Accounting pages: list pages with a search toolbar, status chips or tabs and a totals footer; a
  detail page with the document header and actions; dialogs or a form page for creating; a dashboard with tiles and charts; every
  form validates in the browser and shows the server's field errors; money right-aligned, formatted with `money`/`plain`.
- Labels and ids: every input has a `<label>` (use `Field`), tables have `data-label` attributes on cells (they become cards on phones), charts have text equivalents.
- Respect permissions in the UI (hide actions the user cannot perform) but never rely on it: the server decides.
- No horizontal scroll at 390 px wide. No console errors. Dates are `YYYY-MM-DD` strings; use `today()` from `format.js` (local date), not `toISOString`.
- Build check: `cd frontend && npm run build -- --outDir <scratch>/dist-<module> --emptyOutDir` (do not build into the shared `dist`).

## Running things (Windows, Git Bash or PowerShell)

A throwaway PostgreSQL is running on port 5433 (user `t2n_dev`, no password). **Never touch the developer's real database or the servers on ports 8000 and 5173.**
Use your own database name so parallel runs do not clash:

```powershell
cd C:\projects\t2n_erp\backend
$env:DB_HOST='localhost'; $env:DB_PORT='5433'; $env:DB_USER='t2n_dev'; $env:DB_PASSWORD=''; $env:DB_NAME='t2n_<module>'
$env:PYTHONPATH='<scratchpad>\pylib'     # PyMuPDF, used by tests that read PDFs
.\.venv\Scripts\python.exe manage.py makemigrations <module>        # then commit the generated file
.\.venv\Scripts\python.exe manage.py test apps.<module> --noinput   # creates test_t2n_<module>
```

Run the whole suite (`manage.py test apps`) once at the end: all existing tests must still pass.
Browser tests use Playwright with Edge (`C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`) through the scripts in
`<scratchpad>/e2e` (`acct.mjs` and `sales.mjs` are templates, `freshdb.sh <dbname>` creates a migrated database with an admin
`e2eadmin` / `E2e-Test-Pass-77!`). Use your own ports and database, and a temporary `frontend/vite.e2e.<module>.config.js` that you delete afterwards.
