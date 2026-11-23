"""Activities: tasks, calls, meetings, notes and logged emails, with due-date buckets for the today / overdue / upcoming views."""
import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ..models import Activity
from . import common, leads
from .common import Action

FIELDS = ("type", "subject", "description", "due_date", "due_time", "priority", "outcome")
AUDIT_FIELDS = ("type", "subject", "due_date", "due_time", "priority", "status", "outcome", "owner", "lead", "contact", "account", "deal")


def bucket(activity, today):
    """completed, overdue (due before today), today, upcoming (due after today) or no_date."""
    if activity.status == "completed":
        return "completed"
    if activity.due_date is None:
        return "no_date"
    if activity.due_date < today:
        return "overdue"
    return "today" if activity.due_date == today else "upcoming"


def filter_view(qs, view, today):
    """Narrow a queryset to one of the list views."""
    open_ = qs.filter(status="open")
    if view == "today":
        return open_.filter(due_date=today)
    if view == "overdue":
        return open_.filter(due_date__lt=today)
    if view == "upcoming":
        return open_.filter(due_date__gt=today)
    if view == "open":
        return open_
    if view == "completed":
        return qs.filter(status="completed")
    return qs


def _touch_lead(activity):
    if activity.lead_id:
        leads.refresh_score(activity.lead)


@transaction.atomic
def create(company, actor, data, *, owner=None, lead=None, contact=None, account=None, deal=None):
    activity = Activity(company=company, created_by=actor, owner=owner or actor, lead=lead, contact=contact, account=account, deal=deal)
    common.apply_fields(activity, data, FIELDS, skip_clean=True)
    wants_done = data.get("status") == "completed"
    if activity.type == "note" or wants_done:
        activity.status, activity.completed_at = "completed", timezone.now()
    activity.full_clean(exclude=["company"], validate_unique=False, validate_constraints=False)
    activity.save()
    common.log(company, actor, "crm_activity", activity.pk, Action.CREATED, new=common.snapshot(activity, AUDIT_FIELDS))
    _touch_lead(activity)
    return activity


@transaction.atomic
def update(activity, actor, data, *, owner=...):
    before = common.snapshot(activity, AUDIT_FIELDS)
    common.apply_fields(activity, data, FIELDS)
    if owner is not ...:
        activity.owner = owner
    activity.save()
    after = common.snapshot(activity, AUDIT_FIELDS)
    if before != after:
        common.log(activity.company, actor, "crm_activity", activity.pk, Action.UPDATED, previous=before, new=after)
    return activity


@transaction.atomic
def complete(activity, actor, outcome=""):
    activity = Activity.objects.select_for_update(of=("self",)).get(pk=activity.pk)
    if activity.status == "completed":
        raise ValidationError("This activity is already completed.")
    before = common.snapshot(activity, AUDIT_FIELDS)
    activity.status, activity.completed_at = "completed", timezone.now()
    if (outcome or "").strip():
        activity.outcome = outcome.strip()[:500]
    activity.save()
    common.log(activity.company, actor, "crm_activity", activity.pk, Action.UPDATED, previous=before,
               new=common.snapshot(activity, AUDIT_FIELDS), reason="Completed")
    _touch_lead(activity)
    return activity


@transaction.atomic
def reopen(activity, actor):
    activity = Activity.objects.select_for_update(of=("self",)).get(pk=activity.pk)
    if activity.status != "completed":
        raise ValidationError("This activity is not completed.")
    if activity.type == "note":
        raise ValidationError("A note cannot be reopened.")
    before = common.snapshot(activity, AUDIT_FIELDS)
    activity.status, activity.completed_at = "open", None
    activity.save()
    common.log(activity.company, actor, "crm_activity", activity.pk, Action.UPDATED, previous=before,
               new=common.snapshot(activity, AUDIT_FIELDS), reason="Reopened")
    _touch_lead(activity)
    return activity


@transaction.atomic
def delete(activity, actor):
    common.log(activity.company, actor, "crm_activity", activity.pk, Action.DELETED, previous=common.snapshot(activity, AUDIT_FIELDS))
    lead = activity.lead
    activity.delete()
    if lead is not None:
        leads.refresh_score(lead)
