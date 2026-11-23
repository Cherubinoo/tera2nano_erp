import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from rest_framework import status as http
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounting.models import JournalLine
from apps.company.api import current_company
from apps.company.models import CompanyBankAccount

from . import constants as C
from . import exports, serializers as ser
from .models import ItcFlag, TaxFiling, TaxPayment, TdsCredit
from .services import books, filings, gst, incometax, payments, periods, registers, taxcalendar, tds
from .services.common import Action, audit_event

MAX_ROWS = 500
COMMON_SECTIONS = ["192", "194A", "194C", "194H", "194I", "194J", "194Q", "195"]


def need(request, code):
    if not request.user.has_code(code):
        raise PermissionDenied("Your role does not allow this.")


def _get(model, pk, company, label):
    try:
        obj = model.objects.filter(pk=pk, company=company).first()
    except (ValueError, DjangoValidationError):
        obj = None
    if obj is None:
        raise NotFound(f"No such {label}.")
    return obj


def _range(request, default="month"):
    """(from, to) of the request; the current month, or the financial year to date, when none is given."""
    today = datetime.date.today()
    p = request.query_params
    if default == "fy":
        _, s, e = periods.parse_fy(periods.fy_label_for(today))
        dflt = (s, min(e, today))
    else:
        dflt = (today.replace(day=1), periods.month_end(today.year, today.month))
    return periods.date_range(p.get("from"), p.get("to"), default=dflt)


def _fy(request, key="fy"):
    label = request.query_params.get(key) or periods.fy_label_for(datetime.date.today())
    periods.parse_fy(label, key)
    return label


# --------------------------------------------------------------------------- dashboards and registers

class DashboardView(APIView):
    def get(self, request):
        need(request, "tax.view")
        return Response(taxcalendar.dashboard(current_company(), fy=_fy(request)))


class SummaryView(APIView):
    def get(self, request):
        need(request, "tax.view")
        return Response(gst.summary(current_company(), _fy(request)))


class SalesRegisterView(APIView):
    def get(self, request):
        need(request, "tax.view")
        start, end = _range(request)
        return Response(registers.sales_register(current_company(), start, end))


class SalesRegisterExportView(APIView):
    def get(self, request):
        need(request, "tax.view")
        start, end = _range(request)
        section = request.query_params.get("section", "b2b")
        reg = registers.sales_register(current_company(), start, end)
        result = exports.sales_csv(reg, section)
        if result is None:
            raise ValidationError({"section": f"Choose one of: {', '.join(exports.SECTIONS)}."})
        return exports.csv_response(*result)


class PurchaseRegisterView(APIView):
    def get(self, request):
        need(request, "tax.view")
        start, end = _range(request)
        return Response(registers.purchase_register(current_company(), start, end))


class PurchaseRegisterExportView(APIView):
    def get(self, request):
        need(request, "tax.view")
        start, end = _range(request)
        return exports.csv_response(*exports.purchases_csv(registers.purchase_register(current_company(), start, end)))


class PurchaseFlagView(APIView):
    """Mark the input credit on one purchase (a journal entry) eligible or ineligible; "clear" removes the decision."""

    @transaction.atomic
    def post(self, request):
        need(request, "tax.manage")
        company = current_company()
        d = request.data
        entry_id = d.get("entry_id")
        try:
            exists = JournalLine.objects.filter(entry_id=entry_id, entry__company=company,
                                                account__system_key__in=list(books.IN_KEY.values())).exists()
        except (ValueError, DjangoValidationError):
            exists = False
        if not exists:
            raise NotFound("No such purchase in the books.")
        choice = d.get("eligibility")
        if choice not in ("eligible", "ineligible", "clear"):
            raise ValidationError({"eligibility": "Choose eligible, ineligible or clear."})
        reason = str(d.get("reason") or "").strip()
        if len(reason) > 200:
            raise ValidationError({"reason": "Use at most 200 characters."})
        if choice == "ineligible" and not reason:
            raise ValidationError({"reason": "Say why the credit cannot be claimed (for example blocked under section 17(5))."})
        flag = ItcFlag.objects.filter(company=company, entry_id=entry_id).first()
        before = {"eligibility": flag.eligibility, "reason": flag.reason} if flag else {}
        if choice == "clear":
            if flag:
                flag.delete()
            audit_event(company, request.user, "tax_itc_flag", entry_id, Action.DELETED, previous=before)
            return Response({"entry_id": str(entry_id), "eligibility": ""})
        if flag is None:
            flag = ItcFlag(company=company, entry_id=entry_id)
        flag.eligibility, flag.reason, flag.decided_by = choice, reason, request.user
        flag.save()
        audit_event(company, request.user, "tax_itc_flag", entry_id, Action.UPDATED if before else Action.CREATED, previous=before,
                    new={"eligibility": choice, "reason": reason}, reason=reason)
        return Response({"entry_id": str(entry_id), "eligibility": choice, "reason": reason})


