"""Purchases API. Every endpoint checks the permission first, then validates, then works inside a transaction."""
import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from rest_framework import status as http
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounting.models import Account
from apps.company.api import current_company, private_file_response
from apps.company.models import CompanyBankAccount
from apps.company.services.quotations import plain_number

from .models import (PurchaseOrder, PurchaseRequest, Vendor, VendorBill, VendorPayment, VendorQuotation)
from .services import amounts, bills, orders, payments, posting, pricing, reports, sourcing, vendors
from .services.common import ZERO, money, parse_date

MAX_ROWS = 500


def need(request, code):
    if not request.user.has_code(code):
        raise PermissionDenied("Your role does not allow this.")


def need_view(request):
    need(request, "purchases.view")


def need_manage(request):
    need(request, "purchases.manage")


def can_manage(request):
    return request.user.has_code("purchases.manage")


def _get(model, pk, company, label, select=()):
    try:
        qs = model.objects.filter(pk=pk, company=company)
        if select:
            qs = qs.select_related(*select)
        obj = qs.first()
    except (ValueError, DjangoValidationError):
        obj = None
    if obj is None:
        raise NotFound(f"No such {label}.")
    return obj


def _ref(value, model, field, company, label):
    """A record named in a request body: None when empty, a 400 with a field message when it does not exist."""
    if value in (None, ""):
        return None
    try:
        obj = model.objects.filter(pk=value, company=company).first()
    except (ValueError, DjangoValidationError):
        obj = None
    if obj is None:
        raise ValidationError({field: f"{label} not found."})
    return obj


def _uuid_or_none(value, field):
    if value in (None, ""):
        return None
    import uuid
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ValidationError({field: "Not a valid project."})


def _flag(p, name):
    return p.get(name) in ("true", "1", "yes")


def mask(value):
    value = value or ""
    return "X" * max(len(value) - 4, 0) + value[-4:]


def _rows(qs, fn, today=None):
    count = qs.count()
    return count, [fn(x) for x in qs[:MAX_ROWS]]


# --------------------------------------------------------------------------- vendors

def vendor_dict(v, balance=None, *, full=False):
    """`full` adds the PAN and bank details unmasked; only the detail page asks for it, and only for people who manage."""
    b = balance or {"billed": ZERO, "paid": ZERO, "outstanding": ZERO, "overdue": ZERO, "advance": ZERO, "opening": ZERO,
                    "bills_open": 0}
    out = {f: getattr(v, f) for f in vendors.FIELDS if f not in ("bank_account_number", "pan", "bank_ifsc")}
    out.update({
        "id": str(v.id), "code": v.code, "address_lines": v.address_lines, "created_at": v.created_at,
        "pan": v.pan if full else mask(v.pan), "bank_ifsc": v.bank_ifsc if full else mask(v.bank_ifsc),
        "bank_account_number": v.bank_account_number if full else v.masked_bank_account,
        "bank_account_masked": v.masked_bank_account, "has_bank_details": bool(v.bank_account_last4 or v.bank_ifsc),
        "billed": b["billed"], "paid": b["paid"], "outstanding": b["outstanding"], "overdue": b["overdue"],
        "advance": b["advance"], "net_payable": amounts.net_payable(b), "opening_outstanding": b["opening"],
        "opening_posted": vendors.opening_posted(v),
    })
    return out


def vendor_list_row(v, balance):
    d = vendor_dict(v, balance)
    for key in ("notes", "address_line1", "address_line2", "bank_account_holder", "bank_name"):
        d.pop(key, None)
    return d


class VendorCollectionView(APIView):
    def get(self, request):
        need_view(request)
        company = current_company()
        qs = Vendor.objects.filter(company=company)
        p = request.query_params
        if p.get("active") in ("true", "false"):
            qs = qs.filter(is_active=p["active"] == "true")
        if p.get("q"):
            q = p["q"]
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(gstin__icontains=q) | Q(email__icontains=q)
                           | Q(contact_person__icontains=q) | Q(phone__icontains=q))
        balances = amounts.vendor_balances(company)
        rows = [vendor_list_row(v, balances.get(v.pk)) for v in qs]
        if _flag(p, "owing"):
            rows = [r for r in rows if r["outstanding"] > 0]
        return Response(rows)

    @transaction.atomic
    def post(self, request):
        need_manage(request)
        company = current_company()
        v = vendors.create(company, request.user, request.data)
        return Response(vendor_dict(v, amounts.vendor_balances(company, vendor_id=v.pk).get(v.pk), full=True), status=http.HTTP_201_CREATED)


class VendorLookupView(APIView):
    """`[{id, code, name, gstin}]` of active vendors, for pickers in other modules."""

    def get(self, request):
        if not (request.user.has_code("purchases.view") or request.user.has_code("accounting.view")):
            raise PermissionDenied("Your role does not allow this.")
        qs = Vendor.objects.filter(company=current_company(), is_active=True).order_by("name")
        return Response([{"id": str(v.id), "code": v.code, "name": v.name, "gstin": v.gstin} for v in qs])


