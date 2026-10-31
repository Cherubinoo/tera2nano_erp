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
    ]
    VALUES = [c[0] for c in CHOICES]

    # Documents that are rendered to a branded PDF by the company document service.
    PRINTABLE = [
        QUOTATION, INVOICE, CREDIT_NOTE, MEETING_MINUTES, BUSINESS_LETTER, OFFER_LETTER,
        APPOINTMENT_LETTER, EXPERIENCE_LETTER, HANDOVER_LETTER, COMPLETION_CERTIFICATE, OPERATION,
    ]
    # Documents on which payee bank details may appear.
    BANK_DETAIL_TYPES = [QUOTATION, INVOICE]


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
