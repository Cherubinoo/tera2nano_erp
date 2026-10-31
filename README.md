# Tera2Nano ERP — Company Profile and Management module

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

## Where files are stored

Private files live under `backend/private_media/`, in one folder per document type:
`company/issued/<document_type>/` (generated PDFs, with `logos/` copies used for that document) and
`company/documents/<document_type>/` (uploaded repository documents). Other folders: `company/logo/`,
`company/branding/`, `company/signatures/`. File names are random; originals are kept only in the database.

## Roles

| Role | Can do |
|---|---|
| `admin` (and superusers) | everything, including change history, signatures, restricted documents |
| `finance` | bank accounts (create/change/verify/reveal), confidential documents, issue invoices/credit notes |
| `staff` | read non-sensitive company data (bank numbers masked), issue letters/quotations/minutes |

Set a user's role in Django admin (`/admin/`) or `User.role`.

## Tests

```powershell
cd backend
$env:DB_USER="..."; $env:DB_PASSWORD="..."   # a role allowed to CREATE DATABASE (tests use test_t2n_erp)
.\.venv\Scripts\python manage.py test apps
```
311 tests cover migrations, API behaviour, permissions, persistence, audit immutability,
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
