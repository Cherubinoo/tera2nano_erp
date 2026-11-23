"""Shared choice lists for the company module."""


class DocType:
    QUOTATION = "quotation"
    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    PROJECT = "project"
    EMPLOYEE = "employee"
    MEETING = "meeting"
    MEETING_MINUTES = "meeting_minutes"
    EXPENSE = "expense"
    PAYMENT = "payment"
    PURCHASE_REQUEST = "purchase_request"
    BUSINESS_LETTER = "business_letter"
    OFFER_LETTER = "offer_letter"
    APPOINTMENT_LETTER = "appointment_letter"
    EXPERIENCE_LETTER = "experience_letter"
    HANDOVER_LETTER = "handover_letter"
    COMPLETION_CERTIFICATE = "completion_certificate"
    OPERATION = "operation_document"
    BANK_ACCOUNT = "bank_account"
    PROFORMA = "proforma_invoice"
    DEBIT_NOTE = "debit_note"
    CUSTOMER = "customer"
    JOURNAL = "journal"
    INCOME = "income"
    # Purchases and vendors
    VENDOR = "vendor"
    PURCHASE_ORDER = "purchase_order"
    VENDOR_BILL = "vendor_bill"
    VENDOR_PAYMENT = "vendor_payment"
    VENDOR_QUOTATION = "vendor_quotation"
    # Tax and compliance
    TAX_FILING = "tax_filing"
    TAX_PAYMENT = "tax_payment"
    COMPLIANCE_FILING = "compliance_filing"
    # People
    PAYSLIP = "payslip"
    PAYROLL_RUN = "payroll_run"
    LEAVE_REQUEST = "leave_request"
    # Workplace, approvals, partners, CRM
    CIRCULAR = "circular"
    TASK = "task"
    APPROVAL = "approval"
    PARTNER_TRANSACTION = "partner_transaction"
    LEAD = "lead"
    DEAL = "deal"
    RECURRING_INVOICE = "recurring_invoice"
    FIXED_ASSET = "fixed_asset"

    CHOICES = [
        (QUOTATION, "Quotation"),
        (INVOICE, "Invoice"),
        (CREDIT_NOTE, "Credit note"),
        (PROJECT, "Project"),
        (EMPLOYEE, "Employee"),
        (MEETING, "Meeting"),
        (MEETING_MINUTES, "Meeting minutes"),
        (EXPENSE, "Expense"),
        (PAYMENT, "Payment record"),
        (PURCHASE_REQUEST, "Purchase request"),
        (BUSINESS_LETTER, "Business letter"),
        (OFFER_LETTER, "Offer letter"),
        (APPOINTMENT_LETTER, "Appointment letter"),
        (EXPERIENCE_LETTER, "Experience letter"),
        (HANDOVER_LETTER, "Project handover letter"),
        (COMPLETION_CERTIFICATE, "Project completion certificate"),
        (OPERATION, "Operations document"),
        (BANK_ACCOUNT, "Bank account"),
        (PROFORMA, "Proforma invoice"),
        (DEBIT_NOTE, "Debit note"),
        (CUSTOMER, "Customer"),
        (JOURNAL, "Journal entry"),
        (INCOME, "Income"),
        (VENDOR, "Vendor"),
        (PURCHASE_ORDER, "Purchase order"),
        (VENDOR_BILL, "Vendor bill"),
        (VENDOR_PAYMENT, "Vendor payment"),
        (VENDOR_QUOTATION, "Vendor quotation"),
        (TAX_FILING, "Tax filing"),
        (TAX_PAYMENT, "Tax payment"),
        (COMPLIANCE_FILING, "Compliance filing"),
        (PAYSLIP, "Payslip"),
        (PAYROLL_RUN, "Payroll run"),
        (LEAVE_REQUEST, "Leave request"),
        (CIRCULAR, "Circular"),
        (TASK, "Task"),
        (APPROVAL, "Approval request"),
        (PARTNER_TRANSACTION, "Partner transaction"),
        (LEAD, "Lead"),
        (DEAL, "Deal"),
        (RECURRING_INVOICE, "Recurring invoice profile"),
        (FIXED_ASSET, "Fixed asset"),
    ]
    VALUES = [c[0] for c in CHOICES]

    # Documents that are rendered to a branded PDF by the company document service.
    PRINTABLE = [
        QUOTATION, INVOICE, CREDIT_NOTE, MEETING_MINUTES, BUSINESS_LETTER, OFFER_LETTER,
        APPOINTMENT_LETTER, EXPERIENCE_LETTER, HANDOVER_LETTER, COMPLETION_CERTIFICATE, OPERATION,
        PROFORMA, DEBIT_NOTE, PURCHASE_ORDER, PAYSLIP, CIRCULAR,
    ]
    # Documents built from priced items (see services/quotations.py): customer, lines, GST and totals.
    BILLING = [QUOTATION, INVOICE, PROFORMA, CREDIT_NOTE, DEBIT_NOTE]
    # Documents on which payee bank details may appear.
    BANK_DETAIL_TYPES = [QUOTATION, INVOICE, PROFORMA, DEBIT_NOTE]


class ContactType:
    GENERAL = "general"
    SALES = "sales"
    FINANCE = "finance"
    HR = "hr"
    TECHNICAL = "technical"
    LEGAL = "legal"
    VENDOR = "vendor"
    CHOICES = [
        (GENERAL, "General enquiries"),
        (SALES, "Sales enquiries"),
        (FINANCE, "Finance and billing"),
        (HR, "HR and recruitment"),
        (TECHNICAL, "Technical support"),
        (LEGAL, "Legal and compliance"),
        (VENDOR, "Vendor communication"),
    ]

    # Which contact type is the natural fallback for each printable document.
    FOR_DOCUMENT = {
        DocType.QUOTATION: SALES,
        DocType.INVOICE: FINANCE,
        DocType.CREDIT_NOTE: FINANCE,
        DocType.PROFORMA: FINANCE,
        DocType.DEBIT_NOTE: FINANCE,
        DocType.PURCHASE_ORDER: VENDOR,
        DocType.PAYSLIP: HR,
        DocType.CIRCULAR: GENERAL,
        DocType.MEETING_MINUTES: GENERAL,
        DocType.BUSINESS_LETTER: GENERAL,
        DocType.OFFER_LETTER: HR,
        DocType.APPOINTMENT_LETTER: HR,
        DocType.EXPERIENCE_LETTER: HR,
        DocType.HANDOVER_LETTER: TECHNICAL,
        DocType.COMPLETION_CERTIFICATE: TECHNICAL,
        DocType.OPERATION: GENERAL,
    }


class VerificationStatus:
    UNVERIFIED = "unverified"
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"
    CHOICES = [
        (UNVERIFIED, "Unverified"),
        (PENDING, "Pending verification"),
        (VERIFIED, "Verified"),
        (REJECTED, "Rejected"),
    ]


class AccessClass:
    INTERNAL = "internal"          # any signed-in ERP user
    CONFIDENTIAL = "confidential"  # finance and administrators
    RESTRICTED = "restricted"      # administrators only
    CHOICES = [
        (INTERNAL, "Internal"),
        (CONFIDENTIAL, "Confidential (finance and admins)"),
        (RESTRICTED, "Restricted (admins only)"),
    ]
