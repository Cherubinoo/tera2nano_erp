"""Statutory rules the tax module uses, kept in one place so a change in the law is a change in this file only.

Verify against the current notifications before relying on a date or threshold: this is the working default, not legal advice.
"""
from decimal import Decimal

# Return types. `group` drives the calendar and colours; `kind` says how the due date is worked out (see DUE_RULES).
RETURN_TYPES = {
    "gstr1": {"label": "GSTR-1", "group": "gst", "long": "GSTR-1 (outward supplies)"},
    "gstr3b": {"label": "GSTR-3B", "group": "gst", "long": "GSTR-3B (summary return and tax payment)"},
    "gstr9": {"label": "GSTR-9", "group": "gst", "long": "GSTR-9 (annual return)"},
    "tds_24q": {"label": "TDS 24Q", "group": "tds", "long": "TDS return 24Q (salary)"},
    "tds_26q": {"label": "TDS 26Q", "group": "tds", "long": "TDS return 26Q (payments other than salary)"},
    "itr5": {"label": "ITR-5", "group": "income_tax", "long": "Income tax return ITR-5 (LLP)"},
    "tax_audit": {"label": "Tax audit", "group": "income_tax", "long": "Tax audit report (Form 3CB-3CD)"},
}

# Due dates.
#   monthly:   day `day` of the month that is `months_after` months after the period's month (GSTR-1: 11th, GSTR-3B: 20th).
#   quarterly: {quarter: (month, day, years_after_the_financial_year_starts)} (TDS returns).
#   annual:    (month, day) in the year that is `years_after` after the financial year starts (FY 2026-27 -> 31 Jul 2027).
DUE_RULES = {
    "gstr1": {"kind": "monthly", "day": 11, "months_after": 1},
    "gstr3b": {"kind": "monthly", "day": 20, "months_after": 1},
    "gstr9": {"kind": "annual", "month": 12, "day": 31, "years_after": 1},
    "tds_24q": {"kind": "quarterly", "quarters": {1: (7, 31, 0), 2: (10, 31, 0), 3: (1, 31, 1), 4: (5, 31, 1)}},
    "tds_26q": {"kind": "quarterly", "quarters": {1: (7, 31, 0), 2: (10, 31, 0), 3: (1, 31, 1), 4: (5, 31, 1)}},
    "itr5": {"kind": "annual", "month": 7, "day": 31, "years_after": 1, "audit": {"month": 10, "day": 31}},
    "tax_audit": {"kind": "annual", "month": 9, "day": 30, "years_after": 1},
}

# Advance tax: (instalment, (month, day), cumulative percent of the year's tax that must have been paid by then).
# The first three fall in the financial year, the last (15 March) in the same financial year too (calendar year + 1).
ADVANCE_TAX_SCHEDULE = [(1, (6, 15), 15), (2, (9, 15), 45), (3, (12, 15), 75), (4, (3, 15), 100)]

# Income tax defaults for an LLP (percent). Surcharge and cess are parameters the user sets on the worksheet.
DEFAULT_TAX_RATE = Decimal("30")
DEFAULT_SURCHARGE = Decimal("0")
DEFAULT_CESS = Decimal("4")
ROUND_TAX_TO_TEN = True  # income and tax are rounded to the nearest ten rupees (sections 288A and 288B)

# An unregistered buyer's inter-state invoice above this value is shown separately (GSTR-1 table 5, B2CL).
B2CL_THRESHOLD = Decimal("250000")

# Payment heads per kind of tax payment (challan).
PAYMENT_HEADS = {
    "gst": [("cgst", "CGST"), ("sgst", "SGST / UTGST"), ("igst", "IGST"), ("cess", "Cess"), ("interest", "Interest"), ("penalty", "Penalty / late fee")],
    "tds": [("tds", "TDS (challan 281)"), ("interest", "Interest"), ("fee", "Late filing fee")],
    "advance_tax": [("tax", "Advance tax"), ("interest", "Interest"), ("penalty", "Penalty")],
    "income_tax": [("tax", "Self-assessment tax"), ("interest", "Interest"), ("penalty", "Penalty")],
}
PAYMENT_TYPES = {"gst": "GST", "tds": "TDS", "advance_tax": "Advance tax", "income_tax": "Income tax (self-assessment)"}

# The figures kept on each kind of filing record (the snapshot taken when it is prepared; every one is editable).
GST_HEADS = ("igst", "cgst", "sgst", "cess")
FIGURE_KEYS = {
    "gstr1": ["outward_taxable", "b2b_taxable", "b2c_taxable", "credit_debit_notes_taxable", "igst", "cgst", "sgst", "cess", "invoice_value"],
    "gstr3b": ["outward_taxable", "igst", "cgst", "sgst", "cess", "itc_igst", "itc_cgst", "itc_sgst", "itc_cess", "itc_reversed",
               "net_igst", "net_cgst", "net_sgst", "net_cess", "net_payable", "cash_payable"],
    "gstr9": ["outward_taxable", "igst", "cgst", "sgst", "cess", "itc_igst", "itc_cgst", "itc_sgst", "itc_cess", "tax_paid"],
    "tds_24q": ["tds_deducted", "tds_paid"],
    "tds_26q": ["tds_deducted", "tds_paid"],
    "itr5": ["book_profit", "taxable_income", "total_tax", "tds_credit", "advance_tax_paid", "net_payable"],
    "tax_audit": ["turnover", "book_profit"],
}

SECTION_PATTERN = r"\b(19[2-9][A-Z]{0,2}|206[A-Z]{1,3})\b"
SALARY_SECTION = "192"
