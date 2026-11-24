// The user guide's content, in one place. The Guide pages render it, scripts/build-guide-docs.mjs turns it into docs/MODULE_GUIDE.md,
// and the browser test checks every link in it against the real routes. Names, icons and one-line purposes come from ../../moduleMeta.js.
//
// ACCURACY RULE: write only what the code does. Where something is not connected (no bank feed, no SMS) say so plainly.
// To add a module guide: add an entry to guideModulesA.js or guideModulesB.js (same shape as the others), plus a MODULE_META entry.
import { MODULE_META } from '../../moduleMeta.js';
import { MODULES_A } from './guideModulesA.js';
import { MODULES_B } from './guideModulesB.js';
import { MATRIX, PERMISSIONS, ROLES } from './rolesData.js';

export { MATRIX, PERMISSIONS, ROLES };

export const OVERVIEW = {
  intro: [
    'The Tera2Nano ERP runs the business of one Indian LLP in one place: winning customers, invoicing with GST, buying, keeping the books, filing records for GST, TDS and income tax, running projects and people, looking after the partners\' accounts, staying compliant, and reporting on all of it.',
    'It is a set of connected modules, not separate programs. A won deal becomes a customer; an issued invoice becomes a journal entry; journal entries become the GST figures, the project profit and the financial statements; large items wait in Approvals; and every change is written to one change history. The pages below show that flow end to end and then each module in detail.',
  ],
  howToRead: [
    'Start with the process flow and use Play the process for a guided tour of each step.',
    'The integration map and table show exactly what flows between modules, and which ones post to the books.',
    'Each module has its own guide page with the screens, a step-by-step workflow with example figures, statuses, automatic behaviour and honest limits.',
  ],
  notes: [
    'Figures in examples (₹, GST at 18%, approval limits) are examples or the editable starting rules. Your company\'s own settings apply.',
    'Filing with the government (GST, TDS, MCA, income tax), live bank feeds and payment gateways are not part of the system (bank statements are imported from a file instead). It records and prepares; you file and pay elsewhere. Email is: once the mail server is set up, invoices, quotations, purchase orders, payslips, payment reminders and invitation links are emailed from the app.',
  ],
};

// The end-to-end process. Lanes are rows of the diagram; steps are numbered across all lanes in the order of the tour.
// `to` is the real screen; `module` is the key of the module's guide page (also the fallback when the person may not open `to`).
export const FLOW_LANES = [
  { id: 'main', title: 'Sell and get paid', note: 'From the first enquiry to the figures a partner reads.' },
  { id: 'buy', title: 'Buy and pay vendors', note: 'Posts into the books the way a sale does.', end: { text: 'Posts to the books', to: '/accounting/journal' } },
  { id: 'projects', title: 'Deliver and measure', note: 'Money is never typed in: it comes from journal lines tagged with the project.', end: { text: 'Reads tagged lines from the books', to: '/accounting/journal' } },
  { id: 'people', title: 'People', note: 'Payroll posts into the books.', end: { text: 'Posts to the books', to: '/accounting/journal' } },
  { id: 'partners', title: 'Partners', note: 'Capital, drawings and profit share post into the books.', end: { text: 'Posts to the books', to: '/accounting/journal' } },
  { id: 'compliance', title: 'Compliance', note: 'Shows LLP and labour due dates beside the tax calendar.', end: { text: 'Reads the tax calendar', to: '/tax/calendar' } },
  { id: 'workplace', title: 'Workplace', note: 'Meetings, tasks and circulars.' },
  { id: 'across', title: 'Across everything', note: 'Used by the other lanes.' },
];