class VendorDetailView(APIView):
    def _get(self, pk):
        company = current_company()
        return _get(Vendor, pk, company, "vendor"), company

    def get(self, request, pk):
        need_view(request)
        v, company = self._get(pk)
        return Response(vendor_dict(v, amounts.vendor_balances(company, vendor_id=v.pk).get(v.pk), full=can_manage(request)))

    @transaction.atomic
    def patch(self, request, pk):
        need_manage(request)
        v, company = self._get(pk)
        data = dict(request.data)
        # A masked value sent back by a form that did not touch the field means "unchanged".
        if str(data.get("bank_account_number", "")).startswith("X"):
            data.pop("bank_account_number")
        for key in ("pan", "bank_ifsc"):
            if key in data and data[key] == mask(getattr(v, key)) and data[key].startswith("X"):
                data.pop(key)
        v = vendors.update(v, request.user, data)
        return Response(vendor_dict(v, amounts.vendor_balances(company, vendor_id=v.pk).get(v.pk), full=True))

    def delete(self, request, pk):
        need_manage(request)
        v, _company = self._get(pk)
        vendors.delete(v, request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class VendorStatementView(APIView):
    def get(self, request, pk):
        need_view(request)
        company = current_company()
        v = _get(Vendor, pk, company, "vendor")
        rows = amounts.statement(v)
        return Response({"vendor": vendor_dict(v, amounts.vendor_balances(company, vendor_id=v.pk).get(v.pk)), "rows": rows})


class VendorOpenItemsView(APIView):
    """What can be paid now: bills with a balance (and the opening balance), plus payments with an unapplied amount."""

    def get(self, request, pk):
        need_view(request)
        company = current_company()
        v = _get(Vendor, pk, company, "vendor")
        items = [{"key": k, "label": label, "due_date": due, "balance": balance} for k, label, due, balance in payments.open_items(v)]
        advances = [payment_dict(p) for p in _payment_qs(company).filter(vendor=v, status="recorded") if amounts.unapplied(p) > 0]
        return Response({"owed": items, "advances": advances})


# --------------------------------------------------------------------------- purchase requests

def request_dict(r):
    return {"id": str(r.id), "number": r.number, "title": r.title, "status": r.status, "status_label": r.get_status_display(),
            "request_date": r.request_date, "needed_by": r.needed_by, "items": r.items, "estimated_total": r.estimated_total,
            "requested_by_name": r.requested_by_name, "notes": r.notes, "project": str(r.project_id) if r.project_id else None,
            "decision_note": r.decision_note, "order": str(r.converted_order_id) if r.converted_order_id else None,
            "quotations": r.quotations.count(), "created_at": r.created_at}


def _request_args(request, company):
    d = request.data
    return dict(title=d.get("title"), items=d.get("items"), request_date=parse_date(d.get("request_date"), "request_date", True),
                needed_by=parse_date(d.get("needed_by"), "needed_by"), notes=d.get("notes") or "",
                project_id=_uuid_or_none(d.get("project"), "project"), requested_by_name=d.get("requested_by_name") or "")


class RequestCollectionView(APIView):
    def get(self, request):
        need_view(request)
        qs = PurchaseRequest.objects.filter(company=current_company())
        p = request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("q"):
            qs = qs.filter(Q(title__icontains=p["q"]) | Q(number__icontains=p["q"]) | Q(requested_by_name__icontains=p["q"]))
        count, rows = _rows(qs, request_dict)
        counts = {r["status"]: r["n"] for r in PurchaseRequest.objects.filter(company=current_company()).values("status").annotate(
            n=Count("id"))}
        return Response({"count": count, "results": rows, "status_counts": counts})

    @transaction.atomic
    def post(self, request):
        need_manage(request)
        company = current_company()
        r = sourcing.save_request(company, request.user, **_request_args(request, company))
        return Response(request_dict(r), status=http.HTTP_201_CREATED)


class RequestDetailView(APIView):
    def get(self, request, pk):
        need_view(request)
        return Response(request_dict(_get(PurchaseRequest, pk, current_company(), "purchase request")))

    @transaction.atomic
    def patch(self, request, pk):
        need_manage(request)
        company = current_company()
        r = _get(PurchaseRequest, pk, company, "purchase request")
        args = _request_args(request, company)
        return Response(request_dict(sourcing.save_request(company, request.user, req=r, **args)))

    def delete(self, request, pk):
        need_manage(request)
        r = _get(PurchaseRequest, pk, current_company(), "purchase request")
        sourcing.delete_request(r, request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class RequestActionView(APIView):
    action = ""

    def post(self, request, pk):
        need_manage(request)
        company = current_company()
        r = _get(PurchaseRequest, pk, company, "purchase request")
        d = request.data
        if self.action == "submit":
            sourcing.submit_request(r, request.user)
        elif self.action == "approve":
            sourcing.approve_request(r, request.user, d.get("note") or "")
        elif self.action == "reject":
            sourcing.reject_request(r, request.user, d.get("change_reason"))
        elif self.action == "convert":
            vendor = _ref(d.get("vendor"), Vendor, "vendor", company, "Vendor")
            if vendor is None:
                raise ValidationError({"vendor": "Choose the vendor to order from."})
            order = sourcing.convert_request(r, request.user, vendor)
            return Response({"request": request_dict(PurchaseRequest.objects.get(pk=r.pk)), "order": order_dict(_order(order.pk, company))},
                            status=http.HTTP_201_CREATED)
        return Response(request_dict(PurchaseRequest.objects.get(pk=r.pk)))


# --------------------------------------------------------------------------- vendor quotations

def quotation_dict(q, *, detail=False, today=None):
    key = sourcing.display_status(q, today)
    out = {"id": str(q.id), "number": q.number, "vendor_ref": q.vendor_ref, "vendor": {"id": str(q.vendor_id), "name": q.vendor.name},
           "request": {"id": str(q.request_id), "number": q.request.number, "title": q.request.title} if q.request_id else None,
           "quote_date": q.quote_date, "valid_until": q.valid_until, "status": q.status, "status_key": key,
           "status_label": dict(VendorQuotation.Status.choices)[key], "taxable_total": q.taxable_total, "tax_total": q.tax_total,
           "grand_total": q.grand_total, "delivery_days": q.delivery_days, "payment_terms": q.payment_terms,
           "order": str(q.converted_order_id) if q.converted_order_id else None, "created_at": q.created_at}
    if True:
        out.update({"record": q.record, "form": q.form, "notes": q.notes})
    return out


def _quotation_args(request, company, existing=None):
    d = request.data
    vendor = _ref(d.get("vendor"), Vendor, "vendor", company, "Vendor") or (existing.vendor if existing else None)
    if vendor is None:
        raise ValidationError({"vendor": "Choose the vendor."})
    req = _ref(d.get("request"), PurchaseRequest, "request", company, "Purchase request") if "request" in d else (existing.request if existing else None)
    return dict(vendor=vendor, items=d.get("items"), quote_date=parse_date(d.get("quote_date"), "quote_date", True),
                valid_until=parse_date(d.get("valid_until"), "valid_until"), request=req, vendor_ref=d.get("vendor_ref") or "",
                delivery_days=d.get("delivery_days"), payment_terms=d.get("payment_terms") or "", notes=d.get("notes") or "")


def _quotation_qs(company):
    return VendorQuotation.objects.filter(company=company).select_related("vendor", "request")


class QuotationCollectionView(APIView):
    def get(self, request):
        need_view(request)
        company = current_company()
        today = datetime.date.today()
        qs = _quotation_qs(company)
        p = request.query_params
        if p.get("vendor"):
            qs = qs.filter(vendor_id=p["vendor"])
        if p.get("request"):
            qs = qs.filter(request_id=p["request"])
        if p.get("status") == "expired":
            qs = qs.filter(status="received", valid_until__lt=today)
        elif p.get("status") == "received":
            qs = qs.filter(status="received").filter(Q(valid_until__isnull=True) | Q(valid_until__gte=today))
        elif p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("q"):
            qs = qs.filter(Q(number__icontains=p["q"]) | Q(vendor__name__icontains=p["q"]) | Q(vendor_ref__icontains=p["q"]))
        count, rows = _rows(qs, lambda q: quotation_dict(q, today=today))
        return Response({"count": count, "results": rows})

    @transaction.atomic
    def post(self, request):
        need_manage(request)
        company = current_company()
        q = sourcing.save_quotation(company, request.user, **_quotation_args(request, company))
        return Response(quotation_dict(_quotation_qs(company).get(pk=q.pk)), status=http.HTTP_201_CREATED)


class QuotationDetailView(APIView):
    def get(self, request, pk):
        need_view(request)
        return Response(quotation_dict(_get(VendorQuotation, pk, current_company(), "quotation", ("vendor", "request")), detail=True))

    @transaction.atomic
    def patch(self, request, pk):
        need_manage(request)
        company = current_company()
        q = _get(VendorQuotation, pk, company, "quotation", ("vendor", "request"))
        sourcing.save_quotation(company, request.user, quotation=q, **_quotation_args(request, company, q))
        return Response(quotation_dict(_quotation_qs(company).get(pk=pk), detail=True))

    def delete(self, request, pk):
        need_manage(request)
        q = _get(VendorQuotation, pk, current_company(), "quotation", ("vendor",))
        sourcing.delete_quotation(q, request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class QuotationActionView(APIView):
    action = ""

    def post(self, request, pk):
        need_manage(request)
        company = current_company()
        q = _get(VendorQuotation, pk, company, "quotation", ("vendor", "request"))
        if self.action == "accept":
            sourcing.accept_quotation(q, request.user)
        elif self.action == "reject":
            sourcing.reject_quotation(q, request.user, request.data.get("change_reason"))
        elif self.action == "convert":
            order = sourcing.convert_quotation(q, request.user)
            return Response({"quotation": quotation_dict(_quotation_qs(company).get(pk=pk)), "order": order_dict(_order(order.pk, company))},
                            status=http.HTTP_201_CREATED)
        return Response(quotation_dict(_quotation_qs(company).get(pk=pk)))


class QuotationCompareView(APIView):
    """Quotations received for one purchase request, side by side, with the lowest total marked."""

    def get(self, request):
        need_view(request)
        company = current_company()
        req = _ref(request.query_params.get("request"), PurchaseRequest, "request", company, "Purchase request")
        if req is None:
            raise ValidationError({"request": "Choose the purchase request to compare quotations for."})
        today = datetime.date.today()
        quotes = [q for q in _quotation_qs(company).filter(request=req).exclude(status="rejected")]
        rows = [quotation_dict(q, today=today) for q in quotes]
        live = [r for r in rows if r["status_key"] in ("received", "accepted")]
        lowest = min((r["grand_total"] for r in live), default=None)
        for r in rows:
            r["is_lowest"] = lowest is not None and r["status_key"] in ("received", "accepted") and r["grand_total"] == lowest
            r["difference"] = money(r["grand_total"] - lowest) if lowest is not None else ZERO
        rows.sort(key=lambda r: (r["grand_total"], r["vendor"]["name"]))
        return Response({"request": request_dict(req), "quotations": rows, "lowest_total": lowest})


# --------------------------------------------------------------------------- purchase orders

def _order_qs(company):
    return PurchaseOrder.objects.filter(company=company).select_related("vendor", "issued_document", "source_request", "source_quotation")


def _order(pk, company):
    return _order_qs(company).get(pk=pk)


def order_dict(o, *, detail=True):
    issued = o.issued_document
    out = {"id": str(o.id), "number": o.number, "status": o.status, "status_label": o.get_status_display(),
           "vendor": {"id": str(o.vendor_id), "name": o.vendor.name, "gstin": o.vendor.gstin, "code": o.vendor.code},
           "order_date": o.order_date, "expected_delivery": o.expected_delivery, "taxable_total": o.taxable_total,
           "tax_total": o.tax_total, "grand_total": o.grand_total, "reference": o.reference,
           "project": str(o.project_id) if o.project_id else None, "decision_note": o.decision_note,
           "issued": {"id": str(issued.id), "reference_number": issued.reference_number, "has_print_file": bool(issued.print_file)} if issued else None,
           "request": {"id": str(o.source_request_id), "number": o.source_request.number} if o.source_request_id else None,
           "quotation": {"id": str(o.source_quotation_id), "number": o.source_quotation.number} if o.source_quotation_id else None,
           "created_at": o.created_at}
    if detail:
        got, billed = orders.received_quantities(o), orders.billed_quantities(o)
        lines = []
        for i, line in enumerate(o.record.get("lines", [])):
            lines.append({**line, "index": i, "received": plain_number(got.get(i, ZERO)), "billed": plain_number(billed.get(i, ZERO))})
        out.update({"approval_managed": orders.approval_managed(), "needs_approval": orders.needs_approval(o),
                    "form": o.form, "record": {**o.record, "lines": lines}, "delivery_address": o.delivery_address,
                    "cancel_reason": o.cancel_reason, "approved_at": o.approved_at,
                    "approved_by": o.approved_by.get_username() if o.approved_by_id else "",
                    "receipts": [{"id": str(r.id), "date": r.date, "lines": r.lines, "notes": r.notes} for r in o.receipts.all()],
                    "bills": [{"id": str(b.id), "number": b.number, "vendor_bill_no": b.vendor_bill_no, "status": b.status,
                               "grand_total": b.grand_total} for b in o.bills.exclude(status="void")]})
    return out


def _order_args(request, company, existing=None):
    d = request.data
    vendor = _ref(d.get("vendor"), Vendor, "vendor", company, "Vendor") or (existing.vendor if existing else None)
    if vendor is None:
        raise ValidationError({"vendor": "Choose the vendor."})
    order_date = parse_date(d.get("order_date"), "order_date", True) if "order_date" in d or existing is None else existing.order_date
    form = d["form"] if "form" in d else (existing.form if existing else {})
    if "items" in d and "form" not in d:
        form = {**(existing.form if existing else {}), "items": d["items"], **{k: d[k] for k in ("terms", "notes") if k in d}}
    return dict(vendor=vendor, form=form, order_date=order_date,
                expected_delivery=parse_date(d.get("expected_delivery"), "expected_delivery") if "expected_delivery" in d
                else (existing.expected_delivery if existing else None),
                project_id=_uuid_or_none(d.get("project"), "project") if "project" in d else (existing.project_id if existing else None),
                reference=d.get("reference", existing.reference if existing else "") or "",
                delivery_address=d.get("delivery_address", existing.delivery_address if existing else "") or "")


class OrderCollectionView(APIView):
    def get(self, request):
        need_view(request)
        company = current_company()
        qs = _order_qs(company)
        p = request.query_params
        if p.get("vendor"):
            qs = qs.filter(vendor_id=p["vendor"])
        if p.get("project"):
            qs = qs.filter(project_id=p["project"])
        if p.get("status") == "open":
            qs = qs.filter(status__in=PurchaseOrder.OPEN)
        elif p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("q"):
            qs = qs.filter(Q(number__icontains=p["q"]) | Q(vendor__name__icontains=p["q"]) | Q(reference__icontains=p["q"]))
        if p.get("from"):
            qs = qs.filter(order_date__gte=parse_date(p["from"], "from"))
        if p.get("to"):
            qs = qs.filter(order_date__lte=parse_date(p["to"], "to"))
        count, rows = _rows(qs, lambda o: order_dict(o, detail=False))
        counts = {r["status"]: r["n"] for r in PurchaseOrder.objects.filter(company=company).values("status").annotate(
            n=Count("id"))}
        return Response({"count": count, "results": rows, "total_amount": qs.exclude(status__in=("cancelled",)).aggregate(
            t=Sum("grand_total"))["t"] or ZERO, "status_counts": counts})

    @transaction.atomic
    def post(self, request):
        need_manage(request)
        company = current_company()
        o = orders.save_draft(company, request.user, **_order_args(request, company))
        return Response(order_dict(_order(o.pk, company)), status=http.HTTP_201_CREATED)


class OrderDetailView(APIView):
    def get(self, request, pk):
        need_view(request)
        return Response(order_dict(_get_order(pk, current_company())))

    @transaction.atomic
    def patch(self, request, pk):
        need_manage(request)
        company = current_company()
        o = _get_order(pk, company)
        orders.save_draft(company, request.user, order=o, **_order_args(request, company, o))
        return Response(order_dict(_order(pk, company)))

    def delete(self, request, pk):
        need_manage(request)
        orders.delete(_get_order(pk, current_company()), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


def _get_order(pk, company):
    try:
        o = _order_qs(company).filter(pk=pk).first()
    except (ValueError, DjangoValidationError):
        o = None
    if o is None:
        raise NotFound("No such purchase order.")
    return o


class OrderActionView(APIView):
    action = ""

    def post(self, request, pk):
        need_manage(request)
        company = current_company()
        o = _get_order(pk, company)
        d, user = request.data, request.user
        if self.action == "submit":
            orders.submit(o, user)
        elif self.action == "approve":
            if orders.approval_managed():
                raise ValidationError("Purchase orders are decided in the Approvals module.")
            orders.approve(o, user, d.get("note") or "")
        elif self.action == "reject":
            if orders.approval_managed():
                raise ValidationError("Purchase orders are decided in the Approvals module.")
            orders.reject(o, user, d.get("change_reason"))
        elif self.action == "issue":
            orders.issue(o, user)
        elif self.action == "receive":
            orders.receive(o, user, date=parse_date(d.get("date"), "date", True), lines=d.get("lines"), notes=d.get("notes") or "")
        elif self.action == "close":
            orders.close(o, user, d.get("change_reason") or "")
        elif self.action == "cancel":
            orders.cancel(o, user, d.get("change_reason"))
        elif self.action == "bill":
            bill = bills.from_order(o, user)
            return Response(bill_dict(_bill(bill.pk, company)), status=http.HTTP_201_CREATED)
        return Response(order_dict(_order(pk, company)))


class OrderPdfView(APIView):
    def get(self, request, pk):
        need_view(request)
        o = _get_order(pk, current_company())
        issued = o.issued_document
        if issued is None:
            raise NotFound("This order has not been issued yet, so there is no PDF.")
        plain = request.query_params.get("variant") == "print" and issued.print_file
        field = issued.print_file if plain else issued.file
        name = f"{o.number.replace('/', '-')}{'-print' if plain else ''}.pdf"
        return private_file_response(field, filename=name, attachment=_flag(request.query_params, "download"), mime_type="application/pdf")


# --------------------------------------------------------------------------- bills

def _bill_qs(company):
    return amounts.bills_with_amounts(VendorBill.objects.filter(company=company)).select_related("vendor", "order")


def _bill(pk, company):
    return _bill_qs(company).get(pk=pk)


def bill_dict(b, *, detail=True, today=None):
    key, label = amounts.display_status(b, today)
    out = {"id": str(b.id), "number": b.number, "vendor_bill_no": b.vendor_bill_no, "status": b.status, "status_key": key,
           "status_label": label, "vendor": {"id": str(b.vendor_id), "name": b.vendor.name, "gstin": b.vendor.gstin},
           "bill_date": b.bill_date, "due_date": b.due_date, "taxable_total": b.taxable_total, "tax_total": b.tax_total,
           "grand_total": b.grand_total, "tds_section": b.tds_section, "tds_rate": b.tds_rate, "tds_amount": b.tds_amount,
           "net_payable": b.net_payable, "itc_status": b.itc_status, "itc_label": b.get_itc_status_display(),
           "paid": b.paid_amount, "balance": max(amounts.balance_of(b), ZERO) if b.status == VendorBill.Status.OPEN else None,
           "order": {"id": str(b.order_id), "number": b.order.number} if b.order_id else None,
           "project": str(b.project_id) if b.project_id else None, "created_at": b.created_at,
           "decision_note": b.decision_note, "submitted_at": b.submitted_at}
    if detail:
        out.update({"form": b.form, "record": b.record, "notes": b.notes, "void_reason": b.void_reason, "voided_at": b.voided_at,
                    "approval_managed": bills.approval_managed(),
                    "expense_account": str(b.expense_account_id) if b.expense_account_id else None,
                    "payments": [{"id": str(a.payment_id), "number": a.payment.number, "date": a.payment.date, "amount": a.amount,
                                  "mode": a.payment.get_mode_display(), "status": a.payment.status}
                                 for a in b.allocations.select_related("payment").order_by("payment__date")]})
    return out


def _bill_args(request, company, existing=None):
    d = request.data
    vendor = _ref(d.get("vendor"), Vendor, "vendor", company, "Vendor") or (existing.vendor if existing else None)
    if vendor is None:
        raise ValidationError({"vendor": "Choose the vendor."})
    order = _ref(d.get("order"), PurchaseOrder, "order", company, "Purchase order") if "order" in d else (existing.order if existing else None)
    items = d["items"] if "items" in d else (existing.form.get("items") if existing else None)
    return dict(
        vendor=vendor, order=order, items=items,
        vendor_bill_no=d.get("vendor_bill_no", existing.vendor_bill_no if existing else ""),
        bill_date=parse_date(d.get("bill_date"), "bill_date", True) if "bill_date" in d or existing is None else existing.bill_date,
        due_date=parse_date(d.get("due_date"), "due_date") if "due_date" in d else (existing.due_date if existing else None),
        itc_status=d.get("itc_status", existing.itc_status if existing else "eligible"),
        tds_section=d.get("tds_section", existing.tds_section if existing else ""),
        tds_rate=d.get("tds_rate", existing.tds_rate if existing else None),
        expense_account=d.get("expense_account", str(existing.expense_account_id) if existing and existing.expense_account_id else None),
        project_id=_uuid_or_none(d.get("project"), "project") if "project" in d else (existing.project_id if existing else None),
        notes=d.get("notes", existing.notes if existing else "") or "")


class BillCollectionView(APIView):
    def get(self, request):
        need_view(request)
        company = current_company()
        today = datetime.date.today()
        qs = _bill_qs(company)
        p = request.query_params
        if p.get("vendor"):
            qs = qs.filter(vendor_id=p["vendor"])
        if p.get("project"):
            qs = qs.filter(project_id=p["project"])
        if p.get("order"):
            qs = qs.filter(order_id=p["order"])
        if p.get("itc"):
            qs = qs.filter(itc_status=p["itc"])
        if p.get("status"):
            qs = amounts.apply_status_filter(qs, p["status"], today)
        if p.get("q"):
            qs = qs.filter(Q(number__icontains=p["q"]) | Q(vendor_bill_no__icontains=p["q"]) | Q(vendor__name__icontains=p["q"]))
        if p.get("from"):
            qs = qs.filter(bill_date__gte=parse_date(p["from"], "from"))
        if p.get("to"):
            qs = qs.filter(bill_date__lte=parse_date(p["to"], "to"))
        count = qs.count()
        live = qs.filter(status=VendorBill.Status.OPEN)
        rows = [bill_dict(b, detail=False, today=today) for b in qs[:MAX_ROWS]]
        return Response({"count": count, "results": rows, "total_amount": live.aggregate(t=Sum("grand_total"))["t"] or ZERO,
                         "total_balance": sum((max(amounts.balance_of(b), ZERO) for b in live), ZERO)})

    @transaction.atomic
    def post(self, request):
        need_manage(request)
        company = current_company()
        b = bills.save_draft(company, request.user, **_bill_args(request, company))
        if request.data.get("record") is True:
            bills.record(b, request.user)
        return Response(bill_dict(_bill(b.pk, company)), status=http.HTTP_201_CREATED)


def _get_bill(pk, company):
    try:
        b = _bill_qs(company).filter(pk=pk).first()
    except (ValueError, DjangoValidationError):
        b = None
    if b is None:
        raise NotFound("No such bill.")
    return b


class BillDetailView(APIView):
    def get(self, request, pk):
        need_view(request)
        return Response(bill_dict(_get_bill(pk, current_company())))

    @transaction.atomic
    def patch(self, request, pk):
        need_manage(request)
        company = current_company()
        b = _get_bill(pk, company)
        bills.save_draft(company, request.user, bill=b, **_bill_args(request, company, b))
        if request.data.get("record") is True:
            bills.record(b, request.user)
        return Response(bill_dict(_bill(pk, company)))

    def delete(self, request, pk):
        need_manage(request)
        bills.delete(_get_bill(pk, current_company()), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class BillActionView(APIView):
    action = ""

    def post(self, request, pk):
        need_manage(request)
        company = current_company()
        b = _get_bill(pk, company)
        if self.action == "record":
            bills.record(b, request.user)
        elif self.action == "void":
            bills.void(b, request.data.get("change_reason"), request.user)
        elif self.action == "withdraw":
            bills.take_back(b, request.user)
        elif self.action in ("approve", "reject"):
            if bills.approval_managed():
                raise ValidationError("Vendor bills waiting for approval are decided in the Approvals module.")
            if self.action == "approve":
                bills.approve(b, request.user, request.data.get("note") or "")
            else:
                bills.reject(b, request.user, request.data.get("change_reason"))
        return Response(bill_dict(_bill(pk, company)))


class WorkingView(APIView):
    """Lines, GST and TDS for a document being written. Nothing is saved or numbered."""

    def post(self, request):
        need_view(request)
        company = current_company()
        d = request.data
        vendor = _ref(d.get("vendor"), Vendor, "vendor", company, "Vendor")
        if vendor is None:
            raise ValidationError({"vendor": "Choose the vendor."})
        record = pricing.price(company, vendor, d.get("items"))
        tds_rate = d.get("tds_rate")
        tds = bills._tds(pricing.totals_of(record)["taxable_total"], d.get("tds_section") or "", tds_rate) if (d.get("tds_section") or tds_rate) else ("", ZERO, ZERO)
        grand = pricing.totals_of(record)["grand_total"]
        return Response({"record": record, "tds_amount": tds[2], "net_payable": grand - tds[2],
                         "due_date": bills.default_due_date(vendor, parse_date(d.get("bill_date"), "bill_date") or datetime.date.today())})


# --------------------------------------------------------------------------- payments

def _payment_qs(company):
    return VendorPayment.objects.filter(company=company).select_related("vendor", "paid_from").prefetch_related("allocations__bill")


def payment_dict(p, *, detail=False):
    allocated = sum((a.amount for a in p.allocations.all()), ZERO)
    out = {"id": str(p.id), "number": p.number, "date": p.date, "amount": p.amount, "mode": p.mode, "mode_label": p.get_mode_display(),
           "reference": p.reference, "status": p.status, "vendor": {"id": str(p.vendor_id), "name": p.vendor.name},
           "applied": allocated, "unapplied": money(p.amount - allocated) if p.status == "recorded" else ZERO,
           "paid_from": p.paid_from.display_name if p.paid_from_id else "", "paid_from_id": str(p.paid_from_id) if p.paid_from_id else None,
           "notes": p.notes, "project": str(p.project_id) if p.project_id else None, "created_at": p.created_at,
           "void_requested": p.void_requested, "decision_note": p.decision_note, "approval_managed": payments.approval_managed(),
           "status_label": ("Void pending approval" if p.void_requested and p.status == "recorded" else p.get_status_display())}
    if detail:
        out["void_request_reason"] = p.void_request_reason
        out["allocations"] = [{"bill": str(a.bill_id) if a.bill_id else None, "number": a.bill.number if a.bill_id else "Opening balance",
                               "amount": a.amount, "later": bool(a.batch)} for a in p.allocations.all()]
        out["void_reason"] = p.void_reason
    return out


class PaymentCollectionView(APIView):
    def get(self, request):
        need_view(request)
        company = current_company()
        qs = _payment_qs(company)
        p = request.query_params
        if p.get("vendor"):
            qs = qs.filter(vendor_id=p["vendor"])
        if p.get("status") == "void_pending":      # a recorded payment whose voiding waits for approval
            qs = qs.filter(status="recorded", void_requested=True)
        elif p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("mode"):
            qs = qs.filter(mode=p["mode"])
        if p.get("q"):
            qs = qs.filter(Q(number__icontains=p["q"]) | Q(vendor__name__icontains=p["q"]) | Q(reference__icontains=p["q"]))
        if p.get("from"):
            qs = qs.filter(date__gte=parse_date(p["from"], "from"))
        if p.get("to"):
            qs = qs.filter(date__lte=parse_date(p["to"], "to"))
        count, rows = _rows(qs, payment_dict)
        counts = {r["status"]: r["n"] for r in _payment_qs(company).order_by().values("status").annotate(n=Count("id"))}
        counts["void_pending"] = _payment_qs(company).filter(status="recorded", void_requested=True).count()
        return Response({"count": count, "results": rows, "status_counts": counts,
                         "total_amount": qs.filter(status="recorded").aggregate(t=Sum("amount"))["t"] or ZERO})

    @transaction.atomic
    def post(self, request):
        need_manage(request)
        company = current_company()
        d = request.data
        vendor = _ref(d.get("vendor"), Vendor, "vendor", company, "Vendor")
        if vendor is None:
            raise ValidationError({"vendor": "Choose the vendor."})
        account = _ref(d.get("paid_from"), CompanyBankAccount, "paid_from", company, "Bank account")
        pay = payments.record_payment(
            company, request.user, vendor=vendor, date=parse_date(d.get("date"), "date", True), amount=d.get("amount"),
            mode=d.get("mode") or "bank", reference=d.get("reference") or "", paid_from=account, notes=d.get("notes") or "",
            allocations=d.get("allocations"), auto_allocate=bool(d.get("auto_allocate")),
            project_id=_uuid_or_none(d.get("project"), "project"))
        return Response(payment_dict(_payment_qs(company).get(pk=pay.pk), detail=True), status=http.HTTP_201_CREATED)


def _get_payment(pk, company):
    try:
        p = _payment_qs(company).filter(pk=pk).first()
    except (ValueError, DjangoValidationError):
        p = None
    if p is None:
        raise NotFound("No such payment.")
    return p


class PaymentDetailView(APIView):
    def get(self, request, pk):
        need_view(request)
        return Response(payment_dict(_get_payment(pk, current_company()), detail=True))

    def delete(self, request, pk):
        need_manage(request)
        payments.delete_rejected(_get_payment(pk, current_company()), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class PaymentActionView(APIView):
    action = ""

    def post(self, request, pk):
        need_manage(request)
        company = current_company()
        p = _get_payment(pk, company)
        if self.action == "void":
            payments.void_payment(p, request.data.get("change_reason"), request.user)
        elif self.action == "apply":
            payments.apply_advance(p, request.user, allocations=request.data.get("allocations"),
                                   date=parse_date(request.data.get("date"), "date"))
        elif self.action == "withdraw":
            # a payment waiting to be recorded, or a request to void a recorded one
            (payments.take_back if p.status == "pending_approval" else payments.take_back_void)(p, request.user)
        elif self.action in ("approve", "reject"):
            if payments.approval_managed():
                raise ValidationError("Payments waiting for approval are decided in the Approvals module.")
            note = request.data.get("change_reason") or request.data.get("note")
            if p.status == "pending_approval":
                (payments.approve if self.action == "approve" else payments.reject)(p, request.user, note or "")
            else:
                (payments.approve_void if self.action == "approve" else payments.reject_void)(p, request.user, note or "")
        return Response(payment_dict(_payment_qs(company).get(pk=pk), detail=True))


# --------------------------------------------------------------------------- reports, dashboard, options

class DashboardView(APIView):
    def get(self, request):
        need_view(request)
        return Response(reports.dashboard(current_company(), parse_date(request.query_params.get("as_of"), "as_of")))


class AgeingView(APIView):
    def get(self, request):
        need_view(request)
        as_of = parse_date(request.query_params.get("as_of"), "as_of") or datetime.date.today()
        totals, rows, total = reports.ageing(current_company(), as_of)
        return Response({"as_of": as_of, "buckets": [{"key": k, "label": label, "amount": totals[k]} for k, label in reports.AGING],
                         "total": total, "rows": rows})


class PurchasesReportView(APIView):
    def get(self, request):
        need_view(request)
        company = current_company()
        today = datetime.date.today()
        p = request.query_params
        end = parse_date(p.get("to"), "to") or today
        start = parse_date(p.get("from"), "from") or end.replace(day=1) - datetime.timedelta(days=150)
        if start > end:
            raise ValidationError({"from": "The start date is after the end date."})
        return Response(reports.purchases_report(company, start, end))


class DueCalendarView(APIView):
    def get(self, request):
        need_view(request)
        today = parse_date(request.query_params.get("as_of"), "as_of") or datetime.date.today()
        return Response(reports.due_calendar(current_company(), today))


class OptionsView(APIView):
    """What the forms need: GST rates, expense accounts, TDS sections, the company's state, bank accounts."""

    def get(self, request):
        need_view(request)
        from django.conf import settings
        company = current_company()
        default = posting.default_expense_account(company)
        banks = CompanyBankAccount.objects.filter(company=company, is_active=True)
        try:
            state = pricing.company_state(company)
        except DjangoValidationError:
            state = ""
        return Response({
            "gst_rates": [str(r) for r in settings.GST_CURRENT_RATES], "currency": company.default_currency or "INR",
            "company_state_code": state,
            "expense_accounts": [{"id": str(a.id), "code": a.code, "name": a.name} for a in posting.expense_accounts(company)],
            "default_expense_account": str(default.id), "tds_sections": vendors.TDS_SECTIONS,
            "bank_accounts": [{"id": str(b.id), "name": b.display_name} for b in banks],
            "modes": [{"value": v, "label": label} for v, label in VendorPayment.Mode.choices],
            "itc": [{"value": v, "label": label} for v, label in VendorBill.Itc.choices],
        })
