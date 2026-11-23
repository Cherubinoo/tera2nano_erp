import datetime

from django.db import transaction
from django.db.models import Count, F, Min, Q, Sum
from rest_framework import status as http
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.company.api import current_company

from .models import Account, Activity, Contact, Deal, PipelineStage
from .serializers import deal_dict, quotations_for, stage_dict
from .services import common, deals as deal_service
from .services.common import ZERO
from .views_common import date_param, get_object, need, optional_object, owner_param, paginate, sorted_by, timeline, user_from

RELATED = ("stage", "account", "contact", "owner", "lead")


def _deal_qs(company):
    return Deal.objects.filter(company=company).select_related(*RELATED)


def _with_activity_counts(qs):
    return qs.annotate(open_activities=Count("activities", filter=Q(activities__status="open")),
                       next_due=Min("activities__due_date", filter=Q(activities__status="open")))


def _extra(d):
    return {"open_activities": getattr(d, "open_activities", None), "next_due": getattr(d, "next_due", None)}


def _filtered(request, qs):
    p = request.query_params
    qs = owner_param(request, qs)
    if p.get("stage"):
        qs = qs.filter(stage_id=p["stage"])
    if p.get("status") in ("open", "won", "lost"):
        qs = qs.filter(status=p["status"])
    if p.get("account"):
        qs = qs.filter(account_id=p["account"])
    if p.get("q"):
        q = p["q"].strip()
        qs = qs.filter(Q(name__icontains=q) | Q(account__name__icontains=q) | Q(reference_code__icontains=q) | Q(contact__name__icontains=q))
    start, end = date_param(request, "close_from"), date_param(request, "close_to")
    if start:
        qs = qs.filter(expected_close_date__gte=start)
    if end:
        qs = qs.filter(expected_close_date__lte=end)
    return qs


class DealCollectionView(APIView):
    def get(self, request):
        need(request, "crm.view")
        company = current_company()
        common.ensure_defaults(company)
        qs = _filtered(request, _with_activity_counts(_deal_qs(company)))
        qs = sorted_by(request, qs, {"created_at", "amount", "expected_close_date", "name", "probability"}, "-created_at")
        rows, count, page, size = paginate(request, qs)
        base = _filtered(request, Deal.objects.filter(company=company))
        total = base.aggregate(t=Sum("amount"))["t"] or ZERO
        weighted = sum((deal_service.weighted(a, p) for a, p in base.values_list("amount", "probability")), ZERO)
        today = datetime.date.today()
        return Response({"count": count, "page": page, "page_size": size, "total_amount": total, "total_weighted": weighted,
                         "results": [deal_dict(d, today=today, extra=_extra(d)) for d in rows]})

    @transaction.atomic
    def post(self, request):
        need(request, "crm.manage")
        company = current_company()
        common.ensure_defaults(company)
        d = request.data
        account = optional_object(Account, d.get("account"), company, "account")
        if account is None:
            raise ValidationError({"account": "Choose the account."})
        deal = deal_service.create_deal(
            company, request.user, d, account=account, contact=optional_object(Contact, d.get("contact"), company, "contact"),
            stage=optional_object(PipelineStage, d.get("stage"), company, "stage"), owner=user_from(d.get("owner")) or request.user)
        if d.get("items"):
            deal_service.set_items(deal, request.user, d["items"])
        return Response(_detail(deal.pk, company), status=http.HTTP_201_CREATED)


def _detail(pk, company):
    d = _deal_qs(company).prefetch_related("items", "history").get(pk=pk)
    out = deal_dict(d, detail=True)
    names = [d.account.name] + ([out["customer"]["name"]] if out.get("customer") else [])
    out["quotations"] = quotations_for(company, names, deal_id=d.pk)
    return out


