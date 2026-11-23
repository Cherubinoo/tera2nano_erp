"""Workplace rules: meeting scheduling and recurrence, recording minutes (through the company module), tasks and their
analytics, audiences, circulars and acknowledgements, and asset tags."""
import calendar
import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.accounts import access
from apps.company.constants import DocType
from apps.company.models import CompanyProfileAuditEvent, IssuedDocument
from apps.company.services import audit, documents, numbering

from .models import (AgendaItem, Announcement, Asset, Circular, CircularAck, Decision, Meeting, MeetingAttendee, Task,
                     TaskItem)

Action = CompanyProfileAuditEvent.Action
OPEN = Task.OPEN


def log(company, actor, entity, instance_id, action, *, new=None, previous=None, reason=""):
    return audit.log(company, actor, entity, instance_id, action, new=new, previous=previous, reason=reason)


def is_manager(user):
    return user.has_code("workplace.manage")


def display_name(user):
    return (user.get_full_name() or user.username) if user else ""


# --------------------------------------------------------------------------- meetings and recurrence

def add_months(anchor, months):
    """`anchor` moved by whole months; a day that does not exist (31st) becomes the last day of that month."""
    month_index = anchor.month - 1 + months
    year, month = anchor.year + month_index // 12, month_index % 12 + 1
    return datetime.date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def occurrence_dates(anchor, frequency, after, count):
    """The next `count` dates of a weekly or monthly series that started on `anchor`, all later than `after`."""
    if frequency not in (Meeting.Recurrence.WEEKLY, Meeting.Recurrence.MONTHLY) or count <= 0:
        return []
    out, k = [], 1
    while len(out) < count and k < 2000:
        d = anchor + datetime.timedelta(days=7 * k) if frequency == Meeting.Recurrence.WEEKLY else add_months(anchor, k)
        if d > after:
            out.append(d)
        k += 1
    return out


def meeting_snapshot(m):
    return {"title": m.title, "meeting_type": m.meeting_type, "date": m.date.isoformat(),
            "start_time": m.start_time.strftime("%H:%M") if m.start_time else "",
            "end_time": m.end_time.strftime("%H:%M") if m.end_time else "", "venue": m.venue, "link": m.link,
            "status": m.status, "organiser": display_name(m.organiser), "recurrence": m.recurrence}


def new_meeting_number(company, actor, date):
    return numbering.allocate_number(company, DocType.MEETING, actor=actor, on_date=date).reference_number


def set_people(meeting, attendees, agenda):
    """Replace the attendee and agenda lists (None leaves a list as it is)."""
    if attendees is not None:
        old = {a.user_id: a.attended for a in meeting.attendees.all() if a.user_id}
        meeting.attendees.all().delete()
        MeetingAttendee.objects.bulk_create([
            MeetingAttendee(meeting=meeting, position=i, user=a.get("user"), name=a["name"], designation=a.get("designation", ""),
                            attended=old.get(a["user"].pk) if a.get("user") else None) for i, a in enumerate(attendees)])
    if agenda is not None:
        meeting.agenda.all().delete()
        AgendaItem.objects.bulk_create([
            AgendaItem(meeting=meeting, position=i, title=a["title"], owner_name=a.get("owner_name", ""),
                       minutes=a.get("minutes"), notes=a.get("notes", "")) for i, a in enumerate(agenda)])


