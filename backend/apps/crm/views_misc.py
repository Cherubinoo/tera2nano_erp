import csv
import datetime
import re

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, F, Max, Q
from django.http import HttpResponse
from rest_framework import status as http
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.company.api import current_company
from apps.company.models import CompanyProfileAuditEvent

from . import constants as c
from .models import Account, Activity, Contact, Deal, DealItem, DealStageHistory, Lead, LeadSource, LostReason, PipelineStage, Product
from .serializers import activity_dict, deal_dict, lead_dict, product_dict, stage_dict
from .services import activities as activity_service, analytics, common
from .views_common import (date_param, get_object, need, optional_object, owner_param, paginate, user_from)
from .views_leads import source_names

Action = CompanyProfileAuditEvent.Action
ACT_RELATED = ("owner", "created_by", "lead", "contact", "account", "deal")


# --------------------------------------------------------------------------- lookups and analytics

class LookupsView(APIView):
    """Everything the CRM forms need in one call."""

    def get(self, request):
        need(request, "crm.view")
        company = current_company()
        common.ensure_defaults(company)
        owners = [{"id": str(u.pk), "name": u.get_full_name() or u.get_username(), "role": u.role}
                  for u in get_user_model().objects.filter(is_active=True).order_by("first_name", "username") if u.has_code("crm.view")]
        return Response({
            "owners": owners,
            "stages": [stage_dict(s) for s in PipelineStage.objects.filter(company=company, is_active=True).order_by("position")],
            "sources": [{"key": s.key, "name": s.name} for s in LeadSource.objects.filter(company=company, is_active=True)],
            "lost_reasons": [r.name for r in LostReason.objects.filter(company=company, is_active=True)],
            "lead_statuses": [{"value": v, "label": l} for v, l in c.LEAD_STATUSES],
            "ratings": [{"value": v, "label": l} for v, l in c.RATINGS],
            "activity_types": [{"value": v, "label": l} for v, l in c.ACTIVITY_TYPES],
            "priorities": [{"value": v, "label": l} for v, l in c.PRIORITIES],
            "product_interests": c.PRODUCT_INTERESTS,
            "can_manage": request.user.has_code("crm.manage"),
        })


class DashboardView(APIView):
    def get(self, request):
        need(request, "crm.view")
        company = current_company()
        common.ensure_defaults(company)
        return Response(analytics.dashboard(company, user=request.user))


class ReportSummaryView(APIView):
    def get(self, request):
        need(request, "crm.view")
        company = current_company()
        common.ensure_defaults(company)
        return Response(analytics.reports_summary(company))


# --------------------------------------------------------------------------- activities

def _activity_qs(company):
    return Activity.objects.filter(company=company).select_related(*ACT_RELATED)


def _related_filter(qs, request):
    p = request.query_params
    for kind in ("lead", "contact", "account", "deal"):
        if p.get(kind):
            qs = qs.filter(**{f"{kind}_id": p[kind]})
    return qs


def _resolve_links(request, company, required):
    d = request.data
    links = {
        "lead": optional_object(Lead, d.get("lead"), company, "lead"),
        "contact": optional_object(Contact, d.get("contact"), company, "contact"),
        "account": optional_object(Account, d.get("account"), company, "account"),
        "deal": optional_object(Deal, d.get("deal"), company, "deal"),
    }
    if required and not any(links.values()):
        raise ValidationError({"related_to": "Attach the activity to a lead, contact, account or deal."})
    return links