export const FLOW_STEPS = [
  { id: 'lead', lane: 'main', title: 'Lead', module: 'crm', to: '/crm/leads', screen: 'CRM, Leads',
    what: 'A possible customer is recorded as a lead: who they are, where they came from and what they may buy. The system scores it from 0 to 100 and warns about duplicates.',
    data: 'A lead record (company, contact, source, estimated value) is stored in CRM. Nothing reaches the books.' },
  { id: 'deal', lane: 'main', title: 'Deal', module: 'crm', to: '/crm/deals', screen: 'CRM, Deals',
    what: 'A qualified lead is converted into an account, a contact and a deal on the pipeline board. Moving the deal across stages changes its probability and the forecast; losing it needs a reason.',
    data: 'Lead becomes Contact + Account + Deal inside CRM.' },
  { id: 'quotation', lane: 'main', title: 'Quotation', module: 'sales', to: '/company/quotations', screen: 'Sales, Quotations',
    what: 'The quotation is prepared with items and GST, and issued with its own number and a PDF. If an approval rule covers its size (starting rule: above ₹5,00,000) it waits for the Managing Partner and is not numbered until approved.',
    data: 'The quotation is stored as an issued document. A large one creates a request in Approvals.' },
  { id: 'customer', lane: 'main', title: 'Customer', module: 'sales', to: '/sales/customers', screen: 'Sales, Customers',
    what: 'Winning the deal links the account to an existing customer (same GSTIN or name) or creates a new numbered customer. Customers can also be added by hand.',
    data: 'The CRM account gets the sales customer\'s id; a new customer record is created in Sales if needed.' },
  { id: 'invoice', lane: 'main', title: 'Invoice', module: 'sales', to: '/sales/documents', screen: 'Sales, Invoices and Notes',
    what: 'An invoice is written with GST split by place of supply and issued: it gets a number and a PDF, and is posted to the books. Recurring profiles repeat it on a schedule; the Reminders screen lists unpaid invoices to chase and emails the reminders (by hand or automatically once a day). The invoice PDF can be emailed to the customer from its page.',
    data: 'Issuing posts a journal entry: debit Accounts receivable, credit Sales and GST payable.',
    extras: [{ label: 'Recurring invoices', to: '/sales/recurring' }, { label: 'Reminders', to: '/sales/reminders' }] },
  { id: 'payment', lane: 'main', title: 'Payment received', module: 'sales', to: '/sales/payments', screen: 'Sales, Payments Received',
    what: 'The money is recorded against the customer and applied to invoices, oldest first or as you choose. Any excess stays as the customer\'s advance.',
    data: 'Posts a journal entry: debit bank or cash, credit Accounts receivable (and Customer advances for any excess).' },
  { id: 'books', lane: 'main', title: 'Books', module: 'accounting', to: '/accounting/journal', screen: 'Accounting, Journal',
    what: 'Every posting from every module lands here as a balanced journal entry, along with expenses, income, transfers and depreciation entered in Accounting. Ledgers and the trial balance, profit and loss, balance sheet and cash flow are read from the same lines.',
    data: 'Journal entries and lines (account, debit, credit, customer or vendor, project tag).' },
  { id: 'tax', lane: 'main', title: 'GST and tax', module: 'gst-tax', to: '/tax/gst', screen: 'GST & Tax, GST Summary',
    what: 'GST raised and input credit are summed from the books; the sales register is built from issued invoices and the purchase register from input-GST lines. You prepare return records, set off credit, and record challans and TDS.',
    data: 'Read from the journal and issued invoices. Set-off and challans post their own journal entries.' },
  { id: 'reports', lane: 'main', title: 'Reports', module: 'reports', to: '/reports', screen: 'Reports, Reports Home',
    what: 'The executive dashboard and eight reports read the figures from each module, with a financial-year or date-range filter and CSV download.',
    data: 'Read only. Nothing is stored or posted.' },

  { id: 'request', lane: 'buy', title: 'Purchase request', module: 'purchases', to: '/purchases/requests', screen: 'Purchases, Purchase Requests',
    what: 'Someone asks to buy something. A purchases manager approves it, and it becomes a purchase order, optionally after comparing vendor quotations.',
    data: 'A request record in Purchases (it does not use the Approvals inbox).' },
  { id: 'po', lane: 'buy', title: 'Purchase order', module: 'purchases', to: '/purchases/orders', screen: 'Purchases, Purchase Orders',
    what: 'The order goes to the vendor. Above an approval rule (starting rule: above ₹50,000) it waits for the Finance Manager and then the Managing Partner. Issuing makes a numbered PDF; receiving goods updates the order.',
    data: 'May create a request in Approvals; issuing stores a PDF. Nothing is posted yet.' },
  { id: 'bill', lane: 'buy', title: 'Bill', module: 'purchases', to: '/purchases/bills', screen: 'Purchases, Bills',
    what: 'The vendor\'s bill is entered, with TDS and whether the GST is eligible input credit. A large bill (starting rule: above ₹1,00,000) waits for approval. Recording posts it.',
    data: 'Posts: debit expense and input GST (if eligible), credit Accounts payable and TDS payable.' },
  { id: 'vpay', lane: 'buy', title: 'Vendor payment', module: 'purchases', to: '/purchases/payments', screen: 'Purchases, Payments',
    what: 'The payment is applied to the vendor\'s oldest bills (or the ones you choose); any excess is kept as an advance. Above a rule (starting rule: above ₹50,000) it waits for approval first.',
    data: 'Posts: debit Accounts payable, credit bank or cash.' },

  { id: 'project', lane: 'projects', title: 'Project and budget', module: 'projects', to: '/projects/projects', screen: 'Projects, All Projects',
    what: 'A project has a client (a sales customer), milestones, a team and a budget. Changing the budget goes to approval. A milestone with an amount can create a draft invoice for the client.',
    data: 'Projects read customers from Sales; a milestone creates a draft sales invoice tagged to the project.' },
  { id: 'tasks', lane: 'projects', title: 'Tasks and timesheets', module: 'projects', to: '/projects/tasks', screen: 'Projects, Tasks and Timesheets',
    what: 'Tasks move across a Kanban board; people log hours against the project and task. Hours at the team member\'s rate give the billable value used for unbilled work.',
    data: 'Stored in Projects. Hours do not post any cost to the books.',
    extras: [{ label: 'Timesheets', to: '/projects/timesheets' }] },
  { id: 'profit', lane: 'projects', title: 'Profitability', module: 'projects', to: '/projects/profitability', screen: 'Projects, Project Profitability',
    what: 'Revenue and cost come from the journal lines tagged with the project (invoices, receipts, purchase bills and so on), so profit and margin always agree with the books.',
    data: 'Read from the journal by project tag.' },

  { id: 'attendance', lane: 'people', title: 'Attendance and leave', module: 'hr', to: '/hr/leave', screen: 'People, Attendance and Leave',
    what: 'Attendance is marked or self checked-in. Leave requests go to approval (starting rule: the HR Manager); approved leave appears as On leave and reduces the balance. Unpaid leave reduces pay.',
    data: 'A leave request creates an Approvals request; approval updates the leave balance and the attendance sheet.',
    extras: [{ label: 'Attendance', to: '/hr/attendance' }] },
  { id: 'payroll', lane: 'people', title: 'Payroll', module: 'hr', to: '/hr/payroll', screen: 'People, Payroll',
    what: 'A monthly run generates payslips with PF, ESI and professional tax, is locked, approved by the Managing Partner (starting rule), posted, and then paid.',
    data: 'Posting writes one journal entry (debit salaries and employer contributions, credit net pay and statutory payables); paying writes a second.' },

  { id: 'capital', lane: 'partners', title: 'Capital and drawings', module: 'partners', to: '/partners/transactions', screen: 'Partners, Transactions',
    what: 'Capital introduced, drawings, interest, remuneration and partner loans are recorded. A withdrawal above the threshold waits for the partners\' approval.',
    data: 'Each transaction posts a journal entry tagged to the partner.' },
  { id: 'profitshare', lane: 'partners', title: 'Profit sharing', module: 'partners', to: '/partners/profit-sharing', screen: 'Partners, Profit Sharing',
    what: 'The net profit of a period is read from the books and split by the partners\' ratio; posting credits each partner\'s current account.',
    data: 'Reads profit and loss; posts to retained earnings and the partners\' current accounts.' },

  { id: 'calendar', lane: 'compliance', title: 'Compliance calendar', module: 'compliance', to: '/compliance/calendar', screen: 'Compliance, Calendar',
    what: 'Due dates for LLP forms, income-tax, GST, TDS, PF, ESI and licences, with reminders, filings and acknowledgements. The GST and tax due dates are read from the Tax calendar.',
    data: 'Reads the Tax calendar (read only). Filings record acknowledgement numbers; nothing is filed online.' },

  { id: 'meetings', lane: 'workplace', title: 'Meetings, tasks and circulars', module: 'workplace', to: '/workplace/meetings', screen: 'Workplace, Meetings',
    what: 'Meetings produce decisions and action items that become tasks; minutes and circulars are issued as numbered PDFs; circulars are acknowledged by the people they address.',
    data: 'Minutes and circulars are issued through the document service; action items create tasks.',
    extras: [{ label: 'Tasks', to: '/workplace/tasks' }, { label: 'Circulars', to: '/workplace/circulars' }] },

  { id: 'approvals', lane: 'across', title: 'Approvals', module: 'approvals', to: '/approvals', screen: 'Approvals, Pending My Approval',
    what: 'Anything above a rule waits here: quotations, purchase orders, bills, payments, expenses, leave, payroll, salary revisions, project budgets and partner withdrawals. On approval the owning module carries on (an approved bill posts); a rejection sends it back.',
    data: 'The owning module sends a request; the decision calls back into that module in the same step.' },
  { id: 'documents', lane: 'across', title: 'Documents and attachments', module: 'documents', to: '/company/letters', screen: 'Documents, Letters and Documents',
    what: 'Every issued document (quotation, invoice, purchase order, payslip, circular, minutes, letter) is numbered with a PDF on the letterhead. Files can be attached to the record types that offer an attachment box. Every change in every module is written to the change history.',
    data: 'Issued documents and attachments are stored privately with checksums; changes go to the change history.' },
];

