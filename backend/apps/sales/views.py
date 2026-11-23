import datetime
import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from rest_framework import status as http
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.company.api import current_company
from apps.company.constants import DocType
from apps.company.models import CompanyBankAccount, CompanyProfileAuditEvent, IssuedDocument
from apps.company.services import audit, numbering

from . import quotes, reminders, services
from .models import CreditApplication, Customer, Payment, PaymentAllocation, Quotation, RecurringInvoice, SalesDocument

Action = CompanyProfileAuditEvent.Action
Kind, Status = SalesDocument.Kind, SalesDocument.Status
MAX_ROWS = 500


def need(request, code):
    if not request.user.has_code(code):
        raise PermissionDenied("Your role does not allow this.")


def _id(value, model, label, company):
    if value in (None, ""):
        return None
    try:
        obj = model.objects.filter(pk=value, company=company).first()
    except (ValueError, DjangoValidationError):
        obj = None
    if obj is None:
        raise ValidationError({label: "Not found."})
    return obj


def _uuid(value, label):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ValidationError({label: "Not a valid id."})


def _has_project(data):
    return "project" in data or "project_id" in data


def _project_arg(data):
    """The project a request names; `project_id` is accepted as well as `project`."""
    value = data.get("project")
    return value if value not in (None, "") else data.get("project_id")


def _date(value, label, required=False):
    if value in (None, ""):
        if required:
            raise ValidationError({label: "Enter a date."})
        return None
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError({label: "Use a date in YYYY-MM-DD format."})


# --------------------------------------------------------------------------- customers

CUSTOMER_FIELDS = ("display_name", "legal_name", "customer_type", "contact_name", "email", "phone", "gst_treatment", "gstin",
                   "pan", "address_line1", "address_line2", "city", "state", "state_code", "postal_code", "country",
                   "payment_terms_days", "notes", "is_active")


def customer_dict(c, balance=None):
    b = balance or {"outstanding": services.ZERO, "advance": services.ZERO, "credit": services.ZERO}
    return {
        "id": str(c.id), "reference_code": c.reference_code, **{f: getattr(c, f) for f in CUSTOMER_FIELDS},
        "address_lines": c.address_lines, "outstanding": str(b["outstanding"]), "advance": str(b["advance"]),
        "unused_credit": str(b["credit"]), "net_balance": str(services.net_balance(b)), "created_at": c.created_at,
    }


def _snapshot(c):
    return {f: getattr(c, f) for f in CUSTOMER_FIELDS}


def _apply_customer(c, data):
    for f in CUSTOMER_FIELDS:
        if f in data:
            value = data[f]
            if f == "payment_terms_days":
                value = None if value in (None, "") else value
            elif f == "is_active":
                if not isinstance(value, bool):
                    raise ValidationError({"is_active": "Send true or false."})
            elif isinstance(value, str):
                value = value.strip()
            elif value is None:
                value = ""
            setattr(c, f, value)
    try:
        c.full_clean(exclude=["reference_code"])
    except DjangoValidationError as exc:
        raise ValidationError(exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages})


class CustomerCollectionView(APIView):
    def get(self, request):
        need(request, "sales.view")
        company = current_company()
        qs = Customer.objects.filter(company=company)
        p = request.query_params
        if p.get("active") in ("true", "false"):
            qs = qs.filter(is_active=p["active"] == "true")
        if p.get("q"):
            qs = qs.filter(Q(display_name__icontains=p["q"]) | Q(legal_name__icontains=p["q"]) | Q(gstin__icontains=p["q"])
                           | Q(reference_code__icontains=p["q"]) | Q(email__icontains=p["q"]))
        balances = services.customer_balances(company)
        rows = [customer_dict(c, balances.get(c.pk)) for c in qs]
        if p.get("owing") == "true":
            rows = [r for r in rows if float(r["outstanding"]) > 0]
        return Response(rows)

    @transaction.atomic
    def post(self, request):
        need(request, "sales.manage")
        company = current_company()
        c = Customer(company=company)
        _apply_customer(c, request.data)
        c.reference_code = numbering.allocate_number(company, DocType.CUSTOMER, actor=request.user).reference_number
        c.save()
        services._audit(company, request.user, "customer", c.pk, Action.CREATED, new=_snapshot(c))
        return Response(customer_dict(c), status=http.HTTP_201_CREATED)