class ReconciliationView(APIView):
    def get(self, request):
        need(request, "tax.view")
        start, end = _range(request, "fy")
        return Response(gst.reconciliation(current_company(), start, end))


class LiabilityLedgerView(APIView):
    def get(self, request):
        need(request, "tax.view")
        start, end = _range(request, "fy")
        return Response(gst.liability_ledger(current_company(), start, end))


# --------------------------------------------------------------------------- filings

class FilingCollectionView(APIView):
    def get(self, request):
        need(request, "tax.view")
        company, p, today = current_company(), request.query_params, datetime.date.today()
        qs = TaxFiling.objects.filter(company=company)
        if p.get("type"):
            qs = qs.filter(return_type=p["type"])
        if p.get("group"):
            qs = qs.filter(return_type__in=[k for k, v in C.RETURN_TYPES.items() if v["group"] == p["group"]])
        if p.get("fy"):
            periods.parse_fy(p["fy"], "fy")
            qs = qs.filter(financial_year=p["fy"])
        if p.get("q"):
            qs = qs.filter(Q(reference__icontains=p["q"]) | Q(period_label__icontains=p["q"]) | Q(arn__icontains=p["q"]))
        rows = [ser.filing_dict(f, today) for f in qs]
        counts = {"all": len(rows), "pending": 0, "prepared": 0, "filed": 0, "late": 0}
        for r in rows:
            counts[r["status"]] += 1
        if p.get("status"):
            rows = [r for r in rows if r["status"] == p["status"]]
        rows.sort(key=lambda r: (r["due_date"], r["return_type"]))
        return Response({"results": rows[:MAX_ROWS], "count": len(rows), "counts": counts})

    def post(self, request):
        need(request, "tax.manage")
        f = filings.create(current_company(), request.user, request.data)
        return Response(ser.filing_dict(f, detail=True), status=http.HTTP_201_CREATED)


class FilingGenerateView(APIView):
    def post(self, request):
        need(request, "tax.manage")
        d = request.data
        fy = d.get("financial_year") or ""
        types = d.get("types") or None
        if types is not None and not isinstance(types, list):
            raise ValidationError({"types": "Send a list of return types."})
        created, existing = filings.generate(current_company(), request.user, fy, types, bool(d.get("audit_applicable")))
        return Response({"created": len(created), "existing": existing, "results": [ser.filing_dict(f) for f in created]},
                        status=http.HTTP_201_CREATED if created else http.HTTP_200_OK)


class FilingDetailView(APIView):
    def get(self, request, pk):
        need(request, "tax.view")
        return Response(ser.filing_dict(_get(TaxFiling, pk, current_company(), "filing"), detail=True))

    def patch(self, request, pk):
        need(request, "tax.manage")
        company = current_company()
        f = filings.update(company, request.user, _get(TaxFiling, pk, company, "filing"), request.data)
        return Response(ser.filing_dict(f, detail=True))

    def delete(self, request, pk):
        need(request, "tax.manage")
        company = current_company()
        filings.delete(company, request.user, _get(TaxFiling, pk, company, "filing"), str(request.data.get("reason") or "")[:300])
        return Response(status=http.HTTP_204_NO_CONTENT)


class FilingActionView(APIView):
    action = ""

    def post(self, request, pk):
        need(request, "tax.manage")
        company = current_company()
        f = _get(TaxFiling, pk, company, "filing")
        if self.action == "prepare":
            f = filings.prepare(company, request.user, f)
        elif self.action == "file":
            f = filings.mark_filed(company, request.user, f, request.data)
        elif self.action == "reopen":
            f = filings.reopen(company, request.user, f, request.data.get("reason"))
        return Response(ser.filing_dict(f, detail=True))