// What flows between modules. One list feeds the integration map, the matrix, each module page's "sends to / receives from"
// and the written guide. `map: false` flows are listed but not drawn (the diagram would be crowded).
export const FLOWS = [
  { id: 'crm-sales-customer', from: 'crm', to: 'sales', label: 'won deal creates customer', map: true,
    detail: 'Moving a deal to a Won stage links the account to a sales customer: the one already linked, else an active customer with the same GSTIN or name, else a new numbered customer made from the account.' },
  { id: 'crm-sales-quotation', from: 'crm', to: 'sales', label: 'deal opens Quotations', map: false,
    detail: 'Create quotation on a deal opens the Quotations screen in Sales (the form is not pre-filled). Issued quotations addressed to the same customer name are listed on the deal page.' },
  { id: 'projects-sales-milestone', from: 'projects', to: 'sales', label: 'milestone creates draft invoice', map: true,
    detail: 'A milestone with an amount creates a draft sales invoice for the project\'s client, tagged to the project. A project\'s client is a Sales customer.' },
  { id: 'sales-accounting', from: 'sales', to: 'accounting', label: 'invoice and receipt post journal', map: true,
    detail: 'Issuing or voiding an invoice, credit note or debit note, and recording or voiding a payment received, post to or reverse in the ledger in the same transaction. Proforma invoices post nothing.' },
  { id: 'purchases-accounting', from: 'purchases', to: 'accounting', label: 'bill and payment post journal', map: true,
    detail: 'Recording a vendor bill posts expense, input GST (when ITC is eligible), payable and TDS payable; a vendor payment posts payable (or advance) against bank or cash; a vendor opening balance posts too. Voiding reverses them.' },
  { id: 'hr-accounting', from: 'hr', to: 'accounting', label: 'payroll posts journal', map: true,
    detail: 'Posting a payroll run writes one entry for salaries, employer PF and ESI against net pay and the statutory payables; paying the salaries writes a second entry. Voiding reverses both.' },
  { id: 'partners-accounting', from: 'partners', to: 'accounting', label: 'capital, drawings and profit share post', map: true,
    detail: 'Every partner transaction and every posted profit allocation writes a journal entry (source partner); lines carry the partner.' },
  { id: 'accounting-projects-tag', from: 'accounting', to: 'projects', label: 'project tag on journal lines', map: true,
    detail: 'Journal lines carry the project they were tagged with (on invoices, payments received, purchase requests, orders and bills, and fixed assets). Projects reads them for revenue, cost and profit; it stores no money of its own.' },
  { id: 'accounting-tax', from: 'accounting', to: 'gst-tax', label: 'ledger lines feed GST and TDS', map: true,
    detail: 'GST payable, input credit and TDS payable are summed from journal lines by account; a voided invoice cancels its own liability.' },
  { id: 'sales-tax-register', from: 'sales', to: 'gst-tax', label: 'issued invoices fill sales register', map: false,
    detail: 'The sales register (GSTR-1 layout) is built from issued sales documents: customer GSTIN, place of supply, HSN and rate-wise taxable value.' },
  { id: 'purchases-tax-register', from: 'purchases', to: 'gst-tax', label: 'bills fill purchase register', map: false,
    detail: 'The purchase register lists every journal entry that debits an input-GST account (vendor bills and expenses with GST), with its party and reference; TDS deducted on bills shows in the TDS ledger.' },
  { id: 'hr-tax-tds', from: 'hr', to: 'gst-tax', label: 'payroll TDS in TDS ledger', map: false,
    detail: 'TDS on salary posted by payroll appears in the TDS payable ledger as section 192.' },
  { id: 'tax-accounting', from: 'gst-tax', to: 'accounting', label: 'challans and set-off post journal', map: true,
    detail: 'Recording a tax challan (GST, TDS, advance tax, income tax) posts to the books, and an ITC set-off posts its own entry (sources tax_payment and tax_setoff). Voiding or undoing reverses them.' },
  { id: 'tax-compliance', from: 'gst-tax', to: 'compliance', label: 'tax due dates, read only', map: true,
    detail: 'The Compliance calendar and reminders read the Tax calendar live (for people with tax.view) and show it read-only beside LLP and labour due dates.' },
  { id: 'company-compliance', from: 'company', to: 'compliance', label: 'expiring registrations', map: false,
    detail: 'Registrations, licences and signatory authorisations with end dates, and uploaded documents with expiry dates, appear in Compliance\'s Expiry Watch.' },
  { id: 'accounting-reports', from: 'accounting', to: 'reports', label: 'books feed reports', map: true,
    detail: 'Annual revenue and cash-flow analysis are read from the journal.' },
  { id: 'sales-reports', from: 'sales', to: 'reports', label: 'receivables', map: false,
    detail: 'Pending customer payments reads the sales receivables.' },
  { id: 'purchases-reports', from: 'purchases', to: 'reports', label: 'payables', map: false,
    detail: 'Vendor outstanding reads the payables.' },
  { id: 'hr-reports', from: 'hr', to: 'reports', label: 'salary', map: false,
    detail: 'The salary report reads finalised payroll runs (totals only).' },
  { id: 'partners-reports', from: 'partners', to: 'reports', label: 'partner capital', map: false,
    detail: 'The partner capital report reads capital, current accounts, loans and drawings.' },
  { id: 'projects-reports', from: 'projects', to: 'reports', label: 'profitability', map: false,
    detail: 'The project profitability report reads each project\'s revenue, cost and profit.' },
  { id: 'tax-reports', from: 'gst-tax', to: 'reports', label: 'GST summary', map: false,
    detail: 'The GST summary and tax liability report reads GST raised and credited.' },
  { id: 'hr-projects', from: 'hr', to: 'projects', label: 'employee directory', map: false,
    detail: 'Project team members and task assignees are picked from the HR directory (no pay data), and HR\'s project assignments use the project list.' },
  { id: 'workplace-projects', from: 'projects', to: 'workplace', label: 'project list', map: false,
    detail: 'A workplace task can be tagged to a project picked from the project list.' },
  { id: 'company-sales', from: 'company', to: 'sales', label: 'tax, numbering, letterhead, terms', map: false,
    detail: 'GST settings, the registered state, numbering, letterhead, bank details and default payment terms are used on every quotation and invoice.' },
  { id: 'company-purchases', from: 'company', to: 'purchases', label: 'numbering, letterhead', map: false,
    detail: 'Purchase order numbers and PDFs use the numbering and letterhead; GST on bills splits by the company\'s registered state.' },
  { id: 'company-accounting', from: 'company', to: 'accounting', label: 'tax settings, bank accounts', map: false,
    detail: 'Tax settings decide GST on expenses and income; each company bank account gets its own cash/bank ledger account the first time it is used.' },
  { id: 'company-hr', from: 'company', to: 'hr', label: 'numbering, letterhead, bank', map: false,
    detail: 'Run numbers and payslip PDFs use the numbering and letterhead; salaries are paid from one of the company bank accounts.' },
  { id: 'company-partners', from: 'company', to: 'partners', label: 'partner records', map: false,
    detail: 'The partners are the records kept in Company, Partners and Signatories; statements use the letterhead.' },
  { id: 'company-workplace', from: 'company', to: 'workplace', label: 'numbering, letterhead', map: false,
    detail: 'Minutes and circulars are numbered and drawn on the letterhead.' },
  { id: 'sales-documents', from: 'sales', to: 'documents', label: 'issued PDFs', map: false,
    detail: 'Quotations, invoices and notes are issued documents with a number and PDFs, stored with the company\'s issued documents.' },
  { id: 'purchases-documents', from: 'purchases', to: 'documents', label: 'order PDFs', map: false,
    detail: 'Purchase order PDFs are issued documents.' },
  { id: 'hr-documents', from: 'hr', to: 'documents', label: 'payslips', map: false,
    detail: 'Payslip PDFs are issued documents.' },
  { id: 'partners-documents', from: 'partners', to: 'documents', label: 'statements', map: false,
    detail: 'Partner capital statements are issued documents.' },
  { id: 'workplace-documents', from: 'workplace', to: 'documents', label: 'minutes, circulars', map: false,
    detail: 'Minutes and circulars are issued documents.' },
  { id: 'sales-approvals', from: 'sales', to: 'approvals', label: 'quotation, payment void', map: true,
    detail: 'A quotation above a rule, and the voiding of a received payment above a rule, create a request. Approving a quotation issues it; approving a void reverses the payment.' },
  { id: 'purchases-approvals', from: 'purchases', to: 'approvals', label: 'order, bill, payment', map: true,
    detail: 'A purchase order, vendor bill or vendor payment (or payment void) above a rule creates a request. An approved bill is recorded and posted; an approved payment is numbered, applied and posted; an approved order becomes Approved.' },
  { id: 'accounting-approvals', from: 'accounting', to: 'approvals', label: 'expense', map: true,
    detail: 'An expense above a rule waits as Pending approval and is posted only when approved.' },
  { id: 'hr-approvals', from: 'hr', to: 'approvals', label: 'leave, payroll, salary', map: true,
    detail: 'Leave requests, payroll runs and salary revisions create requests. Approving leave updates balance and attendance, approving a run lets it be posted, approving a revision creates the new salary structure.' },
  { id: 'partners-approvals', from: 'partners', to: 'approvals', label: 'withdrawal', map: true,
    detail: 'Capital withdrawn or a drawing above the threshold creates a request; approval posts it.' },
  { id: 'projects-approvals', from: 'projects', to: 'approvals', label: 'budget change', map: true,
    detail: 'A new or changed project budget under a rule waits; approval applies it, and the project keeps the old budget until then.' },
];

