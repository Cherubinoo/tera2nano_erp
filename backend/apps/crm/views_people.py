from django.db import transaction
from django.db.models import Count, Q, Sum
from rest_framework import status as http
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.company.api import current_company
from apps.company.models import CompanyProfileAuditEvent
from apps.sales.models import Customer

from .models import Account, Activity, Contact, Deal
from .serializers import account_dict, contact_dict, deal_dict, quotations_for
from .services import common
from .services.common import ZERO
from .services.leads import ACCOUNT_AUDIT, CONTACT_AUDIT
from .views_common import get_object, need, optional_object, owner_param, paginate, sorted_by, timeline, user_from

Action = CompanyProfileAuditEvent.Action
ACCOUNT_FIELDS = ("name", "industry", "website", "email", "phone", "gstin", "address", "city", "state", "notes")
CONTACT_FIELDS = ("name", "designation", "email", "phone", "notes")
DEAL_RELATED = ("stage", "account", "contact", "owner", "lead")


def _counts(account_ids):
    """{account id: contact count, open deal count and value, won deal count} without double counting joins."""
    out = {i: {"contacts": 0, "open_deals": 0, "open_value": ZERO, "won_deals": 0} for i in account_ids}
    for r in Contact.objects.filter(account_id__in=account_ids).values("account_id").annotate(n=Count("id")):
        out[r["account_id"]]["contacts"] = r["n"]
    for r in Deal.objects.filter(account_id__in=account_ids).values("account_id").annotate(
            o=Count("id", filter=Q(status="open")), v=Sum("amount", filter=Q(status="open")), w=Count("id", filter=Q(status="won"))):
        out[r["account_id"]].update(open_deals=r["o"], open_value=r["v"] or ZERO, won_deals=r["w"])
    return out


# --------------------------------------------------------------------------- accounts

class AccountCollectionView(APIView):
    def get(self, request):
        need(request, "crm.view")
        company = current_company()
        p = request.query_params
        qs = owner_param(request, Account.objects.filter(company=company).select_related("owner"))
        if p.get("active", "true") in ("true", "false"):
            qs = qs.filter(is_active=p.get("active", "true") == "true")
        if p.get("customer") in ("yes", "no"):
            qs = qs.filter(customer_id__isnull=p["customer"] == "no")
        if p.get("q"):
            q = p["q"].strip()
            qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(industry__icontains=q) | Q(city__icontains=q))
        qs = sorted_by(request, qs, {"name", "created_at", "city"}, "name")
        rows, count, page, size = paginate(request, qs, default=50)
        counts = _counts([a.pk for a in rows])
        return Response({"count": count, "page": page, "page_size": size,
                         "results": [account_dict(a, counts=counts[a.pk]) for a in rows]})

    @transaction.atomic
    def post(self, request):
        need(request, "crm.manage")
        company = current_company()
        a = Account(company=company, owner=user_from(request.data.get("owner")) or request.user)
        common.apply_fields(a, request.data, ACCOUNT_FIELDS)
        a.save()
        common.log(company, request.user, "crm_account", a.pk, Action.CREATED, new=common.snapshot(a, ACCOUNT_AUDIT))
        return Response(account_dict(a, counts=_counts([a.pk])[a.pk]), status=http.HTTP_201_CREATED)


