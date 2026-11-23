"""What has been paid on each bill, what each vendor is owed, and a vendor's statement."""
import datetime
from decimal import Decimal

from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from .common import ZERO, money
from ..models import Vendor, VendorBill, VendorPayment, VendorPaymentAllocation

Status = VendorBill.Status
DEC = DecimalField(max_digits=16, decimal_places=2)


def bills_with_amounts(qs):
    """Annotate bills with paid_amount (payments applied, voided payments excluded) and balance_due."""
    paid = VendorPaymentAllocation.objects.filter(bill=OuterRef("pk")).exclude(
        payment__status=VendorPayment.Status.VOID).values("bill")
    total = Coalesce(Subquery(paid.annotate(t=Sum("amount")).values("t"), output_field=DEC), Value(ZERO, output_field=DEC),
                     output_field=DEC)
    return qs.annotate(paid_amount=total).annotate(balance_due=F("net_payable") - F("paid_amount"))


def balance_of(bill):
    return money(bill.net_payable - bill.paid_amount)


def display_status(bill, today=None):
    """(key, label): draft, pending_approval, void, paid, overdue, partial or open (recorded and nothing paid yet)."""
    today = today or datetime.date.today()
    if bill.status == Status.DRAFT:
        return "draft", "Draft"
    if bill.status == Status.PENDING_APPROVAL:
        return "pending_approval", "Pending approval"
    if bill.status == Status.VOID:
        return "void", "Void"
    if balance_of(bill) <= 0:
        return "paid", "Paid"
    if bill.due_date and bill.due_date < today:
        return "overdue", "Overdue"
    if bill.paid_amount > 0:
        return "partial", "Partially paid"
    return "open", "Open"


def apply_status_filter(qs, status, today):
    live = Q(status=Status.OPEN)
    if status in ("draft", "pending_approval", "void"):
        return qs.filter(status=status)
    if status == "open":
        return qs.filter(live, balance_due__gt=0)  # everything still owed
    if status == "unpaid":
        return qs.filter(live, balance_due__gt=0, paid_amount=0)
    if status == "overdue":
        return qs.filter(live, balance_due__gt=0, due_date__lt=today)
    if status == "partial":
        return qs.filter(live, balance_due__gt=0, paid_amount__gt=0)
    if status == "paid":
        return qs.filter(live, balance_due__lte=0)
    return qs


# --------------------------------------------------------------------------- vendors

def opening_outstanding(vendor):
    """What is still owed of the opening balance (payments can be applied to it like to a bill)."""
    if vendor.opening_balance <= 0:
        return ZERO
    used = VendorPaymentAllocation.objects.filter(payment__vendor=vendor, bill__isnull=True).exclude(
        payment__status=VendorPayment.Status.VOID).aggregate(t=Sum("amount"))["t"] or ZERO
    return max(money(vendor.opening_balance - used), ZERO)


def unapplied(payment):
    applied = payment.allocations.aggregate(t=Sum("amount"))["t"] or ZERO
    return money(payment.amount - applied)


def vendor_balances(company, today=None, vendor_id=None):
    """{vendor id: dict(billed, paid, outstanding, overdue, advance, opening)} for bills and payments that count."""
    today = today or datetime.date.today()
    out = {}

    def row(vid):
        return out.setdefault(vid, {"billed": ZERO, "paid": ZERO, "outstanding": ZERO, "overdue": ZERO, "advance": ZERO,
                                    "opening": ZERO, "bills_open": 0})

    qs = bills_with_amounts(VendorBill.objects.filter(company=company, status=Status.OPEN))
    if vendor_id:
        qs = qs.filter(vendor_id=vendor_id)
    for b in qs:
        r = row(b.vendor_id)
        r["billed"] += b.grand_total
        balance = max(balance_of(b), ZERO)
        r["outstanding"] += balance
        if balance > 0:
            r["bills_open"] += 1
            if b.due_date and b.due_date < today:
                r["overdue"] += balance
    pays = VendorPayment.objects.filter(company=company, status=VendorPayment.Status.RECORDED)
    if vendor_id:
        pays = pays.filter(vendor_id=vendor_id)
    allocated = {p: t for p, t in VendorPaymentAllocation.objects.filter(payment__in=pays).values_list("payment").annotate(t=Sum("amount"))}
    for p in pays:
        r = row(p.vendor_id)
        r["paid"] += p.amount
        r["advance"] += max(money(p.amount - allocated.get(p.pk, ZERO)), ZERO)
    vendors = Vendor.objects.filter(company=company, opening_balance__gt=0)
    if vendor_id:
        vendors = vendors.filter(pk=vendor_id)
    for v in vendors:
        left = opening_outstanding(v)
        r = row(v.pk)
        r["opening"] = left
        r["outstanding"] += left
        if left > 0 and v.opening_balance_date and v.opening_balance_date < today:
            r["overdue"] += left
    return out


def net_payable(b):
    """What we owe the vendor after setting their unapplied advances against what is outstanding."""
    return money(b["outstanding"] - b["advance"])


def statement(vendor):
    """Opening balance, bills and payments in date order with a running balance (what we owe the vendor)."""
    rows = []
    if vendor.opening_balance > 0:
        rows.append({"date": vendor.opening_balance_date, "sort": (vendor.opening_balance_date or datetime.date.min, 0, ""),
                     "type": "Opening balance", "number": vendor.code, "debit": ZERO, "credit": vendor.opening_balance,
                     "id": str(vendor.pk), "kind": "opening"})
    for b in VendorBill.objects.filter(vendor=vendor, status=Status.OPEN):
        rows.append({"date": b.bill_date, "sort": (b.bill_date, 1, str(b.created_at)), "type": "Bill", "number": b.number,
                     "reference": b.vendor_bill_no, "debit": ZERO, "credit": b.net_payable, "id": str(b.pk), "kind": "bill"})
    for p in VendorPayment.objects.filter(vendor=vendor, status=VendorPayment.Status.RECORDED):
        rows.append({"date": p.date, "sort": (p.date, 2, str(p.created_at)), "type": "Payment", "number": p.number,
                     "reference": p.reference, "debit": p.amount, "credit": ZERO, "id": str(p.pk), "kind": "payment"})
    rows.sort(key=lambda r: r["sort"])
    running = ZERO
    for r in rows:
        running += r["credit"] - r["debit"]
        r["balance"] = money(running)
        r.setdefault("reference", "")
        del r["sort"]
    return rows