class ActivityCollectionView(APIView):
    def get(self, request):
        need(request, "crm.view")
        company = current_company()
        p = request.query_params
        today = datetime.date.today()
        qs = _related_filter(owner_param(request, _activity_qs(company)), request)
        if p.get("type"):
            qs = qs.filter(type=p["type"])
        if p.get("q"):
            qs = qs.filter(Q(subject__icontains=p["q"]) | Q(description__icontains=p["q"]) | Q(outcome__icontains=p["q"]))
        start, end = date_param(request, "due_from"), date_param(request, "due_to")
        if start:
            qs = qs.filter(due_date__gte=start)
        if end:
            qs = qs.filter(due_date__lte=end)
        open_ = qs.filter(status="open")
        counts = {"overdue": open_.filter(due_date__lt=today).count(), "today": open_.filter(due_date=today).count(),
                  "upcoming": open_.filter(due_date__gt=today).count(), "open": open_.count(),
                  "completed": qs.filter(status="completed").count()}
        view = p.get("view", "open")
        qs = activity_service.filter_view(qs, view, today)
        if view == "completed":
            qs = qs.order_by("-completed_at")
        elif view == "overdue":
            qs = qs.order_by("due_date", "due_time")
        else:
            qs = qs.order_by(F("due_date").asc(nulls_last=True), F("due_time").asc(nulls_last=True), "-created_at")
        rows, count, page, size = paginate(request, qs, default=50)
        return Response({"count": count, "page": page, "page_size": size, "counts": counts, "today": today.isoformat(),
                         "results": [activity_dict(a, today) for a in rows]})

    @transaction.atomic
    def post(self, request):
        need(request, "crm.manage")
        company = current_company()
        links = _resolve_links(request, company, True)
        a = activity_service.create(company, request.user, request.data, owner=user_from(request.data.get("owner")), **links)
        return Response(activity_dict(_activity_qs(company).get(pk=a.pk)), status=http.HTTP_201_CREATED)


