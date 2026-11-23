"""Employees, departments and designations: validation, saving, who-sees-what, and the org chart."""
import datetime
import re

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Count, Q

from apps.company.constants import DocType
from apps.company.services import numbering
from apps.core import crypto, india

from ..models import Department, Designation, Employee, LeaveRequest, ProjectAssignment
from . import common
from .common import Action, boolean, choice, date_value, text

PHONE_RE = re.compile(r"^[0-9+()\-. ]{7,20}$")
User = get_user_model()

EDITABLE_FIELDS = ("first_name", "last_name", "date_of_birth", "gender", "personal_email", "work_email", "phone", "address",
                   "emergency_name", "emergency_relation", "emergency_phone", "date_of_joining", "employment_type", "department",
                   "designation", "manager", "work_location", "status", "exit_date", "exit_reason", "bank_name", "bank_ifsc",
                   "bank_account_holder", "uan", "esi_number", "user")
SELF_FIELDS = ("phone", "address", "emergency_name", "emergency_relation", "emergency_phone")
SECRETS = {"pan": "pan", "aadhaar": "aadhaar", "bank_account_number": "bank_account"}


def snapshot(e):
    snap = common.snapshot(e, EDITABLE_FIELDS)
    # only a fingerprint or the last four characters: enough to see that a value changed (and redacted in the audit entity anyway)
    snap["pan"], snap["aadhaar"] = e.pan_hash[:10], e.aadhaar_hash[:10]
    snap["bank_account"] = e.bank_account_encrypted and e.bank_account_last4
    return snap


def _email(data, key, label):
    value = text(data, key, label, 254)
    if value:
        try:
            validate_email(value)
        except ValidationError:
            raise ValidationError({key: "Enter a valid email address."})
    return value


def _phone(data, key, label):
    value = text(data, key, label, 30)
    if value and not PHONE_RE.match(value):
        raise ValidationError({key: f"{label}: enter digits, spaces and + ( ) - only."})
    return value


def _pick(model, company, value, key, label):
    if value in (None, ""):
        return None
    try:
        obj = model.objects.filter(pk=value, company=company).first()
    except (ValueError, ValidationError):
        obj = None
    if obj is None:
        raise ValidationError({key: f"{label} not found."})
    return obj


