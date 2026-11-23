"""The compliance calendar: generating a financial year's due dates, and filing, reassigning and closing items."""
import datetime
import uuid

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .. import constants as C
from ..models import CalendarItem, Obligation
from . import catalogue, common, penalty, schedule

ITEM_AUDIT = ("title", "form_no", "category", "due_date", "status", "assignee_id", "priority", "reminder_days", "filed_date",
              "ack_number", "fee_paid", "penalty_paid", "remarks", "na_reason", "reference", "period_label")
OPEN, FILED, NA = CalendarItem.Status.OPEN, CalendarItem.Status.FILED, CalendarItem.Status.NOT_APPLICABLE


# ---------------------------------------------------------------------------- generation
def default_assignees():
    """{role: user} using the longest-serving active user of each role, so generated items arrive with an owner."""
    out = {}
    for user in common.assignable_users().order_by("date_joined", "username"):
        out.setdefault(user.role, user)
    return out


def generate(company, fy, actor=None):
    """Create the calendar for financial year `fy` ('2026-27'). Safe to run again at any time.

    A missing item is created. An open item whose rule now gives another date is moved, unless someone moved it by hand.
    Filed and not-applicable items are never touched. Returns counts.
    """
    schedule.parse_fy(fy)
    catalogue.seed_catalogue(company)
    assignees = default_assignees()
    existing = {(i.obligation_id, i.period_key): i for i in CalendarItem.objects.filter(company=company, fy_label=fy, obligation__isnull=False)}
    # A rule that changed its period keys (a monthly lag edited) must not make a second item for the same due date.
    result = {"fy": fy, "created": 0, "updated": 0, "unchanged": 0, "skipped_closed": 0}
    fresh, changed = [], []
    for ob in Obligation.objects.filter(company=company, enabled=True).exclude(frequency="event"):
        if ob.due_rule.get("type") not in ("dates", "monthly"):
            continue
        for due in schedule.due_dates(ob.due_rule, fy):
            item = existing.get((ob.pk, due.period_key))
            if item is None:
                user = assignees.get(ob.assignee_role)
                fresh.append(CalendarItem(
                    company=company, obligation=ob, fy_label=fy, period_key=due.period_key, period_label=due.period_label,
                    title=ob.name, form_no=ob.form_no, category=ob.category, penalty_rule=ob.penalty_rule, due_date=due.due_date,
                    assignee=user, priority=ob.default_priority, reminder_days=ob.reminder_days or list(C.DEFAULT_REMINDER_DAYS)))
            elif item.status != OPEN:
                result["skipped_closed"] += 1
            elif item.due_date != due.due_date and not item.due_date_overridden:
                item.due_date = due.due_date
                item.period_label = due.period_label
                changed.append(item)
            else:
                result["unchanged"] += 1
    with transaction.atomic():
        CalendarItem.objects.bulk_create(fresh, ignore_conflicts=True)
        if changed:
            CalendarItem.objects.bulk_update(changed, ["due_date", "period_label", "updated_at"])
        result["created"], result["updated"] = len(fresh), len(changed)
        if fresh or changed:
            common.log(company, actor, "compliance_item", None, "created" if fresh else "updated",
                       new={"fy": fy, "created": len(fresh), "moved": len(changed), "what": "calendar generated"})
    return result


# ---------------------------------------------------------------------------- reading
def item_dict(item, today=None, *, users=None):
    today = today or datetime.date.today()
    key = schedule.status_key(item.status, item.due_date, today)
    late = None
    on_time = None
    if item.status == FILED and item.filed_date:
        late = schedule.days_late(item.due_date, item.filed_date)
        on_time = late == 0
    elif key == "overdue":
        late = (today - item.due_date).days
    est = None
    if late and item.penalty_rule != "none" and item.status != NA:
        end = item.filed_date if item.status == FILED else today
        try:
            est = penalty.estimate(item.penalty_rule, item.due_date, end)
        except ValidationError:
            est = None
    a = item.assignee
    return {
        "id": str(item.id), "reference": item.reference, "obligation_id": str(item.obligation_id) if item.obligation_id else None,
        "title": item.title, "period_label": item.period_label, "form_no": item.form_no, "category": item.category,
        "category_label": C.CATEGORY_LABELS.get(item.category, item.category), "due_date": item.due_date,
        "due_date_overridden": item.due_date_overridden, "fy": item.fy_label, "event_date": item.event_date,
        "status": item.status, "status_key": key, "status_label": C.STATUS_LABELS[key],
        "days_to_due": (item.due_date - today).days, "days_late": late, "on_time": on_time,
        "assignee": {"id": str(a.pk), "name": common.user_name(a)} if a else None, "priority": item.priority,
        "reminder_days": item.reminder_days or list(C.DEFAULT_REMINDER_DAYS), "penalty_rule": item.penalty_rule,
        "estimated_penalty": str(est["amount"]) if est and est["amount"] else None,
        "filed_date": item.filed_date, "ack_number": item.ack_number,
        "fee_paid": f"{item.fee_paid:.2f}" if item.fee_paid is not None else None,
        "penalty_paid": f"{item.penalty_paid:.2f}" if item.penalty_paid is not None else None,
        "remarks": item.remarks, "na_reason": item.na_reason, "notes": item.notes, "is_adhoc": item.obligation_id is None,
    }