class FilingSetoffView(APIView):
    def get(self, request, pk):
        need(request, "tax.view")
        company = current_company()
        f = _get(TaxFiling, pk, company, "filing")
        if f.return_type != "gstr3b":
            raise ValidationError({"detail": "Set-off belongs to a GSTR-3B return."})
        return Response(gst.settlement(company, f))

    @transaction.atomic
    def post(self, request, pk):
        need(request, "tax.manage")
        company = current_company()
        f = TaxFiling.objects.select_for_update(of=("self",)).filter(pk=pk, company=company).first()
        if f is None:
            raise NotFound("No such filing.")
        entry = gst.post_setoff(company, f, request.user)
        audit_event(company, request.user, "tax_filing", f.pk, Action.UPDATED, previous={"setoff": ""}, new={"setoff": str(entry.pk)},
                    reason="Input credit set off")
        return Response({**ser.filing_dict(f, detail=True), "settlement": gst.settlement(company, f)}, status=http.HTTP_201_CREATED)


class FilingSetoffUndoView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "tax.manage")
        company = current_company()
        f = _get(TaxFiling, pk, company, "filing")
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError({"reason": "Say why the set-off is being undone."})
        gst.undo_setoff(company, f, request.user, reason)
        audit_event(company, request.user, "tax_filing", f.pk, Action.UPDATED, previous={"setoff": "posted"}, new={"setoff": ""}, reason=reason)
        return Response(ser.filing_dict(f, detail=True))


# --------------------------------------------------------------------------- payments

class PaymentCollectionView(APIView):
    def get(self, request):
        need(request, "tax.view")
        company, p = current_company(), request.query_params
        qs = TaxPayment.objects.filter(company=company)
        if p.get("type"):
            qs = qs.filter(tax_type=p["type"])
        if p.get("fy"):
            periods.parse_fy(p["fy"], "fy")
            qs = qs.filter(financial_year=p["fy"])
        if p.get("status") in ("recorded", "void"):
            qs = qs.filter(status=p["status"])
        if p.get("q"):
            qs = qs.filter(Q(reference__icontains=p["q"]) | Q(cin__icontains=p["q"]) | Q(period_label__icontains=p["q"]) | Q(notes__icontains=p["q"]))
        rows = list(qs[:MAX_ROWS])
        total = sum((r.total for r in rows if r.status == "recorded"), books.ZERO)
        return Response({"results": [ser.payment_dict(r) for r in rows], "count": qs.count(), "total": total})

    def post(self, request):
        need(request, "tax.manage")
        pay = payments.create(current_company(), request.user, request.data)
        return Response(ser.payment_dict(pay), status=http.HTTP_201_CREATED)


class PaymentDetailView(APIView):
    def get(self, request, pk):
        need(request, "tax.view")
        return Response(ser.payment_dict(_get(TaxPayment, pk, current_company(), "payment")))

    def patch(self, request, pk):
        need(request, "tax.manage")
        company = current_company()
        p = payments.update_notes(company, request.user, _get(TaxPayment, pk, company, "payment"), request.data)
        return Response(ser.payment_dict(p))


class PaymentVoidView(APIView):
    def post(self, request, pk):
        need(request, "tax.manage")
        company = current_company()
        p = payments.void(company, request.user, _get(TaxPayment, pk, company, "payment"), request.data.get("reason"))
        return Response(ser.payment_dict(p))


# --------------------------------------------------------------------------- TDS

class TdsLedgerView(APIView):
    def get(self, request):
        need(request, "tax.view")
        start, end = _range(request, "fy")
        return Response(tds.ledger(current_company(), start, end))