FLOWS.push(
  { id: 'approvals-sales', from: 'approvals', to: 'sales', label: 'decision', map: false,
    detail: 'An approved quotation is issued; a rejected one returns to draft with a note. An approved payment void reverses the payment.' },
  { id: 'approvals-purchases', from: 'approvals', to: 'purchases', label: 'decision', map: false,
    detail: 'An approved order becomes Approved, an approved bill is recorded and posted, an approved vendor payment (or void) is applied and posted. A rejection returns the record with the reason.' },
  { id: 'approvals-accounting', from: 'approvals', to: 'accounting', label: 'decision', map: false,
    detail: 'An approved expense is posted to the books; a rejected one goes back to draft.' },
  { id: 'approvals-hr', from: 'approvals', to: 'hr', label: 'decision', map: false,
    detail: 'Approved leave becomes Approved; an approved payroll run can be posted; an approved salary revision creates the new salary structure.' },
  { id: 'approvals-partners', from: 'approvals', to: 'partners', label: 'decision', map: false,
    detail: 'An approved withdrawal is posted; a rejected one is refused.' },
  { id: 'approvals-projects', from: 'approvals', to: 'projects', label: 'decision', map: false,
    detail: 'An approved budget change is applied; a rejected or withdrawn one leaves the budget as it was.' },
);