def totals(rows):
    out = {k: 0 for k in ("upcoming", "due_soon", "overdue", "filed", "not_applicable")}
    for r in rows:
        out[r["status_key"]] += 1
    out["total"] = len(rows)
    return out


# ---------------------------------------------------------------------------- changes
def _save_with_audit(item, actor, before, action="updated", reason=""):
    item.save()
    after = common.snapshot(item, ITEM_AUDIT)
    if before != after:
        common.log(item.company, actor, "compliance_item", item.pk, action, previous=before, new=after, reason=reason)


def _lock(item_id, company):
    try:
        return CalendarItem.objects.select_for_update(of=("self",)).select_related("assignee", "obligation").filter(
            pk=item_id, company=company).first()
    except (ValueError, ValidationError):
        return None


def update_item(item, actor, data):
    before = common.snapshot(item, ITEM_AUDIT)
    closed = item.status != OPEN
    if "assignee" in data:
        item.assignee = common.get_user(data["assignee"])
    if "priority" in data:
        item.priority = common.pick(data["priority"], "priority", C.PRIORITIES)
    if "reminder_days" in data:
        item.reminder_days = common.clean_reminders(data["reminder_days"]) or list(C.DEFAULT_REMINDER_DAYS)
    if "notes" in data:
        item.notes = common.clean_text(data["notes"], "notes", max_length=2000)
    if "remarks" in data:
        item.remarks = common.clean_text(data["remarks"], "remarks", max_length=2000)
    if "due_date" in data:
        new_due = common.parse_date(data["due_date"], "due_date", required=True)
        if new_due != item.due_date:
            if closed:
                raise ValidationError({"due_date": "Reopen the item before changing its due date."})
            item.due_date = new_due
            item.fy_label = schedule.fy_for_date(new_due) if item.obligation_id is None else item.fy_label
            item.due_date_overridden = item.obligation_id is not None
    if item.obligation_id is None:
        if "title" in data:
            item.title = common.clean_text(data["title"], "title", max_length=200, required=True, label="the task name")
        if "category" in data:
            item.category = common.pick(data["category"], "category", C.CATEGORIES)
        if "form_no" in data:
            item.form_no = common.clean_text(data["form_no"], "form_no", max_length=60)
    _save_with_audit(item, actor, before)
    return item


def create_item(company, actor, data):
    """A task added by hand: either free-standing, or the filing of an event obligation (Form 4 ...) from the event's date."""
    ob = None
    if data.get("obligation"):
        try:
            ob = Obligation.objects.filter(pk=data["obligation"], company=company).first()
        except (ValueError, ValidationError):
            ob = None
        if ob is None:
            raise ValidationError({"obligation": "Not found."})
    event_date = common.parse_date(data.get("event_date"), "event_date")
    due = common.parse_date(data.get("due_date"), "due_date")
    if ob is not None:
        if event_date is None:
            raise ValidationError({"event_date": "Enter the date of the event."})
        due = due or schedule.event_due(ob.due_rule, event_date) if ob.frequency == "event" else due
    if due is None:
        raise ValidationError({"due_date": "Enter the due date."})
    item = CalendarItem(
        company=company, obligation=ob, fy_label=schedule.fy_for_date(due), event_date=event_date, due_date=due,
        title=ob.name if ob else common.clean_text(data.get("title"), "title", max_length=200, required=True, label="the task name"),
        form_no=ob.form_no if ob else common.clean_text(data.get("form_no"), "form_no", max_length=60),
        category=ob.category if ob else common.pick(data.get("category"), "category", C.CATEGORIES, default=C.OTHER),
        penalty_rule=ob.penalty_rule if ob else "none",
        priority=common.pick(data.get("priority"), "priority", C.PRIORITIES, default=ob.default_priority if ob else "normal"),
        reminder_days=common.clean_reminders(data.get("reminder_days")) or (ob.reminder_days if ob else None) or list(C.DEFAULT_REMINDER_DAYS),
        notes=common.clean_text(data.get("notes"), "notes", max_length=2000))
    item.period_key = f"event:{event_date.isoformat()}" if ob else f"adhoc:{uuid.uuid4()}"
    item.period_label = f"Event on {event_date.strftime('%d %b %Y')}" if ob else common.clean_text(data.get("period_label"), "period_label", max_length=80)
    if data.get("assignee"):
        item.assignee = common.get_user(data["assignee"])
    elif ob:
        item.assignee = default_assignees().get(ob.assignee_role)
    try:
        with transaction.atomic():
            item.save()
    except IntegrityError:
        raise ValidationError({"event_date": "There is already a filing for this event."})
    common.log(company, actor, "compliance_item", item.pk, "created", new=common.snapshot(item, ITEM_AUDIT))
    return item


