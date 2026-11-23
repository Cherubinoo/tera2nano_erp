"""Read models: dashboard, reminders, expiry watch, summary, and the company registration viewer."""
import datetime
from collections import Counter

from django.db.models import Count, Q
from django.urls import Resolver404, resolve

from apps.company.models import AuthorizedSignatory, CompanyAddress, CompanyDocument, CompanyPartner, CompanyTaxRegistration
from apps.company.constants import AccessClass

from .. import constants as C
from ..models import CalendarItem, Contract, LegalCase, Policy
from . import common, filings, legal, schedule

OPEN = CalendarItem.Status.OPEN
FILED = CalendarItem.Status.FILED
NA = CalendarItem.Status.NOT_APPLICABLE


def mask(value):
    value = value or ""
    return value if len(value) <= 4 else "•" * (len(value) - 4) + value[-4:]


# ---------------------------------------------------------------------------- expiry watch
def _tone(days):
    if days < 0:
        return "overdue"
    if days <= C.DUE_SOON_DAYS:
        return "due_soon"
    return "upcoming"


def expiry_rows(company, user, today=None, *, ahead_days=90):
    """Licences, registrations and signatory authorizations (read from the company module) plus contract expiries.

    Everything already expired and still in force is listed; anything expiring within `ahead_days` too."""
    today = today or datetime.date.today()
    limit = today + datetime.timedelta(days=ahead_days)
    rows = []

    def add(source, kind, title, detail, date, link, read_only, ref=""):
        days = (date - today).days
        rows.append({"source": source, "kind": kind, "title": title, "detail": detail, "date": date, "days_left": days,
                     "tone": _tone(days), "expired": days < 0, "link": link, "read_only": read_only, "ref": ref})

    allowed = {AccessClass.INTERNAL}
    if user is None or user.has_code("documents.view_confidential"):
        allowed.add(AccessClass.CONFIDENTIAL)
    if user is None or user.has_code("documents.view_restricted"):
        allowed.add(AccessClass.RESTRICTED)
    for d in CompanyDocument.objects.filter(company=company, is_current=True, expiry_date__isnull=False, expiry_date__lte=limit,
                                            access_classification__in=allowed):
        add("company", d.get_document_type_display(), d.title, "Company document", d.expiry_date, "/company/documents", True)
    for r in CompanyTaxRegistration.objects.filter(company=company, is_active=True, effective_to__isnull=False, effective_to__lte=limit):
        add("company", r.get_registration_type_display(), r.get_registration_type_display(),
            f"Registration {mask(r.registration_number)}", r.effective_to, "/company/legal", True)
    for s in AuthorizedSignatory.objects.filter(company=company, status="active", valid_to__isnull=False, valid_to__lte=limit):
        add("company", "Signatory authorization", s.name, s.get_authorization_type_display(), s.valid_to, "/company/signatories", True)
    for c in Contract.objects.filter(company=company, status=Contract.Status.ACTIVE).exclude(expiry_date__isnull=True):
        eff = legal.effective_expiry(c, today)
        if eff <= limit:
            add("contract", c.get_contract_type_display(), c.title, c.counterparty, eff, f"/compliance/contracts?open={c.pk}", False, c.reference)
    rows.sort(key=lambda r: (r["date"], r["title"]))
    return rows


