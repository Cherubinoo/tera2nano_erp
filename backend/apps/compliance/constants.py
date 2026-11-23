"""Choice lists, penalty formulas and the starting catalogue of Indian LLP obligations.

The penalty figures are ESTIMATES of what the law currently says (see `docs` strings); they are plain data so they can be
corrected when a rule changes, without touching code. Always check the current amount on the portal before paying.
"""
from decimal import Decimal

# ---------------------------------------------------------------------------- categories and choices
MCA = "mca"
INCOME_TAX = "income_tax"
GST = "gst"
TDS = "tds"
LABOUR = "labour"
LOCAL = "local"
OTHER = "other"
CATEGORIES = [
    (MCA, "MCA / ROC"), (INCOME_TAX, "Income tax"), (GST, "GST"), (TDS, "TDS"), (LABOUR, "Labour"), (LOCAL, "Local"),
    (OTHER, "Other"),
]
CATEGORY_LABELS = dict(CATEGORIES)

FREQUENCIES = [
    ("annual", "Annual"), ("half_yearly", "Half-yearly"), ("quarterly", "Quarterly"), ("monthly", "Monthly"),
    ("event", "On an event"), ("one_time", "One time"),
]
PRIORITIES = [("low", "Low"), ("normal", "Normal"), ("high", "High"), ("critical", "Critical")]
PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "low": 3}

PENALTY_CHOICES = [
    ("none", "No automatic estimate"), ("llp_per_day", "LLP Act additional fee (per day)"),
    ("itr_234f", "Income tax late fee (section 234F)"), ("gst_late_fee", "GST late fee (per day)"),
    ("tds_234e", "TDS return late fee (section 234E)"),
]

EVENT_TYPES = [
    ("incorporation", "Incorporation"), ("name_change", "Change of name"), ("partner_change", "Change in partners"),
    ("address_change", "Change of registered office / address"), ("agreement_amendment", "LLP agreement amendment"),
    ("charge", "Charge registered"), ("kyc", "KYC / DIN event"), ("licence", "Licence or registration"),
    ("other", "Other"),
]
EVENT_TYPE_LABELS = dict(EVENT_TYPES)

CONTRACT_TYPES = [
    ("nda", "NDA"), ("client", "Client contract"), ("vendor", "Vendor contract"), ("lease", "Lease / rent"),
    ("mou", "MoU"), ("licence", "Licence"), ("employment", "Employment / consultancy"), ("other", "Other"),
]
CASE_TYPES = [
    ("legal_notice", "Legal notice"), ("court_case", "Court case"), ("tribunal", "Tribunal"),
    ("arbitration", "Arbitration"), ("regulatory", "Regulatory notice"), ("other", "Other"),
]
PARTY_ROLES = [
    ("plaintiff", "We are the plaintiff / applicant"), ("respondent", "We are the defendant / respondent"),
    ("notice_sent", "Notice sent by us"), ("notice_received", "Notice received by us"),
]

# ---------------------------------------------------------------------------- statuses and windows
DUE_SOON_DAYS = 15
DEFAULT_REMINDER_DAYS = [15, 7, 1]
DEFAULT_HORIZON_DAYS = 30
MAX_HORIZON_DAYS = 365
STATUS_LABELS = {
    "upcoming": "Upcoming", "due_soon": "Due soon", "overdue": "Overdue", "filed": "Filed", "not_applicable": "Not applicable",
}

# ---------------------------------------------------------------------------- penalty formulas (estimates)
PENALTY_RULES = {
    "llp_per_day": {
        "label": "LLP Act additional fee",
        "basis": "Section 76 of the LLP Act, 2008 and the MCA additional-fee rule: Rs 100 for every day of delay, for each form, "
                 "with no upper limit.",
        "per_day": Decimal("100"),
        "cap": None,
    },
    "itr_234f": {
        "label": "Income tax late fee (section 234F)",
        "basis": "Rs 5,000 for a return filed late; Rs 10,000 if filed after 31 December of the same year; Rs 1,000 where total "
                 "income is up to Rs 5,00,000. Interest under sections 234A/B/C is not included.",
        "fee": Decimal("5000"),
        "fee_after_cutoff": Decimal("10000"),
        "cutoff_month": 12,
        "cutoff_day": 31,
        "low_income_fee": Decimal("1000"),
        "low_income_limit": Decimal("500000"),
    },
    "gst_late_fee": {
        "label": "GST late fee (GSTR-1 / GSTR-3B)",
        "basis": "Rs 50 a day (Rs 25 CGST + Rs 25 SGST), Rs 20 a day for a nil return, up to Rs 10,000 (Rs 500 for nil). "
                 "Interest at 18% a year on unpaid tax is not included.",
        "per_day": Decimal("50"),
        "per_day_nil": Decimal("20"),
        "cap": Decimal("10000"),
        "cap_nil": Decimal("500"),
    },
    "tds_234e": {
        "label": "TDS return late fee (section 234E)",
        "basis": "Rs 200 for every day of delay, limited to the TDS the return covers. Interest under section 201(1A) is not included.",
        "per_day": Decimal("200"),
    },
}