class CustomerDetailView(APIView):
    def _get(self, pk):
        c = Customer.objects.filter(pk=pk, company=current_company()).first()
        if c is None:
            raise NotFound("No such customer.")
        return c

    def get(self, request, pk):
        need(request, "sales.view")
        c = self._get(pk)
        return Response(customer_dict(c, services.customer_balances(c.company).get(c.pk)))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "sales.manage")
        c = self._get(pk)
        before = _snapshot(c)
        _apply_customer(c, request.data)
        if before["is_active"] and not c.is_active:
            owed = services.customer_balances(c.company).get(c.pk, {}).get("outstanding", services.ZERO)
            if owed > 0:
                raise ValidationError({"is_active": f"This customer still owes {owed}; it cannot be switched off."})
        c.save()
        after = _snapshot(c)
        if before != after:
            action = Action.UPDATED
            if before["is_active"] != after["is_active"]:
                action = Action.ACTIVATED if after["is_active"] else Action.DEACTIVATED
            services._audit(c.company, request.user, "customer", c.pk, action, previous=before, new=after)
        return Response(customer_dict(c, services.customer_balances(c.company).get(c.pk)))


class CustomerStatementView(APIView):
    def get(self, request, pk):
        need(request, "sales.view")
        c = Customer.objects.filter(pk=pk, company=current_company()).first()
        if c is None:
            raise NotFound("No such customer.")
        rows = services.statement(c)
        return Response({"customer": customer_dict(c, services.customer_balances(c.company).get(c.pk)),
                         "rows": [{**r, "debit": str(r["debit"]), "credit": str(r["credit"]), "balance": str(r["balance"])}
                                  for r in rows]})


# --------------------------------------------------------------------------- documents

def _issued(d):
    i = d.issued_document
    return {"id": str(i.id), "reference_number": i.reference_number, "has_print_file": bool(i.print_file)} if i else None


def _project_map(rows):
    """{project id: {id, code, name}} for the tagged records in `rows`, in one query."""
    return services.project_refs(current_company(), [r.project_id for r in rows])


def document_dict(d, *, detail=False, today=None, projects=None):
    if projects is None:
        projects = _project_map([d])
    key, label = services.display_status(d, today)
    out = {
        "id": str(d.id), "kind": d.kind, "kind_label": d.get_kind_display(), "status": d.status, "status_key": key,
        "status_label": label, "number": d.number, "issue_date": d.issue_date, "due_date": d.due_date,
        "customer": {"id": str(d.customer_id), "name": d.customer.display_name}, "currency": d.currency,
        "taxable_total": str(d.taxable_total), "tax_total": str(d.tax_total), "grand_total": str(d.grand_total),
        "paid": str(services.money(d.paid_amount + d.credited_amount)) if d.is_receivable else None,
        "balance": str(max(services.balance_of(d), services.ZERO)) if d.is_receivable and d.status == Status.ISSUED else None,
        "credit_left": str(services.credit_left(d)) if d.kind == Kind.CREDIT_NOTE and d.status == Status.ISSUED else None,
        "against": {"id": str(d.against_id), "number": d.against.number} if d.against_id else None,
        "reason": d.reason, "issued": _issued(d), "created_at": d.created_at,
        "project": str(d.project_id) if d.project_id else None,
        "project_id": str(d.project_id) if d.project_id else None,
        "project_ref": (projects or {}).get(d.project_id),
        "recurring_id": str(d.recurring_id) if d.recurring_id else None, "recurring_run_date": d.recurring_run_date,
    }
    if detail:
        profile = RecurringInvoice.objects.filter(pk=d.recurring_id).first() if d.recurring_id else None
        out.update({
            "recurring": ({"id": str(profile.pk), "name": profile.name, "reference_code": profile.reference_code,
                           "run_date": d.recurring_run_date} if profile else None),
            "reminders": [{
                "id": str(r.id), "status": r.status, "status_label": r.get_status_display(), "days_offset": r.days_offset,
                "label": reminders.offset_label(r.days_offset), "subject": r.subject, "body": r.body, "to_email": r.to_email,
                "amount_due": str(r.amount_due), "note": r.note, "created_at": r.created_at,
                "by": r.created_by.get_username() if r.created_by_id else "",
            } for r in d.reminders.select_related("created_by").order_by("-created_at")] if d.is_receivable else [],
        })
        out.update({
            "form": d.form, "record": d.record, "void_reason": d.void_reason, "voided_at": d.voided_at,
            "source_quotation": (
                {"id": str(d.source_quotation_id), "number": d.source_quotation.reference_number} if d.source_quotation_id else None),
            "payments": [{
                "id": str(a.payment_id), "reference_code": a.payment.reference_code, "date": a.payment.date,
                "mode": a.payment.get_mode_display(), "amount": str(a.amount), "status": a.payment.status,
            } for a in d.payment_allocations.select_related("payment").order_by("payment__date")],
            "credits_received": [{
                "id": str(c.credit_note_id), "number": c.credit_note.number, "amount": str(c.amount),
                "status": c.credit_note.status,
            } for c in d.credits_received.select_related("credit_note")],
            "credit_applications": [{
                "id": str(c.document_id), "number": c.document.number, "amount": str(c.amount),
            } for c in d.credit_applications.select_related("document")],
            "notes": [{"id": str(n.id), "kind": n.kind, "kind_label": n.get_kind_display(), "number": n.number,
                       "status": n.status, "grand_total": str(n.grand_total)}
                      for n in d.notes.order_by("created_at")],
        })
    return out