def apply(company, e, data, *, creating):
    """Validate `data` and set it on the employee (not saved). Only keys present are touched on an update."""
    today = datetime.date.today()
    has = lambda k: creating or k in data  # noqa: E731
    if has("first_name"):
        e.first_name = text(data, "first_name", "First name", 80, required=True)
    if has("last_name"):
        e.last_name = text(data, "last_name", "Last name", 80)
    if has("date_of_birth"):
        dob = date_value(data, "date_of_birth", "Date of birth")
        if dob and (dob >= today or dob.year < 1920):
            raise ValidationError({"date_of_birth": "Enter a valid date of birth."})
        e.date_of_birth = dob
    if has("gender"):
        e.gender = choice(data, "gender", "gender", Employee.Gender.values, default="") or ""
    if has("personal_email"):
        e.personal_email = _email(data, "personal_email", "Personal email")
    if has("work_email"):
        e.work_email = _email(data, "work_email", "Work email")
    if has("phone"):
        e.phone = _phone(data, "phone", "Phone")
    if has("address"):
        e.address = text(data, "address", "Address", 600)
    if has("emergency_name"):
        e.emergency_name = text(data, "emergency_name", "Emergency contact name", 120)
    if has("emergency_relation"):
        e.emergency_relation = text(data, "emergency_relation", "Relationship", 60)
    if has("emergency_phone"):
        e.emergency_phone = _phone(data, "emergency_phone", "Emergency contact phone")
    if has("date_of_joining"):
        e.date_of_joining = date_value(data, "date_of_joining", "Date of joining", required=True)
        if e.date_of_joining > today + datetime.timedelta(days=366):
            raise ValidationError({"date_of_joining": "The joining date is too far in the future."})
        if e.date_of_birth and e.date_of_joining < e.date_of_birth + datetime.timedelta(days=365 * 14):
            raise ValidationError({"date_of_joining": "The joining date must be at least 14 years after the date of birth."})
    if has("employment_type"):
        e.employment_type = choice(data, "employment_type", "employment type", Employee.Type.values, default="full_time")
    if has("department"):
        e.department = _pick(Department, company, data.get("department"), "department", "Department")
    if has("designation"):
        e.designation = _pick(Designation, company, data.get("designation"), "designation", "Designation")
    if has("manager"):
        manager = _pick(Employee, company, data.get("manager"), "manager", "Reporting manager")
        if manager is not None:
            if e.pk and manager.pk == e.pk:
                raise ValidationError({"manager": "An employee cannot report to themselves."})
            up, hops = manager, 0
            while up is not None and hops < 50:  # a manager chain that returns to this employee is a loop
                if e.pk and up.manager_id == e.pk:
                    raise ValidationError({"manager": "That would make a reporting loop."})
                up, hops = up.manager, hops + 1
            if manager.status == Employee.Status.EXITED:
                raise ValidationError({"manager": "That person has left the company."})
        e.manager = manager
    if has("work_location"):
        e.work_location = text(data, "work_location", "Work location", 120)
    if has("status"):
        e.status = choice(data, "status", "status", Employee.Status.values, default="active")
    if has("exit_date") or has("status"):
        e.exit_date = date_value(data, "exit_date", "Exit date") if "exit_date" in data else e.exit_date
        if "exit_reason" in data:
            e.exit_reason = text(data, "exit_reason", "Exit reason", 300)
        if e.status == Employee.Status.EXITED:
            if not e.exit_date:
                raise ValidationError({"exit_date": "Enter the last working day for someone who has left."})
            if e.exit_date < e.date_of_joining:
                raise ValidationError({"exit_date": "The exit date cannot be before the joining date."})
        elif e.status == Employee.Status.ACTIVE:
            e.exit_date, e.exit_reason = None, ""
        elif e.exit_date and e.exit_date < e.date_of_joining:
            raise ValidationError({"exit_date": "The exit date cannot be before the joining date."})
    if has("bank_name"):
        e.bank_name = text(data, "bank_name", "Bank name", 120)
    if has("bank_ifsc"):
        e.bank_ifsc = text(data, "bank_ifsc", "IFSC", 11).upper()
        if e.bank_ifsc and not india.IFSC_RE.match(e.bank_ifsc):
            raise ValidationError({"bank_ifsc": "Enter a valid 11-character IFSC code."})
    if has("bank_account_holder"):
        e.bank_account_holder = text(data, "bank_account_holder", "Account holder name", 150)
    if has("uan"):
        e.uan = text(data, "uan", "UAN", 12)
        if e.uan and not re.fullmatch(r"\d{12}", e.uan):
            raise ValidationError({"uan": "The UAN is 12 digits."})
    if has("esi_number"):
        e.esi_number = text(data, "esi_number", "ESI number", 17)
        if e.esi_number and not re.fullmatch(r"\d{10,17}", e.esi_number):
            raise ValidationError({"esi_number": "The ESI number is 10 to 17 digits."})
    # identifiers: present = replace, empty = clear
    if "pan" in data:
        pan = text(data, "pan", "PAN", 10).upper()
        if pan and not india.PAN_RE.match(pan):
            raise ValidationError({"pan": "Enter a valid 10-character PAN, for example ABCDE1234F."})
        e.set_secret("pan", pan)
        if pan and Employee.objects.exclude(pk=e.pk).filter(pan_hash=e.pan_hash).exists():
            raise ValidationError({"pan": "Another employee already has this PAN."})
    if "aadhaar" in data:
        aadhaar = re.sub(r"[\s-]", "", text(data, "aadhaar", "Aadhaar", 14))
        if aadhaar and not re.fullmatch(r"[2-9]\d{11}", aadhaar):
            raise ValidationError({"aadhaar": "The Aadhaar number is 12 digits."})
        e.set_secret("aadhaar", aadhaar)
        if aadhaar and Employee.objects.exclude(pk=e.pk).filter(aadhaar_hash=e.aadhaar_hash).exists():
            raise ValidationError({"aadhaar": "Another employee already has this Aadhaar number."})
    if "bank_account_number" in data:
        number = text(data, "bank_account_number", "Account number", 20)
        if number and not india.ACCOUNT_RE.match(number):
            raise ValidationError({"bank_account_number": "The account number is 6 to 20 digits."})
        e.set_secret("bank_account", number)
    if e.bank_account_last4 and not e.bank_ifsc:
        raise ValidationError({"bank_ifsc": "Enter the IFSC code for the salary account."})
    if "user_id" in data or (creating and "user" in data):
        uid = data.get("user_id", data.get("user"))
        if uid in (None, ""):
            e.user = None
        else:
            try:
                user = User.objects.filter(pk=uid, is_active=True).first()
            except (ValueError, ValidationError):
                user = None
            if user is None:
                raise ValidationError({"user_id": "No such active user."})
            if Employee.objects.exclude(pk=e.pk).filter(user=user).exists():
                raise ValidationError({"user_id": "That user is already linked to another employee."})
            e.user = user