class ActivityDetailView(APIView):
    def get(self, request, pk):
        need(request, "crm.view")
        company = current_company()
        get_object(Activity, pk, company, "activity")
        return Response(activity_dict(_activity_qs(company).get(pk=pk)))

    def patch(self, request, pk):
        need(request, "crm.manage")
        company = current_company()
        a = get_object(Activity, pk, company, "activity")
        owner = user_from(request.data["owner"]) if "owner" in request.data else ...
        activity_service.update(a, request.user, request.data, owner=owner)
        return Response(activity_dict(_activity_qs(company).get(pk=pk)))

    def delete(self, request, pk):
        need(request, "crm.manage")
        a = get_object(Activity, pk, current_company(), "activity")
        activity_service.delete(a, request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class ActivityCompleteView(APIView):
    def post(self, request, pk):
        need(request, "crm.manage")
        company = current_company()
        a = get_object(Activity, pk, company, "activity")
        activity_service.complete(a, request.user, request.data.get("outcome") or "")
        return Response(activity_dict(_activity_qs(company).get(pk=pk)))


class ActivityReopenView(APIView):
    def post(self, request, pk):
        need(request, "crm.manage")
        company = current_company()
        a = get_object(Activity, pk, company, "activity")
        activity_service.reopen(a, request.user)
        return Response(activity_dict(_activity_qs(company).get(pk=pk)))


# --------------------------------------------------------------------------- settings lists

class SettingsListView(APIView):
    """Shared CRUD for the small settings lists. Subclasses name the model, entity and fields."""

    model = None
    entity = ""
    fields = ("name",)
    label = ""

    def row(self, obj, used):
        raise NotImplementedError

    def used(self, obj):
        raise NotImplementedError

    def qs(self, company):
        return self.model.objects.filter(company=company)

    def get(self, request):
        need(request, "crm.view")
        company = current_company()
        common.ensure_defaults(company)
        return Response([self.row(o, self.used(o)) for o in self.qs(company)])

    @transaction.atomic
    def post(self, request):
        need(request, "crm.manage")
        company = current_company()
        common.ensure_defaults(company)
        obj = self.model(company=company, position=(self.qs(company).aggregate(m=Max("position"))["m"] or 0) + 1)
        self.prepare(obj, request.data)
        common.apply_fields(obj, request.data, self.fields)
        self.before_save(obj, request.data, True)
        obj.save()
        self.after_create(obj)
        common.log(company, request.user, self.entity, obj.pk, Action.CREATED, new=common.snapshot(obj, self.audit_fields))
        return Response(self.row(obj, 0), status=http.HTTP_201_CREATED)

    audit_fields = ("name", "is_active")

    def prepare(self, obj, data):
        pass

    def before_save(self, obj, data, creating):
        pass

    def after_create(self, obj):
        pass


class SettingsDetailView(APIView):
    list_view = None

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "crm.manage")
        company = current_company()
        v = self.list_view()
        obj = get_object(v.model, pk, company, v.label)
        before = common.snapshot(obj, v.audit_fields)
        common.apply_fields(obj, request.data, v.fields + ("is_active",))
        v.before_save(obj, request.data, False)
        obj.save()
        after = common.snapshot(obj, v.audit_fields)
        if before != after:
            common.log(company, request.user, v.entity, obj.pk, Action.UPDATED, previous=before, new=after)
        return Response(v.row(obj, v.used(obj)))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "crm.manage")
        company = current_company()
        v = self.list_view()
        obj = get_object(v.model, pk, company, v.label)
        if v.used(obj):
            raise ValidationError(f"This {v.label} is in use. Switch it off instead of deleting it.")
        v.check_delete(obj)
        common.log(company, request.user, v.entity, obj.pk, Action.DELETED, previous=common.snapshot(obj, v.audit_fields))
        obj.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class StageListView(SettingsListView):
    model, entity, label = PipelineStage, "crm_stage", "stage"
    fields = ("name", "probability", "colour")
    audit_fields = ("name", "probability", "colour", "kind", "position", "is_active")

    def row(self, s, used):
        return stage_dict(s, used)

    def used(self, s):
        # deals sitting in it, or any stage history that points at it
        return s.deals.count() + DealStageHistory.objects.filter(Q(to_stage=s) | Q(from_stage=s)).count()

    def prepare(self, s, data):
        s.kind = data.get("kind") or "open"
        if s.kind not in ("open", "won", "lost"):
            raise ValidationError({"kind": "Choose open, won or lost."})
        if s.kind == "won":
            s.probability = 100
        elif s.kind == "lost":
            s.probability = 0

    def before_save(self, s, data, creating):
        others = PipelineStage.objects.filter(company=s.company, is_active=True).exclude(pk=s.pk)
        if s.is_active and s.kind in ("won", "lost") and others.filter(kind=s.kind).exists():
            raise ValidationError({"kind": f"There can be only one {s.kind} stage."})
        if s.kind == "open" and s.is_active is False:
            if s.pk and s.deals.filter(status="open").exists():
                raise ValidationError({"is_active": "Open deals are in this stage. Move them first."})
            if not others.filter(kind="open").exists():
                raise ValidationError({"is_active": "Keep at least one open stage."})
        if s.kind != "open" and not s.is_active:
            raise ValidationError({"is_active": "The won and lost stages cannot be switched off."})

    def after_create(self, s):
        if s.kind == "open":  # a new open stage goes before the closed ones
            first_closed = PipelineStage.objects.filter(company=s.company).exclude(kind="open").exclude(pk=s.pk).order_by("position").first()
            if first_closed is not None:
                PipelineStage.objects.filter(company=s.company, position__gte=first_closed.position).exclude(pk=s.pk).update(position=F("position") + 1)
                s.position = first_closed.position
                s.save(update_fields=["position"])

    def check_delete(self, s):
        if s.kind != "open":
            raise ValidationError("The won and lost stages cannot be deleted.")
        if not PipelineStage.objects.filter(company=s.company, kind="open", is_active=True).exclude(pk=s.pk).exists():
            raise ValidationError("Keep at least one open stage.")


class StageDetailView(SettingsDetailView):
    list_view = StageListView

    def patch(self, request, pk):
        need(request, "crm.manage")
        s = get_object(PipelineStage, pk, current_company(), "stage")
        if "kind" in request.data and request.data["kind"] != s.kind:
            raise ValidationError({"kind": "The type of a stage cannot be changed."})
        if s.kind == "won" and request.data.get("probability") not in (None, "", 100, "100"):
            raise ValidationError({"probability": "A won stage is always 100%."})
        if s.kind == "lost" and request.data.get("probability") not in (None, "", 0, "0"):
            raise ValidationError({"probability": "A lost stage is always 0%."})
        return super().patch(request, pk)