class DealBoardView(APIView):
    """Columns for the Kanban board: every active stage with its deals. Closed columns show the last 90 days."""

    CAP = 200

    def get(self, request):
        need(request, "crm.view")
        company = current_company()
        common.ensure_defaults(company)
        today = datetime.date.today()
        qs = _filtered(request, _with_activity_counts(_deal_qs(company)))
        qs = qs.exclude(status__in=("won", "lost"), closed_on__lt=today - datetime.timedelta(days=90))
        columns = []
        by_stage = {}
        for d in qs.order_by(F("expected_close_date").asc(nulls_last=True), "-created_at"):
            by_stage.setdefault(d.stage_id, []).append(d)
        for s in PipelineStage.objects.filter(company=company, is_active=True).order_by("position"):
            ds = by_stage.get(s.pk, [])
            value = sum((x.amount for x in ds), ZERO)
            columns.append({
                **stage_dict(s), "count": len(ds), "value": value,
                "weighted": sum((deal_service.weighted(x.amount, x.probability) for x in ds), ZERO),
                "deals": [deal_dict(x, today=today, extra=_extra(x)) for x in ds[:self.CAP]],
            })
        open_cols = [c for c in columns if c["kind"] == "open"]
        return Response({
            "columns": columns,
            "totals": {"open_count": sum(c["count"] for c in open_cols), "open_value": sum((c["value"] for c in open_cols), ZERO),
                       "weighted": sum((c["weighted"] for c in open_cols), ZERO)},
        })


class DealDetailView(APIView):
    def get(self, request, pk):
        need(request, "crm.view")
        company = current_company()
        get_object(Deal, pk, company, "deal")
        return Response(_detail(pk, company))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "crm.manage")
        company = current_company()
        deal = get_object(Deal, pk, company, "deal")
        d = request.data
        kwargs = {}
        if d.get("account"):
            kwargs["account"] = optional_object(Account, d["account"], company, "account")
        if "contact" in d:
            kwargs["contact"] = optional_object(Contact, d["contact"], company, "contact")
        if "owner" in d:
            kwargs["owner"] = user_from(d["owner"])
        data = dict(d)
        if "project_id" in d:
            value = d["project_id"]
            deal.project_id = deal_service.resolve_uuid(value) if value else None
            deal.save(update_fields=["project_id", "updated_at"])
        deal_service.update_deal(deal, request.user, data, **kwargs)
        return Response(_detail(pk, company))

    def delete(self, request, pk):
        need(request, "crm.manage")
        deal = get_object(Deal, pk, current_company(), "deal")
        deal_service.delete_deal(deal, request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class DealMoveView(APIView):
    def post(self, request, pk):
        need(request, "crm.manage")
        company = current_company()
        deal = get_object(Deal, pk, company, "deal")
        stage = optional_object(PipelineStage, request.data.get("stage"), company, "stage")
        if stage is None:
            raise ValidationError({"stage": "Choose the stage."})
        deal_service.move_stage(deal, stage, request.user, lost_reason=request.data.get("lost_reason") or "",
                                competitor=request.data.get("competitor") or "", note=request.data.get("note") or "")
        return Response(_detail(pk, company))


class DealItemsView(APIView):
    def put(self, request, pk):
        need(request, "crm.manage")
        company = current_company()
        deal = get_object(Deal, pk, company, "deal")
        if deal.status != "open":
            raise ValidationError({"items": "The items of a closed deal cannot be changed."})
        deal_service.set_items(deal, request.user, request.data.get("items"))
        return Response(_detail(pk, company))


class DealCustomerView(APIView):
    """Link or create the sales Customer for a won deal's account (normally done when the deal is won)."""

    @transaction.atomic
    def post(self, request, pk):
        need(request, "crm.manage")
        company = current_company()
        deal = get_object(Deal, pk, company, "deal")
        if deal.status != "won":
            raise ValidationError("Only a won deal becomes a customer.")
        customer, created = deal_service.ensure_customer(deal.account, request.user)
        deal.customer_id = customer.pk
        deal.save(update_fields=["customer_id", "updated_at"])
        return Response({"created": created, **_detail(pk, company)})


class DealTimelineView(APIView):
    def get(self, request, pk):
        need(request, "crm.view")
        company = current_company()
        deal = get_object(Deal, pk, company, "deal")
        acts = Activity.objects.filter(deal=deal).select_related("owner", "created_by", "lead", "contact", "account", "deal")
        return Response(timeline(company, "deal", deal.pk, acts))
