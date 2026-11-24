"""API for recurring invoice profiles and payment reminders (see recurring.py and reminders.py)."""
import datetime
import urllib.parse

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework import status as http
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.company.api import current_company

from . import recurring, reminders, services
from .models import PaymentReminder, RecurringInvoice, ReminderRule, SalesDocument
from .views import _document_qs, document_dict, need

RStatus = RecurringInvoice.Status


# --------------------------------------------------------------------------- recurring invoices

def schedule_label(p):
    if p.frequency != RecurringInvoice.Frequency.CUSTOM:
        return p.get_frequency_display()
    n, unit = p.interval_count, p.interval_unit[:-1] if p.interval_count == 1 else p.interval_unit
    return f"Every {n} {unit}" if n != 1 else f"Every {unit}"


def profile_dict(p, *, detail=False, today=None, projects=None):
    today = today or datetime.date.today()
    if projects is None:
        projects = services.project_refs(p.company, [p.project_id])
    is_due = p.status == RStatus.ACTIVE and p.next_run_date is not None and p.next_run_date <= today
    out = {
        "id": str(p.id), "reference_code": p.reference_code, "name": p.name,
        "customer": {"id": str(p.customer_id), "name": p.customer.display_name},
        "status": p.status, "status_label": p.get_status_display(), "mode": p.mode, "mode_label": p.get_mode_display(),
        "frequency": p.frequency, "frequency_label": p.get_frequency_display(), "schedule_label": schedule_label(p),
        "interval_unit": p.interval_unit, "interval_count": p.interval_count,
        "start_date": p.start_date, "end_date": p.end_date, "max_occurrences": p.max_occurrences,
        "next_run_date": p.next_run_date, "last_run_date": p.last_run_date, "occurrences_done": p.occurrences_done,
        "remaining": (max(p.max_occurrences - p.occurrences_done, 0) if p.max_occurrences is not None else None),
        "due_days": p.due_days, "apply_terms": p.apply_terms, "project": str(p.project_id) if p.project_id else None,
        "project_ref": projects.get(p.project_id), "last_error": p.last_error, "last_error_at": p.last_error_at,
        "last_generated_at": p.last_generated_at, "is_due": is_due, "created_at": p.created_at,
    }
    if detail:
        out.update({"form": p.form, "notes": p.notes, "terms": p.terms, "stop_reason": p.stop_reason})
        out["customer"].update({"state": p.customer.state, "state_code": p.customer.state_code, "gstin": p.customer.gstin})
        out["upcoming"] = []
        if p.next_run_date:
            out["upcoming"] = recurring.schedule_dates(
                p.start_date, recurring.profile_step(p), first=p.next_run_date, count=6, end_date=p.end_date,
                remaining=out["remaining"])
        try:  # one invoice as it would be made now: totals and the GST split for the customer's place of supply
            rec = services.work_out(p.company, p.customer, SalesDocument.Kind.INVOICE, recurring.build_form(p), p.next_run_date or today, None)
            out["preview"] = {"totals": rec["totals"], "gst_applied": rec["gst_applied"], "place_of_supply": rec["place_of_supply"],
                              "supply_type": rec["supply_type"], "currency": rec["currency"], "lines": rec["lines"]}
        except DjangoValidationError as exc:
            out["preview"] = None
            out["preview_error"] = "; ".join(exc.messages)
        children = _document_qs(p.company).filter(recurring_id=p.pk).order_by("-recurring_run_date", "-created_at")
        out["children"] = [document_dict(d, today=today, projects={}) for d in children]
    return out


def _get(pk):
    try:
        p = RecurringInvoice.objects.select_related("customer", "company").filter(pk=pk, company=current_company()).first()
    except (ValueError, DjangoValidationError):
        p = None
    if p is None:
        raise NotFound("No such recurring invoice.")
    return p


def _can_issue(request):
    return request.user.has_code("sales.issue")