class StageReorderView(APIView):
    @transaction.atomic
    def post(self, request):
        need(request, "crm.manage")
        company = current_company()
        ids = request.data.get("ids")
        stages = {str(s.pk): s for s in PipelineStage.objects.filter(company=company)}
        if not isinstance(ids, list) or sorted(map(str, ids)) != sorted(stages):
            raise ValidationError({"ids": "Send every stage once, in the order you want."})
        ordered = [stages[str(i)] for i in ids]
        kinds = [s.kind for s in ordered]
        if "open" in kinds[kinds.index("won") if "won" in kinds else len(kinds):] and "won" in kinds:
            raise ValidationError({"ids": "Open stages must come before the won and lost stages."})
        for pos, s in enumerate(ordered, start=1):
            if s.position != pos:
                s.position = pos
                s.save(update_fields=["position", "updated_at"])
        common.log(company, request.user, "crm_stage", ordered[0].pk, Action.UPDATED, new={"order": [s.name for s in ordered]}, reason="Reordered")
        return Response([stage_dict(s, s.deals.count()) for s in ordered])


class SourceListView(SettingsListView):
    model, entity, label = LeadSource, "crm_source", "source"

    def row(self, s, used):
        return {"id": str(s.pk), "key": s.key, "name": s.name, "is_active": s.is_active, "position": s.position, "leads": used}

    def used(self, s):
        return Lead.objects.filter(company=s.company, source=s.key).count()

    def prepare(self, s, data):
        base = re.sub(r"[^a-z0-9]+", "_", str(data.get("name") or "").lower()).strip("_")[:40] or "source"
        key, n = base, 2
        while LeadSource.objects.filter(company=s.company, key=key).exists():
            key, n = f"{base}_{n}", n + 1
        s.key = key

    def check_delete(self, s):
        pass


class SourceDetailView(SettingsDetailView):
    list_view = SourceListView


class LostReasonListView(SettingsListView):
    model, entity, label = LostReason, "crm_lost_reason", "reason"

    def row(self, r, used):
        return {"id": str(r.pk), "name": r.name, "is_active": r.is_active, "position": r.position, "deals": used}

    def used(self, r):
        return Deal.objects.filter(company=r.company, lost_reason=r.name).count()

    def check_delete(self, r):
        pass


class LostReasonDetailView(SettingsDetailView):
    list_view = LostReasonListView


# --------------------------------------------------------------------------- products

class ProductListView(APIView):
    def get(self, request):
        need(request, "crm.view")
        company = current_company()
        qs = Product.objects.filter(company=company)
        if request.query_params.get("active") == "true":
            qs = qs.filter(is_active=True)
        if request.query_params.get("q"):
            qs = qs.filter(Q(name__icontains=request.query_params["q"]) | Q(code__icontains=request.query_params["q"]))
        return Response([product_dict(p) for p in qs])

    @transaction.atomic
    def post(self, request):
        need(request, "crm.manage")
        company = current_company()
        p = Product(company=company)
        common.apply_fields(p, request.data, ("name", "code", "description", "unit_price", "gst_rate"))
        _check_code(p)
        p.save()
        common.log(company, request.user, "crm_product", p.pk, Action.CREATED, new=common.snapshot(p, PRODUCT_AUDIT))
        return Response(product_dict(p), status=http.HTTP_201_CREATED)


PRODUCT_AUDIT = ("name", "code", "unit_price", "gst_rate", "is_active")


def _check_code(p):
    if p.code and Product.objects.exclude(pk=p.pk).filter(company=p.company, code=p.code).exists():
        raise ValidationError({"code": "Another product has this code."})


class ProductDetailView(APIView):
    @transaction.atomic
    def patch(self, request, pk):
        need(request, "crm.manage")
        company = current_company()
        p = get_object(Product, pk, company, "product")
        before = common.snapshot(p, PRODUCT_AUDIT)
        common.apply_fields(p, request.data, ("name", "code", "description", "unit_price", "gst_rate", "is_active"))
        _check_code(p)
        p.save()
        after = common.snapshot(p, PRODUCT_AUDIT)
        if before != after:
            common.log(company, request.user, "crm_product", p.pk, Action.UPDATED, previous=before, new=after)
        return Response(product_dict(p))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "crm.manage")
        company = current_company()
        p = get_object(Product, pk, company, "product")
        if DealItem.objects.filter(product=p).exists():
            raise ValidationError("This product is on deals. Switch it off instead of deleting it.")
        common.log(company, request.user, "crm_product", p.pk, Action.DELETED, previous=common.snapshot(p, PRODUCT_AUDIT))
        p.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- CSV exports

