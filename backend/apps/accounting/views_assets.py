"""Fixed-asset endpoints: categories, assets, the purchase posting, depreciation runs, disposals and their reports."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework import status as http
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.company.api import current_company
from apps.company.models import CompanyProfileAuditEvent

from .models import Account, AssetCategory, AssetDisposal, DepreciationLine, DepreciationRun, FixedAsset
from .services import asset_reports, assets, chart, depreciation as dep, ledger

MAX_ROWS = 500
ZERO = assets.ZERO


def need(request, code):
    if not request.user.has_code(code):
        raise PermissionDenied("Your role does not allow this.")


def _date(value, label, default=None):
    if value in (None, ""):
        return default
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError({label: "Use a date in YYYY-MM-DD format."})


def _month(value, label, default=None):
    """A month as YYYY-MM (or any date in it): the first day of it."""
    if value in (None, ""):
        return default
    try:
        d = datetime.date.fromisoformat(f"{value}-01" if len(str(value)) == 7 else str(value))
    except ValueError:
        raise ValidationError({label: "Use a month in YYYY-MM format."})
    return d.replace(day=1)


def _get(model, pk, label, **extra):
    try:
        qs = model if hasattr(model, "filter") else model.objects.all()
        obj = qs.filter(pk=pk, company=current_company(), **extra).first()
    except (ValueError, DjangoValidationError):
        obj = None
    if obj is None:
        raise NotFound(f"No such {label}.")
    return obj


# --------------------------------------------------------------------------- shapes

def _opt(v):
    return str(v) if v else ""


def category_dict(c, assets_count=None):
    return {"id": str(c.pk), "name": c.name, "method": c.method, "method_label": c.get_method_display(),
            "useful_life_years": c.useful_life_years, "rate_percent": c.rate_percent, "salvage_percent": c.salvage_percent,
            "half_year_rule": c.half_year_rule, "is_active": c.is_active,
            "cost_account": {"id": str(c.cost_account_id), "code": c.cost_account.code, "name": c.cost_account.name},
            "accumulated_account": {"id": str(c.accumulated_account_id), "code": c.accumulated_account.code, "name": c.accumulated_account.name},
            "expense_account": {"id": str(c.expense_account_id), "code": c.expense_account.code, "name": c.expense_account.name},
            "assets": assets_count if assets_count is not None else c.assets.count()}


def _asset_qs(company):
    return FixedAsset.objects.filter(company=company).select_related("category").annotate(
        accum=Coalesce(Sum("depreciation_lines__amount", filter=Q(depreciation_lines__active=True)), Value(ZERO),
                       output_field=DecimalField(max_digits=16, decimal_places=2)))


def asset_dict(a, detail=False):
    accumulated = getattr(a, "accum", None)
    if accumulated is None:
        accumulated = assets.posted_depreciation(a)
    in_use = a.status == FixedAsset.Status.IN_USE
    salvage = dep.terms_of(a).salvage
    out = {"id": str(a.pk), "code": a.code, "name": a.name, "category": {"id": str(a.category_id), "name": a.category.name},
           "purchase_date": a.purchase_date, "put_to_use_date": a.put_to_use_date, "cost": a.cost, "accumulated": accumulated,
           "wdv": a.cost - accumulated if in_use else ZERO, "status": a.status, "status_label": a.get_status_display(),
           "acquisition": a.acquisition, "acquisition_label": a.get_acquisition_display(),
           "in_books": a.acquisition != FixedAsset.Acquisition.PENDING, "location": a.location, "serial_number": a.serial_number,
           "project": str(a.project_id) if a.project_id else None,
           "vendor": {"id": str(a.vendor_id), "name": a.vendor_name} if a.vendor_id else None,
           "bill": {"id": str(a.bill_id), "number": a.bill_number} if a.bill_id else None,
           "method": a.method, "method_label": a.get_method_display(), "useful_life_years": a.useful_life_years,
           "rate_percent": a.rate_percent, "salvage_percent": a.salvage_percent, "half_year_rule": a.half_year_rule,
           "salvage_value": salvage, "depreciable": a.cost - salvage, "notes": a.notes}
    if detail:
        out["has_depreciation"] = DepreciationLine.objects.filter(asset=a, active=True).exists()
        d = a.disposals.filter(status=AssetDisposal.Status.POSTED).first()
        out["disposal"] = disposal_dict(d) if d else None
    return out


def disposal_dict(d):
    return {"id": str(d.pk), "asset": {"id": str(d.asset_id), "code": d.asset.code, "name": d.asset.name}, "kind": d.kind,
            "kind_label": d.get_kind_display(), "date": d.date, "proceeds": d.proceeds, "cost": d.cost, "accumulated": d.accumulated,
            "final_depreciation": d.final_depreciation, "wdv": d.wdv, "gain_loss": d.gain_loss, "buyer": d.buyer, "notes": d.notes,
            "status": d.status, "reverse_reason": d.reverse_reason,
            "proceeds_account": d.proceeds_account.name if d.proceeds_account_id else ""}


def run_dict(r):
    return {"id": str(r.pk), "period_start": r.period_start, "period_end": r.period_end, "label": f"{r.period_start:%B %Y}",
            "total": r.total, "asset_count": r.asset_count, "status": r.status, "reverse_reason": r.reverse_reason,
            "created_at": r.created_at}


def plan_dict(p):
    return {"period_start": p["period_start"], "period_end": p["period_end"], "label": f"{p['period_start']:%B %Y}", "total": p["total"],
            "already": p["already"],
            "rows": [{"asset": {"id": str(r["asset"].pk), "code": r["asset"].code, "name": r["asset"].name},
                      "category": r["asset"].category.name, "amount": r["amount"], "accumulated": r["accumulated"], "wdv": r["wdv"]}
                     for r in p["rows"]],
            "skipped": [{"asset": {"id": str(s["asset"].pk), "code": s["asset"].code, "name": s["asset"].name}, "reason": s["reason"]}
                        for s in p["skipped"]]}


# --------------------------------------------------------------------------- categories

class CategoryCollectionView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        qs = AssetCategory.objects.filter(company=current_company()).select_related("cost_account", "accumulated_account", "expense_account")
        return Response([category_dict(c) for c in qs])

    @transaction.atomic
    def post(self, request):
        need(request, "accounting.manage")
        c = assets.save_category(current_company(), request.user, request.data)
        return Response(category_dict(c), status=http.HTTP_201_CREATED)


class CategorySetupView(APIView):
    @transaction.atomic
    def post(self, request):
        need(request, "accounting.manage")
        made = assets.setup_standard_categories(current_company(), request.user)
        return Response({"created": len(made), "categories": [category_dict(c) for c in made]})


class CategoryDetailView(APIView):
    def _find(self, pk):
        try:
            c = AssetCategory.objects.filter(pk=pk, company=current_company()).select_related(
                "cost_account", "accumulated_account", "expense_account").first()
        except (ValueError, DjangoValidationError):
            c = None
        if c is None:
            raise NotFound("No such category.")
        return c

    def get(self, request, pk):
        need(request, "accounting.view")
        return Response(category_dict(self._find(pk)))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "accounting.manage")
        c = assets.save_category(current_company(), request.user, request.data, self._find(pk))
        return Response(category_dict(c))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "accounting.manage")
        assets.delete_category(self._find(pk), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- assets

class OptionsView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        company = current_company()
        chart.ensure_chart(company)
        accounts = Account.objects.filter(company=company, is_active=True)
        pick = lambda qs: [{"id": str(a.id), "code": a.code, "name": a.name} for a in qs.order_by("code")]
        return Response({
            "categories": [category_dict(c) for c in AssetCategory.objects.filter(company=company, is_active=True)
                           .select_related("cost_account", "accumulated_account", "expense_account")],
            "money_accounts": pick(chart.money_accounts(company)),
            "payable_account": pick(accounts.filter(system_key="payable"))[0],
            "proceeds_accounts": pick(accounts.filter(subtype__in=["cash", "bank", "receivable"])),
            "fixed_asset_accounts": pick(accounts.filter(subtype="fixed_asset")),
            "accumulated_accounts": pick(accounts.filter(subtype="accumulated_depreciation")),
            "expense_accounts": pick(accounts.filter(type="expense")),
            "statuses": [{"value": v, "label": l} for v, l in FixedAsset.Status.choices],
            "methods": [{"value": v, "label": l} for v, l in AssetCategory.Method.choices],
            "kinds": [{"value": v, "label": l} for v, l in AssetDisposal.Kind.choices],
        })


class AssetCollectionView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        p = request.query_params
        qs = _asset_qs(current_company())
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("category"):
            qs = qs.filter(category_id=p["category"])
        if p.get("project"):
            qs = qs.filter(project_id=p["project"])
        if p.get("q"):
            qs = qs.filter(Q(code__icontains=p["q"]) | Q(name__icontains=p["q"]) | Q(serial_number__icontains=p["q"])
                           | Q(location__icontains=p["q"]) | Q(vendor_name__icontains=p["q"]))
        counts = {s: FixedAsset.objects.filter(company=current_company(), status=s).count() for s in FixedAsset.Status.values}
        rows = [asset_dict(a) for a in qs.order_by("code")[:MAX_ROWS]]
        return Response({"count": qs.count(), "status_counts": counts, "results": rows})

    @transaction.atomic
    def post(self, request):
        need(request, "accounting.manage")
        d = request.data
        acq = d.get("acquisition") or "later"
        a = assets.create_asset(current_company(), request.user, d, acquisition=acq, paid_from=d.get("paid_from"))
        return Response(asset_dict(_asset_qs(a.company).get(pk=a.pk), detail=True), status=http.HTTP_201_CREATED)


def _get_asset(pk):
    try:
        a = _asset_qs(current_company()).filter(pk=pk).first()
    except (ValueError, DjangoValidationError):
        a = None
    if a is None:
        raise NotFound("No such asset.")
    return a


def _fresh(pk):
    return Response(asset_dict(_get_asset(pk), detail=True))


class AssetDetailView(APIView):
    def get(self, request, pk):
        need(request, "accounting.view")
        return _fresh(pk)

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "accounting.manage")
        assets.update_asset(_get_asset(pk), request.user, request.data)
        return _fresh(pk)

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "accounting.manage")
        assets.delete_asset(_get_asset(pk), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class AcquisitionView(APIView):
    """Put the purchase in the books: `mode` "post" (Dr asset, Cr `paid_from`, default accounts payable) or "existing"."""

    @transaction.atomic
    def post(self, request, pk):
        need(request, "accounting.manage")
        a = _get_asset(pk)
        mode = request.data.get("mode") or "post"
        if mode == "post":
            assets.record_acquisition(a, request.user, request.data.get("paid_from"))
        elif mode == "existing":
            assets.mark_existing(a, request.user)
        else:
            raise ValidationError({"mode": "Choose post or existing."})
        return _fresh(pk)


class AcquisitionUndoView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "accounting.manage")
        assets.undo_acquisition(_get_asset(pk), request.user, request.data.get("change_reason"))
        return _fresh(pk)


class ScheduleView(APIView):
    def get(self, request, pk):
        need(request, "accounting.view")
        a = _get_asset(pk)
        today = datetime.date.today()
        disposal = a.disposals.filter(status=AssetDisposal.Status.POSTED).first()
        stop = disposal.date if disposal else None
        rows = assets.schedule_for(a, stop=stop, through=_date(request.query_params.get("through"), "through"))
        live = {l.period_start: l for l in DepreciationLine.objects.filter(asset=a, active=True)}
        out, years = [], {}
        for r in rows:
            posted = r["period_start"] in live
            out.append({**r, "state": "posted" if posted else ("due" if r["period_end"] <= today or r["period_start"] <= today else "upcoming"),
                        "fraction": round(float(r["fraction"]), 4)})
            years[r["fy"]] = years.get(r["fy"], ZERO) + r["charge"]
        terms = dep.terms_of(a)
        return Response({"asset": {"id": str(a.pk), "code": a.code, "name": a.name, "cost": a.cost, "salvage_value": terms.salvage,
                                   "depreciable": terms.depreciable, "method": a.method, "method_label": a.get_method_display(),
                                   "put_to_use_date": a.put_to_use_date, "status": a.status},
                         "rows": out, "years": [{"fy": y, "charge": c} for y, c in years.items()],
                         "total": sum((r["charge"] for r in rows), ZERO)})


# --------------------------------------------------------------------------- depreciation runs

class DepreciationPreviewView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        company, today = current_company(), datetime.date.today()
        first = _month(request.query_params.get("period"), "period", dep.month_start(today))
        last = _month(request.query_params.get("to"), "to", first)
        plans = assets.preview(company, first, last, today)
        return Response({"months": [plan_dict(p) for p in plans], "total": sum((p["total"] for p in plans), ZERO)})


class DepreciationRunView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        qs = DepreciationRun.objects.filter(company=current_company())
        return Response([run_dict(r) for r in qs[:MAX_ROWS]])

    @transaction.atomic
    def post(self, request):
        need(request, "accounting.manage")
        company, today = current_company(), datetime.date.today()
        first = _month(request.data.get("period"), "period")
        if first is None:
            raise ValidationError({"period": "Choose the month to depreciate."})
        last = _month(request.data.get("to"), "to", first)
        result = assets.run_depreciation(company, request.user, first, last, today)
        return Response({"runs": [run_dict(r) for r in result["runs"]], "total": result["total"], "months": result["months"],
                         "skipped": [{"asset": s["asset"].code, "reason": s["reason"]} for s in result["skipped"]]})


class DepreciationRunDetailView(APIView):
    def get(self, request, pk):
        need(request, "accounting.view")
        r = _get(DepreciationRun, pk, "depreciation run")
        out = run_dict(r)
        out["lines"] = [{"asset": {"id": str(l.asset_id), "code": l.asset.code, "name": l.asset.name}, "amount": l.amount,
                         "accumulated": l.accumulated, "active": l.active}
                        for l in r.lines.select_related("asset").order_by("asset__code")]
        return Response(out)


class DepreciationRunReverseView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "accounting.manage")
        r = assets.reverse_run(_get(DepreciationRun, pk, "depreciation run"), request.user, request.data.get("change_reason"))
        return Response(run_dict(r))


# --------------------------------------------------------------------------- disposal

class DisposalPreviewView(APIView):
    def get(self, request, pk):
        need(request, "accounting.view")
        a = _get_asset(pk)
        date = _date(request.query_params.get("date"), "date")
        if date is None:
            raise ValidationError({"date": "Enter the disposal date."})
        proceeds = assets._dec(request.query_params.get("proceeds") or 0, "proceeds", "the proceeds", minimum=Decimal("0"))
        if a.status != FixedAsset.Status.IN_USE:
            raise ValidationError("This asset has already been disposed of.")
        fig = assets.disposal_figures(a, date, proceeds)
        return Response({"final_depreciation": fig["final_depreciation"], "accumulated": fig["accumulated"], "wdv": fig["wdv"],
                         "proceeds": proceeds, "gain_loss": fig["gain_loss"], "cost": a.cost})


class DisposeView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "accounting.manage")
        d = request.data
        disposal = assets.dispose(_get_asset(pk), request.user, kind=d.get("kind"), date=d.get("date"), proceeds=d.get("proceeds"),
                                  proceeds_account=d.get("proceeds_account"), buyer=d.get("buyer") or "", notes=d.get("notes") or "")
        return Response(disposal_dict(AssetDisposal.objects.select_related("asset", "proceeds_account").get(pk=disposal.pk)),
                        status=http.HTTP_201_CREATED)


class DisposalCollectionView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        qs = AssetDisposal.objects.filter(company=current_company()).select_related("asset", "proceeds_account")
        return Response([disposal_dict(d) for d in qs[:MAX_ROWS]])


class DisposalReverseView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "accounting.manage")
        d = _get(AssetDisposal.objects.select_related("asset", "proceeds_account"), pk, "disposal")
        assets.reverse_disposal(d, request.user, request.data.get("change_reason"))
        return Response(disposal_dict(AssetDisposal.objects.select_related("asset", "proceeds_account").get(pk=d.pk)))


# --------------------------------------------------------------------------- reports

class AssetDashboardView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        return Response(asset_reports.dashboard(current_company()))


class RegisterReportView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        p = request.query_params
        as_on = _date(p.get("as_on"), "as_on", datetime.date.today())
        return Response(asset_reports.register(current_company(), as_on, category=p.get("category") or None,
                                               status=p.get("status") or None, q=p.get("q") or None, project=p.get("project") or None))


class DepreciationByYearView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        return Response(asset_reports.depreciation_by_year(current_company(), request.query_params.get("fy") or None))


class DisposalsReportView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        company = current_company()
        today = datetime.date.today()
        start = _date(request.query_params.get("from"), "from", dep.fy_start(today))
        end = _date(request.query_params.get("to"), "to", dep.fy_end(today))
        if end < start:
            raise ValidationError({"to": "The end date is before the start date."})
        return Response(asset_reports.disposals_report(company, start, end))