// Cross-cutting behaviour that is not a single arrow between two modules.
export const CROSS_CUTTING = [
  { title: 'Approvals', text: 'Ten kinds of record can wait for approval: quotation, purchase order, vendor bill, payment, expense, leave request, payroll run, salary revision, project budget and partner withdrawal. A record is held only when an active rule covers its amount. Without a matching rule most records go straight through.' },
  { title: 'Ledger', text: 'All money postings go through one ledger service inside the same transaction as the record that caused them: Sales, Purchases, People, Partners, GST & Tax and Accounting itself. CRM, Projects, Approvals, Compliance, Workplace, Reports, Documents, Company and Administration never post.' },
  { title: 'Attachments', text: 'Files can be attached to specific record types only: CRM leads, deals and accounts; vendors and vendor bills; employees; partners; projects; tax filings and payments; compliance filings, events, contracts, cases and policies; meetings, circulars and office assets; fixed assets. Sales invoices, customers, expenses and payroll runs have no attachment box.' },
  { title: 'Change history', text: 'Every module writes its creates, changes, voids and decisions to one append-only change history, shown in Administration (needs audit.view). Sensitive values such as full bank account numbers are not copied into it.' },
  { title: 'Numbering', text: 'Every reference number is generated from the rules in Company, Document Numbering (for example T2N/INV/26/1115/00001). Nobody types a number, and a voided number is never reused.' },
  { title: 'Permissions', text: 'Every screen and every action is checked on the server against the role\'s permissions; the screens only hide what the server would refuse. Administration shows and edits the permission matrix.' },
  { title: 'Email', text: 'Once the mail server is set up on the server (Administration, Email settings), invoices and credit notes, quotations and letters, purchase orders, payslips, payment reminders and invitation links are emailed from their pages with the PDF attached. Every message, sent or failed, is listed under Administration, Sent emails, where a failed one can be sent again. Without a mail server, messages are saved on the server but not delivered, and the app says so.' },
  { title: 'Not connected', text: 'No SMS or push messages are sent, and approvals, circulars and compliance reminders show inside the system only (they are not emailed). There is no live bank feed (statements are imported from a file), payment gateway, government portal filing or calendar sync.' },
];

