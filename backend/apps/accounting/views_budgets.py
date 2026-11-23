"""Budget endpoints: budgets and their lines, revisions, status, the expense check and the budget-versus-actual report."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import status as http
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.company.api import current_company
from apps.company.models import FinancialYear

from .models import Account, Budget, BudgetRevision
from .services import budgets, chart, reports

ZERO = Decimal("0.00")


def need(request, code):
    if not request.user.has_code(code):
        raise PermissionDenied("Your role does not allow this.")


def _get_budget(pk):
    try:
        b = Budget.objects.filter(pk=pk, company=current_company()).select_related("financial_year").first()
    except (ValueError, DjangoValidationError):
        b = None
    if b is None:
        raise NotFound("No such budget.")
    return b


def budget_dict(b, detail=False):
    lines = list(b.lines.select_related("account"))
    income = sum((l.annual for l in lines if l.account.type == "income"), ZERO)
    expense = sum((l.annual for l in lines if l.account.type == "expense"), ZERO)
    fy = b.financial_year
    out = {"id": str(b.pk), "name": b.name, "status": b.status, "status_label": b.get_status_display(), "version": b.version,
           "notes": b.notes, "financial_year": {"id": str(fy.pk), "label": fy.label, "start": fy.start_date, "end": fy.end_date},
           "total_income": income, "total_expense": expense, "line_count": len(lines), "updated_at": b.updated_at}
    if detail:
        labels = budgets.project_labels(b.company, {l.project_id for l in lines})
        out["months"] = [{"month": f"{m:%Y-%m}", "label": m.strftime("%b")} for m in budgets.fy_months(fy)]
        out["lines"] = [{"id": str(l.pk), "account": {"id": str(l.account_id), "code": l.account.code, "name": l.account.name,
                                                       "type": l.account.type},
                         "project": {"id": str(l.project_id), "label": labels.get(l.project_id, "Project")} if l.project_id else None,
                         "phasing": l.phasing, "annual": l.annual, "monthly": l.monthly} for l in lines]
        out["revisions"] = [revision_dict(r) for r in b.revisions.select_related("created_by")]
    return out


def revision_dict(r, detail=False):
    out = {"version": r.version, "reason": r.reason, "total_income": r.total_income, "total_expense": r.total_expense,
           "lines": len(r.snapshot), "created_at": r.created_at, "created_by": r.created_by.get_username() if r.created_by_id else ""}
    if detail:
        out["snapshot"] = r.snapshot
    return out


class BudgetCollectionView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        p = request.query_params
        qs = Budget.objects.filter(company=current_company()).select_related("financial_year")
        if p.get("financial_year"):
            qs = qs.filter(financial_year_id=p["financial_year"])
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        return Response([budget_dict(b) for b in qs])

    @transaction.atomic
    def post(self, request):
        need(request, "accounting.manage")
        b = budgets.create_budget(current_company(), request.user, request.data)
        return Response(budget_dict(_get_budget(b.pk), detail=True), status=http.HTTP_201_CREATED)


class BudgetOptionsView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        company = current_company()
        chart.ensure_chart(company)
        today = datetime.date.today()
        for d in (today,):
            from apps.company.services import numbering
            numbering.ensure_financial_year(company, d)
        accounts = Account.objects.filter(company=company, is_active=True, type__in=("income", "expense")).order_by("code")
        return Response({
            "financial_years": [{"id": str(f.pk), "label": f.label, "start": f.start_date, "end": f.end_date,
                                 "current": f.start_date <= today <= f.end_date}
                                for f in FinancialYear.objects.filter(company=company).order_by("-start_date")],
            "accounts": [{"id": str(a.pk), "code": a.code, "name": a.name, "type": a.type} for a in accounts],
            "statuses": [{"value": v, "label": l} for v, l in Budget.Status.choices],
        })


class BudgetDetailView(APIView):
    def get(self, request, pk):
        need(request, "accounting.view")
        return Response(budget_dict(_get_budget(pk), detail=True))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "accounting.manage")
        budgets.update_budget(_get_budget(pk), request.user, request.data)
        return Response(budget_dict(_get_budget(pk), detail=True))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "accounting.manage")
        budgets.delete_budget(_get_budget(pk), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class BudgetStatusView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "accounting.manage")
        budgets.set_status(_get_budget(pk), request.user, request.data.get("status"))
        return Response(budget_dict(_get_budget(pk), detail=True))


class BudgetRevisionsView(APIView):
    def get(self, request, pk):
        need(request, "accounting.view")
        b = _get_budget(pk)
        return Response([revision_dict(r) for r in b.revisions.select_related("created_by")])


class BudgetRevisionDetailView(APIView):
    def get(self, request, pk, version):
        need(request, "accounting.view")
        b = _get_budget(pk)
        r = BudgetRevision.objects.filter(budget=b, version=version).select_related("created_by").first()
        if r is None:
            raise NotFound("No such revision.")
        return Response(revision_dict(r, detail=True))


class BudgetCheckView(APIView):
    """The over-budget warning for an expense about to be entered. Always 200; `warning` is null when it fits the budget."""

    def get(self, request):
        need(request, "accounting.view")
        p, company = request.query_params, current_company()
        try:
            account = Account.objects.filter(pk=p.get("account"), company=company).first() if p.get("account") else None
            amount = Decimal(str(p.get("amount") or "0"))
            on_date = datetime.date.fromisoformat(p["date"]) if p.get("date") else datetime.date.today()
            project = p.get("project") or None
            if project:
                import uuid
                project = uuid.UUID(project)
        except (ValueError, ArithmeticError, DjangoValidationError):
            raise ValidationError("Send the account, amount and date of the expense.")
        if account is None or account.type != "expense" or amount <= 0:
            return Response({"warning": None})
        return Response({"warning": budgets.expense_warning(company, account=account, on_date=on_date, amount=amount, project_id=project)})


class BudgetVsActualView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        company, p = current_company(), request.query_params
        if p.get("budget"):
            b = _get_budget(p["budget"])
        else:
            b = budgets.active_budget_for(company, datetime.date.today())
            if b is None:
                raise NotFound("There is no active budget for this financial year. Choose a budget.")
        project = p.get("project") or None
        if project:
            import uuid
            try:
                project = uuid.UUID(project)
            except ValueError:
                raise ValidationError({"project": "Choose a valid cost centre."})
        return Response(budgets.budget_vs_actual(company, b, through=p.get("through") or None, project=project))
