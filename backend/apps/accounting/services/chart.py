"""The standard chart of accounts for a services LLP, and lookup of the accounts the system posts to.

Accounts the system posts to carry a `system_key`; they are created on first use (and by the "set up" button), can be renamed,
but not removed or changed to another kind. Everything else in the chart is ordinary and can be edited or switched off.
"""
from django.db import IntegrityError, transaction

from apps.company.models import CompanyBankAccount

from ..models import Account

A = Account.Subtype

# (system key or "", code, name, subtype). A system key marks an account that automatic postings use.
DEFAULT_CHART = [
    ("cash", "1000", "Cash in hand", A.CASH),
    ("bank", "1010", "Bank (default account)", A.BANK),
    ("receivable", "1200", "Accounts receivable", A.RECEIVABLE),
    ("gst_cgst_in", "1310", "GST input credit: CGST", A.TAX_ASSET),
    ("gst_sgst_in", "1320", "GST input credit: SGST / UTGST", A.TAX_ASSET),
    ("gst_igst_in", "1330", "GST input credit: IGST", A.TAX_ASSET),
    ("tds_receivable", "1400", "TDS receivable", A.TAX_ASSET),
    ("", "1500", "Prepaid expenses and advances", A.OTHER_CURRENT_ASSET),
    ("", "1600", "Computers and equipment", A.FIXED_ASSET),
    ("", "1610", "Furniture and fittings", A.FIXED_ASSET),
    ("", "1620", "Office equipment", A.FIXED_ASSET),
    ("accumulated_depreciation", "1650", "Accumulated depreciation", A.ACCUMULATED_DEPRECIATION),
    ("payable", "2000", "Accounts payable", A.PAYABLE),
    ("gst_cgst_out", "2110", "GST payable: CGST", A.TAX_LIABILITY),
    ("gst_sgst_out", "2120", "GST payable: SGST / UTGST", A.TAX_LIABILITY),
    ("gst_igst_out", "2130", "GST payable: IGST", A.TAX_LIABILITY),
    ("tds_payable", "2150", "TDS payable", A.TAX_LIABILITY),
    ("customer_advances", "2200", "Customer advances", A.OTHER_CURRENT_LIABILITY),
    ("salaries_payable", "2300", "Salaries payable", A.OTHER_CURRENT_LIABILITY),
    ("", "2400", "Other current liabilities", A.OTHER_CURRENT_LIABILITY),
    ("", "2500", "Partners' loans", A.LONG_TERM_LIABILITY),
    ("capital", "3000", "Partners' capital", A.PARTNER_CAPITAL),
    ("partner_current", "3100", "Partners' current accounts", A.PARTNER_CURRENT),
    ("retained", "3200", "Retained earnings", A.RETAINED),
    ("opening_balance_equity", "3900", "Opening balance adjustments", A.PARTNER_CAPITAL),
    ("sales", "4000", "Sales", A.INCOME),
    ("sales_returns", "4100", "Sales returns and credit notes", A.INCOME),
    ("gain_on_disposal", "4600", "Profit on sale of fixed assets", A.OTHER_INCOME),
    ("other_income", "4500", "Other income", A.OTHER_INCOME),
    ("", "5000", "Direct costs of services", A.DIRECT_COST),
    ("salaries", "5100", "Salaries and wages", A.EXPENSE),
    ("", "5200", "Rent", A.EXPENSE),
    ("", "5300", "Software and subscriptions", A.EXPENSE),
    ("", "5400", "Travel and conveyance", A.EXPENSE),
    ("", "5500", "Professional and legal fees", A.EXPENSE),
    ("", "5600", "Office expenses", A.EXPENSE),
    ("", "5650", "Marketing and advertising", A.EXPENSE),
    ("", "5700", "Bank charges", A.OTHER_EXPENSE),
    ("depreciation", "5800", "Depreciation", A.DEPRECIATION),
    ("", "5900", "Other expenses", A.OTHER_EXPENSE),
    ("round_off", "5950", "Round off", A.OTHER_EXPENSE),
    ("loss_on_disposal", "5970", "Loss on sale of fixed assets", A.OTHER_EXPENSE),  # 5960 belongs to tax interest and penalty
]


@transaction.atomic
def ensure_chart(company):
    """Create any default account that is missing. Existing accounts (renamed or not) are left as they are."""
    existing_keys = set(Account.objects.filter(company=company).exclude(system_key="").values_list("system_key", flat=True))
    existing_codes = set(Account.objects.filter(company=company).values_list("code", flat=True))
    made = []
    for key, code, name, subtype in DEFAULT_CHART:
        if key and key in existing_keys:
            continue
        if not key and Account.objects.filter(company=company).exists() and code in existing_codes:
            continue
        if code in existing_codes:  # the code was taken by something else: find the next free one
            code = next(str(n) for n in range(int(code) + 1, int(code) + 500) if str(n) not in existing_codes)
        if not key and Account.objects.filter(company=company, name=name).exists():
            continue
        acct = Account.objects.create(company=company, code=code, name=name, type=Account.TYPE_OF[subtype],
                                      subtype=subtype, system_key=key)
        existing_codes.add(code)
        made.append(acct)
    return made


def system_account(company, key):
    """The account the system posts to for `key`, creating the standard chart on first use."""
    acct = Account.objects.filter(company=company, system_key=key).first()
    if acct is None:
        ensure_chart(company)
        acct = Account.objects.filter(company=company, system_key=key).first()
    if acct is None:
        raise KeyError(f"No system account '{key}'.")
    return acct


def ledger_for_bank(company, bank):
    """The ledger account that tracks a company bank account, created the first time it is needed."""
    acct = Account.objects.filter(company=company, bank_account=bank).first()
    if acct is not None:
        return acct
    ensure_chart(company)
    used = set(Account.objects.filter(company=company).values_list("code", flat=True))
    code = next(str(n) for n in range(1011, 1200) if str(n) not in used)
    try:
        with transaction.atomic():
            return Account.objects.create(company=company, code=code, name=f"Bank: {bank.display_name}", type="asset",
                                          subtype=A.BANK, bank_account=bank)
    except IntegrityError:  # created at the same moment elsewhere
        return Account.objects.get(company=company, bank_account=bank)


def money_accounts(company):
    """Cash and bank ledger accounts, one per company bank account that is in use."""
    ensure_chart(company)
    for bank in CompanyBankAccount.objects.filter(company=company, is_active=True):
        ledger_for_bank(company, bank)
    return Account.objects.filter(company=company, subtype__in=[A.CASH, A.BANK], is_active=True)