def _document_qs(company):
    return services.with_amounts(SalesDocument.objects.filter(company=company)).select_related(
        "customer", "against", "issued_document", "source_quotation")


def _get_document(pk, company):
    try:
        d = _document_qs(company).filter(pk=pk).first()
    except (ValueError, DjangoValidationError):
        d = None
    if d is None:
        raise NotFound("No such document.")
    return d


def _prepare(request, company, *, existing=None):
    """Customer, dates and form from a request body, checked."""
    data = request.data
    customer = _id(data.get("customer"), Customer, "customer", company) or (existing.customer if existing else None)
    if customer is None:
        raise ValidationError({"customer": "Choose the customer."})
    issue_date = _date(data.get("issue_date"), "issue_date", required=True) if "issue_date" in data or existing is None \
        else existing.issue_date
    due_date = _date(data.get("due_date"), "due_date")
    against = _id(data.get("against"), SalesDocument, "against", company) if "against" in data else (existing.against if existing else None)
    return customer, issue_date, due_date, against


class DocumentCollectionView(APIView):
    def get(self, request):
        need(request, "sales.view")
        company = current_company()
        today = datetime.date.today()
        p = request.query_params
        qs = _document_qs(company)
        if p.get("kind"):
            qs = qs.filter(kind=p["kind"])
        if p.get("customer"):
            qs = qs.filter(customer_id=p["customer"])
        if p.get("project"):
            qs = qs.filter(project_id=_uuid(p["project"], "project"))
        if p.get("recurring"):
            qs = qs.filter(recurring_id=_uuid(p["recurring"], "recurring"))
        if p.get("status"):
            qs = services.apply_status_filter(qs, p["status"], today)
        if p.get("q"):
            qs = qs.filter(Q(number__icontains=p["q"]) | Q(customer__display_name__icontains=p["q"]) | Q(reason__icontains=p["q"]))
        if p.get("from"):
            qs = qs.filter(issue_date__gte=_date(p["from"], "from"))
        if p.get("to"):
            qs = qs.filter(issue_date__lte=_date(p["to"], "to"))
        count = qs.count()
        rows = list(qs[:MAX_ROWS])
        refs = _project_map(rows)
        return Response({
            "count": count, "results": [document_dict(d, today=today, projects=refs) for d in rows],
            "total_amount": str(qs.aggregate(t=Sum("grand_total"))["t"] or services.ZERO),
            "total_balance": str(sum((max(services.balance_of(d), services.ZERO) for d in qs.filter(
                kind__in=SalesDocument.RECEIVABLE, status=Status.ISSUED)), services.ZERO)),
        })

    @transaction.atomic
    def post(self, request):
        need(request, "sales.manage")
        company = current_company()
        customer, issue_date, due_date, against = _prepare(request, company)
        quote = None
        if request.data.get("source_quotation"):
            quote = IssuedDocument.objects.filter(pk=request.data["source_quotation"], company=company,
                                                  document_type=DocType.QUOTATION).first()
            if quote is None:
                raise ValidationError({"source_quotation": "Quotation not found."})
        kind = request.data.get("kind")
        doc = services.save_draft(
            company, request.user, kind=kind, customer=customer, form=request.data.get("form") or {}, issue_date=issue_date,
            due_date=due_date, against=against, reason=request.data.get("reason") or "", source_quotation=quote,
            project_id=_project_arg(request.data) or None)
        return Response(document_dict(_get_document(doc.pk, company), detail=True), status=http.HTTP_201_CREATED)


