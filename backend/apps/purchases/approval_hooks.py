"""Connects purchase orders to the approvals module when it is installed. Without it, purchases works on its own:
a person with purchases.manage approves a waiting order through `POST orders/<id>/approve/`."""
from django.core.exceptions import ValidationError

from .models import PurchaseOrder, VendorBill, VendorPayment
from .services import bills, orders, payments


def _order(request_obj):
    return PurchaseOrder.objects.filter(pk=getattr(request_obj, "source_id", None)).first()


def _last_decision(request_obj):
    return request_obj.decisions.select_related("approver").order_by("-decided_at").first()


def _decider(request_obj):
    d = _last_decision(request_obj)
    return d.approver if d is not None else None


def _note(request_obj):
    d = _last_decision(request_obj)
    return d.comment if d is not None else ""


def on_approved(request_obj):
    order = _order(request_obj)
    if order is not None and order.status == PurchaseOrder.Status.PENDING_APPROVAL:
        orders.approve(order, _decider(request_obj) or request_obj.requested_by, _note(request_obj))


def on_rejected(request_obj):
    order = _order(request_obj)
    if order is not None and order.status == PurchaseOrder.Status.PENDING_APPROVAL:
        orders.reject(order, _decider(request_obj), _note(request_obj) or "Rejected in approvals")


def on_cancelled(request_obj):
    order = _order(request_obj)
    if order is not None:
        orders.withdraw(order, request_obj.requested_by)


def on_resubmitted(request_obj):
    order = _order(request_obj)
    if order is not None:
        orders.resubmit(order, request_obj.requested_by)


def _bill(request_obj):
    return VendorBill.objects.filter(pk=getattr(request_obj, "source_id", None)).first()


def bill_approved(request_obj):
    bill = _bill(request_obj)
    if bill is not None and bill.status == VendorBill.Status.PENDING_APPROVAL:
        bills.approve(bill, _decider(request_obj) or request_obj.requested_by, _note(request_obj))


def bill_rejected(request_obj):
    bill = _bill(request_obj)
    if bill is not None and bill.status == VendorBill.Status.PENDING_APPROVAL:
        bills.reject(bill, _decider(request_obj), _note(request_obj) or "Rejected in approvals")


def bill_cancelled(request_obj):
    bill = _bill(request_obj)
    if bill is not None:
        bills.withdraw(bill, request_obj.requested_by)


def bill_resubmitted(request_obj):
    bill = _bill(request_obj)
    if bill is not None:
        bills.resubmit(bill, request_obj.requested_by)


def describe_bill(source_id):
    try:
        bill = VendorBill.objects.filter(pk=source_id).select_related("vendor").first()
    except (ValueError, TypeError, ValidationError):
        bill = None
    if bill is None:
        return None
    return {"title": f"Vendor bill {bill.vendor_bill_no} from {bill.vendor.name}", "amount": bill.grand_total,
            "summary": f"Total {bill.grand_total}, net payable {bill.net_payable}", "url": f"/purchases/bills/{bill.pk}",
            "project_id": bill.project_id, "requested_by_ok": lambda user: user.has_code("purchases.manage")}


def describe(source_id):
    try:
        order = PurchaseOrder.objects.filter(pk=source_id).select_related("vendor").first()
    except (ValueError, TypeError, ValidationError):
        order = None
    if order is None:
        return None
    return {"title": f"Purchase order to {order.vendor.name}", "amount": order.grand_total,
            "summary": f"{len(order.record.get('lines', []))} item(s), total {order.grand_total}",
            "url": f"/purchases/orders/{order.pk}", "project_id": order.project_id,
            "requested_by_ok": lambda user: user.has_code("purchases.manage")}


def _payment(request_obj):
    try:
        return VendorPayment.objects.filter(pk=getattr(request_obj, "source_id", None)).first()
    except (ValueError, TypeError, ValidationError):
        return None


def payment_approved(request_obj):
    p = _payment(request_obj)
    if p is None:
        return
    who, note = _decider(request_obj) or request_obj.requested_by, _note(request_obj)
    if p.status == VendorPayment.Status.PENDING_APPROVAL:
        payments.approve(p, who, note)
    elif p.status == VendorPayment.Status.RECORDED and p.void_requested:
        payments.approve_void(p, who, note)


def payment_rejected(request_obj):
    p = _payment(request_obj)
    if p is None:
        return
    why = _note(request_obj) or "Rejected in approvals"
    if p.status == VendorPayment.Status.PENDING_APPROVAL:
        payments.reject(p, _decider(request_obj), why)
    elif p.void_requested:
        payments.reject_void(p, _decider(request_obj), why)


def payment_cancelled(request_obj):
    p = _payment(request_obj)
    if p is None:
        return
    if p.status == VendorPayment.Status.PENDING_APPROVAL:
        payments.withdraw(p, request_obj.requested_by)
    elif p.void_requested:
        payments.withdraw_void(p, request_obj.requested_by)


def payment_resubmitted(request_obj):
    p = _payment(request_obj)
    if p is None:
        return
    if p.status == VendorPayment.Status.REJECTED:
        payments.resubmit(p, request_obj.requested_by)
    elif p.status == VendorPayment.Status.RECORDED:
        payments.resubmit_void(p, request_obj.requested_by)


def describe_payment(source_id):
    try:
        p = VendorPayment.objects.filter(pk=source_id).select_related("vendor").first()
    except (ValueError, TypeError, ValidationError):
        p = None
    if p is None:
        return None
    if p.status == VendorPayment.Status.PENDING_APPROVAL:
        title, summary = payments.payment_title(p), f"{p.get_mode_display()}, {p.amount}"
    elif p.void_requested:
        title, summary = payments.void_title(p), f"Reason: {p.void_request_reason}"
    else:
        return None
    return {"title": title, "amount": p.amount, "summary": summary, "url": f"/purchases/payments/{p.pk}", "project_id": p.project_id,
            "requested_by_ok": lambda user: user.has_code("purchases.manage")}


def register():
    try:
        from apps.approvals import api
    except ImportError:
        return
    if hasattr(api, "register_handler"):
        api.register_handler("purchase_order", on_approved=on_approved, on_rejected=on_rejected, on_cancelled=on_cancelled,
                             on_resubmitted=on_resubmitted, label="Purchase order", required_codes=("purchases.manage",),
                             describe=describe)
        api.register_handler("vendor_bill", on_approved=bill_approved, on_rejected=bill_rejected, on_cancelled=bill_cancelled,
                             on_resubmitted=bill_resubmitted, label="Vendor bill", required_codes=("purchases.manage",),
                             describe=describe_bill)
        api.register_handler("payment", on_approved=payment_approved, on_rejected=payment_rejected, on_cancelled=payment_cancelled,
                             on_resubmitted=payment_resubmitted, label="Payment", required_codes=("finance.manage",),
                             describe=describe_payment, chain=True)