# ---------------------------------------------------------------------------- tax calendar (optional, read only)
def tax_rows(request, today, horizon):
    """Rows from GET /api/v1/tax/calendar/?days= when the tax module is there and the user may see it.

    The tax module answers {"events": [{id, kind, kind_label, title, period, due_date, days_left, overdue, status, link ...}]}
    (filed items are already left out). Never raises: a missing, forbidden or broken tax module is simply absent."""
    try:
        from rest_framework.test import APIRequestFactory, force_authenticate

        match = resolve("/api/v1/tax/calendar/")
        req = APIRequestFactory().get("/api/v1/tax/calendar/", {"days": max(1, min(int(horizon), 400))})
        force_authenticate(req, user=request.user)
        response = match.func(req, *match.args, **match.kwargs)
        if getattr(response, "status_code", 500) != 200:
            return []
        data = response.data
        items = data.get("events", data.get("results", [])) if isinstance(data, dict) else data
        out = []
        end = today + datetime.timedelta(days=horizon)
        for it in items or []:
            raw = it.get("due_date") or it.get("date")
            if not raw or str(it.get("status", "")).lower() in ("filed", "paid", "done", "completed", "not_applicable"):
                continue
            date = datetime.date.fromisoformat(str(raw)[:10])
            if date > end:
                continue
            days = (date - today).days
            out.append({"kind": "tax", "source": "Tax", "id": str(it.get("id", "")), "title": str(it.get("title") or it.get("name") or "Tax due"),
                        "subtitle": " · ".join(x for x in (str(it.get("kind_label") or ""), str(it.get("period") or it.get("period_label") or "")) if x),
                        "due_date": date, "days_left": days, "tone": _tone(days), "priority": "normal", "assignee": "",
                        "link": str(it.get("link") or "/tax"), "read_only": True, "category": "tax"})
        return out
    except Exception:  # the tax module is optional (Resolver404 included) and must never break compliance
        return []


# ---------------------------------------------------------------------------- reminders
def reminders(company, request, today=None, horizon=C.DEFAULT_HORIZON_DAYS):
    """Everything that needs attention within `horizon` days, overdue first. One flat, sorted list."""
    user = request.user
    today = today or datetime.date.today()
    end = today + datetime.timedelta(days=horizon)
    rows = []
    qs = CalendarItem.objects.filter(company=company, status=OPEN, due_date__lte=end).select_related("assignee")
    for i in qs:
        days = (i.due_date - today).days
        rows.append({
            "kind": "filing", "source": "Compliance", "id": str(i.id), "title": i.title, "subtitle": i.period_label,
            "due_date": i.due_date, "days_left": days, "tone": _tone(days), "priority": i.priority,
            "assignee": common.user_name(i.assignee), "link": f"/compliance/filings?open={i.id}", "read_only": False,
            "reminder_today": days in (i.reminder_days or C.DEFAULT_REMINDER_DAYS), "category": i.category,
        })
    for c in Contract.objects.filter(company=company, status=Contract.Status.ACTIVE).exclude(expiry_date__isnull=True).select_related("owner"):
        eff = legal.effective_expiry(c, today)
        if eff <= end:
            days = (eff - today).days
            rows.append({"kind": "contract", "source": "Compliance", "id": str(c.id), "title": f"Contract: {c.title}",
                         "subtitle": f"{c.counterparty}" + (" (auto-renews)" if c.auto_renew else ""), "due_date": eff, "days_left": days,
                         "tone": _tone(days), "priority": "normal", "assignee": common.user_name(c.owner),
                         "link": f"/compliance/contracts?open={c.pk}", "read_only": False})
    for r in expiry_rows(company, user, today, ahead_days=horizon):
        if r["source"] == "company":
            rows.append({"kind": "licence", "source": "Company", "id": "", "title": f"{r['kind']}: {r['title']}", "subtitle": r["detail"],
                         "due_date": r["date"], "days_left": r["days_left"], "tone": r["tone"], "priority": "normal", "assignee": "",
                         "link": r["link"], "read_only": True})
    for case in LegalCase.objects.filter(company=company, next_hearing_date__isnull=False, next_hearing_date__lte=end).exclude(
            status__in=[LegalCase.Status.DISPOSED, LegalCase.Status.CLOSED]).select_related("owner"):
        days = (case.next_hearing_date - today).days
        rows.append({"kind": "hearing", "source": "Compliance", "id": str(case.id), "title": f"Hearing: {case.title}",
                     "subtitle": " ".join(x for x in (case.forum, case.case_number) if x), "due_date": case.next_hearing_date,
                     "days_left": days, "tone": _tone(days), "priority": "high", "assignee": case.lawyer or common.user_name(case.owner),
                     "link": f"/compliance/cases?open={case.pk}", "read_only": False})
    for p in Policy.objects.filter(company=company, status=Policy.Status.ACTIVE, review_date__isnull=False, review_date__lte=end):
        days = (p.review_date - today).days
        rows.append({"kind": "policy", "source": "Compliance", "id": str(p.id), "title": f"Policy review: {p.name}", "subtitle": f"Version {p.version}",
                     "due_date": p.review_date, "days_left": days, "tone": _tone(days), "priority": "low", "assignee": common.user_name(p.owner),
                     "link": f"/compliance/policies?open={p.pk}", "read_only": False})
    if request.user.has_code("tax.view"):
        rows.extend(tax_rows(request, today, horizon))
    rows.sort(key=lambda r: (r["due_date"], C.PRIORITY_ORDER.get(r["priority"], 2), r["title"]))
    return rows