class RecurringCollectionView(APIView):
    def get(self, request):
        need(request, "sales.view")
        company, today = current_company(), datetime.date.today()
        p = request.query_params
        qs = RecurringInvoice.objects.filter(company=company).select_related("customer", "company")
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("customer"):
            qs = qs.filter(customer_id=p["customer"])
        if p.get("q"):
            qs = qs.filter(Q(name__icontains=p["q"]) | Q(customer__display_name__icontains=p["q"]) | Q(reference_code__icontains=p["q"]))
        rows = list(qs.order_by("status", "next_run_date", "name")[:500])
        refs = services.project_refs(company, [r.project_id for r in rows])
        base = RecurringInvoice.objects.filter(company=company)
        return Response({
            "count": len(rows), "results": [profile_dict(r, today=today, projects=refs) for r in rows],
            "summary": {
                "active": base.filter(status=RStatus.ACTIVE).count(), "paused": base.filter(status=RStatus.PAUSED).count(),
                "due_now": recurring.due_count(company, today),
                "finished": base.filter(status__in=(RStatus.STOPPED, RStatus.COMPLETED)).count(),
            },
        })

    def post(self, request):
        need(request, "sales.manage")
        p = recurring.create(current_company(), request.user, request.data, can_issue=_can_issue(request))
        return Response(profile_dict(_get(p.pk), detail=True), status=http.HTTP_201_CREATED)