def _safe(value):
    """Stop spreadsheet programs from running a cell that starts like a formula."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _csv_response(name, header, rows):
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{name}-{datetime.date.today().isoformat()}.csv"'
    resp.write("﻿")
    w = csv.writer(resp)
    w.writerow(header)
    for r in rows:
        w.writerow([_safe(x) for x in r])
    return resp


def _created_range(request, qs):
    start, end = date_param(request, "from"), date_param(request, "to")
    if start:
        qs = qs.filter(created_at__date__gte=start)
    if end:
        qs = qs.filter(created_at__date__lte=end)
    return qs


class ExportView(APIView):
    kinds = ("leads", "deals", "activities")

    def get(self, request, kind):
        need(request, "crm.view")
        if kind not in self.kinds:
            from rest_framework.exceptions import NotFound
            raise NotFound("No such report.")
        company = current_company()
        p = request.query_params
        if kind == "leads":
            qs = owner_param(request, _created_range(request, Lead.objects.filter(company=company).select_related("owner")))
            if p.get("status"):
                qs = qs.filter(status=p["status"])
            names = source_names(company)
            rows = [[x.reference_code, x.company_name, x.contact_name, x.email, x.phone, names.get(x.source, x.source), x.industry,
                     x.product_interest, x.estimated_value, x.get_status_display(), x.get_rating_display(), x.score,
                     (x.owner.get_full_name() or x.owner.get_username()) if x.owner else "", x.city, x.state,
                     x.created_at.date().isoformat()] for x in qs.order_by("created_at")]
            return _csv_response("crm-leads", ["Lead ID", "Company", "Contact", "Email", "Phone", "Source", "Industry", "Product interest",
                                               "Estimated value", "Status", "Rating", "Score", "Owner", "City", "State", "Created"], rows)
        if kind == "deals":
            qs = owner_param(request, _created_range(request, Deal.objects.filter(company=company).select_related(
                "account", "contact", "stage", "owner")))
            if p.get("status") in ("open", "won", "lost"):
                qs = qs.filter(status=p["status"])
            rows = [[x.reference_code, x.name, x.account.name, x.contact.name if x.contact else "", x.stage.name, x.status, x.amount,
                     x.probability, x.weighted, x.expected_close_date or "", x.closed_on or "", x.lost_reason, x.competitor,
                     (x.owner.get_full_name() or x.owner.get_username()) if x.owner else "", x.next_step, x.created_at.date().isoformat()]
                    for x in qs.order_by("created_at")]
            return _csv_response("crm-deals", ["Deal ID", "Deal", "Account", "Contact", "Stage", "Status", "Amount", "Probability %",
                                               "Weighted", "Expected close", "Closed on", "Lost reason", "Competitor", "Owner",
                                               "Next step", "Created"], rows)
        qs = owner_param(request, _created_range(request, Activity.objects.filter(company=company).select_related(*ACT_RELATED)))
        if p.get("type"):
            qs = qs.filter(type=p["type"])
        rows = []
        for a in qs.order_by("created_at"):
            d = activity_dict(a)
            rel = d["related"] or {}
            rows.append([a.get_type_display(), a.subject, a.get_status_display(), a.due_date or "", a.due_time or "", a.priority,
                         rel.get("type", ""), rel.get("name", ""), (a.owner.get_full_name() or a.owner.get_username()) if a.owner else "",
                         a.completed_at.date().isoformat() if a.completed_at else "", a.outcome, a.created_at.date().isoformat()])
        return _csv_response("crm-activities", ["Type", "Subject", "Status", "Due date", "Due time", "Priority", "Related to type",
                                                "Related to", "Owner", "Completed on", "Outcome", "Created"], rows)