# ---------------------------------------------------------------------------- the starting catalogue
# due_rule shapes (see services/schedule.py):
#   {"type": "dates", "dates": [{"month": 5, "day": 30, "fy": "prev", "label": "FY {fy}"}]}   fixed dates in the financial year
#   {"type": "monthly", "day": 11 | "last", "lag": 1}                                         every month, for the month `lag` earlier
#   {"type": "event", "days": 30}                                                              days after an event; never generated
_ANNUAL = lambda m, d, fy="prev", label="FY {fy}": {"type": "dates", "dates": [{"month": m, "day": d, "fy": fy, "label": label}]}  # noqa: E731

CATALOGUE = [
    # --- MCA / ROC
    dict(code="LLP-FORM-11", name="LLP annual return", form_no="LLP Form 11", category=MCA, authority="Ministry of Corporate Affairs (Registrar of Companies)",
         frequency="annual", due_rule=_ANNUAL(5, 30), due_rule_text="30 May, for the previous financial year (within 60 days of year end)",
         penalty_rule="llp_per_day", penalty_note="Rs 100 per day of delay, no cap (Section 76 LLP Act).", assignee_role="managing_partner",
         default_priority="high", sort_order=10, description="Annual return of the LLP: partners, contribution and changes during the year."),
    dict(code="LLP-FORM-8", name="Statement of account and solvency", form_no="LLP Form 8", category=MCA, authority="Ministry of Corporate Affairs (Registrar of Companies)",
         frequency="annual", due_rule=_ANNUAL(10, 30), due_rule_text="30 October, for the previous financial year (within 30 days of six months from year end)",
         penalty_rule="llp_per_day", penalty_note="Rs 100 per day of delay, no cap (Section 76 LLP Act).", assignee_role="managing_partner",
         default_priority="high", sort_order=11, description="Statement of account and solvency signed by the designated partners."),
    dict(code="DIR-3-KYC", name="DIR-3 KYC for designated partners", form_no="DIR-3 KYC", category=MCA, authority="Ministry of Corporate Affairs",
         frequency="annual", due_rule=_ANNUAL(9, 30, "prev", "KYC for FY {fy}"), due_rule_text="30 September every year",
         penalty_rule="none", penalty_note="Rs 5,000 late fee; the DIN is deactivated until KYC is done.", assignee_role="managing_partner",
         default_priority="high", sort_order=12, description="Annual KYC of every designated partner holding a DIN."),
    dict(code="LLP-FORM-3", name="LLP agreement / changes in the agreement", form_no="LLP Form 3", category=MCA, authority="Ministry of Corporate Affairs (Registrar of Companies)",
         frequency="event", due_rule={"type": "event", "days": 30}, due_rule_text="Within 30 days of the agreement or its change",
         penalty_rule="llp_per_day", penalty_note="Rs 100 per day of delay (additional fee).", assignee_role="managing_partner", default_priority="high",
         sort_order=13),
    dict(code="LLP-FORM-4", name="Change in partners or designated partners", form_no="LLP Form 4", category=MCA, authority="Ministry of Corporate Affairs (Registrar of Companies)",
         frequency="event", due_rule={"type": "event", "days": 30}, due_rule_text="Within 30 days of the change",
         penalty_rule="llp_per_day", penalty_note="Rs 100 per day of delay (additional fee).", assignee_role="managing_partner", default_priority="high",
         sort_order=14),
    dict(code="LLP-FORM-5", name="Change of name of the LLP", form_no="LLP Form 5", category=MCA, authority="Ministry of Corporate Affairs (Registrar of Companies)",
         frequency="event", due_rule={"type": "event", "days": 30}, due_rule_text="Within 30 days of the change",
         penalty_rule="llp_per_day", penalty_note="Rs 100 per day of delay (additional fee).", assignee_role="managing_partner", sort_order=15),
    dict(code="LLP-FORM-15", name="Notice of change in registered office or particulars", form_no="LLP Form 15", category=MCA, authority="Ministry of Corporate Affairs (Registrar of Companies)",
         frequency="event", due_rule={"type": "event", "days": 30}, due_rule_text="Within 30 days of the change",
         penalty_rule="llp_per_day", penalty_note="Rs 100 per day of delay (additional fee).", assignee_role="managing_partner", sort_order=16,
         description="Event filing. Check the current form number and fee on the MCA portal before filing."),
    dict(code="LLP-FORM-17", name="Other LLP event filing", form_no="LLP Form 17", category=MCA, authority="Ministry of Corporate Affairs (Registrar of Companies)",
         frequency="event", due_rule={"type": "event", "days": 30}, due_rule_text="Within 30 days of the event",
         penalty_rule="llp_per_day", penalty_note="Rs 100 per day of delay (additional fee).", assignee_role="managing_partner", sort_order=17,
         description="Event filing. Check the current form number and fee on the MCA portal before filing."),
    # --- Income tax
    dict(code="ITR-5", name="Income tax return (LLP, accounts not audited)", form_no="ITR-5", category=INCOME_TAX, authority="Income Tax Department",
         frequency="annual", due_rule=_ANNUAL(7, 31, "prev", "FY {fy} (AY {ay})"), due_rule_text="31 July (accounts not required to be audited)",
         penalty_rule="itr_234f", penalty_note="Late fee Rs 5,000 (Rs 1,000 if income up to Rs 5 lakh) plus interest.", assignee_role="finance_manager",
         default_priority="high", sort_order=20),
    dict(code="ITR-5-AUDIT", name="Income tax return (LLP, tax audit applies)", form_no="ITR-5", category=INCOME_TAX, authority="Income Tax Department",
         frequency="annual", due_rule=_ANNUAL(10, 31, "prev", "FY {fy} (AY {ay})"), due_rule_text="31 October (accounts required to be audited)",
         penalty_rule="itr_234f", penalty_note="Late fee Rs 5,000 plus interest.", assignee_role="finance_manager", default_priority="high",
         enabled=False, sort_order=21, description="Switch this on, and ITR-5 above off, when the LLP's accounts must be audited."),
    dict(code="TAX-AUDIT", name="Tax audit report", form_no="Form 3CB-3CD", category=INCOME_TAX, authority="Income Tax Department",
         frequency="annual", due_rule=_ANNUAL(9, 30, "prev", "FY {fy}"), due_rule_text="30 September (only if a tax audit applies)",
         penalty_rule="none", penalty_note="Penalty up to 0.5% of turnover, maximum Rs 1,50,000 (section 271B).", assignee_role="finance_manager",
         enabled=False, sort_order=22),
    dict(code="ADV-TAX", name="Advance tax instalment", form_no="Challan 280", category=INCOME_TAX, authority="Income Tax Department",
         frequency="quarterly",
         due_rule={"type": "dates", "dates": [
             {"month": 6, "day": 15, "fy": "cur", "label": "1st instalment (15%) FY {fy}"},
             {"month": 9, "day": 15, "fy": "cur", "label": "2nd instalment (45%) FY {fy}"},
             {"month": 12, "day": 15, "fy": "cur", "label": "3rd instalment (75%) FY {fy}"},
             {"month": 3, "day": 15, "fy": "cur", "label": "4th instalment (100%) FY {fy}"}]},
         due_rule_text="15 June, 15 September, 15 December, 15 March", penalty_rule="none",
         penalty_note="Interest under sections 234B and 234C on any shortfall.", assignee_role="finance_manager", sort_order=23),
    # --- TDS
    dict(code="TDS-PAY", name="TDS deposit for the previous month", form_no="Challan 281", category=TDS, authority="Income Tax Department",
         frequency="monthly", due_rule={"type": "monthly", "day": 7, "lag": 1}, due_rule_text="7th of the following month",
         penalty_rule="none", penalty_note="Interest 1% a month (not deducted) / 1.5% a month (deducted, not paid).", assignee_role="finance_manager",
         default_priority="high", sort_order=30),
    dict(code="TDS-RETURN", name="TDS return (24Q / 26Q)", form_no="24Q / 26Q", category=TDS, authority="Income Tax Department",
         frequency="quarterly",
         due_rule={"type": "dates", "dates": [
             {"month": 5, "day": 31, "fy": "prev", "label": "Q4 FY {fy} (Jan-Mar)"},
             {"month": 7, "day": 31, "fy": "cur", "label": "Q1 FY {fy} (Apr-Jun)"},
             {"month": 10, "day": 31, "fy": "cur", "label": "Q2 FY {fy} (Jul-Sep)"},
             {"month": 1, "day": 31, "fy": "cur", "label": "Q3 FY {fy} (Oct-Dec)"}]},
         due_rule_text="31 July, 31 October, 31 January and 31 May", penalty_rule="tds_234e",
         penalty_note="Late fee Rs 200 per day (section 234E) up to the TDS amount.", assignee_role="finance_manager", default_priority="high",
         sort_order=31),
    # --- GST
    dict(code="GSTR-1", name="GSTR-1 outward supplies", form_no="GSTR-1", category=GST, authority="GST Council / GSTN",
         frequency="monthly", due_rule={"type": "monthly", "day": 11, "lag": 1}, due_rule_text="11th of the following month (monthly filer)",
         penalty_rule="gst_late_fee", penalty_note="Late fee Rs 50 a day (Rs 20 for nil).", assignee_role="finance_manager", default_priority="high",
         sort_order=40),
    dict(code="GSTR-3B", name="GSTR-3B summary return and tax payment", form_no="GSTR-3B", category=GST, authority="GST Council / GSTN",
         frequency="monthly", due_rule={"type": "monthly", "day": 20, "lag": 1}, due_rule_text="20th of the following month",
         penalty_rule="gst_late_fee", penalty_note="Late fee Rs 50 a day (Rs 20 for nil) plus 18% interest on tax.", assignee_role="finance_manager",
         default_priority="high", sort_order=41),
    dict(code="GSTR-9", name="GST annual return", form_no="GSTR-9", category=GST, authority="GST Council / GSTN",
         frequency="annual", due_rule=_ANNUAL(12, 31), due_rule_text="31 December, for the previous financial year",
         penalty_rule="gst_late_fee", penalty_note="Late fee Rs 200 a day (turnover based) up to 0.5% of turnover.", assignee_role="finance_manager",
         sort_order=42),
    # --- Labour
    dict(code="PF-MONTHLY", name="Provident fund (ECR and payment)", form_no="EPF ECR", category=LABOUR, authority="EPFO",
         frequency="monthly", due_rule={"type": "monthly", "day": 15, "lag": 1}, due_rule_text="15th of the following month",
         penalty_rule="none", penalty_note="Interest 12% a year and damages up to 25%.", assignee_role="hr_manager", sort_order=50),
    dict(code="ESI-MONTHLY", name="ESI contribution", form_no="ESIC challan", category=LABOUR, authority="ESIC",
         frequency="monthly", due_rule={"type": "monthly", "day": 15, "lag": 1}, due_rule_text="15th of the following month",
         penalty_rule="none", penalty_note="Interest 12% a year.", assignee_role="hr_manager", sort_order=51),
    dict(code="PROF-TAX", name="Professional tax", form_no="PT challan", category=LABOUR, authority="State commercial taxes department",
         frequency="monthly", due_rule={"type": "monthly", "day": 20, "lag": 1}, due_rule_text="20th of the following month (varies by state)",
         penalty_rule="none", penalty_note="Interest and penalty as the state law provides.", assignee_role="hr_manager", sort_order=52,
         description="The date differs between states; change it in this catalogue if yours is different."),
    # --- Local and licences (switched off until the company holds the licence)
    dict(code="UDYAM-UPDATE", name="MSME / Udyam registration update", form_no="Udyam", category=LOCAL, authority="Ministry of MSME",
         frequency="annual", due_rule=_ANNUAL(3, 31, "cur", "FY {fy}"), due_rule_text="31 March: update turnover and investment",
         penalty_rule="none", penalty_note="Loss of MSME benefits if not updated.", assignee_role="finance_manager", enabled=False, sort_order=60),
    dict(code="TRADE-LICENCE", name="Trade licence renewal", form_no="Trade licence", category=LOCAL, authority="Local municipal body",
         frequency="annual", due_rule=_ANNUAL(3, 31, "cur", "FY {fy}"), due_rule_text="31 March (varies by municipality)",
         penalty_rule="none", penalty_note="Penalty as the municipal rules provide.", assignee_role="admin", enabled=False, sort_order=61),
    dict(code="SHOPS-EST", name="Shops and Establishment renewal", form_no="Shops & Establishment", category=LOCAL, authority="State labour department",
         frequency="annual", due_rule=_ANNUAL(12, 31, "cur", "FY {fy}"), due_rule_text="31 December (varies by state)",
         penalty_rule="none", penalty_note="Penalty as the state law provides.", assignee_role="admin", enabled=False, sort_order=62),
    dict(code="FSSAI", name="FSSAI licence annual return (optional)", form_no="FSSAI Form D-1", category=OTHER, authority="FSSAI",
         frequency="annual", due_rule=_ANNUAL(5, 31), due_rule_text="31 May (only for food businesses)",
         penalty_rule="none", penalty_note="Rs 100 per day late fee.", assignee_role="admin", enabled=False, sort_order=63),
]