def create(company, data, actor):
    e = Employee(company=company)
    apply(company, e, data, creating=True)
    e.code = numbering.allocate_number(company, DocType.EMPLOYEE, actor=actor).reference_number
    e.save()
    common.log(company, actor, "employee", e.pk, Action.CREATED, new=snapshot(e))
    return e


def update(e, data, actor, *, fields=None):
    before = snapshot(e)
    was = e.status
    if fields is not None:
        data = {k: v for k, v in data.items() if k in fields}
    apply(e.company, e, data, creating=False)
    e.save()
    after = snapshot(e)
    if before != after:
        action = Action.DEACTIVATED if (was != "exited" and e.status == "exited") else Action.UPDATED
        common.log(e.company, actor, "employee", e.pk, action, previous=before, new=after, reason=e.exit_reason if action == Action.DEACTIVATED else "")
    if was != "exited" and e.status == "exited":
        wind_down(e)
    return e


def wind_down(e):
    """What leaving the company ends: waiting leave requests are withdrawn and open project assignments are closed."""
    LeaveRequest.objects.filter(employee=e, status__in=("draft", "pending_approval")).update(status="cancelled", decision_note="Employee left")
    ProjectAssignment.objects.filter(employee=e).filter(Q(end_date__isnull=True) | Q(end_date__gt=e.exit_date)).update(end_date=e.exit_date)


def can_delete(e):
    """A record made by mistake can be deleted; one with history cannot (mark the person as exited instead)."""
    blockers = []
    if e.payslips.exists():
        blockers.append("payslips")
    if e.leave_requests.exists():
        blockers.append("leave requests")
    if e.attendance.exists():
        blockers.append("attendance")
    if e.reviews.exists() or e.goals.exists():
        blockers.append("performance records")
    if e.assignments.exists():
        blockers.append("project assignments")
    if e.reports.exists():
        blockers.append("people reporting to them")
    return blockers


# --------------------------------------------------------------------------- departments and designations

def named(model, company, data, instance=None, label="Name"):
    name = text(data, "name", label, 120, required=True)
    clash = model.objects.filter(company=company, name__iexact=name)
    if instance is not None:
        clash = clash.exclude(pk=instance.pk)
    if clash.exists():
        raise ValidationError({"name": f"{label} already exists."})
    return name


def department_rows(company):
    counts = dict(Employee.objects.filter(company=company).exclude(status="exited").values_list("department_id").annotate(n=Count("id")))
    rows = []
    for d in Department.objects.filter(company=company).select_related("head"):
        rows.append({"id": str(d.id), "name": d.name, "description": d.description, "is_active": d.is_active,
                     "head": {"id": str(d.head_id), "name": d.head.name} if d.head_id else None, "headcount": counts.get(d.pk, 0)})
    return rows


def designation_rows(company):
    counts = dict(Employee.objects.filter(company=company).exclude(status="exited").values_list("designation_id").annotate(n=Count("id")))
    return [{"id": str(d.id), "name": d.name, "is_active": d.is_active, "headcount": counts.get(d.pk, 0)}
            for d in Designation.objects.filter(company=company)]


def org_chart(company):
    """Reporting tree of everyone still with the company. People whose manager has left appear at the top."""
    people = list(Employee.objects.filter(company=company).exclude(status="exited").select_related("department", "designation"))
    nodes = {p.pk: {"id": str(p.id), "name": p.name, "code": p.code, "designation": p.designation.name if p.designation else "",
                    "department": p.department.name if p.department else "", "reports": []} for p in people}
    roots = []
    for p in people:
        parent = nodes.get(p.manager_id)
        (parent["reports"] if parent else roots).append(nodes[p.pk])
    return roots


def lookup(company):
    return [{"id": str(e.id), "code": e.code, "name": e.name, "department": e.department.name if e.department else "",
             "designation": e.designation.name if e.designation else "", "user_id": str(e.user_id) if e.user_id else None}
            for e in Employee.objects.filter(company=company).exclude(status="exited").select_related("department", "designation")]