class TdsCreditCollectionView(APIView):
    def get(self, request):
        need(request, "tax.view")
        company, p = current_company(), request.query_params
        qs = TdsCredit.objects.filter(company=company)
        if p.get("fy"):
            periods.parse_fy(p["fy"], "fy")
            qs = qs.filter(financial_year=p["fy"])
        if p.get("status") in TdsCredit.Status.values:
            qs = qs.filter(status=p["status"])
        if p.get("q"):
            qs = qs.filter(Q(deductor_name__icontains=p["q"]) | Q(certificate_no__icontains=p["q"]) | Q(deductor_tan__icontains=p["q"]))
        return Response({"results": [ser.credit_dict(c) for c in qs[:MAX_ROWS]], "summary": tds.receivable_summary(company, p.get("fy") or None)})

    @transaction.atomic
    def post(self, request):
        need(request, "tax.manage")
        company = current_company()
        values = tds.clean_credit(request.data)
        c = TdsCredit.objects.create(company=company, created_by=request.user, **values)
        audit_event(company, request.user, "tax_credit", c.pk, Action.CREATED, new=ser.credit_dict(c))
        return Response(ser.credit_dict(c), status=http.HTTP_201_CREATED)


class TdsCreditDetailView(APIView):
    def get(self, request, pk):
        need(request, "tax.view")
        return Response(ser.credit_dict(_get(TdsCredit, pk, current_company(), "TDS credit")))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "tax.manage")
        company = current_company()
        c = _get(TdsCredit, pk, company, "TDS credit")
        before = ser.credit_dict(c)
        for k, v in tds.clean_credit(request.data, instance=c).items():
            setattr(c, k, v)
        c.save()
        audit_event(company, request.user, "tax_credit", c.pk, Action.UPDATED, previous=before, new=ser.credit_dict(c))
        return Response(ser.credit_dict(c))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "tax.manage")
        company = current_company()
        c = _get(TdsCredit, pk, company, "TDS credit")
        snap = ser.credit_dict(c)
        c.delete()
        audit_event(company, request.user, "tax_credit", pk, Action.DELETED, previous=snap)
        return Response(status=http.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- advance tax and income tax

class IncomeTaxView(APIView):
    def _payload(self, company, fy):
        comp = incometax.compute(company, fy)
        return {"financial_year": fy, "computation": comp, "schedule": incometax.schedule(company, fy, comp=comp)}

    def get(self, request):
        need(request, "tax.view")
        return Response(self._payload(current_company(), _fy(request)))

    @transaction.atomic
    def put(self, request):
        need(request, "tax.manage")
        company, fy = current_company(), _fy(request)
        year, before, after = incometax.save_inputs(company, fy, request.data)
        audit_event(company, request.user, "tax_worksheet", year.pk, Action.UPDATED, previous=before, new=after)
        return Response(self._payload(company, fy))


# --------------------------------------------------------------------------- calendar and options

class CalendarView(APIView):
    def get(self, request):
        need(request, "tax.view")
        try:
            days = int(request.query_params.get("days", 90))
        except ValueError:
            raise ValidationError({"days": "Enter a number of days."})
        if not 1 <= days <= 400:
            raise ValidationError({"days": "Choose between 1 and 400 days."})
        return Response(taxcalendar.calendar(current_company(), days=days))


class OptionsView(APIView):
    def get(self, request):
        need(request, "tax.view")
        company, today = current_company(), datetime.date.today()
        cur = periods.parse_fy(periods.fy_label_for(today))[0]
        return Response({
            "current_fy": periods.fy_label(cur),
            "financial_years": [periods.fy_label(y) for y in range(cur + 1, cur - 5, -1)],
            "return_types": [{"value": k, "label": v["label"], "long": v["long"], "group": v["group"],
                              "kind": C.DUE_RULES[k]["kind"]} for k, v in C.RETURN_TYPES.items()],
            "payment_types": [{"value": k, "label": v, "heads": [{"value": h, "label": lbl} for h, lbl in C.PAYMENT_HEADS[k]]}
                              for k, v in C.PAYMENT_TYPES.items()],
            "banks": [{"id": str(b.id), "label": b.display_name} for b in CompanyBankAccount.objects.filter(company=company, is_active=True)],
            "sections": COMMON_SECTIONS, "gst_registered": filings.gst_registered(company), "gstin": getattr(company, "gstin", ""),
            "advance_tax": [{"instalment": n, "due_date": due, "cumulative_pct": pct} for n, due, pct in periods.advance_tax_dates(periods.fy_label(cur))],
            "b2cl_threshold": C.B2CL_THRESHOLD,
        })
