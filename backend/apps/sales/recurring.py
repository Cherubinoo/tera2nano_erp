"""Recurring invoices: schedules, profiles and the idempotent generator.

A profile is anchored on its start date. The n-th run is the start date plus n periods (weeks, or whole months counted from
the anchor and clamped to the end of a short month), so a profile that starts on 31 January bills on 28 (or 29) February,
31 March, 30 April and so on, and one that starts on 29 February bills on 28 February in other years. Nothing is ever
derived from the previous run date, so a short month never drags later runs earlier.

`run_due` creates every invoice due on or before a date, at most `MAX_CATCH_UP` per profile per call (the rest stay due and
are created by the next call), never twice for the same (profile, run date): the generated invoice records both, and the
database refuses a second one. Invoices are made through the normal sales service (`save_draft`, `issue`), so numbering, GST
and posting to the books are exactly those of an invoice typed by hand.
"""
import calendar
import datetime
import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.company.constants import DocType
from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import numbering

from . import services
from .models import Customer, RecurringInvoice, SalesDocument

log = logging.getLogger(__name__)
Status, Mode, Freq = RecurringInvoice.Status, RecurringInvoice.Mode, RecurringInvoice.Frequency
Action = CompanyProfileAuditEvent.Action

MAX_CATCH_UP = 12          # invoices one profile creates in one run_due call; the rest stay due for the next call
MAX_YEAR = 2100
FIXED_STEPS = {Freq.WEEKLY: ("days", 7), Freq.MONTHLY: ("months", 1), Freq.QUARTERLY: ("months", 3),
               Freq.HALF_YEARLY: ("months", 6), Freq.YEARLY: ("months", 12)}
FORM_KEYS = ("items", "subject", "enquiry_ref", "include_bank", "show_signature")


# --------------------------------------------------------------------------- schedule

def step_for(frequency, unit="", count=None):
    """(unit, size) of one period: ("days", 7), ("months", 3) ..."""
    if frequency == Freq.CUSTOM:
        return unit, count
    return FIXED_STEPS[frequency]


def profile_step(p):
    return step_for(p.frequency, p.interval_unit, p.interval_count)


def add_months(anchor, months):
    """`anchor` moved on by whole months, its day clamped to the length of the month it lands in."""
    total = anchor.month - 1 + months
    year, month = anchor.year + total // 12, total % 12 + 1
    return datetime.date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def occurrence(anchor, step, n):
    """The n-th run date (0 is the anchor itself)."""
    unit, size = step
    if unit == "days":
        return anchor + datetime.timedelta(days=size * n)
    return add_months(anchor, size * n)


