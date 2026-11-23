import csv
import datetime

from django.http import HttpResponse
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.company.api import current_company
from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import audit

from .services import hub
from .services.common import csv_safe, resolve_period


def need(request, code):
    if not request.user.has_code(code):
        raise PermissionDenied("Your role does not allow this.")


class HomeView(APIView):
    """The hub landing page: one card per report the user may open, with a live headline number."""

    def get(self, request):
        need(request, "reports.view")
        return Response(hub.home(current_company(), request.user, datetime.date.today()))


class DashboardView(APIView):
    """Executive dashboard: headline figures for the financial year to date; a tile only for those who may see its source."""

    def get(self, request):
        need(request, "reports.view")
        return Response(hub.dashboard(current_company(), request.user, datetime.date.today()))


def _report(request, key):
    """The report asked for, once the user is known to hold reports.view and the source module's permission."""
    need(request, "reports.view")
    spec = hub.BY_KEY.get(key)
    if spec is None:
        raise NotFound("No such report.")
    need(request, spec.perm)
    today = datetime.date.today()
    period = resolve_period(request.query_params, today, allow_all=spec.allow_all)
    return spec, period, today, spec.build(current_company(), period, today, request.user)


class ReportView(APIView):
    def get(self, request, key):
        spec, _period, _today, data = _report(request, key)
        return Response({"key": spec.key, "title": spec.title, **data})


class ExportView(APIView):
    """The same figures as a CSV file (opens in Excel; cells that start like a formula are made safe)."""

    def get(self, request, key):
        spec, period, today, data = _report(request, key)
        rows = [[spec.title], [f"Period: {period.label}", f"Created {today.isoformat()}"], [], *spec.csv(data)]
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{spec.key}-{today.isoformat()}.csv"'
        resp.write("﻿")
        writer = csv.writer(resp)
        for row in rows:
            writer.writerow([csv_safe(x) for x in row])
        if spec.audit_export:
            audit.log(current_company(), request.user, "report_export", None, CompanyProfileAuditEvent.Action.DOWNLOADED,
                      new={"report": spec.key, "period": period.label, "rows": len(rows)})
        return resp
