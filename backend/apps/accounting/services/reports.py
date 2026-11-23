"""Financial statements, all read from the journal.

Every figure is a sum of journal lines, so the statements always agree with each other: the trial balance balances, the balance
sheet balances (assets = liabilities + equity), and the cash-flow statement ends on the real change in cash and bank.
"""
import datetime
from decimal import Decimal

from django.db.models import Sum

from apps.company.models import FinancialYear

from ..models import Account, JournalEntry, JournalLine
from . import chart, ledger

ZERO = Decimal("0.00")
S = Account.Subtype
CASH_SUBTYPES = (S.CASH, S.BANK)


def _debit_positive(totals, account_id):
    d, c = totals.get(account_id, (ZERO, ZERO))
    return ledger.money(d - c)


def _accounts(company):
    chart.ensure_chart(company)
    return list(Account.objects.filter(company=company).order_by("code"))


def financial_year_for(company, on_date):
    return FinancialYear.objects.filter(company=company, start_date__lte=on_date, end_date__gte=on_date).first()


def default_period(company, today=None):
    """The current financial year as (start, end), else the calendar year to date."""
    today = today or datetime.date.today()
    fy = financial_year_for(company, today)
    if fy:
        return fy.start_date, fy.end_date
    return datetime.date(today.year if today.month >= 4 else today.year - 1, 4, 1), today


def _row(a, amount):
    return {"account_id": str(a.id), "code": a.code, "name": a.name, "amount": amount}


# --------------------------------------------------------------------------- trial balance and ledger

def trial_balance(company, as_of):
    totals = ledger.totals(company, end=as_of)
    rows, debit, credit = [], ZERO, ZERO
    for a in _accounts(company):
        net = _debit_positive(totals, a.id)
        if net == 0 and a.id not in totals:
            continue
        d, c = (net, ZERO) if net >= 0 else (ZERO, -net)
        rows.append({"account_id": str(a.id), "code": a.code, "name": a.name, "type": a.type, "subtype": a.subtype,
                     "debit": d, "credit": c})
        debit += d
        credit += c
    return {"as_of": as_of, "rows": rows, "total_debit": debit, "total_credit": credit, "balanced": debit == credit}


def account_ledger(company, account, start, end):
    """Opening balance, every line in the period with a running balance, and the closing balance (natural side)."""
    sign = 1 if account.is_debit_normal else -1
    before = ledger.totals(company, end=start - datetime.timedelta(days=1), account_ids=[account.id]).get(account.id, (ZERO, ZERO))
    opening = ledger.money((before[0] - before[1]) * sign)
    lines = JournalLine.objects.filter(account=account, entry__date__gte=start, entry__date__lte=end).select_related("entry").order_by(
        "entry__date", "entry__created_at", "created_at")
    running, rows, debit, credit = opening, [], ZERO, ZERO
    for l in lines:
        running += (l.debit - l.credit) * sign
        debit += l.debit
        credit += l.credit
        rows.append({"id": str(l.id), "entry_id": str(l.entry_id), "date": l.entry.date, "reference": l.entry.reference,
                     "narration": l.entry.narration, "source": l.entry.source, "memo": l.memo, "debit": l.debit, "credit": l.credit,
                     "balance": ledger.money(running), "reconciled": l.reconciliation_id is not None})
    return {"account": {"id": str(account.id), "code": account.code, "name": account.name, "type": account.type, "normal": "debit" if sign == 1 else "credit"},
            "start": start, "end": end, "opening": opening, "rows": rows, "total_debit": debit, "total_credit": credit,
            "closing": ledger.money(running)}


# --------------------------------------------------------------------------- profit and loss

def _pl_sections(company, start, end):
    totals = ledger.totals(company, start=start, end=end)
    groups = {"income": [], "direct": [], "expense": [], "other_income": [], "other_expense": []}
    for a in _accounts(company):
        if a.type not in ("income", "expense"):
            continue
        net = ledger.natural_balance(a, *totals.get(a.id, (ZERO, ZERO)))
        if net == 0 and a.id not in totals:
            continue
        key = {S.INCOME: "income", S.DIRECT_COST: "direct", S.EXPENSE: "expense", S.DEPRECIATION: "expense",
               S.OTHER_INCOME: "other_income", S.OTHER_EXPENSE: "other_expense"}[a.subtype]
        groups[key].append(_row(a, net))
    return groups


def _sum(rows):
    return sum((r["amount"] for r in rows), ZERO)