# ---------------------------------------------------------------------------- summary and dashboard
def summary(company, request, today=None):
    today = today or datetime.date.today()
    horizon = today + datetime.timedelta(days=30)
    soon = today + datetime.timedelta(days=C.DUE_SOON_DAYS)
    open_items = CalendarItem.objects.filter(company=company, status=OPEN)
    agg = open_items.aggregate(
        overdue=Count("id", filter=Q(due_date__lt=today)),
        due_soon=Count("id", filter=Q(due_date__gte=today, due_date__lte=soon)),
        due_30=Count("id", filter=Q(due_date__gte=today, due_date__lte=horizon)))
    nxt = open_items.filter(due_date__gte=today).order_by("due_date", "title").first()
    contracts = [legal.contract_state(c, today) for c in Contract.objects.filter(company=company, status=Contract.Status.ACTIVE)]
    licences = [r for r in expiry_rows(company, request.user, today, ahead_days=30) if r["source"] == "company"]
    hearings = LegalCase.objects.filter(company=company, next_hearing_date__gte=today, next_hearing_date__lte=horizon).exclude(
        status__in=[LegalCase.Status.DISPOSED, LegalCase.Status.CLOSED]).count()
    fy = schedule.fy_for_date(today)
    tax = tax_rows(request, today, 30) if request.user.has_code("tax.view") else []
    return {
        "tax_due_30_days": len(tax), "tax_overdue": sum(1 for t in tax if t["days_left"] < 0),
        "fy": fy, "overdue": agg["overdue"], "due_soon": agg["due_soon"], "due_in_30_days": agg["due_30"],
        "contracts_expiring": sum(1 for s in contracts if s in ("expiring", "expired")),
        "licences_expiring": len(licences), "hearings_30_days": hearings, "score": score(company, fy, today),
        "attention": agg["overdue"] + agg["due_soon"],
        "next_due": {"id": str(nxt.id), "title": nxt.title, "period": nxt.period_label, "due_date": nxt.due_date,
                     "days_left": (nxt.due_date - today).days} if nxt else None,
    }


def score(company, fy, today):
    """% of this year's due items filed on time. Counts items already due (not marked not applicable); None when nothing has fallen due."""
    due = CalendarItem.objects.filter(company=company, fy_label=fy, due_date__lte=today).exclude(status=NA)
    total = due.count()
    if not total:
        return None
    ontime = sum(1 for i in due.filter(status=FILED).only("due_date", "filed_date") if i.filed_date <= i.due_date)
    return round(ontime * 100 / total, 1)