class RecurringDetailView(APIView):
    def get(self, request, pk):
        need(request, "sales.view")
        return Response(profile_dict(_get(pk), detail=True))

    def patch(self, request, pk):
        need(request, "sales.manage")
        recurring.update(_get(pk), request.user, request.data, can_issue=_can_issue(request))
        return Response(profile_dict(_get(pk), detail=True))

    def delete(self, request, pk):
        need(request, "sales.manage")
        recurring.delete(_get(pk), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class RecurringActionView(APIView):
    """pause, resume, stop and generate (the "Run now" button)."""
    action = ""

    def post(self, request, pk):
        need(request, "sales.manage")
        p = _get(pk)
        result = {}
        if self.action == "pause":
            recurring.pause(p, request.user)
        elif self.action == "resume":
            recurring.resume(p, request.user)
        elif self.action == "stop":
            recurring.stop(p, request.user, request.data.get("reason", ""))
        else:
            doc = recurring.generate_next(p, request.user, can_issue=_can_issue(request))
            result = {"invoice": {"id": str(doc.pk), "number": doc.number, "status": doc.status}}
        return Response({**profile_dict(_get(pk), detail=True), **result})


class RecurringRunDueView(APIView):
    """Create every invoice that is due today, for all active profiles."""

    def post(self, request):
        need(request, "sales.manage")
        result = recurring.run_due(current_company(), datetime.date.today(), actor=request.user, can_issue=_can_issue(request))
        return Response(result)


class RecurringPreviewView(APIView):
    """The next run dates for a schedule being typed in the form."""

    def post(self, request):
        need(request, "sales.view")
        d = request.data
        start = recurring._date(d.get("start_date"), "start_date", required=True)
        end = recurring._date(d.get("end_date"), "end_date")
        freq = d.get("frequency")
        if freq not in RecurringInvoice.Frequency.values:
            raise ValidationError({"frequency": "Choose how often the invoice repeats."})
        unit, count = d.get("interval_unit") or "", None
        if freq == RecurringInvoice.Frequency.CUSTOM:
            if unit not in RecurringInvoice.Unit.values:
                raise ValidationError({"interval_unit": "Choose days or months."})
            count = recurring._int(d.get("interval_count"), "interval_count", 1, 365 if unit == "days" else 60, required=True)
        maximum = recurring._int(d.get("max_occurrences"), "max_occurrences", 1, 1000)
        dates = recurring.schedule_dates(start, recurring.step_for(freq, unit, count), count=6, end_date=end, remaining=maximum)
        return Response({"dates": dates})


# --------------------------------------------------------------------------- reminders

def rule_dict(r):
    return {"id": str(r.id), "days_offset": r.days_offset, "label": reminders.offset_label(r.days_offset),
            "subject_template": r.subject_template, "body_template": r.body_template, "is_active": r.is_active}


def mailto(email, subject, body):
    query = urllib.parse.urlencode({"subject": subject, "body": body}, quote_via=urllib.parse.quote)
    return f"mailto:{urllib.parse.quote(email or '', safe='@')}?{query}"


def reminder_dict(r, emails=None):
    """`emails`: {reminder id: email outcome} from reminders.email_outcomes (looked up for this row when not given)."""
    if emails is None:
        emails = reminders.email_outcomes([r.pk])
    return {
        "id": str(r.id), "document": {"id": str(r.document_id), "number": r.document.number},
        "customer": {"id": str(r.document.customer_id), "name": r.document.customer.display_name},
        "rule": {"id": str(r.rule_id) if r.rule_id else None, "days_offset": r.days_offset, "label": reminders.offset_label(r.days_offset)},
        "status": r.status, "status_label": r.get_status_display(), "subject": r.subject, "body": r.body, "to_email": r.to_email,
        "amount_due": str(r.amount_due), "due_date": r.due_date, "days_overdue": r.days_overdue, "note": r.note,
        "by": r.created_by.get_username() if r.created_by_id else ("Automatic" if r.status == PaymentReminder.Status.SENT else ""),
        "created_at": r.created_at,
        "mailto": mailto(r.to_email, r.subject, r.body) if r.status == PaymentReminder.Status.SENT else "",
        "email": emails.get(str(r.pk)),
    }


class RemindersView(APIView):
    """Invoices that need a reminder today, and the text each reminder would carry."""

    def get(self, request):
        need(request, "sales.view")
        company, today = current_company(), datetime.date.today()
        rows = []
        for doc, rule in reminders.due_reminders(company, today):
            subject, body = reminders.render(doc, rule, today)
            email = doc.customer.email
            rows.append({
                "document": {"id": str(doc.pk), "number": doc.number, "issue_date": doc.issue_date},
                "customer": {"id": str(doc.customer_id), "name": doc.customer.display_name, "email": email},
                "due_date": doc.due_date, "days_overdue": max((today - doc.due_date).days, 0),
                "balance": str(max(services.balance_of(doc), services.ZERO)), "currency": doc.currency,
                "rule": {"id": str(rule.pk), "days_offset": rule.days_offset, "label": reminders.offset_label(rule.days_offset)},
                "subject": subject, "body": body, "mailto": mailto(email, subject, body),
            })
        return Response({"today": today, "count": len(rows), "results": rows})


class ReminderActionView(APIView):
    action = ""

    def post(self, request):
        need(request, "sales.manage")
        company = current_company()
        doc_id, offset = request.data.get("document"), request.data.get("days_offset")
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            raise ValidationError({"days_offset": "Send the rule's days offset."})
        if self.action == "send":
            # to, cc, subject and body are optional: what the person checked in the send dialog
            d = request.data
            row = reminders.send(company, request.user, doc_id, offset, to=d.get("to"), cc=d.get("cc"), subject=d.get("subject"),
                                 body=d.get("body"))
        else:
            row = reminders.skip(company, request.user, doc_id, offset, request.data.get("note", ""))
        row = PaymentReminder.objects.select_related("document__customer", "created_by").get(pk=row.pk)
        return Response(reminder_dict(row), status=http.HTTP_201_CREATED)


class ReminderHistoryView(APIView):
    def get(self, request):
        need(request, "sales.view")
        p = request.query_params
        qs = PaymentReminder.objects.filter(company=current_company()).select_related("document__customer", "created_by")
        if p.get("document"):
            try:
                qs = qs.filter(document_id=p["document"])
            except (ValueError, DjangoValidationError):
                raise ValidationError({"document": "Not a valid id."})
        if p.get("status") in PaymentReminder.Status.values:
            qs = qs.filter(status=p["status"])
        rows = list(qs[:200])
        emails = reminders.email_outcomes([r.pk for r in rows])
        return Response({"results": [reminder_dict(r, emails) for r in rows]})


class ReminderRuleCollectionView(APIView):
    def get(self, request):
        need(request, "sales.view")
        return Response({"results": [rule_dict(r) for r in reminders.rules_for(current_company())],
                         "placeholders": [{"key": k, "label": v} for k, v in reminders.PLACEHOLDERS.items()]})

    def post(self, request):
        need(request, "sales.manage")
        rule = reminders.create_rule(current_company(), request.user, request.data)
        return Response(rule_dict(rule), status=http.HTTP_201_CREATED)


class ReminderRuleDetailView(APIView):
    def patch(self, request, pk):
        need(request, "sales.manage")
        try:
            rule = ReminderRule.objects.select_related("company").filter(pk=pk, company=current_company()).first()
        except (ValueError, DjangoValidationError):
            rule = None
        if rule is None:
            raise NotFound("No such reminder rule.")
        return Response(rule_dict(reminders.update_rule(rule, request.user, request.data)))