export const GLOSSARY = [
  { term: 'Accounts payable', meaning: 'What the company owes its vendors for bills it has recorded.' },
  { term: 'Accounts receivable', meaning: 'What customers owe the company for invoices issued and not yet paid.' },
  { term: 'Advance (customer or vendor)', meaning: 'Money received from a customer, or paid to a vendor, before there is an invoice or bill to set it against. It is kept as a balance and applied later.' },
  { term: 'Advance tax', meaning: 'Income tax paid in instalments during the financial year, on the estimated profit, instead of all at once at the end. Recorded in GST & Tax.' },
  { term: 'Ageing', meaning: 'Grouping unpaid invoices or bills by how many days past their due date they are (current, 1 to 30, 31 to 60, 61 to 90, over 90).' },
  { term: 'Challan', meaning: 'The payment slip for a tax deposit (for example Challan 280 for income tax, 281 for TDS). The system records each tax payment and posts it.' },
  { term: 'Chart of accounts', meaning: 'The list of accounts the books use: assets, liabilities, equity, income and expenses.' },
  { term: 'CGST, SGST, UTGST, IGST', meaning: 'The parts of GST. For a supply inside one state, CGST (central) and SGST (state, or UTGST in a union territory) are charged in equal halves. For a supply to another state, IGST is charged instead.' },
  { term: 'Credit note / debit note', meaning: 'A document that reduces (credit note) or increases (debit note) what a customer owes on an earlier invoice, with its GST. Issued from the invoice.' },
  { term: 'Current account (partner)', meaning: 'A partner\'s running account for profit share, interest and remuneration credited, less drawings. Separate from the capital account.' },
  { term: 'Designated partner / DPIN', meaning: 'A partner who has the legal duties of the LLP; the DPIN (or DIN) is their identification number. DIR-3 KYC is their yearly KYC.' },
  { term: 'Double entry, debit and credit', meaning: 'Every event is recorded as equal debits and credits across accounts. The system refuses an entry whose debits and credits differ.' },
  { term: 'Drawing', meaning: 'Money a partner takes out of the LLP against their share of profit. It reduces the partner\'s current account.' },
  { term: 'ESI', meaning: 'Employees\' State Insurance. Both employee and employer contribute when the monthly gross is within the ESI ceiling.' },
  { term: 'Financial year (FY)', meaning: '1 April to 31 March, written like 2026-27. The calendar and most reports work by financial year.' },
  { term: 'Form 8, Form 11 (LLP)', meaning: 'LLP Form 8 is the statement of account and solvency (due 30 October); LLP Form 11 is the annual return (due 30 May). Other LLP forms (3, 4, 5, 15, 17) are filed within 30 days of the event.' },
  { term: 'Form 26AS', meaning: 'The tax statement on the income-tax portal that lists TDS deducted from your receipts. The TDS tracker marks a certificate "Matched in Form 26AS" when you have checked it.' },
  { term: 'GST, GSTIN', meaning: 'Goods and Services Tax. The GSTIN is the 15-character registration number of a taxpayer.' },
  { term: 'GSTR-1, GSTR-3B, GSTR-9', meaning: 'GSTR-1 lists outward supplies; GSTR-3B is the summary return and payment; GSTR-9 is the annual return. The Returns screen keeps a record of each.' },
  { term: 'HSN / SAC', meaning: 'Classification codes for goods (HSN) and services (SAC) printed on GST invoices.' },
  { term: 'ITC (input tax credit)', meaning: 'GST paid on purchases that can be set against the GST collected on sales. A vendor bill is marked eligible, ineligible or blocked (Section 17(5)).' },
  { term: 'ITR-5', meaning: 'The income-tax return form for an LLP. A tax audit report (Form 3CB-3CD) is needed only if audit applies.' },
  { term: 'Journal entry, ledger, trial balance', meaning: 'A journal entry is one balanced posting. A ledger is all the entries for one account with a running balance. The trial balance lists every account\'s balance and must add up.' },
  { term: 'Kanban', meaning: 'A board of cards in columns such as To do, In progress and Done. Drag a card to move it.' },
  { term: 'LLP, LLPIN', meaning: 'Limited Liability Partnership. The LLPIN is its registration number with the Registrar.' },
  { term: 'Loss of pay (LOP)', meaning: 'Days of unpaid absence. Each pay component is reduced in proportion to the days not worked.' },
  { term: 'PAN, TAN', meaning: 'PAN identifies a taxpayer; TAN is the number used by anyone who deducts or collects tax at source.' },
  { term: 'Place of supply', meaning: 'The state where a supply is treated as made. If it equals the company\'s registered state the supply is intra-state (CGST and SGST), otherwise inter-state (IGST).' },
  { term: 'PF (provident fund)', meaning: 'Employee and employer contributions, a percentage of the earned basic salary up to the wage ceiling unless the employee is on full-basic PF.' },
  { term: 'Professional tax', meaning: 'A state tax on salaries, taken from each employee by slab.' },
  { term: 'Proforma invoice', meaning: 'A preview invoice that is numbered but is not an accounting event: it posts nothing to the books.' },
  { term: 'Reconciliation (bank)', meaning: 'Checking the books against the bank statement: each statement line is matched to its entry in the books (or the entry is created from it), and what is left over (cheques not yet presented, deposits in transit) explains the difference between the two balances.' },
  { term: 'Reversal / void', meaning: 'Cancelling a posted record by posting the mirror image rather than deleting it, so history stays complete.' },
  { term: 'Section 17(5)', meaning: 'The part of the GST law that blocks input credit on certain purchases. A bill can be marked Blocked.' },
  { term: 'SLM and WDV (depreciation)', meaning: 'Straight-line method spreads an asset\'s cost evenly (the Companies Act way). Written-down value charges a percentage of the remaining value each year (the income-tax block rate).' },
  { term: 'SRN', meaning: 'Service request number given by the MCA portal when a form is filed; stored on legal events and filings.' },
  { term: 'TDS', meaning: 'Tax deducted at source. The company deducts it from payments to vendors (by section, such as 194C contractors or 194J professionals) and from salaries, and deposits it with a challan. Customers may deduct it from what they pay the company.' },
  { term: 'Voucher', meaning: 'An expense or income entry made directly in Accounting, outside invoices and bills.' },
];