class DocumentDetailView(APIView):
    def get(self, request, pk):
        need(request, "sales.view")
        return Response(document_dict(_get_document(pk, current_company()), detail=True))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "sales.manage")
        company = current_company()
        doc = _get_document(pk, company)
        customer, issue_date, due_date, against = _prepare(request, company, existing=doc)
        services.save_draft(
            company, request.user, kind=doc.kind, customer=customer,
            form=request.data["form"] if "form" in request.data else doc.form, issue_date=issue_date,
            due_date=due_date if "due_date" in request.data else doc.due_date, against=against,
            reason=request.data.get("reason", doc.reason) or "", source_quotation=doc.source_quotation, doc=doc,
            project_id=(_project_arg(request.data) or None) if _has_project(request.data) else doc.project_id)
        return Response(document_dict(_get_document(pk, company), detail=True))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "sales.manage")
        doc = _get_document(pk, current_company())
        if doc.status != Status.DRAFT:
            raise ValidationError("Only a draft can be deleted. Void an issued document instead.")
        services._audit(doc.company, request.user, "sales_document", doc.pk, Action.DELETED,
                        previous={"kind": doc.kind, "customer": doc.customer.display_name, "status": "draft"})
        doc.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class DocumentIssueView(APIView):
    def post(self, request, pk):
        need(request, "sales.issue")
        company = current_company()
        doc = _get_document(pk, company)
        services.issue(doc, request.user)
        return Response(document_dict(_get_document(pk, company), detail=True))


class DocumentVoidView(APIView):
    def post(self, request, pk):
        need(request, "sales.issue")
        company = current_company()
        doc = _get_document(pk, company)
        services.void(doc, request.data.get("change_reason"), request.user)
        return Response(document_dict(_get_document(pk, company), detail=True))


class DocumentCloneView(APIView):
    """A new draft with the same customer and items. `kind` turns, say, a proforma into an invoice."""

    @transaction.atomic
    def post(self, request, pk):
        need(request, "sales.manage")
        company = current_company()
        doc = _get_document(pk, company)
        kind = request.data.get("kind") or doc.kind
        if kind in (Kind.CREDIT_NOTE, Kind.DEBIT_NOTE):
            raise ValidationError({"kind": "Credit and debit notes are raised from an invoice."})
        form = dict(doc.form)
        form.pop("valid_until", None)
        new = services.save_draft(company, request.user, kind=kind, customer=doc.customer, form=form,
                                  issue_date=datetime.date.today(), source_quotation=doc.source_quotation,
                                  project_id=doc.project_id)
        return Response(document_dict(_get_document(new.pk, company), detail=True), status=http.HTTP_201_CREATED)


class DocumentWorkingView(APIView):
    """Lines and totals for a document being written. Nothing is saved or numbered."""

    def post(self, request):
        need(request, "sales.view")
        company = current_company()
        customer, issue_date, due_date, _against = _prepare(request, company)
        kind = request.data.get("kind")
        if kind not in Kind.values:
            raise ValidationError({"kind": "Unknown document type."})
        due = due_date or services.default_due_date(company, customer, issue_date, kind)
        record = services.work_out(company, customer, kind, services._clean_form(request.data.get("form") or {}), issue_date,
                                   due if kind in SalesDocument.RECEIVABLE else None)
        return Response({"record": record, "due_date": due})