def generate_occurrences(meeting, count, actor):
    """Create the next `count` meetings of the series `meeting` belongs to (skipping dates that already exist)."""
    if meeting.recurrence == Meeting.Recurrence.NONE:
        raise ValidationError({"recurrence": "This meeting does not repeat."})
    if not 1 <= count <= 52:
        raise ValidationError({"count": "Create between 1 and 52 meetings at a time."})
    if meeting.series_id is None:
        meeting.series_id = meeting.pk
        meeting.save(update_fields=["series_id", "updated_at"])
    members = Meeting.objects.filter(company=meeting.company, series_id=meeting.series_id)
    dates = list(members.values_list("date", flat=True))
    anchor, latest = min(dates), max(dates)
    created = []
    for d in occurrence_dates(anchor, meeting.recurrence, latest, count):
        if d in dates:
            continue
        nxt = Meeting.objects.create(
            company=meeting.company, number=new_meeting_number(meeting.company, actor, d), title=meeting.title,
            meeting_type=meeting.meeting_type, date=d, start_time=meeting.start_time, end_time=meeting.end_time,
            venue=meeting.venue, link=meeting.link, description=meeting.description, organiser=meeting.organiser,
            recurrence=meeting.recurrence, series_id=meeting.series_id, created_by=actor)
        set_people(nxt, [{"user": a.user, "name": a.name, "designation": a.designation} for a in meeting.attendees.all()],
                   [{"title": a.title, "owner_name": a.owner_name, "minutes": a.minutes, "notes": a.notes}
                    for a in meeting.agenda.all()])
        log(meeting.company, actor, "meeting", nxt.pk, Action.CREATED, new={**meeting_snapshot(nxt), "number": nxt.number,
                                                                          "series": "generated"})
        created.append(nxt)
    return created


def minutes_for(meeting):
    return IssuedDocument.objects.filter(company=meeting.company, document_type=DocType.MEETING_MINUTES,
                                         source_id=meeting.number).order_by("created_at") if meeting.number else IssuedDocument.objects.none()


def minutes_draft(meeting):
    """The minutes form pre-filled from the meeting: the same shape the company module's minutes use."""
    tasks = Task.objects.filter(company=meeting.company, source="meeting", source_id=str(meeting.pk)).select_related("assignee")
    has_venue = bool(meeting.venue)
    mode = "hybrid" if (meeting.link and has_venue) else "online" if meeting.link else "in_person"
    return {
        "title": meeting.title, "date": meeting.date.isoformat(),
        "start_time": meeting.start_time.strftime("%H:%M") if meeting.start_time else "",
        "end_time": meeting.end_time.strftime("%H:%M") if meeting.end_time else "",
        "venue": meeting.venue or ("Online" if meeting.link else ""), "mode": mode,
        "minute_taker": display_name(meeting.organiser), "next_meeting": "",
        "attendees": [{"name": a.name, "designation": a.designation,
                       "status": "absent" if a.attended is False else "present"} for a in meeting.attendees.all()],
        "agenda": [a.title for a in meeting.agenda.all()],
        "items": [{"heading": a.title, "discussion": a.notes, "decision": ""} for a in meeting.agenda.all()]
                 + [{"heading": "Decision", "discussion": "", "decision": d.text} for d in meeting.decisions.all()],
        "actions": [{"action": t.title, "owner": display_name(t.assignee),
                     "due_date": t.due_date.isoformat() if t.due_date else ""} for t in tasks],
    }


@transaction.atomic
def record_minutes(meeting, minutes, actor):
    """Issue the minutes through the company module (its generator, numbering and PDF), tagged with this meeting's ID."""
    from apps.company.serializers import validate_minutes

    meeting = Meeting.objects.select_for_update(of=("self",)).get(pk=meeting.pk)
    if meeting.status == Meeting.Status.CANCELLED:
        raise ValidationError({"status": "A cancelled meeting has no minutes."})
    minutes = validate_minutes({**minutes, "meeting_id": meeting.number})
    doc = documents.issue_document(
        DocType.MEETING_MINUTES, actor=actor, content={"minutes": minutes}, issue_date=meeting.date,
        source_module="meeting", source_id=meeting.number)
    before = meeting.status
    if meeting.status != Meeting.Status.HELD:
        meeting.status = Meeting.Status.HELD
        meeting.save(update_fields=["status", "updated_at"])
    present = {a["name"].lower(): a["status"] != "present" for a in minutes["attendees"]}
    for a in meeting.attendees.all():
        if a.name.lower() in present:
            a.attended = not present[a.name.lower()]
            a.save(update_fields=["attended", "updated_at"])
    log(meeting.company, actor, "meeting", meeting.pk, Action.UPDATED, previous={"status": before},
        new={"status": "held", "minutes": doc.reference_number}, reason="Minutes recorded")
    return doc