// One short line per role: how that role is used in practice (the table shows the exact permissions).
export const ROLE_NOTES = {
  admin: 'Holds every permission and cannot be reduced. Manages users, the permission matrix and invitations.',
  managing_partner: 'Holds every permission, today and for every module added later, and cannot be reduced: a partner can never be locked out. Signs off the large approvals and runs the business end to end. Can invite members, with any access role except Admin.',
  partner: 'Holds every permission, exactly like the Managing Partner, and cannot be reduced. The approval rules still apply: a partner cannot approve their own request, and a rule that names the Partner role asks every partner to agree. Can invite members, with any access role except Admin.',
  finance_manager: 'Runs the money side: sales, purchases, the books, tax and compliance filings, payroll figures and approvals. Cannot change company details or run CRM.',
  hr_manager: 'Runs people: employees, attendance, leave and payroll, plus workplace management. Can view projects and reports. Can invite new employees and project managers with a link.',
  project_manager: 'Runs delivery: projects, plus workplace management. Can view sales, purchases, people, CRM and reports.',
  employee: 'Sees the company details and the workplace, issues documents, and uses their own pages (profile, attendance, leave, payslips, tasks).',
};

// ---------------------------------------------------------------------------------------------------------------------------------
// Assembling the module guides

const RAW = [...MODULES_A, ...MODULES_B];