class DocumentOptionsView(APIView):
    def get(self, request):
        need(request, "sales.view")
        kind = request.query_params.get("kind") or Kind.INVOICE
        if kind not in Kind.values:
            raise ValidationError({"kind": "Unknown document type."})
        return Response(services.form_options(current_company(), kind))


class QuotationPrefillView(APIView):
    """Items of an issued quotation, ready to start an invoice or proforma from."""

    def get(self, request, issued_id):
        need(request, "sales.view")
        company = current_company()
        q = IssuedDocument.objects.filter(pk=issued_id, company=company, document_type=DocType.QUOTATION).first()
        rec = (q.snapshot or {}).get("quotation") if q else None
        if not rec:
            raise NotFound("This quotation has no itemised details to copy.")
        items = [{"description": line["description"], "hsn_sac": line.get("hsn_sac", ""), "unit": line.get("unit", ""),
                  "quantity": line["quantity"], "rate": line["rate"],
                  "discount_percent": line.get("discount_percent", "0"), "gst_rate": line.get("gst_rate")}
                 for line in rec["lines"]]
        cust = rec["customer"]
        match = None
        if cust.get("gstin"):
            match = Customer.objects.filter(company=company, gstin=cust["gstin"], is_active=True).first()
        match = match or Customer.objects.filter(company=company, is_active=True, display_name__iexact=cust["name"]).first()
        return Response({
            "quotation": {"id": str(q.id), "number": q.reference_number}, "customer_name": cust["name"],
            "matched_customer": str(match.id) if match else None,
            "customer": {"name": cust["name"], "gstin": cust.get("gstin", ""), "state_code": cust.get("state_code", ""),
                         "address": cust.get("address", [])},
            "gst_applied": rec.get("gst_applied", False), "place_of_supply": rec.get("place_of_supply"),
            "supply_type": rec.get("supply_type", ""), "currency": rec.get("currency", "INR"),
            "form": {"items": items, "subject": rec.get("subject", ""), "details": rec.get("details", []),
                     "sections": rec.get("sections", []), "enquiry_ref": rec.get("enquiry_ref", ""),
                     "notes": rec.get("notes", ""), "terms": rec.get("terms", ""), "intro": rec.get("intro", ""),
                     "show_signature": rec.get("show_signature", False)},
        })


# --------------------------------------------------------------------------- payments

def payment_dict(p, *, detail=False, projects=None):
    if projects is None:
        projects = _project_map([p])
    allocated = sum((a.amount for a in p.allocations.all()), services.ZERO)
    out = {
        "id": str(p.id), "reference_code": p.reference_code, "date": p.date, "amount": str(p.amount),
        "mode": p.mode, "mode_label": p.get_mode_display(), "reference": p.reference, "status": p.status,
        "customer": {"id": str(p.customer_id), "name": p.customer.display_name},
        "applied": str(allocated), "unapplied": str(services.money(p.amount - allocated)),
        "deposit_account": p.deposit_account.display_name if p.deposit_account_id else "", "notes": p.notes,
        "project": str(p.project_id) if p.project_id else None,
        "project_id": str(p.project_id) if p.project_id else None, "project_ref": projects.get(p.project_id),
    }
    out.update({"void_requested": p.void_requested, "approval_managed": services.payment_approval_managed(),
                "status_label": "Void" if p.status == "void" else ("Void pending approval" if p.void_requested else "Recorded")})
    if detail:
        out["allocations"] = [{"document": str(a.document_id), "number": a.document.number, "amount": str(a.amount)}
                              for a in p.allocations.select_related("document")]
        out["void_reason"] = p.void_reason
        out["void_request_reason"] = p.void_request_reason
        out["decision_note"] = p.decision_note
    return out


def _payment_qs(company):
    return Payment.objects.filter(company=company).select_related("customer", "deposit_account").prefetch_related("allocations")