# --------------------------------------------------------------------------- tasks

# Moves allowed between statuses. A finished (done / cancelled) task can only be reopened.
TASK_FLOW = {
    "todo": {"in_progress", "blocked", "done", "cancelled"},
    "in_progress": {"todo", "blocked", "done", "cancelled"},
    "blocked": {"todo", "in_progress", "done", "cancelled"},
    "done": {"todo"},
    "cancelled": {"todo"},
}


def task_snapshot(t):
    return {"title": t.title, "status": t.status, "priority": t.priority, "assignee": display_name(t.assignee),
            "due_date": t.due_date.isoformat() if t.due_date else "", "source": t.source, "labels": ", ".join(t.labels or [])}


def new_task(company, actor, **fields):
    task = Task(company=company, reporter=fields.pop("reporter", actor), **fields)
    task.number = numbering.allocate_number(company, DocType.TASK, actor=actor, on_date=datetime.date.today()).reference_number
    task.save()
    return task


def move_task(task, status):
    """Change a task's status along the allowed flow. Returns the previous status."""
    if status not in Task.Status.values:
        raise ValidationError({"status": "Unknown status."})
    if status == task.status:
        raise ValidationError({"status": f"The task is already {task.get_status_display().lower()}."})
    if status not in TASK_FLOW[task.status]:
        raise ValidationError({"status": f"A task that is {task.get_status_display().lower()} can only be reopened (set to To do)."})
    previous = task.status
    task.status = status
    task.completed_at = timezone.now() if status == Task.Status.DONE else None
    task.save(update_fields=["status", "completed_at", "updated_at"])
    return previous


def is_overdue(task, today):
    return bool(task.due_date and task.due_date < today and task.status in OPEN)


def monday(d):
    return d - datetime.timedelta(days=d.weekday())


def task_analytics(qs, today, weeks=8):
    """Counts for the task charts: by status, open tasks per assignee, tasks completed in each of the last `weeks` weeks."""
    qs = qs.order_by()
    by_status = {s: 0 for s in Task.Status.values}
    for row in qs.values("status").annotate(n=Count("id")):
        by_status[row["status"]] = row["n"]
    assignees = [{"user_id": str(r["assignee_id"]) if r["assignee_id"] else None,
                  "name": r["assignee__first_name"] and f"{r['assignee__first_name']} {r['assignee__last_name']}".strip()
                  or r["assignee__username"] or "Unassigned", "open": r["n"]}
                 for r in qs.filter(status__in=OPEN).values("assignee_id", "assignee__username", "assignee__first_name",
                                                            "assignee__last_name").annotate(n=Count("id")).order_by("-n")[:8]]
    start = monday(today) - datetime.timedelta(weeks=weeks - 1)
    done = list(qs.filter(status=Task.Status.DONE, completed_at__isnull=False,
                          completed_at__date__gte=start).values_list("completed_at", flat=True))
    out = []
    for i in range(weeks):
        week_start = start + datetime.timedelta(weeks=i)
        week_end = week_start + datetime.timedelta(days=6)
        n = sum(1 for c in done if week_start <= timezone.localtime(c).date() <= week_end)
        out.append({"week_start": week_start.isoformat(), "label": week_start.strftime("%d %b"), "completed": n})
    overdue = qs.filter(status__in=OPEN, due_date__lt=today).count()
    return {"by_status": by_status, "by_assignee": assignees, "completed_per_week": out, "overdue": overdue,
            "open": sum(by_status[s] for s in OPEN), "total": sum(by_status.values())}


# --------------------------------------------------------------------------- audiences, circulars

def audience_q(user, prefix=""):
    """Rows whose audience includes `user`: addressed to everyone, or to the user's role."""
    return Q(**{f"{prefix}audience": "all"}) | Q(**{f"{prefix}audience": "roles", f"{prefix}roles__contains": [user.role]})