export const MODULE_ORDER = Object.entries(MODULE_META)
  .filter(([key]) => key !== 'guide')
  .sort((a, b) => a[1].order - b[1].order)
  .map(([key]) => key);

export const RESERVED_KEYS = ['integration', 'glossary', 'roles', 'operate'];

const flowsFrom = (key) => FLOWS.filter((f) => f.from === key);
const flowsTo = (key) => FLOWS.filter((f) => f.to === key);
const uniq = (list) => [...new Set(list)];

export const MODULE_GUIDES = MODULE_ORDER.map((key) => {
  const g = RAW.find((m) => m.key === key);
  if (!g) throw new Error(`guideData: no guide content for module "${key}"`);
  const meta = MODULE_META[key];
  const sends = flowsFrom(key);
  const receives = flowsTo(key);
  return {
    ...g,
    name: meta.name,
    icon: meta.icon,
    purpose: meta.purpose,
    sends,
    receives,
    sendsTo: uniq(sends.map((f) => f.to)),
    receivesFrom: uniq(receives.map((f) => f.from)),
  };
});

export const guideFor = (key) => MODULE_GUIDES.find((g) => g.key === key);
export const nameOf = (key) => MODULE_META[key]?.name || key;

// The roles that hold each of a module's permission codes by default, for the "Who uses it" table.
export function roleRows(codes) {
  return codes.map((code) => {
    const perm = PERMISSIONS.find((p) => p.code === code);
    if (!perm) throw new Error(`guideData: unknown permission code ${code}`);
    return { code, text: perm.text, roles: ROLES.filter((r) => (MATRIX[r.value] || []).includes(code)).map((r) => r.label) };
  });
}

// Every link in the guide, with where it lives, for the route check.
export function allLinks() {
  const out = [];
  FLOW_STEPS.forEach((s) => {
    out.push({ where: `flow step ${s.id}`, to: s.to });
    (s.extras || []).forEach((e) => out.push({ where: `flow step ${s.id} extra ${e.label}`, to: e.to }));
  });
  MODULE_GUIDES.forEach((g) => g.screens.forEach((s) => out.push({ where: `${g.key} screen ${s.label}`, to: s.to })));
  return out;
}