class PaymentCollectionView(APIView):
    def get(self, request):
        need(request, "sales.view")
        qs = _payment_qs(current_company())
        p = request.query_params
        if p.get("customer"):
            qs = qs.filter(customer_id=p["customer"])
        if p.get("project"):
            qs = qs.filter(project_id=_uuid(p["project"], "project"))
        if p.get("status") == "void_pending":      # a recorded payment whose voiding waits for approval
            qs = qs.filter(status="recorded", void_requested=True)
        elif p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("q"):
            qs = qs.filter(Q(reference_code__icontains=p["q"]) | Q(customer__display_name__icontains=p["q"]) | Q(reference__icontains=p["q"]))
        rows = list(qs[:MAX_ROWS])
        refs = _project_map(rows)
        company = current_company()
        counts = {r["status"]: r["n"] for r in Payment.objects.filter(company=company).values("status").annotate(n=Count("id"))}
        counts["void_pending"] = Payment.objects.filter(company=company, status="recorded", void_requested=True).count()
        return Response({"count": qs.count(), "results": [payment_dict(x, projects=refs) for x in rows], "status_counts": counts,
                         "total_amount": str(qs.filter(status="recorded").aggregate(t=Sum("amount"))["t"] or services.ZERO)})

    @transaction.atomic
    def post(self, request):
        need(request, "sales.manage")
        company = current_company()
        d = request.data
        customer = _id(d.get("customer"), Customer, "customer", company)
        if customer is None:
            raise ValidationError({"customer": "Choose the customer."})
        account = _id(d.get("deposit_account"), CompanyBankAccount, "deposit_account", company)
        allocations = d.get("allocations") or []
        if not isinstance(allocations, list):
            raise ValidationError({"allocations": "Send a list."})
        payment = services.record_payment(
            company, request.user, customer=customer, date=_date(d.get("date"), "date", required=True), amount=d.get("amount"),
            mode=d.get("mode") or "bank_transfer", reference=d.get("reference") or "", deposit_account=account,
            notes=d.get("notes") or "", allocations=allocations, auto_allocate=bool(d.get("auto_allocate")),
            project_id=_project_arg(d) or None)
        return Response(payment_dict(_payment_qs(company).get(pk=payment.pk), detail=True), status=http.HTTP_201_CREATED)


class PaymentDetailView(APIView):
    def get(self, request, pk):
        need(request, "sales.view")
        p = _payment_qs(current_company()).filter(pk=pk).first()
        if p is None:
            raise NotFound("No such payment.")
        return Response(payment_dict(p, detail=True))


class PaymentVoidView(APIView):
    """`void`: void a payment (above an approval rule this only asks for approval); `withdraw`: take that request back;
    `approve` / `reject`: decide it here, only when the approvals module is not installed."""
    action = "void"

    def post(self, request, pk):
        need(request, "sales.manage")
        company = current_company()
        p = _payment_qs(company).filter(pk=pk).first()
        if p is None:
            raise NotFound("No such payment.")
        if self.action == "void":
            services.void_payment(p, request.data.get("change_reason"), request.user)
        elif self.action == "withdraw":
            services.take_back_void(p, request.user)
        else:
            if services.payment_approval_managed():
                raise ValidationError("Voids waiting for approval are decided in the Approvals module.")
            if self.action == "approve":
                services.approve_void(p, request.user, request.data.get("note") or "")
            else:
                services.reject_void(p, request.user, request.data.get("change_reason") or request.data.get("note"))
        return Response(payment_dict(_payment_qs(company).get(pk=pk), detail=True))


class CreditApplyView(APIView):
    @transaction.atomic
    def post(self, request):
        need(request, "sales.manage")
        company = current_company()
        note = _id(request.data.get("credit_note"), SalesDocument, "credit_note", company)
        doc = _id(request.data.get("document"), SalesDocument, "document", company)
        if note is None or doc is None:
            raise ValidationError({"credit_note": "Choose a credit note and an invoice."})
        services.apply_credit(note, doc, request.data.get("amount"), request.user)
        return Response(document_dict(_get_document(doc.pk, company), detail=True))


# --------------------------------------------------------------------------- reports

class DashboardView(APIView):
    def get(self, request):
        need(request, "sales.view")
        return Response(services.dashboard(current_company()))