def profit_and_loss(company, start, end):
    g = _pl_sections(company, start, end)
    income, direct, expense = _sum(g["income"]), _sum(g["direct"]), _sum(g["expense"])
    other_in, other_out = _sum(g["other_income"]), _sum(g["other_expense"])
    gross = income - direct
    operating = gross - expense
    return {"start": start, "end": end, "income": g["income"], "total_income": income, "direct_costs": g["direct"],
            "total_direct_costs": direct, "gross_profit": gross, "expenses": g["expense"], "total_expenses": expense,
            "operating_profit": operating, "other_income": g["other_income"], "total_other_income": other_in,
            "other_expenses": g["other_expense"], "total_other_expenses": other_out, "net_profit": operating + other_in - other_out}


def net_profit(company, start, end):
    """Profit between two dates; `start` None means from the beginning of the books."""
    return profit_and_loss(company, start or datetime.date(1900, 1, 1), end)["net_profit"] if end else ZERO


# --------------------------------------------------------------------------- balance sheet

def balance_sheet(company, as_of):
    totals = ledger.totals(company, end=as_of)
    sections = {k: [] for k in ("current_assets", "fixed_assets", "current_liabilities", "long_term_liabilities", "equity")}
    where = {S.CASH: "current_assets", S.BANK: "current_assets", S.RECEIVABLE: "current_assets", S.OTHER_CURRENT_ASSET: "current_assets",
             S.TAX_ASSET: "current_assets", S.FIXED_ASSET: "fixed_assets", S.ACCUMULATED_DEPRECIATION: "fixed_assets",
             S.PAYABLE: "current_liabilities", S.TAX_LIABILITY: "current_liabilities", S.OTHER_CURRENT_LIABILITY: "current_liabilities",
             S.LONG_TERM_LIABILITY: "long_term_liabilities", S.PARTNER_CAPITAL: "equity", S.PARTNER_CURRENT: "equity", S.RETAINED: "equity"}
    for a in _accounts(company):
        if a.subtype not in where:
            continue
        net = ledger.natural_balance(a, *totals.get(a.id, (ZERO, ZERO)))
        if net == 0:
            continue
        sections[where[a.subtype]].append(_row(a, net))  # accumulated depreciation is a credit balance: already negative here
    fy = financial_year_for(company, as_of)
    if fy:
        prior = net_profit(company, None, fy.start_date - datetime.timedelta(days=1))
        current = profit_and_loss(company, fy.start_date, as_of)["net_profit"]
    else:
        prior, current = ZERO, net_profit(company, None, as_of)
    sections["equity"] += [{"account_id": "", "code": "", "name": "Retained earnings: previous years", "amount": prior},
                           {"account_id": "", "code": "", "name": "Profit for the current year", "amount": current}]
    t = {k: _sum(v) for k, v in sections.items()}
    assets = t["current_assets"] + t["fixed_assets"]
    liabilities = t["current_liabilities"] + t["long_term_liabilities"]
    return {"as_of": as_of, "financial_year": fy.label if fy else "", **sections,
            "total_current_assets": t["current_assets"], "total_fixed_assets": t["fixed_assets"], "total_assets": assets,
            "total_current_liabilities": t["current_liabilities"], "total_long_term_liabilities": t["long_term_liabilities"],
            "total_liabilities": liabilities, "total_equity": t["equity"], "total_liabilities_and_equity": liabilities + t["equity"],
            "balanced": assets == liabilities + t["equity"]}


# --------------------------------------------------------------------------- cash flow (indirect method)