def dashboard(company, request, fy=None, today=None):
    today = today or datetime.date.today()
    fy = fy or schedule.fy_for_date(today)
    schedule.parse_fy(fy)
    items = list(CalendarItem.objects.filter(company=company, fy_label=fy).select_related("assignee"))
    keys = [schedule.status_key(i.status, i.due_date, today) for i in items]
    status_counts = Counter(keys)
    categories = Counter(i.category for i in items if i.status != NA)
    month_rows = {ym: {"on_time": 0, "late": 0, "overdue": 0, "pending": 0} for ym in schedule.fy_months(fy)}
    parts = {"on_time": 0, "late": 0, "overdue": 0, "pending": 0}
    work = Counter()
    for i, k in zip(items, keys):
        if i.status == NA:
            continue
        if i.status == FILED:
            bucket = "on_time" if i.filed_date <= i.due_date else "late"
        elif k == "overdue":
            bucket = "overdue"
        else:
            bucket = "pending"
        parts[bucket] += 1
        slot = month_rows.get((i.due_date.year, i.due_date.month))
        if slot is not None:
            slot[bucket] += 1
        if i.status == OPEN:
            work[common.user_name(i.assignee) or "Unassigned"] += 1
    open_all = CalendarItem.objects.filter(company=company, status=OPEN).select_related("assignee")
    horizon = today + datetime.timedelta(days=30)
    upcoming = [filings.item_dict(i, today) for i in open_all.filter(due_date__lte=today + datetime.timedelta(days=90)).order_by("due_date", "title")[:8]]
    watch = expiry_rows(company, request.user, today, ahead_days=90)
    fys = sorted(set(CalendarItem.objects.filter(company=company).values_list("fy_label", flat=True)) | {fy}, reverse=True)
    return {
        "fy": fy, "fys": fys, "today": today, "score": score(company, fy, today),
        "overdue": open_all.filter(due_date__lt=today).count(),
        "due_in_30_days": open_all.filter(due_date__gte=today, due_date__lte=horizon).count(),
        "due_soon": open_all.filter(due_date__gte=today, due_date__lte=today + datetime.timedelta(days=C.DUE_SOON_DAYS)).count(),
        "filed": status_counts["filed"], "total": sum(1 for i in items if i.status != NA),
        "generated": bool(items), "upcoming": upcoming,
        "by_category": [{"key": k, "label": C.CATEGORY_LABELS[k], "value": categories.get(k, 0)} for k, _ in C.CATEGORIES if categories.get(k)],
        "by_status": [{"key": k, "label": C.STATUS_LABELS[k], "value": status_counts.get(k, 0)} for k in C.STATUS_LABELS],
        "monthly": [{"label": datetime.date(y, m, 1).strftime("%b %y"), **month_rows[(y, m)]} for y, m in schedule.fy_months(fy)],
        "on_time_split": parts,
        "workload": [{"label": k, "value": v} for k, v in work.most_common(10)],
        "expiry_watch": watch[:8], "expiry_watch_total": len(watch),
    }


# ---------------------------------------------------------------------------- company registration viewer
def registry(company, user):
    """LLP master data from the company module, read only. Identifiers are masked unless the user can manage compliance."""
    manage = user.has_code("compliance.manage")
    show = (lambda v: v) if manage else mask
    office = CompanyAddress.objects.filter(company=company, is_active=True).order_by("-is_primary", "address_type").first()
    partners = []
    for p in CompanyPartner.objects.filter(company=company).order_by("-is_designated_partner", "full_name"):
        partners.append({
            "id": str(p.id), "name": p.full_name, "type": p.get_partner_type_display(), "designated": p.is_designated_partner,
            "dpin": show(p.dpin) if p.dpin else "", "appointed": p.appointment_date, "ceased": p.cessation_date, "active": p.is_active,
        })
    return {
        "legal_name": company.legal_name, "display_name": company.display_name, "entity_type": company.get_legal_entity_type_display(),
        "llpin": company.llpin, "pan": show(company.pan), "tan": show(company.tan), "gstin": show(company.gstin),
        "date_of_incorporation": company.date_of_incorporation, "certificate_reference": company.incorporation_certificate_reference,
        "registered_office": (f"{office.line1}, {office.city}, {office.state} {office.postal_code}".strip() if office else ""),
        "partners": partners, "designated_partners": sum(1 for p in partners if p["designated"] and p["active"]),
        "source": "Company profile",
    }