class ReceivablesReportView(APIView):
    def get(self, request):
        need(request, "sales.view")
        totals, rows = services.receivables_report(current_company())
        return Response({
            "buckets": [{"key": k, "label": label, "amount": str(totals[k])} for k, label, _ in services.AGING],
            "total": str(sum(totals.values(), services.ZERO)),
            "rows": [{**r, **{k: str(r[k]) for k, _, _ in services.AGING}, "total": str(r["total"])} for r in rows],
        })


class OpenItemsView(APIView):
    """Invoices and debit notes of a customer with something left to pay, and credit notes with something left to use."""

    def get(self, request, pk):
        need(request, "sales.view")
        company = current_company()
        customer = _id(pk, Customer, "customer", company)
        if customer is None:
            raise NotFound("No such customer.")
        qs = _document_qs(company).filter(customer=customer, status=Status.ISSUED)
        owed = [document_dict(d) for d in qs.filter(kind__in=SalesDocument.RECEIVABLE, balance_due__gt=0).order_by("due_date", "issue_date")]
        credits = [document_dict(d) for d in qs.filter(kind=Kind.CREDIT_NOTE) if services.credit_left(d) > 0]
        return Response({"owed": owed, "credits": credits})


# --------------------------------------------------------------------------- quotations held for approval

def _quote_qs(company):
    return Quotation.objects.filter(company=company).select_related("created_by", "issued_document")


def _get_quote(pk, company):
    try:
        q = _quote_qs(company).filter(pk=pk).first()
    except (ValueError, DjangoValidationError):
        q = None
    if q is None:
        raise NotFound("No such quotation.")
    return q


def _need_quotes(request):
    if not request.user.has_code("documents.issue"):
        raise PermissionDenied("Your role may not issue quotations.")


def _may_change(request, q):
    """The person who prepared a quotation, or someone who manages sales, may edit, send, withdraw or delete it."""
    if not (request.user.has_code("sales.manage") or (q.created_by_id and q.created_by_id == request.user.pk)):
        raise PermissionDenied("Only the person who prepared this quotation, or someone who manages sales, can do this.")


class QuoteCollectionView(APIView):
    """Quotations waiting for approval, or sent back to draft. (An issued quotation is an issued document.)"""

    def get(self, request):
        _need_quotes(request)
        qs = _quote_qs(current_company())
        p = request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("q"):
            qs = qs.filter(customer_name__icontains=p["q"])
        counts = {r["status"]: r["n"] for r in Quotation.objects.filter(company=current_company()).values("status").annotate(n=Count("id"))}
        return Response({"count": qs.count(), "status_counts": counts, "results": [quotes.as_dict(q) for q in qs[:MAX_ROWS]]})


class QuoteDetailView(APIView):
    def get(self, request, pk):
        _need_quotes(request)
        return Response(quotes.as_dict(_get_quote(pk, current_company()), detail=True))

    def patch(self, request, pk):
        _need_quotes(request)
        company = current_company()
        q = _get_quote(pk, company)
        _may_change(request, q)
        quotes.update_draft(q, request.user, content=request.data.get("content", q.content),
                            issue_date=_date(request.data.get("issue_date"), "issue_date") or q.issue_date)
        return Response(quotes.as_dict(_get_quote(pk, company), detail=True))

    def delete(self, request, pk):
        _need_quotes(request)
        q = _get_quote(pk, current_company())
        _may_change(request, q)
        quotes.delete(q, request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class QuoteActionView(APIView):
    """issue (send a draft; above a rule it waits), withdraw (take it back), approve / reject (only without approvals)."""
    action = ""

    def post(self, request, pk):
        _need_quotes(request)
        company = current_company()
        q = _get_quote(pk, company)
        if self.action in ("issue", "withdraw"):
            _may_change(request, q)
            (quotes.send if self.action == "issue" else quotes.take_back)(q, request.user)
        else:
            need(request, "sales.manage")
            if quotes.approval_managed():
                raise ValidationError("Quotations waiting for approval are decided in the Approvals module.")
            if self.action == "approve":
                quotes.approve(q, request.user, request.data.get("note") or "")
            else:
                quotes.reject(q, request.user, request.data.get("change_reason") or request.data.get("note"))
        return Response(quotes.as_dict(_get_quote(pk, company), detail=True))