def cash_flow(company, start, end):
    """Profit adjusted for what did not move cash, grouped into operating, investing and financing, ending on the real cash change."""
    day_before = start - datetime.timedelta(days=1)
    opening, closing = ledger.totals(company, end=day_before), ledger.totals(company, end=end)
    profit = profit_and_loss(company, start, end)["net_profit"]
    operating, investing, financing = [], [], []
    for a in _accounts(company):
        if a.type in ("income", "expense") or a.subtype in CASH_SUBTYPES:
            continue
        delta = _debit_positive(closing, a.id) - _debit_positive(opening, a.id)
        effect = -delta  # more owed to us (an asset up) uses cash; more owed by us (a liability up) provides it
        if effect == 0:
            continue
        item = {"account_id": str(a.id), "code": a.code, "name": a.name, "amount": effect}
        if a.subtype == S.ACCUMULATED_DEPRECIATION:
            item["name"] = "Depreciation (not a cash cost)"
            operating.insert(0, item)
        elif a.subtype in (S.RECEIVABLE, S.OTHER_CURRENT_ASSET, S.TAX_ASSET, S.PAYABLE, S.TAX_LIABILITY, S.OTHER_CURRENT_LIABILITY):
            item["name"] = f"Change in {a.name}"
            operating.append(item)
        elif a.subtype == S.FIXED_ASSET:
            item["name"] = f"Purchase or sale of: {a.name}"
            investing.append(item)
        else:
            financing.append(item)
    cash_accounts = [a for a in _accounts(company) if a.subtype in CASH_SUBTYPES]
    open_cash = sum((_debit_positive(opening, a.id) for a in cash_accounts), ZERO)
    close_cash = sum((_debit_positive(closing, a.id) for a in cash_accounts), ZERO)
    op, inv, fin = profit + _sum(operating), _sum(investing), _sum(financing)
    return {"start": start, "end": end, "net_profit": profit, "operating": operating, "net_operating": op, "investing": investing,
            "net_investing": inv, "financing": financing, "net_financing": fin, "net_change": op + inv + fin,
            "opening_cash": open_cash, "closing_cash": close_cash, "ties": open_cash + op + inv + fin == close_cash}


# --------------------------------------------------------------------------- dashboard

def dashboard(company, today=None):
    today = today or datetime.date.today()
    fy_start, fy_end = default_period(company, today)
    fy_end = min(fy_end, today)
    month_start = today.replace(day=1)
    totals = ledger.totals(company, end=today)
    cash_rows = [{"account_id": str(a.id), "name": a.name, "balance": ledger.natural_balance(a, *totals.get(a.id, (ZERO, ZERO)))}
                 for a in _accounts(company) if a.subtype in CASH_SUBTYPES and a.is_active]

    def balance_of(key):
        a = Account.objects.filter(company=company, system_key=key).first()
        return ledger.natural_balance(a, *totals.get(a.id, (ZERO, ZERO))) if a else ZERO

    ytd = profit_and_loss(company, fy_start, fy_end)
    month = profit_and_loss(company, month_start, today)
    months, (year, mon) = [], (today.year, today.month)
    for _ in range(6):
        months.append((year, mon))
        mon -= 1
        if mon == 0:
            year, mon = year - 1, 12
    months.reverse()
    first = datetime.date(*months[0], 1)
    inc = {m: ZERO for m in months}
    exp = {m: ZERO for m in months}
    lines = JournalLine.objects.filter(entry__company=company, entry__date__gte=first, entry__date__lte=today,
                                       account__type__in=["income", "expense"]).select_related("entry", "account")
    for l in lines:
        key = (l.entry.date.year, l.entry.date.month)
        if l.account.type == "income":
            inc[key] += l.credit - l.debit
        else:
            exp[key] += l.debit - l.credit
    breakdown = sorted(({"name": r["name"], "amount": r["amount"]} for r in ytd["direct_costs"] + ytd["expenses"] + ytd["other_expenses"]
                        if r["amount"] > 0), key=lambda r: -r["amount"])
    top, rest = breakdown[:6], breakdown[6:]
    if rest:
        top.append({"name": "Everything else", "amount": _sum(rest)})
    recent = JournalEntry.objects.filter(company=company).prefetch_related("lines")[:8]
    return {
        "today": today, "currency": company.default_currency, "period": {"start": fy_start, "end": fy_end},
        "cash": cash_rows, "cash_total": _sum([{"amount": r["balance"]} for r in cash_rows]),
        "receivable": balance_of("receivable"), "payable": balance_of("payable"),
        "ytd": {"income": ytd["total_income"] + ytd["total_other_income"], "expenses": ytd["total_direct_costs"] + ytd["total_expenses"] + ytd["total_other_expenses"], "profit": ytd["net_profit"]},
        "month": {"income": month["total_income"] + month["total_other_income"], "expenses": month["total_direct_costs"] + month["total_expenses"] + month["total_other_expenses"], "profit": month["net_profit"]},
        "monthly": [{"month": f"{y}-{m:02d}", "label": datetime.date(y, m, 1).strftime("%b"), "income": inc[(y, m)], "expenses": exp[(y, m)]} for y, m in months],
        "expense_breakdown": top,
        "recent": [{"id": str(e.id), "date": e.date, "reference": e.reference, "narration": e.narration, "source": e.source,
                    "status": e.status, "amount": sum((l.debit for l in e.lines.all()), ZERO)} for e in recent],
    }
