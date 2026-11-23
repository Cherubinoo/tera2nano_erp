"""Shared view helpers: permission checks, lookups that 404 cleanly, and the dict builders for each record."""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.company.api import current_company

from .models import Employee


def need(request, *codes):
    """Allow the request when the user holds any of the codes."""
    if not any(request.user.has_code(c) for c in codes):
        raise PermissionDenied("Your role does not allow this.")


def me(request):
    """The signed-in user's employee record, or None."""
    return getattr(request.user, "employee", None)


def get_or_404(model, pk, label, **extra):
    try:
        obj = model.objects.filter(pk=pk, **extra).first()
    except (ValueError, DjangoValidationError):
        obj = None
    if obj is None:
        raise NotFound(f"No such {label}.")
    return obj


def get_employee(pk, company=None):
    return get_or_404(Employee, pk, "employee", company=company or current_company())


def is_self(request, employee):
    mine = me(request)
    return mine is not None and mine.pk == employee.pk


def sees_salary(request, employee):
    return request.user.has_code("hr.payroll") or is_self(request, employee)


def is_team_lead(request, employee):
    mine = me(request)
    return mine is not None and employee.manager_id == mine.pk


def ref(obj):
    return {"id": str(obj.pk), "name": obj.name} if obj is not None else None


def employee_dict(e, request, *, detail=False, reveal=False):
    """What `request.user` may see of an employee. Directory fields for hr.view; personal and payroll identifiers (masked) for
    hr.manage, hr.payroll and the person themselves; full identifiers only on request, for hr.manage, and audited by the view."""
    u = request.user
    full_access = u.has_code("hr.manage") or u.has_code("hr.payroll") or is_self(request, e)
    out = {
        "id": str(e.id), "code": e.code, "name": e.name, "first_name": e.first_name, "last_name": e.last_name,
        "work_email": e.work_email, "phone": e.phone, "department": ref(e.department), "designation": ref(e.designation),
        "manager": ref(e.manager), "employment_type": e.employment_type, "employment_type_label": e.get_employment_type_display(),
        "work_location": e.work_location, "status": e.status, "status_label": e.get_status_display(),
        "date_of_joining": e.date_of_joining, "exit_date": e.exit_date, "exit_reason": e.exit_reason,
        "has_login": e.user_id is not None, "is_self": is_self(request, e),
    }
    if full_access:
        out.update({
            "date_of_birth": e.date_of_birth, "gender": e.gender, "personal_email": e.personal_email, "address": e.address,
            "emergency_name": e.emergency_name, "emergency_relation": e.emergency_relation, "emergency_phone": e.emergency_phone,
            "pan_masked": e.masked("pan"), "aadhaar_masked": e.masked("aadhaar"), "bank_account_masked": e.masked("bank_account"),
            "bank_name": e.bank_name, "bank_ifsc": e.bank_ifsc, "bank_account_holder": e.bank_account_holder,
            "uan": e.uan, "esi_number": e.esi_number,
        })
    if detail:
        out["user_id"] = str(e.user_id) if e.user_id else None
        out["username"] = e.user.get_username() if e.user_id else ""
        out["report_count"] = e.reports.exclude(status="exited").count()
        out["created_at"] = e.created_at
        if reveal and u.has_code("hr.manage"):
            out["pan"], out["aadhaar"], out["bank_account_number"] = e.reveal("pan"), e.reveal("aadhaar"), e.reveal("bank_account")
    return out


def brief(e):
    return {"id": str(e.id), "name": e.name, "code": e.code}