class AccountDetailView(APIView):
    def _get(self, pk):
        company = current_company()
        return company, get_object(Account, pk, company, "account")

    def get(self, request, pk):
        need(request, "crm.view")
        company, a = self._get(pk)
        customer = Customer.objects.filter(pk=a.customer_id).first() if a.customer_id else None
        out = account_dict(a, counts=_counts([a.pk])[a.pk], customer=customer, detail=True)
        out["contacts"] = [contact_dict(x) for x in a.contacts.select_related("account", "owner")]
        out["deals"] = [deal_dict(d) for d in a.deals.select_related(*DEAL_RELATED)]
        names = [a.name] + ([customer.display_name, customer.legal_name] if customer else [])
        out["quotations"] = quotations_for(company, names)
        return Response(out)

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "crm.manage")
        company, a = self._get(pk)
        before = common.snapshot(a, ACCOUNT_AUDIT)
        common.apply_fields(a, request.data, ACCOUNT_FIELDS + ("is_active",))
        if "owner" in request.data:
            a.owner = user_from(request.data["owner"])
        if not a.is_active and Deal.objects.filter(account=a, status="open").exists():
            raise ValidationError({"is_active": "This account has open deals. Close or move them first."})
        a.save()
        after = common.snapshot(a, ACCOUNT_AUDIT)
        if before != after:
            common.log(company, request.user, "crm_account", a.pk, Action.UPDATED, previous=before, new=after)
        return Response(account_dict(a, counts=_counts([a.pk])[a.pk]))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "crm.manage")
        company, a = self._get(pk)
        if a.deals.exists():
            raise ValidationError("This account has deals. Delete or move them first, or switch the account off.")
        if a.customer_id:
            raise ValidationError("This account is linked to a customer. Switch it off instead of deleting it.")
        common.log(company, request.user, "crm_account", a.pk, Action.DELETED, previous=common.snapshot(a, ACCOUNT_AUDIT))
        Activity.objects.filter(account=a, lead=None, contact=None, deal=None).delete()
        a.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class AccountTimelineView(APIView):
    def get(self, request, pk):
        need(request, "crm.view")
        company = current_company()
        a = get_object(Account, pk, company, "account")
        acts = Activity.objects.filter(account=a).select_related("owner", "created_by", "lead", "contact", "account", "deal")
        return Response(timeline(company, "crm_account", a.pk, acts))


# --------------------------------------------------------------------------- contacts

def _contact_values(request, company, c, creating):
    common.apply_fields(c, request.data, CONTACT_FIELDS + ("is_primary",), skip_clean=True)
    if "account" in request.data:
        c.account = optional_object(Account, request.data["account"], company, "account")
    if "owner" in request.data:
        c.owner = user_from(request.data["owner"])
    elif creating:
        c.owner = request.user
    c.full_clean(exclude=["company"], validate_unique=False, validate_constraints=False)


def _one_primary(c):
    if c.is_primary and c.account_id:
        Contact.objects.filter(account_id=c.account_id, is_primary=True).exclude(pk=c.pk).update(is_primary=False)


class ContactCollectionView(APIView):
    def get(self, request):
        need(request, "crm.view")
        company = current_company()
        p = request.query_params
        qs = owner_param(request, Contact.objects.filter(company=company).select_related("account", "owner"))
        if p.get("account"):
            qs = qs.filter(account_id=p["account"])
        if p.get("q"):
            q = p["q"].strip()
            qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(account__name__icontains=q)
                           | Q(designation__icontains=q))
        qs = sorted_by(request, qs, {"name", "created_at"}, "name")
        rows, count, page, size = paginate(request, qs, default=50)
        return Response({"count": count, "page": page, "page_size": size, "results": [contact_dict(x) for x in rows]})

    @transaction.atomic
    def post(self, request):
        need(request, "crm.manage")
        company = current_company()
        c = Contact(company=company)
        _contact_values(request, company, c, True)
        c.save()
        _one_primary(c)
        common.log(company, request.user, "crm_contact", c.pk, Action.CREATED, new=common.snapshot(c, CONTACT_AUDIT))
        return Response(contact_dict(c), status=http.HTTP_201_CREATED)


class ContactDetailView(APIView):
    def _get(self, pk):
        company = current_company()
        return company, get_object(Contact, pk, company, "contact")

    def get(self, request, pk):
        need(request, "crm.view")
        company, c = self._get(pk)
        out = contact_dict(c)
        out["deals"] = [deal_dict(d) for d in c.deals.select_related(*DEAL_RELATED)]
        return Response(out)

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "crm.manage")
        company, c = self._get(pk)
        before = common.snapshot(c, CONTACT_AUDIT)
        _contact_values(request, company, c, False)
        c.save()
        _one_primary(c)
        after = common.snapshot(c, CONTACT_AUDIT)
        if before != after:
            common.log(company, request.user, "crm_contact", c.pk, Action.UPDATED, previous=before, new=after)
        return Response(contact_dict(c))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "crm.manage")
        company, c = self._get(pk)
        common.log(company, request.user, "crm_contact", c.pk, Action.DELETED, previous=common.snapshot(c, CONTACT_AUDIT))
        Activity.objects.filter(contact=c, lead=None, account=None, deal=None).delete()
        c.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class ContactTimelineView(APIView):
    def get(self, request, pk):
        need(request, "crm.view")
        company = current_company()
        c = get_object(Contact, pk, company, "contact")
        acts = Activity.objects.filter(contact=c).select_related("owner", "created_by", "lead", "contact", "account", "deal")
        return Response(timeline(company, "crm_contact", c.pk, acts))
