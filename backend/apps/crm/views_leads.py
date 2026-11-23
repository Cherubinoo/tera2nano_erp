from django.db import transaction
from django.db.models import Count, Q, Sum
from rest_framework import status as http
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.company.api import current_company

from . import constants as c
from .models import Account, Activity, Lead, LeadSource, PipelineStage
from .serializers import activity_dict, lead_dict
from .services import common, deals as deal_service, leads as lead_service
from .views_common import get_object, need, optional_object, owner_param, paginate, sorted_by, timeline, user_from

LEAD_QS = Lead.objects.select_related("owner", "converted_account", "converted_contact", "converted_deal")


def source_names(company):
    return dict(LeadSource.objects.filter(company=company).values_list("key", "name"))


def _duplicate_response(exc):
    return Response({"detail": str(exc), "code": "duplicate", "duplicates": exc.duplicates}, status=http.HTTP_409_CONFLICT)


class LeadCollectionView(APIView):
    def get(self, request):
        need(request, "crm.view")
        company = current_company()
        common.ensure_defaults(company)
        p = request.query_params
        base = LEAD_QS.filter(company=company)
        qs = owner_param(request, base)
        if p.get("source"):
            qs = qs.filter(source=p["source"])
        if p.get("rating"):
            qs = qs.filter(rating=p["rating"])
        if p.get("q"):
            q = p["q"].strip()
            qs = qs.filter(Q(company_name__icontains=q) | Q(contact_name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q)
                           | Q(reference_code__icontains=q) | Q(product_interest__icontains=q))
        counts = {r["status"]: r["n"] for r in qs.values("status").annotate(n=Count("id"))}  # before the status filter, for the tabs
        status = p.get("status")
        if status == "open":
            qs = qs.exclude(status__in=("converted", "unqualified"))
        elif status:
            qs = qs.filter(status=status)
        qs = sorted_by(request, qs, {"created_at", "score", "estimated_value", "company_name", "status"}, "-created_at")
        rows, count, page, size = paginate(request, qs)
        names = source_names(company)
        return Response({
            "count": count, "page": page, "page_size": size, "status_counts": counts,
            "total_value": qs.aggregate(t=Sum("estimated_value"))["t"] or 0,
            "results": [lead_dict(x, source_names=names) for x in rows],
        })

    @transaction.atomic
    def post(self, request):
        need(request, "crm.manage")
        company = current_company()
        common.ensure_defaults(company)
        owner = user_from(request.data.get("owner")) or request.user
        try:
            lead = lead_service.create_lead(company, request.user, request.data, owner=owner,
                                            allow_duplicates=bool(request.data.get("allow_duplicates")))
        except lead_service.DuplicateFound as exc:
            transaction.set_rollback(True)
            return _duplicate_response(exc)
        return Response(lead_dict(LEAD_QS.get(pk=lead.pk), source_names=source_names(company), detail=True), status=http.HTTP_201_CREATED)


class LeadDetailView(APIView):
    def get(self, request, pk):
        need(request, "crm.view")
        company = current_company()
        lead = get_object(Lead, pk, company, "lead")
        out = lead_dict(LEAD_QS.get(pk=lead.pk), source_names=source_names(company), detail=True)
        out["duplicates"] = lead_service.find_duplicates(company, email=lead.email, phone=lead.phone, company_name=lead.company_name,
                                                         exclude_lead=lead)
        return Response(out)

    def patch(self, request, pk):
        need(request, "crm.manage")
        company = current_company()
        lead = get_object(Lead, pk, company, "lead")
        owner = user_from(request.data.get("owner")) if "owner" in request.data else ...
        lead_service.update_lead(lead, request.user, request.data, owner=owner)
        return Response(lead_dict(LEAD_QS.get(pk=lead.pk), source_names=source_names(company), detail=True))

    def delete(self, request, pk):
        need(request, "crm.manage")
        lead = get_object(Lead, pk, current_company(), "lead")
        lead_service.delete_lead(lead, request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class LeadTimelineView(APIView):
    def get(self, request, pk):
        need(request, "crm.view")
        company = current_company()
        lead = get_object(Lead, pk, company, "lead")
        acts = Activity.objects.filter(lead=lead).select_related("owner", "created_by", "lead", "contact", "account", "deal")
        return Response(timeline(company, "lead", lead.pk, acts))


class LeadConvertView(APIView):
    def post(self, request, pk):
        need(request, "crm.manage")
        company = current_company()
        common.ensure_defaults(company)
        lead = get_object(Lead, pk, company, "lead")
        d = request.data
        stage = optional_object(PipelineStage, d.get("stage"), company, "stage")
        account = optional_object(Account, d.get("account"), company, "account")
        create_deal = d.get("create_deal", True) not in (False, "false", 0)
        amount = None
        if d.get("amount") not in (None, ""):
            amount = common.decimal_from(d["amount"], "amount")
        lead, contact, account, deal = lead_service.convert(
            lead, request.user, create_deal=create_deal, deal_name=(d.get("deal_name") or "").strip(), amount=amount,
            expected_close_date=d.get("expected_close_date") or None, stage=stage, account=account,
            owner=user_from(d.get("owner")) if d.get("owner") else None)
        return Response({
            "lead": lead_dict(LEAD_QS.get(pk=lead.pk), source_names=source_names(company)),
            "contact": {"id": str(contact.pk), "name": contact.name}, "account": {"id": str(account.pk), "name": account.name},
            "deal": {"id": str(deal.pk), "name": deal.name, "reference_code": deal.reference_code} if deal else None,
        })


class LeadBulkView(APIView):
    def post(self, request):
        need(request, "crm.manage")
        company = current_company()
        ids = request.data.get("ids")
        if not isinstance(ids, list) or not ids:
            raise ValidationError({"ids": "Select at least one lead."})
        if len(ids) > 500:
            raise ValidationError({"ids": "Select at most 500 leads at a time."})
        action = request.data.get("action")
        owner = None
        if action == "assign":
            owner = user_from(request.data.get("owner"))
            if owner is None:
                raise ValidationError({"owner": "Choose who the leads are assigned to."})
        try:
            done, skipped = lead_service.bulk(company, request.user, ids, action, owner=owner, status=request.data.get("status"))
        except (ValueError, TypeError):
            raise ValidationError({"ids": "Not valid lead ids."})
        return Response({"done": done, "skipped_converted": skipped})


class LeadImportView(APIView):
    """POST {csv: "<text>", commit: false|true, import_duplicates: false|true}: a preview with row errors, or the import."""

    def post(self, request):
        need(request, "crm.manage")
        company = current_company()
        common.ensure_defaults(company)
        text = request.data.get("csv")
        if not isinstance(text, str) or not text.strip():
            raise ValidationError({"csv": "Choose a CSV file."})
        owner = user_from(request.data.get("owner")) or request.user
        with transaction.atomic():
            result = lead_service.import_leads(company, request.user, text, commit=bool(request.data.get("commit")),
                                               import_duplicates=bool(request.data.get("import_duplicates")), owner=owner)
        return Response(result)


class DuplicateCheckView(APIView):
    def get(self, request):
        need(request, "crm.view")
        p = request.query_params
        exclude = None
        if p.get("exclude"):
            exclude = get_object(Lead, p["exclude"], current_company(), "lead")
        return Response(lead_service.find_duplicates(current_company(), email=p.get("email", ""), phone=p.get("phone", ""),
                                                     company_name=p.get("company", ""), exclude_lead=exclude))