def first_on_or_after(anchor, step, day):
    """The first scheduled run date that is `day` or later."""
    if day <= anchor:
        return anchor
    unit, size = step
    if unit == "days":
        return occurrence(anchor, step, -(-(day - anchor).days // size))
    n = max(0, ((day.year - anchor.year) * 12 + day.month - anchor.month) // size - 1)
    while occurrence(anchor, step, n) < day:
        n += 1
    return occurrence(anchor, step, n)


def next_after(anchor, step, day):
    return first_on_or_after(anchor, step, day + datetime.timedelta(days=1))


def schedule_dates(anchor, step, *, first=None, count=6, end_date=None, remaining=None):
    """The next `count` run dates from `first` (default: the anchor), stopping at `end_date` or after `remaining` runs."""
    out, day = [], first or anchor
    while len(out) < count and (remaining is None or len(out) < remaining) and (end_date is None or day <= end_date):
        out.append(day)
        day = next_after(anchor, step, day)
    return out


# --------------------------------------------------------------------------- input

def _text(value, label, limit, required=False):
    value = (value or "").strip() if isinstance(value, str) or value is None else None
    if value is None:
        raise ValidationError({label: "Send text."})
    if required and not value:
        raise ValidationError({label: "This is required."})
    if len(value) > limit:
        raise ValidationError({label: f"Use at most {limit} characters."})
    return value


def _date(value, label, required=False):
    if value in (None, ""):
        if required:
            raise ValidationError({label: "Enter a date."})
        return None
    if isinstance(value, datetime.date):
        d = value
    else:
        try:
            d = datetime.date.fromisoformat(str(value))
        except ValueError:
            raise ValidationError({label: "Use a date in YYYY-MM-DD format."})
    if not 2000 <= d.year <= MAX_YEAR:
        raise ValidationError({label: f"Use a date between 2000 and {MAX_YEAR}."})
    return d


def _int(value, label, low, high, required=False):
    if value in (None, ""):
        if required:
            raise ValidationError({label: "Enter a number."})
        return None
    try:
        n = int(str(value).strip())
    except ValueError:
        raise ValidationError({label: "Enter a whole number."})
    if not low <= n <= high:
        raise ValidationError({label: f"Use a number from {low} to {high}."})
    return n


def _form(raw):
    if not isinstance(raw, dict):
        raise ValidationError({"form": "Send the invoice template as an object."})
    form = {k: v for k, v in raw.items() if k in FORM_KEYS}
    for key in ("subject", "enquiry_ref"):
        if key in form:
            form[key] = _text(form[key], key, 200)
            if not form[key]:
                del form[key]
    if not isinstance(form.get("items"), list) or not form["items"]:
        raise ValidationError({"items": "Add at least one item."})
    return form


def _snapshot(p):
    return {"name": p.name, "customer": p.customer.display_name, "status": p.status, "mode": p.mode,
            "frequency": p.frequency, "interval": f"{p.interval_count} {p.interval_unit}" if p.frequency == Freq.CUSTOM else "",
            "start_date": p.start_date.isoformat(), "end_date": p.end_date.isoformat() if p.end_date else "",
            "max_occurrences": p.max_occurrences, "due_days": p.due_days, "apply_terms": p.apply_terms,
            "items": len(p.form.get("items") or []), "project": str(p.project_id) if p.project_id else ""}


def build_form(p):
    """The `form` of one generated invoice: the template, plus notes and terms when they are to be applied."""
    form = {k: v for k, v in p.form.items() if k in FORM_KEYS}
    if p.apply_terms:
        if p.notes.strip():
            form["notes"] = p.notes.strip()
        if p.terms.strip():
            form["terms"] = p.terms.strip()
    return form


def _parse(company, data, current, can_issue):
    """The checked field values for a create (current is None) or an edit; keys missing from `data` keep their value."""
    def get(key, default=None):
        return data[key] if key in data else (getattr(current, key) if current is not None else default)

    out = {}
    out["name"] = _text(get("name", ""), "name", 150, required=True)
    if "customer" in data or current is None:
        customer = Customer.objects.filter(pk=data.get("customer") or None, company=company).first() if _is_uuid(data.get("customer")) else None
        if customer is None:
            raise ValidationError({"customer": "Choose the customer."})
    else:
        customer = current.customer
    if not customer.is_active:
        raise ValidationError({"customer": "This customer is switched off."})
    out["customer"] = customer

    mode = get("mode", Mode.DRAFT)
    if mode not in Mode.values:
        raise ValidationError({"mode": "Choose draft or issue."})
    if mode == Mode.ISSUE and not can_issue and (current is None or current.mode != Mode.ISSUE):
        raise ValidationError({"mode": "Your role can create draft invoices only. Choose \"Create as draft\"."})
    out["mode"] = mode

    freq = get("frequency", Freq.MONTHLY)
    if freq not in Freq.values:
        raise ValidationError({"frequency": "Choose how often the invoice repeats."})
    out["frequency"] = freq
    if freq == Freq.CUSTOM:
        unit = get("interval_unit", "")
        if unit not in RecurringInvoice.Unit.values:
            raise ValidationError({"interval_unit": "Choose days or months."})
        out["interval_unit"] = unit
        out["interval_count"] = _int(get("interval_count"), "interval_count", 1, 365 if unit == "days" else 60, required=True)
    else:
        out["interval_unit"], out["interval_count"] = "", None

    out["start_date"] = _date(get("start_date"), "start_date", required=True)
    out["end_date"] = _date(get("end_date"), "end_date")
    if out["end_date"] and out["end_date"] < out["start_date"]:
        raise ValidationError({"end_date": "The end date cannot be before the start date."})
    out["max_occurrences"] = _int(get("max_occurrences"), "max_occurrences", 1, 1000)
    out["due_days"] = _int(get("due_days"), "due_days", 0, 365)
    out["apply_terms"] = bool(get("apply_terms", False))
    out["notes"] = _text(get("notes", ""), "notes", 2000)
    out["terms"] = _text(get("terms", ""), "terms", 4000)

    form = _form(data["form"] if "form" in data else (current.form if current is not None else {}))
    try:  # prices the items now, so a bad item or GST rate is refused when the profile is saved, not when it runs
        services.work_out(company, customer, SalesDocument.Kind.INVOICE, form, out["start_date"], None)
    except ValidationError as exc:
        raise ValidationError({"items": "; ".join(exc.messages)})
    out["form"] = form
    project = data.get("project", data.get("project_id")) if ("project" in data or "project_id" in data) else (
        current.project_id if current is not None else None)
    out["project_id"] = services.resolve_project(company, project, current=current.project_id if current is not None else None)
    return out


def _is_uuid(value):
    import uuid
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def _settle(p):
    """Mark the profile completed once it has made all its invoices or its next run is past the end date."""
    if p.status in (Status.STOPPED, Status.COMPLETED):
        return
    done = p.max_occurrences is not None and p.occurrences_done >= p.max_occurrences
    over = p.next_run_date is not None and p.end_date is not None and p.next_run_date > p.end_date
    if done or over:
        p.status, p.next_run_date = Status.COMPLETED, None


# --------------------------------------------------------------------------- profiles

@transaction.atomic
def create(company, actor, data, *, can_issue):
    values = _parse(company, data, None, can_issue)
    p = RecurringInvoice(company=company, created_by=actor, next_run_date=values["start_date"], **values)
    p.reference_code = numbering.allocate_number(company, DocType.RECURRING_INVOICE, actor=actor).reference_number
    _settle(p)
    p.save()
    services._audit(company, actor, "recurring_invoice", p.pk, Action.CREATED, new=_snapshot(p))
    return p


def _locked(pk):
    return RecurringInvoice.objects.select_for_update(of=("self",)).select_related("customer", "company").get(pk=pk)


@transaction.atomic
def update(profile, actor, data, *, can_issue, today=None):
    """Change a profile. Invoices already made are untouched; the new details apply from the next run."""
    today = today or datetime.date.today()
    p = _locked(profile.pk)
    if p.status in (Status.STOPPED, Status.COMPLETED):
        raise ValidationError(f"A {p.get_status_display().lower()} profile cannot be edited. Create a new one instead.")
    before = _snapshot(p)
    values = _parse(p.company, data, p, can_issue)
    schedule = ("frequency", "interval_unit", "interval_count", "start_date")
    changed = any(values[k] != getattr(p, k) for k in schedule)
    if values["start_date"] != p.start_date and p.occurrences_done:
        raise ValidationError({"start_date": "Invoices have already been made, so the start date cannot change."})
    for key, value in values.items():
        setattr(p, key, value)
    if changed:
        if not p.occurrences_done:
            p.next_run_date = p.start_date
        else:  # new rhythm from the next run on: never back-fill the periods the old rhythm covered
            p.next_run_date = first_on_or_after(p.start_date, profile_step(p), max(today, p.last_run_date + datetime.timedelta(days=1)))
    _settle(p)
    p.save()
    services._audit(p.company, actor, "recurring_invoice", p.pk, Action.UPDATED, previous=before, new=_snapshot(p))
    return p


@transaction.atomic
def pause(profile, actor):
    p = _locked(profile.pk)
    if p.status != Status.ACTIVE:
        raise ValidationError("Only an active profile can be paused.")
    p.status = Status.PAUSED
    p.save(update_fields=["status", "updated_at"])
    services._audit(p.company, actor, "recurring_invoice", p.pk, Action.DEACTIVATED, previous={"status": "active"}, new={"status": "paused"})
    return p


@transaction.atomic
def resume(profile, actor, today=None):
    """Paused -> active. Runs missed while paused are not made: the next run is the first scheduled date from today."""
    today = today or datetime.date.today()
    p = _locked(profile.pk)
    if p.status != Status.PAUSED:
        raise ValidationError("Only a paused profile can be resumed.")
    if p.next_run_date and p.next_run_date < today:
        p.next_run_date = first_on_or_after(p.start_date, profile_step(p), today)
    p.status = Status.ACTIVE
    _settle(p)
    p.save()
    services._audit(p.company, actor, "recurring_invoice", p.pk, Action.ACTIVATED, previous={"status": "paused"},
                    new={"status": p.status, "next_run_date": p.next_run_date.isoformat() if p.next_run_date else ""})
    return p


@transaction.atomic
def stop(profile, actor, reason=""):
    p = _locked(profile.pk)
    if p.status not in (Status.ACTIVE, Status.PAUSED):
        raise ValidationError("Only an active or paused profile can be stopped.")
    previous = p.status
    p.status, p.next_run_date, p.stop_reason = Status.STOPPED, None, (reason or "").strip()[:300]
    p.save()
    services._audit(p.company, actor, "recurring_invoice", p.pk, Action.DEACTIVATED, previous={"status": previous},
                    new={"status": "stopped"}, reason=p.stop_reason or "Stopped")
    return p


@transaction.atomic
def delete(profile, actor):
    p = _locked(profile.pk)
    if p.occurrences_done or SalesDocument.objects.filter(recurring_id=p.pk).exists():
        raise ValidationError("This profile has already made invoices. Stop it instead of deleting it.")
    services._audit(p.company, actor, "recurring_invoice", p.pk, Action.DELETED, previous=_snapshot(p))
    p.delete()


# --------------------------------------------------------------------------- generation

def _generate(pk, actor, today, can_issue, require_due):
    """Make the next invoice of a profile (one run) and move the schedule on. Raises on any problem; the caller rolls back."""
    p = _locked(pk)
    if p.status != Status.ACTIVE or p.next_run_date is None:
        raise ValidationError("This profile is not active.")
    run_date = p.next_run_date
    if require_due and run_date > today:
        raise ValidationError("This profile is not due yet.")
    if p.mode == Mode.ISSUE and not can_issue:
        raise ValidationError("Your role cannot issue invoices. Ask someone who can, or switch the profile to \"Create as draft\".")
    acting = actor or p.created_by
    doc = SalesDocument.objects.filter(recurring_id=p.pk, recurring_run_date=run_date).first()
    created = doc is None
    if created:
        issue_date = min(run_date, today)  # a run generated early by hand is dated today
        due = issue_date + datetime.timedelta(days=p.due_days) if p.due_days is not None else None
        doc = services.save_draft(p.company, acting, kind=SalesDocument.Kind.INVOICE, customer=p.customer, form=build_form(p),
                                  issue_date=issue_date, due_date=due, project_id=p.project_id)
        doc.recurring_id, doc.recurring_run_date = p.pk, run_date
        doc.save(update_fields=["recurring_id", "recurring_run_date", "updated_at"])
        if p.mode == Mode.ISSUE:
            doc = services.issue(doc, acting)
    p.occurrences_done += 1
    p.last_run_date, p.last_generated_at = run_date, timezone.now()
    p.last_error, p.last_error_at = "", None
    p.next_run_date = next_after(p.start_date, profile_step(p), run_date)
    _settle(p)
    p.save()
    services._audit(p.company, actor, "recurring_invoice", p.pk, Action.UPDATED,
                    new={"generated": doc.number or "draft invoice", "run_date": run_date.isoformat(),
                         "invoice": str(doc.pk), "occurrences_done": p.occurrences_done,
                         "next_run_date": p.next_run_date.isoformat() if p.next_run_date else ""},
                    reason=("Generated for the run of %s" % run_date.isoformat()) + ("" if created else " (invoice already existed)"))
    return doc


def _message(exc):
    if isinstance(exc, ValidationError):
        return "; ".join(exc.messages)
    detail = getattr(exc, "detail", None)  # DRF ValidationError
    if detail is not None:
        return str(detail)
    return "Unexpected error: %s" % exc


def _record_error(pk, message):
    RecurringInvoice.objects.filter(pk=pk).update(last_error=message[:500], last_error_at=timezone.now())


def attempt(pk, actor, today, *, can_issue=True, require_due=True):
    """One run of one profile in its own transaction. Returns (invoice, None) or (None, error message); a failure leaves the
    schedule where it was and is written on the profile as last_error."""
    try:
        with transaction.atomic():
            return _generate(pk, actor, today, can_issue, require_due), None
    except Exception as exc:  # noqa: BLE001 - one profile's failure must never stop the others
        if not isinstance(exc, (ValidationError,)) and getattr(exc, "detail", None) is None:
            log.exception("Recurring invoice %s failed", pk)
        message = _message(exc)
        _record_error(pk, message)
        return None, message


def generate_next(profile, actor, *, today=None, can_issue=True):
    """The "Run now" button: make the next invoice of an active profile whether or not its date has come."""
    today = today or datetime.date.today()
    p = RecurringInvoice.objects.get(pk=profile.pk)
    if p.status != Status.ACTIVE:
        raise ValidationError("Only an active profile can generate an invoice. Resume it first." if p.status == Status.PAUSED
                              else "This profile has finished; there is nothing left to generate.")
    if p.mode == Mode.ISSUE and not can_issue:
        raise ValidationError("Your role cannot issue invoices. Ask someone who can, or switch the profile to \"Create as draft\".")
    doc, error = attempt(p.pk, actor, today, can_issue=can_issue, require_due=False)
    if error:
        raise ValidationError(error)
    return doc


def run_due(company, today=None, *, actor=None, can_issue=True, limit=MAX_CATCH_UP):
    """Create the invoices due on or before `today` for every active profile.

    A profile catches up missed periods one run at a time, at most `limit` per call. Returns
    {"generated": [...], "errors": [...], "still_due": n profiles with more runs waiting}.
    """
    today = today or datetime.date.today()
    due = RecurringInvoice.objects.filter(company=company, status=Status.ACTIVE, next_run_date__lte=today)
    generated, errors, still_due = [], [], 0
    for p in due.order_by("next_run_date", "created_at"):
        if p.mode == Mode.ISSUE and not can_issue:
            continue  # this person may not issue; leave it for someone who can (or the scheduled command)
        made = 0
        while made < limit:
            run_date = RecurringInvoice.objects.values_list("next_run_date", flat=True).get(pk=p.pk)
            doc, error = attempt(p.pk, actor, today, can_issue=can_issue, require_due=True)
            if error:
                errors.append({"profile": str(p.pk), "name": p.name, "run_date": run_date.isoformat() if run_date else "", "error": error})
                break
            made += 1
            generated.append({"profile": str(p.pk), "name": p.name, "invoice": str(doc.pk), "number": doc.number,
                              "run_date": run_date.isoformat(), "status": doc.status})
            fresh = RecurringInvoice.objects.get(pk=p.pk)
            if fresh.status != Status.ACTIVE or fresh.next_run_date is None or fresh.next_run_date > today:
                break
        else:
            still_due += 1
    return {"generated": generated, "errors": errors, "still_due": still_due}


def due_count(company, today=None):
    today = today or datetime.date.today()
    return RecurringInvoice.objects.filter(company=company, status=Status.ACTIVE, next_run_date__lte=today).count()