def mark_filed(item, actor, data, today=None):
    today = today or datetime.date.today()
    if item.status != OPEN:
        raise ValidationError({"detail": "This item is already " + ("filed." if item.status == FILED else "closed as not applicable.") + " Reopen it first."})
    filed = common.parse_date(data.get("filed_date"), "filed_date", required=True)
    if filed > today:
        raise ValidationError({"filed_date": "The filing date cannot be in the future."})
    before = common.snapshot(item, ITEM_AUDIT)
    item.filed_date = filed
    item.ack_number = common.clean_text(data.get("ack_number"), "ack_number", max_length=80)
    item.fee_paid = common.parse_money(data.get("fee_paid"), "fee_paid")
    item.penalty_paid = common.parse_money(data.get("penalty_paid"), "penalty_paid")
    item.remarks = common.clean_text(data.get("remarks"), "remarks", max_length=2000)
    item.filed_by = actor
    item.status = FILED
    item.na_reason = ""
    if not item.reference:
        item.reference = common.new_reference(item.company, "CF", actor=actor, on_date=filed)
    _save_with_audit(item, actor, before, action="updated")
    return item


def mark_not_applicable(item, actor, reason):
    reason = common.clean_text(reason, "reason", max_length=300, required=True, label="the reason it does not apply")
    if item.status == FILED:
        raise ValidationError({"detail": "A filed item cannot be marked not applicable. Reopen it first."})
    before = common.snapshot(item, ITEM_AUDIT)
    item.status = NA
    item.na_reason = reason
    _save_with_audit(item, actor, before, reason=reason)
    return item


def reopen(item, actor, reason):
    reason = common.clean_text(reason, "reason", max_length=300, required=True, label="the reason for reopening")
    if item.status == OPEN:
        raise ValidationError({"detail": "This item is already open."})
    before = common.snapshot(item, ITEM_AUDIT)
    item.status = OPEN
    item.filed_date = item.filed_by = None
    item.na_reason = ""
    # The acknowledgement number, fees and remarks stay as a record of the earlier filing until it is filed again.
    _save_with_audit(item, actor, before, reason=reason)
    return item


def delete_item(item, actor):
    if item.obligation_id and not item.period_key.startswith("event:"):
        raise ValidationError({"detail": "A generated item cannot be deleted. Mark it not applicable instead."})
    if item.status == FILED:
        raise ValidationError({"detail": "A filed item cannot be deleted. Reopen it first."})
    before = common.snapshot(item, ITEM_AUDIT)
    pk = item.pk
    item.delete()
    common.log(item.company, actor, "compliance_item", pk, "deleted", previous=before)


BULK_ACTIONS = ("assign", "priority", "not_applicable", "reopen", "reminders")


def bulk(company, actor, data, today=None):
    """Apply one action to many items. Items that cannot take it are reported and the rest are done: a person who ticks 30
    rows expects the 29 valid ones to change."""
    action = data.get("action")
    if action not in BULK_ACTIONS:
        raise ValidationError({"action": "Choose one of: " + ", ".join(BULK_ACTIONS) + "."})
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids:
        raise ValidationError({"ids": "Choose at least one item."})
    if len(ids) > 500:
        raise ValidationError({"ids": "Choose at most 500 items at a time."})
    # Check what applies to every item before touching any, so a bad value is one clear error, not 40 failed rows.
    assignee = common.get_user(data.get("assignee")) if action == "assign" else None
    if action == "priority":
        common.pick(data.get("priority"), "priority", C.PRIORITIES)
    if action == "reminders" and not common.clean_reminders(data.get("reminder_days")):
        raise ValidationError({"reminder_days": "Give the days before the due date, such as 15, 7, 1."})
    if action in ("not_applicable", "reopen"):
        common.clean_text(data.get("reason"), "reason", max_length=300, required=True, label="a reason")
    done, failed = 0, []
    for item_id in ids:
        item = _lock(item_id, company)
        if item is None:
            failed.append({"id": str(item_id), "error": "Not found."})
            continue
        try:
            with transaction.atomic():
                if action == "assign":
                    update_item(item, actor, {"assignee": assignee.pk if assignee else None})
                elif action == "priority":
                    update_item(item, actor, {"priority": data.get("priority")})
                elif action == "reminders":
                    update_item(item, actor, {"reminder_days": data.get("reminder_days")})
                elif action == "not_applicable":
                    mark_not_applicable(item, actor, data.get("reason"))
                else:
                    reopen(item, actor, data.get("reason"))
            done += 1
        except ValidationError as exc:
            msgs = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            text = "; ".join(m for v in msgs.values() for m in (v if isinstance(v, list) else [v]))
            failed.append({"id": str(item.pk), "title": item.title, "error": text})
    return {"done": done, "failed": failed}