def in_audience(obj, user):
    return obj.audience == "all" or user.role in (obj.roles or [])


def recipients(circular):
    from django.contrib.auth import get_user_model

    users = get_user_model().objects.filter(is_active=True)
    if circular.audience == "roles":
        users = users.filter(role__in=circular.roles or [])
    return users.order_by("first_name", "username")


def check_audience(audience, roles):
    if audience not in ("all", "roles"):
        raise ValidationError({"audience": "Choose everyone or selected roles."})
    if audience == "all":
        return []
    if not isinstance(roles, list) or not roles:
        raise ValidationError({"roles": "Choose at least one role."})
    bad = [r for r in roles if r not in access.ROLE_VALUES]
    if bad:
        raise ValidationError({"roles": f"Unknown role: {bad[0]}."})
    return list(dict.fromkeys(roles))


@transaction.atomic
def issue_circular(circular, actor):
    """Number the circular, print it on the company letterhead and freeze it."""
    circular = Circular.objects.select_for_update(of=("self",)).get(pk=circular.pk)
    if circular.status != Circular.Status.DRAFT:
        raise ValidationError("Only a draft can be issued.")
    labels = dict(access.ROLES)
    content = {"recipient": [ln for ln in (circular.addressed_to or "").splitlines() if ln.strip()]
               or (["All staff"] if circular.audience == "all" else [labels.get(r, r) for r in circular.roles]),
               "subject": circular.subject, "paragraphs": [circular.body]}
    issued = documents.issue_document(DocType.CIRCULAR, actor=actor, content=content, issue_date=circular.issue_date,
                                      source_module="workplace", source_id=str(circular.pk))
    circular.number, circular.issued_document, circular.status, circular.issued_by = (
        issued.reference_number, issued, Circular.Status.ISSUED, actor)
    circular.save()
    log(circular.company, actor, "circular", circular.pk, Action.ISSUED,
        new={"number": circular.number, "status": "issued", "subject": circular.subject})
    return circular


def acknowledge(circular, user):
    if circular.status != Circular.Status.ISSUED:
        raise ValidationError("Only an issued circular can be acknowledged.")
    if not in_audience(circular, user):
        raise ValidationError("This circular is not addressed to you.")
    ack, created = CircularAck.objects.get_or_create(circular=circular, user=user, defaults={"acknowledged_at": timezone.now()})
    if created:
        log(circular.company, user, "circular", circular.pk, Action.UPDATED, new={"acknowledged_by": user.username})
    return ack


def ack_report(circular):
    acked = {a.user_id: a.acknowledged_at for a in circular.acknowledgements.all()}
    rows = [{"user_id": str(u.pk), "name": display_name(u), "role": u.role, "role_label": dict(access.ROLES).get(u.role, u.role),
             "acknowledged": u.pk in acked, "acknowledged_at": acked.get(u.pk)} for u in recipients(circular)]
    done = sum(1 for r in rows if r["acknowledged"])
    return {"recipients": len(rows), "acknowledged": done, "pending": len(rows) - done, "rows": rows}


# --------------------------------------------------------------------------- announcements

def published(qs, today):
    return qs.filter(publish_on__lte=today).filter(Q(expires_on__isnull=True) | Q(expires_on__gte=today))


# --------------------------------------------------------------------------- assets

def new_asset_tag(company, on_date):
    """AST-26-2710-0001: office assets have no DocType of their own, so the tag is counted per day here."""
    prefix = f"{numbering.company_code(company)}-AST-{on_date:%y}-{on_date:%d%m}-"
    n = Asset.objects.filter(company=company, tag__startswith=prefix).count() + 1
    while Asset.objects.filter(company=company, tag=f"{prefix}{n:04d}").exists():
        n += 1
    return f"{prefix}{n:04d}"


def save_new_asset(asset, on_date):
    for _ in range(5):
        asset.tag = new_asset_tag(asset.company, on_date)
        try:
            with transaction.atomic():
                asset.save()
            return asset
        except IntegrityError:
            continue
    raise ValidationError("Could not allocate an asset tag. Try again.")
