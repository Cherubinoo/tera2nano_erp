import datetime
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from rest_framework import status as http
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import access
from apps.company.api import current_company, private_file_response
from apps.company.constants import DocType
from apps.company.models import CompanyProfileAuditEvent, IssuedDocument
from apps.company.services import audit

from . import services
from .models import (AgendaItem, Announcement, Asset, Circular, CircularAck, Decision, Meeting, MeetingAttendee, Task,
                     TaskComment, TaskItem, Visitor)

Action = CompanyProfileAuditEvent.Action
User = get_user_model()
MAX_ROWS = 500
ROLE_LABELS = dict(access.ROLES)
log = services.log
name_of = services.display_name


# --------------------------------------------------------------------------- input helpers

def need(request, code):
    if not request.user.has_code(code):
        raise PermissionDenied("Your role does not allow this.")


def manager(request):
    return services.is_manager(request.user)


def text(data, key, label, limit, *, required=False, default=""):
    value = data.get(key, default) if key in data else default
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError({key: f"{label} must be text."})
    value = value.strip()
    if required and not value:
        raise ValidationError({key: f"Enter {label[0].lower() + label[1:]}."})
    if len(value) > limit:
        raise ValidationError({key: f"{label} is too long (at most {limit} characters)."})
    return value


def date_of(value, key, *, required=False, label="date"):
    if value in (None, ""):
        if required:
            raise ValidationError({key: f"Enter the {label}."})
        return None
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError({key: "Use a date in YYYY-MM-DD format."})


def time_of(value, key):
    if value in (None, ""):
        return None
    try:
        return datetime.time.fromisoformat(str(value)[:5])
    except ValueError:
        raise ValidationError({key: "Use a time in HH:MM format."})


def choice(value, key, enum, label):
    if value not in enum.values:
        raise ValidationError({key: f"Choose a valid {label}."})
    return value


def user_of(value, key, *, required=False):
    if value in (None, ""):
        if required:
            raise ValidationError({key: "Choose a person."})
        return None
    try:
        user = User.objects.filter(pk=value, is_active=True).first()
    except (ValueError, DjangoValidationError):
        user = None
    if user is None:
        raise ValidationError({key: "That person was not found."})
    return user


def get_or_404(qs, pk, what):
    try:
        obj = qs.filter(pk=pk).first()
    except (ValueError, DjangoValidationError):
        obj = None
    if obj is None:
        raise NotFound(f"No such {what}.")
    return obj


def hhmm(t):
    return t.strftime("%H:%M") if t else ""


def person(user):
    return {"id": str(user.pk), "name": name_of(user)} if user else None


def flag(request, key):
    return request.query_params.get(key) in ("1", "true")


# --------------------------------------------------------------------------- people

class PeopleView(APIView):
    """Active users to pick from (name and role only). Open to every signed-in user."""

    def get(self, request):
        return Response([{"id": str(u.pk), "name": name_of(u), "username": u.username, "role": u.role,
                          "role_label": ROLE_LABELS.get(u.role, u.role)}
                         for u in User.objects.filter(is_active=True).order_by("first_name", "username")])


# --------------------------------------------------------------------------- meetings

def meeting_qs(request):
    qs = Meeting.objects.filter(company=current_company()).select_related("organiser")
    if not manager(request):
        mine = MeetingAttendee.objects.filter(user=request.user).values("meeting_id")
        qs = qs.filter(Q(organiser=request.user) | Q(created_by=request.user) | Q(pk__in=mine))
    return qs


def minutes_dict(d):
    return {"id": str(d.id), "reference_number": d.reference_number, "status": d.status, "issue_date": d.issue_date,
            "has_print_file": bool(d.print_file)}


def task_brief(t, today):
    return {"id": str(t.id), "number": t.number, "title": t.title, "status": t.status, "status_label": t.get_status_display(),
            "due_date": t.due_date, "overdue": services.is_overdue(t, today), "assignee": person(t.assignee),
            "priority": t.priority}


def meeting_dict(m, *, detail=False, minutes=None, today=None):
    out = {
        "id": str(m.id), "number": m.number, "title": m.title, "meeting_type": m.meeting_type,
        "type_label": m.get_meeting_type_display(), "date": m.date, "start_time": hhmm(m.start_time), "end_time": hhmm(m.end_time),
        "venue": m.venue, "link": m.link, "status": m.status, "status_label": m.get_status_display(),
        "status_reason": m.status_reason, "organiser": person(m.organiser), "recurrence": m.recurrence,
        "recurrence_label": m.get_recurrence_display(), "series_id": str(m.series_id) if m.series_id else None,
        "minutes_count": len(minutes) if minutes is not None else None,
        "attendee_count": getattr(m, "attendee_total", None),
    }
    if detail:
        today = today or datetime.date.today()
        out.update({
            "description": m.description,
            "attendees": [{"id": str(a.id), "user": str(a.user_id) if a.user_id else None, "name": a.name,
                           "designation": a.designation, "external": a.external, "attended": a.attended}
                          for a in m.attendees.all()],
            "agenda": [{"id": str(a.id), "title": a.title, "owner_name": a.owner_name, "minutes": a.minutes, "notes": a.notes}
                       for a in m.agenda.all()],
            "decisions": [{"id": str(d.id), "text": d.text} for d in m.decisions.all()],
            "actions": [task_brief(t, today) for t in Task.objects.filter(company=m.company, source="meeting", source_id=str(m.pk))
                        .select_related("assignee")],
            "minutes": [minutes_dict(d) for d in (minutes if minutes is not None else services.minutes_for(m))],
            "series_count": Meeting.objects.filter(series_id=m.series_id).count() if m.series_id else 0,
        })
    return out


def list_minutes(company, numbers):
    by = {}
    for d in IssuedDocument.objects.filter(company=company, document_type=DocType.MEETING_MINUTES, source_id__in=numbers):
        by.setdefault(d.source_id, []).append(d)
    return by


def parse_meeting(data, existing=None):
    """Checked fields of a meeting body; only the keys sent when `existing` is given."""
    fields, creating = {}, existing is None
    if creating or "title" in data:
        fields["title"] = text(data, "title", "The meeting title", 200, required=True)
    if creating or "meeting_type" in data:
        fields["meeting_type"] = choice(data.get("meeting_type") or Meeting.Type.TEAM, "meeting_type", Meeting.Type, "meeting type")
    if creating or "date" in data:
        fields["date"] = date_of(data.get("date"), "date", required=True, label="meeting date")
    for key in ("start_time", "end_time"):
        if creating or key in data:
            fields[key] = time_of(data.get(key), key)
    start = fields.get("start_time", existing.start_time if existing else None)
    end = fields.get("end_time", existing.end_time if existing else None)
    if start and end and end <= start:
        raise ValidationError({"end_time": "The end time must be after the start time."})
    if end and not start:
        raise ValidationError({"start_time": "Enter the start time as well."})
    for key, label, limit in (("venue", "The venue", 200), ("description", "The description", 4000)):
        if creating or key in data:
            fields[key] = text(data, key, label, limit)
    if creating or "link" in data:
        link = text(data, "link", "The meeting link", 300)
        if link and not link.lower().startswith(("http://", "https://")):
            raise ValidationError({"link": "A meeting link must start with http:// or https://."})
        fields["link"] = link
    if creating or "organiser" in data:
        fields["organiser"] = user_of(data.get("organiser"), "organiser")
    if creating or "recurrence" in data:
        fields["recurrence"] = choice(data.get("recurrence") or Meeting.Recurrence.NONE, "recurrence", Meeting.Recurrence, "repeat option")
    return fields


def parse_attendees(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 100:
        raise ValidationError({"attendees": "Send a list of at most 100 attendees."})
    out, seen = [], set()
    for a in value:
        if not isinstance(a, dict):
            raise ValidationError({"attendees": "Each attendee must be an object."})
        user = user_of(a.get("user"), "attendees")
        if user:
            if user.pk in seen:
                continue
            seen.add(user.pk)
        name = name_of(user) if user else text(a, "name", "The attendee name", 200, required=True)
        out.append({"user": user, "name": name, "designation": text(a, "designation", "The designation", 200)})
    return out


def parse_agenda(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 50:
        raise ValidationError({"agenda": "Send a list of at most 50 agenda items."})
    out = []
    for a in value:
        if not isinstance(a, dict):
            raise ValidationError({"agenda": "Each agenda item must be an object."})
        minutes = a.get("minutes")
        if minutes in (None, ""):
            minutes = None
        else:
            try:
                minutes = int(minutes)
            except (TypeError, ValueError):
                raise ValidationError({"agenda": "Time allotted must be a whole number of minutes."})
            if not 0 < minutes <= 600:
                raise ValidationError({"agenda": "Time allotted must be between 1 and 600 minutes."})
        out.append({"title": text(a, "title", "The agenda item", 300, required=True),
                    "owner_name": text(a, "owner_name", "The owner", 200), "minutes": minutes,
                    "notes": text(a, "notes", "The notes", 1000)})
    return out


def _meeting_detail(request, pk):
    m = get_or_404(meeting_qs(request), pk, "meeting")
    return m


class MeetingCollectionView(APIView):
    def get(self, request):
        need(request, "workplace.view")
        p = request.query_params
        today = datetime.date.today()
        qs = meeting_qs(request)
        if p.get("from"):
            qs = qs.filter(date__gte=date_of(p["from"], "from"))
        if p.get("to"):
            qs = qs.filter(date__lte=date_of(p["to"], "to"))
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("type"):
            qs = qs.filter(meeting_type=p["type"])
        if p.get("upcoming") in ("1", "true"):
            qs = qs.filter(date__gte=today, status__in=(Meeting.Status.SCHEDULED, Meeting.Status.RESCHEDULED))
        if p.get("q"):
            qs = qs.filter(Q(title__icontains=p["q"]) | Q(number__icontains=p["q"]) | Q(venue__icontains=p["q"]))
        qs = qs.annotate(attendee_total=Count("attendees", distinct=True))
        count = qs.count()
        rows = list(qs.order_by("-date", "-start_time")[:MAX_ROWS] if p.get("order") == "desc" else qs[:MAX_ROWS])
        by = list_minutes(current_company(), [m.number for m in rows if m.number])
        return Response({"count": count, "results": [meeting_dict(m, minutes=by.get(m.number, [])) for m in rows]})

    @transaction.atomic
    def post(self, request):
        need(request, "workplace.manage")
        company, d = current_company(), request.data
        fields = parse_meeting(d)
        attendees, agenda = parse_attendees(d.get("attendees")), parse_agenda(d.get("agenda"))
        extra = d.get("occurrences") or 0
        try:
            extra = int(extra)
        except (TypeError, ValueError):
            raise ValidationError({"occurrences": "Enter a whole number."})
        if not 0 <= extra <= 52:
            raise ValidationError({"occurrences": "Create between 0 and 52 further meetings."})
        if fields["recurrence"] == Meeting.Recurrence.NONE:
            extra = 0
        m = Meeting(company=company, created_by=request.user, **fields)
        m.organiser = fields["organiser"] or request.user
        m.number = services.new_meeting_number(company, request.user, m.date)
        if m.recurrence != Meeting.Recurrence.NONE:
            m.series_id = m.pk
        m.save()
        services.set_people(m, attendees or [], agenda or [])
        log(company, request.user, "meeting", m.pk, Action.CREATED, new={**services.meeting_snapshot(m), "number": m.number})
        if extra:
            services.generate_occurrences(m, extra, request.user)
        return Response(meeting_dict(m, detail=True), status=http.HTTP_201_CREATED)


class MeetingDetailView(APIView):
    def get(self, request, pk):
        need(request, "workplace.view")
        return Response(meeting_dict(_meeting_detail(request, pk), detail=True))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "workplace.manage")
        m = _meeting_detail(request, pk)
        before = services.meeting_snapshot(m)
        for key, value in parse_meeting(request.data, m).items():
            setattr(m, key, value)
        if m.recurrence != Meeting.Recurrence.NONE and m.series_id is None:
            m.series_id = m.pk
        m.save()
        services.set_people(m, parse_attendees(request.data.get("attendees")), parse_agenda(request.data.get("agenda")))
        after = services.meeting_snapshot(m)
        if before != after or "attendees" in request.data or "agenda" in request.data:
            log(m.company, request.user, "meeting", m.pk, Action.UPDATED, previous=before, new=after)
        return Response(meeting_dict(m, detail=True))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "workplace.manage")
        m = _meeting_detail(request, pk)
        if services.minutes_for(m).exists():
            raise ValidationError("Minutes have been issued for this meeting, so it cannot be deleted. Cancel it instead.")
        log(m.company, request.user, "meeting", m.pk, Action.DELETED, previous={**services.meeting_snapshot(m), "number": m.number})
        m.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class MeetingStatusView(APIView):
    """Mark a meeting held or cancelled, or reopen a cancelled one."""
    FLOW = {"scheduled": {"held", "cancelled"}, "rescheduled": {"held", "cancelled"}, "cancelled": {"scheduled"}, "held": set()}

    @transaction.atomic
    def post(self, request, pk):
        need(request, "workplace.manage")
        m = _meeting_detail(request, pk)
        target = request.data.get("status")
        if target not in Meeting.Status.values or target == "rescheduled":
            raise ValidationError({"status": "Choose held, cancelled or scheduled (use Reschedule to move a meeting)."})
        if target not in self.FLOW[m.status]:
            raise ValidationError({"status": f"A meeting that is {m.get_status_display().lower()} cannot be marked {target}."})
        reason = text(request.data, "reason", "The reason", 300, required=target == "cancelled")
        before = m.status
        m.status, m.status_reason = target, reason if target == "cancelled" else ""
        m.save(update_fields=["status", "status_reason", "updated_at"])
        log(m.company, request.user, "meeting", m.pk, Action.UPDATED, previous={"status": before}, new={"status": target}, reason=reason)
        return Response(meeting_dict(m, detail=True))


class MeetingRescheduleView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "workplace.manage")
        m = _meeting_detail(request, pk)
        if m.status not in (Meeting.Status.SCHEDULED, Meeting.Status.RESCHEDULED):
            raise ValidationError("Only a scheduled meeting can be rescheduled.")
        d = request.data
        before = services.meeting_snapshot(m)
        old = (m.date, m.start_time, m.end_time)
        m.date = date_of(d.get("date"), "date", required=True, label="new date")
        if "start_time" in d:
            m.start_time = time_of(d.get("start_time"), "start_time")
        if "end_time" in d:
            m.end_time = time_of(d.get("end_time"), "end_time")
        if m.start_time and m.end_time and m.end_time <= m.start_time:
            raise ValidationError({"end_time": "The end time must be after the start time."})
        if m.end_time and not m.start_time:
            raise ValidationError({"start_time": "Enter the start time as well."})
        if (m.date, m.start_time, m.end_time) == old:
            raise ValidationError("Choose a different date or time.")
        reason = text(d, "reason", "The reason", 300)
        m.status, m.status_reason = Meeting.Status.RESCHEDULED, reason
        m.save()
        log(m.company, request.user, "meeting", m.pk, Action.UPDATED, previous=before, new=services.meeting_snapshot(m), reason=reason)
        return Response(meeting_dict(m, detail=True))


class MeetingGenerateView(APIView):
    """Create the next occurrences of a recurring meeting."""

    @transaction.atomic
    def post(self, request, pk):
        need(request, "workplace.manage")
        m = _meeting_detail(request, pk)
        try:
            count = int(request.data.get("count", 1))
        except (TypeError, ValueError):
            raise ValidationError({"count": "Enter a whole number."})
        try:
            made = services.generate_occurrences(m, count, request.user)
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages})
        return Response({"created": [meeting_dict(x) for x in made]}, status=http.HTTP_201_CREATED)


class MeetingDecisionView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "workplace.manage")
        m = _meeting_detail(request, pk)
        body = text(request.data, "text", "The decision", 1000, required=True)
        d = Decision.objects.create(meeting=m, text=body, position=m.decisions.count(), created_by=request.user)
        log(m.company, request.user, "meeting", m.pk, Action.UPDATED, new={"decision": body})
        return Response({"id": str(d.id), "text": d.text}, status=http.HTTP_201_CREATED)


class MeetingDecisionDetailView(APIView):
    @transaction.atomic
    def delete(self, request, pk, decision_id):
        need(request, "workplace.manage")
        m = _meeting_detail(request, pk)
        d = get_or_404(m.decisions, decision_id, "decision")
        log(m.company, request.user, "meeting", m.pk, Action.UPDATED, previous={"decision": d.text})
        d.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class MeetingActionView(APIView):
    """An action item from the meeting: it is a task, linked back to the meeting."""

    @transaction.atomic
    def post(self, request, pk):
        need(request, "workplace.manage")
        m = _meeting_detail(request, pk)
        fields = parse_task(request.data, request.user, creating=True)
        task = services.new_task(m.company, request.user, source="meeting", source_id=str(m.pk),
                                 source_label=f"{m.number} {m.title}"[:200], **fields)
        log(m.company, request.user, "task", task.pk, Action.CREATED, new={**services.task_snapshot(task), "number": task.number})
        return Response(task_dict(task, detail=True), status=http.HTTP_201_CREATED)


class MeetingMinutesView(APIView):
    """GET: the minutes form pre-filled from the meeting. POST: issue the minutes through the company module."""

    def get(self, request, pk):
        need(request, "workplace.manage")
        m = _meeting_detail(request, pk)
        return Response({"draft": services.minutes_draft(m), "meeting_id": m.number,
                         "issued": [minutes_dict(d) for d in services.minutes_for(m)]})

    def post(self, request, pk):
        need(request, "workplace.manage")
        need(request, "documents.issue")
        m = _meeting_detail(request, pk)
        minutes = request.data.get("minutes")
        if not isinstance(minutes, dict):
            raise ValidationError({"minutes": "Send the minutes."})
        doc = services.record_minutes(m, minutes, request.user)
        m.refresh_from_db()
        return Response({"minutes": minutes_dict(doc), "meeting": meeting_dict(m, detail=True)}, status=http.HTTP_201_CREATED)


class MeetingMinutesFileView(APIView):
    def get(self, request, pk, document_id):
        need(request, "workplace.view")
        m = _meeting_detail(request, pk)
        doc = get_or_404(services.minutes_for(m), document_id, "minutes")
        stem = (doc.reference_number or str(doc.id)).replace("/", "-")
        if request.query_params.get("variant") == "print" and doc.print_file:
            return private_file_response(doc.print_file, filename=f"{stem}-print.pdf", attachment=not flag(request, "inline"),
                                         mime_type="application/pdf")
        return private_file_response(doc.file, filename=f"{stem}.pdf", attachment=not flag(request, "inline"),
                                     mime_type="application/pdf")


# --------------------------------------------------------------------------- tasks

def task_qs(request):
    qs = Task.objects.filter(company=current_company()).select_related("assignee", "reporter")
    if not manager(request):
        qs = qs.filter(Q(assignee=request.user) | Q(reporter=request.user))
    return qs


def can_edit(user, task):
    return services.is_manager(user) or task.reporter_id == user.pk


def can_work(user, task):
    return can_edit(user, task) or task.assignee_id == user.pk


def task_dict(t, *, detail=False, today=None):
    today = today or datetime.date.today()
    items = list(t.items.all())
    out = {
        "id": str(t.id), "number": t.number, "title": t.title, "status": t.status, "status_label": t.get_status_display(),
        "priority": t.priority, "priority_label": t.get_priority_display(), "due_date": t.due_date,
        "overdue": services.is_overdue(t, today), "assignee": person(t.assignee), "reporter": person(t.reporter),
        "labels": t.labels or [], "source": t.source, "source_id": t.source_id, "source_label": t.source_label,
        "project": str(t.project_id) if t.project_id else None, "completed_at": t.completed_at, "created_at": t.created_at,
        "subtasks_done": sum(1 for i in items if i.done), "subtasks_total": len(items),
        "comment_count": t.comments.count(),
    }
    if detail:
        out.update({
            "description": t.description,
            "items": [{"id": str(i.id), "text": i.text, "done": i.done} for i in items],
            "comments": [{"id": str(c.id), "author": person(c.author), "body": c.body, "created_at": c.created_at}
                         for c in t.comments.select_related("author")],
        })
    return out


def parse_labels(value):
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > 10:
        raise ValidationError({"labels": "Send a list of at most 10 labels."})
    out = []
    for v in value:
        if not isinstance(v, str) or not v.strip() or len(v.strip()) > 30:
            raise ValidationError({"labels": "A label is 1 to 30 characters."})
        if v.strip() not in out:
            out.append(v.strip())
    return out


def parse_task(data, actor, *, creating, existing=None):
    fields = {}
    if creating or "title" in data:
        fields["title"] = text(data, "title", "The task title", 250, required=True)
    if creating or "description" in data:
        fields["description"] = text(data, "description", "The description", 6000)
    if "assignee" in data:
        fields["assignee"] = user_of(data.get("assignee"), "assignee")
    elif creating:
        fields["assignee"] = actor
    if creating or "due_date" in data:
        fields["due_date"] = date_of(data.get("due_date"), "due_date")
    if creating or "priority" in data:
        fields["priority"] = choice(data.get("priority") or Task.Priority.MEDIUM, "priority", Task.Priority, "priority")
    if creating or "labels" in data:
        fields["labels"] = parse_labels(data.get("labels"))
    if "project" in data:
        pid = data.get("project")
        try:
            fields["project_id"] = uuid.UUID(str(pid)) if pid else None
        except ValueError:
            raise ValidationError({"project": "Not a valid project."})
    return fields


class TaskCollectionView(APIView):
    def get(self, request):
        p = request.query_params
        today = datetime.date.today()
        qs = task_qs(request)
        if flag(request, "mine"):
            qs = qs.filter(assignee=request.user)
        if p.get("assignee") == "me":
            qs = qs.filter(assignee=request.user)
        elif p.get("assignee") == "none":
            qs = qs.filter(assignee__isnull=True)
        elif p.get("assignee"):
            qs = qs.filter(assignee_id=p["assignee"])
        if p.get("status") == "open":
            qs = qs.filter(status__in=Task.OPEN)
        elif p.get("status"):
            qs = qs.filter(status=p["status"])
        for key in ("priority", "source", "source_id"):
            if p.get(key):
                qs = qs.filter(**{key: p[key]})
        if p.get("project"):
            qs = qs.filter(project_id=p["project"])
        if p.get("label"):
            qs = qs.filter(labels__contains=[p["label"]])
        if flag(request, "overdue"):
            qs = qs.filter(status__in=Task.OPEN, due_date__lt=today)
        if p.get("q"):
            qs = qs.filter(Q(title__icontains=p["q"]) | Q(number__icontains=p["q"]) | Q(description__icontains=p["q"]))
        count = qs.count()
        rows = list(qs.order_by(F("due_date").asc(nulls_last=True), "-created_at")[:MAX_ROWS])
        return Response({"count": count, "overdue": qs.filter(status__in=Task.OPEN, due_date__lt=today).count(),
                         "results": [task_dict(t, today=today) for t in rows]})

    @transaction.atomic
    def post(self, request):
        company = current_company()
        fields = parse_task(request.data, request.user, creating=True)
        if not manager(request) and fields["assignee"] != request.user:
            raise PermissionDenied("You can only create tasks for yourself.")
        source, source_id, source_label = "manual", "", ""
        if request.data.get("source") and request.data["source"] != "manual":
            if not manager(request):
                raise PermissionDenied("Your role does not allow this.")
            source = text(request.data, "source", "The source", 30)
            source_id = text(request.data, "source_id", "The source reference", 60)
            source_label = text(request.data, "source_label", "The source label", 200)
            if source == "meeting":
                m = get_or_404(Meeting.objects.filter(company=company), source_id, "meeting")
                source_id, source_label = str(m.pk), f"{m.number} {m.title}"[:200]
        task = services.new_task(company, request.user, source=source, source_id=source_id, source_label=source_label, **fields)
        log(company, request.user, "task", task.pk, Action.CREATED, new={**services.task_snapshot(task), "number": task.number})
        return Response(task_dict(task, detail=True), status=http.HTTP_201_CREATED)


def _task(request, pk):
    return get_or_404(task_qs(request), pk, "task")


class TaskDetailView(APIView):
    def get(self, request, pk):
        return Response(task_dict(_task(request, pk), detail=True))

    @transaction.atomic
    def patch(self, request, pk):
        task = _task(request, pk)
        data = request.data
        before = services.task_snapshot(task)
        if not can_edit(request.user, task):
            if not (can_work(request.user, task) and set(data) <= {"status"}):
                raise PermissionDenied("You can change the status of a task assigned to you, but not edit it.")
        fields = parse_task(data, request.user, creating=False)
        if not manager(request) and "assignee" in fields and fields["assignee"] not in (request.user, task.assignee):
            raise PermissionDenied("You can only assign your own tasks to yourself.")
        for key, value in fields.items():
            setattr(task, key, value)
        task.save()
        if "status" in data:
            services.move_task(task, data["status"])
        after = services.task_snapshot(task)
        if before != after:
            log(task.company, request.user, "task", task.pk, Action.UPDATED, previous=before, new=after)
        return Response(task_dict(task, detail=True))

    @transaction.atomic
    def delete(self, request, pk):
        task = _task(request, pk)
        if not can_edit(request.user, task):
            raise PermissionDenied("Only the person who raised a task, or a manager, can delete it.")
        log(task.company, request.user, "task", task.pk, Action.DELETED, previous={**services.task_snapshot(task), "number": task.number})
        task.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class TaskStatusView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        task = _task(request, pk)
        if not can_work(request.user, task):
            raise PermissionDenied("You cannot change this task.")
        previous = services.move_task(task, request.data.get("status"))
        log(task.company, request.user, "task", task.pk, Action.UPDATED, previous={"status": previous}, new={"status": task.status})
        return Response(task_dict(task, detail=True))


class TaskCommentView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        task = _task(request, pk)
        body = text(request.data, "body", "The comment", 4000, required=True)
        c = TaskComment.objects.create(task=task, author=request.user, body=body)
        return Response({"id": str(c.id), "author": person(request.user), "body": c.body, "created_at": c.created_at},
                        status=http.HTTP_201_CREATED)


class TaskCommentDetailView(APIView):
    @transaction.atomic
    def delete(self, request, pk, comment_id):
        task = _task(request, pk)
        c = get_or_404(task.comments, comment_id, "comment")
        if c.author_id != request.user.pk and not manager(request):
            raise PermissionDenied("You can only remove your own comments.")
        c.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class TaskItemView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        task = _task(request, pk)
        if not can_work(request.user, task):
            raise PermissionDenied("You cannot change this task.")
        if task.items.count() >= 50:
            raise ValidationError({"text": "A task can have at most 50 checklist items."})
        i = TaskItem.objects.create(task=task, text=text(request.data, "text", "The checklist item", 300, required=True),
                                    position=task.items.count())
        return Response({"id": str(i.id), "text": i.text, "done": i.done}, status=http.HTTP_201_CREATED)


class TaskItemDetailView(APIView):
    @transaction.atomic
    def patch(self, request, pk, item_id):
        task = _task(request, pk)
        if not can_work(request.user, task):
            raise PermissionDenied("You cannot change this task.")
        i = get_or_404(task.items, item_id, "checklist item")
        if "done" in request.data:
            if not isinstance(request.data["done"], bool):
                raise ValidationError({"done": "Send true or false."})
            i.done = request.data["done"]
        if "text" in request.data:
            i.text = text(request.data, "text", "The checklist item", 300, required=True)
        i.save()
        return Response({"id": str(i.id), "text": i.text, "done": i.done})

    @transaction.atomic
    def delete(self, request, pk, item_id):
        task = _task(request, pk)
        if not can_work(request.user, task):
            raise PermissionDenied("You cannot change this task.")
        get_or_404(task.items, item_id, "checklist item").delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class TaskAnalyticsView(APIView):
    def get(self, request):
        qs = task_qs(request)
        if flag(request, "mine"):
            qs = qs.filter(assignee=request.user)
        return Response(services.task_analytics(qs, datetime.date.today()))


# --------------------------------------------------------------------------- announcements

def announcement_dict(a, today, *, full=False):
    state = "scheduled" if a.publish_on > today else "expired" if a.expires_on and a.expires_on < today else "live"
    return {"id": str(a.id), "title": a.title, "body": a.body, "audience": a.audience, "roles": a.roles or [],
            "role_labels": [ROLE_LABELS.get(r, r) for r in (a.roles or [])], "pinned": a.pinned, "publish_on": a.publish_on,
            "expires_on": a.expires_on, "state": state, "created_by": person(a.created_by), "created_at": a.created_at}


def parse_announcement(data, existing=None):
    creating = existing is None
    f = {}
    if creating or "title" in data:
        f["title"] = text(data, "title", "The title", 200, required=True)
    if creating or "body" in data:
        f["body"] = text(data, "body", "The message", 10000, required=True)
    if creating or "audience" in data or "roles" in data:
        audience = data.get("audience", existing.audience if existing else "all")
        f["roles"] = services.check_audience(audience, data.get("roles", existing.roles if existing else []))
        f["audience"] = audience
    if creating or "pinned" in data:
        if "pinned" in data and not isinstance(data["pinned"], bool):
            raise ValidationError({"pinned": "Send true or false."})
        f["pinned"] = bool(data.get("pinned", False))
    if creating or "publish_on" in data:
        f["publish_on"] = date_of(data.get("publish_on") or datetime.date.today().isoformat(), "publish_on", label="publish date")
    if creating or "expires_on" in data:
        f["expires_on"] = date_of(data.get("expires_on"), "expires_on", label="expiry date")
    publish = f.get("publish_on", existing.publish_on if existing else None)
    expires = f.get("expires_on", existing.expires_on if existing else None)
    if expires and publish and expires < publish:
        raise ValidationError({"expires_on": "The expiry date cannot be before the publish date."})
    return f


def _snap(a):
    return {"title": a.title, "audience": a.audience, "roles": ", ".join(a.roles or []), "pinned": a.pinned,
            "publish_on": a.publish_on.isoformat(), "expires_on": a.expires_on.isoformat() if a.expires_on else ""}


class AnnouncementCollectionView(APIView):
    """The feed: what is published now and addressed to the signed-in user. `?all=1` lists everything (managers)."""

    def get(self, request):
        today = datetime.date.today()
        qs = Announcement.objects.filter(company=current_company()).select_related("created_by")
        if flag(request, "all"):
            need(request, "workplace.manage")
        else:
            qs = services.published(qs, today).filter(services.audience_q(request.user))
        return Response([announcement_dict(a, today) for a in qs[:MAX_ROWS]])

    @transaction.atomic
    def post(self, request):
        need(request, "workplace.manage")
        company = current_company()
        a = Announcement(company=company, created_by=request.user, **parse_announcement(request.data))
        a.save()
        log(company, request.user, "announcement", a.pk, Action.CREATED, new=_snap(a))
        return Response(announcement_dict(a, datetime.date.today()), status=http.HTTP_201_CREATED)


class AnnouncementDetailView(APIView):
    def _get(self, pk):
        return get_or_404(Announcement.objects.filter(company=current_company()).select_related("created_by"), pk, "announcement")

    def get(self, request, pk):
        a = self._get(pk)
        today = datetime.date.today()
        if not manager(request) and not (a.publish_on <= today and (not a.expires_on or a.expires_on >= today)
                                         and services.in_audience(a, request.user)):
            raise NotFound("No such announcement.")
        return Response(announcement_dict(a, today))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "workplace.manage")
        a = self._get(pk)
        before = _snap(a)
        for k, v in parse_announcement(request.data, a).items():
            setattr(a, k, v)
        a.save()
        if before != _snap(a):
            log(a.company, request.user, "announcement", a.pk, Action.UPDATED, previous=before, new=_snap(a))
        return Response(announcement_dict(a, datetime.date.today()))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "workplace.manage")
        a = self._get(pk)
        log(a.company, request.user, "announcement", a.pk, Action.DELETED, previous=_snap(a))
        a.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- circulars

def circular_qs(request):
    qs = Circular.objects.filter(company=current_company()).select_related("issued_document", "created_by")
    if not manager(request):
        qs = qs.filter(status=Circular.Status.ISSUED).filter(services.audience_q(request.user))
    return qs


def circular_dict(c, user, *, my_acks, counts=None, detail=False):
    i = c.issued_document
    out = {
        "id": str(c.id), "number": c.number, "subject": c.subject, "addressed_to": c.addressed_to, "audience": c.audience,
        "roles": c.roles or [], "role_labels": [ROLE_LABELS.get(r, r) for r in (c.roles or [])], "issue_date": c.issue_date,
        "status": c.status, "status_label": c.get_status_display(),
        "issued": {"id": str(i.id), "reference_number": i.reference_number, "has_print_file": bool(i.print_file)} if i else None,
        "acknowledged": c.pk in my_acks, "acknowledged_at": my_acks.get(c.pk),
        "created_by": person(c.created_by),
    }
    if detail:
        out["body"] = c.body
    if counts is not None:
        out.update(counts)
    return out


def ack_counts(circulars):
    """Recipients and acknowledgements per issued circular, from two grouped queries."""
    roles = dict(User.objects.filter(is_active=True).values_list("role").annotate(n=Count("id")))
    total = sum(roles.values())
    acked = dict(CircularAck.objects.filter(circular__in=circulars, user__is_active=True).values_list("circular").annotate(n=Count("id")))
    out = {}
    for c in circulars:
        if c.status != Circular.Status.ISSUED:
            out[c.pk] = {"recipients": None, "acknowledged_count": None}
            continue
        n = total if c.audience == "all" else sum(roles.get(r, 0) for r in (c.roles or []))
        out[c.pk] = {"recipients": n, "acknowledged_count": acked.get(c.pk, 0)}
    return out


def parse_circular(data, existing=None):
    creating = existing is None
    f = {}
    if creating or "subject" in data:
        f["subject"] = text(data, "subject", "The subject", 250, required=True)
    if creating or "body" in data:
        f["body"] = text(data, "body", "The circular text", 20000, required=True)
    if creating or "addressed_to" in data:
        f["addressed_to"] = text(data, "addressed_to", "The addressee", 300)
    if creating or "audience" in data or "roles" in data:
        audience = data.get("audience", existing.audience if existing else "all")
        f["roles"] = services.check_audience(audience, data.get("roles", existing.roles if existing else []))
        f["audience"] = audience
    if creating or "issue_date" in data:
        f["issue_date"] = date_of(data.get("issue_date") or datetime.date.today().isoformat(), "issue_date", label="issue date")
    return f


def _csnap(c):
    return {"subject": c.subject, "audience": c.audience, "roles": ", ".join(c.roles or []), "issue_date": c.issue_date.isoformat(),
            "status": c.status}


def _circular(request, pk):
    return get_or_404(circular_qs(request), pk, "circular")


class CircularCollectionView(APIView):
    def get(self, request):
        need(request, "workplace.view")
        qs = circular_qs(request)
        p = request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("q"):
            qs = qs.filter(Q(subject__icontains=p["q"]) | Q(number__icontains=p["q"]))
        rows = list(qs[:MAX_ROWS])
        mine = dict(CircularAck.objects.filter(user=request.user, circular__in=rows).values_list("circular", "acknowledged_at"))
        counts = ack_counts(rows)
        if p.get("pending") in ("1", "true"):
            rows = [c for c in rows if c.status == Circular.Status.ISSUED and c.pk not in mine]
        return Response({"count": len(rows), "results": [circular_dict(c, request.user, my_acks=mine, counts=counts[c.pk]) for c in rows]})

    @transaction.atomic
    def post(self, request):
        need(request, "workplace.manage")
        company = current_company()
        c = Circular(company=company, created_by=request.user, **parse_circular(request.data))
        c.save()
        log(company, request.user, "circular", c.pk, Action.CREATED, new=_csnap(c))
        return Response(circular_dict(c, request.user, my_acks={}, detail=True), status=http.HTTP_201_CREATED)


class CircularDetailView(APIView):
    def _out(self, request, c):
        mine = dict(CircularAck.objects.filter(user=request.user, circular=c).values_list("circular", "acknowledged_at"))
        return circular_dict(c, request.user, my_acks=mine, counts=ack_counts([c])[c.pk], detail=True)

    def get(self, request, pk):
        need(request, "workplace.view")
        return Response(self._out(request, _circular(request, pk)))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "workplace.manage")
        c = _circular(request, pk)
        if c.status != Circular.Status.DRAFT:
            raise ValidationError("An issued circular cannot be edited.")
        before = _csnap(c)
        for k, v in parse_circular(request.data, c).items():
            setattr(c, k, v)
        c.save()
        if before != _csnap(c):
            log(c.company, request.user, "circular", c.pk, Action.UPDATED, previous=before, new=_csnap(c))
        return Response(self._out(request, c))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "workplace.manage")
        c = _circular(request, pk)
        if c.status != Circular.Status.DRAFT:
            raise ValidationError("An issued circular cannot be deleted.")
        log(c.company, request.user, "circular", c.pk, Action.DELETED, previous=_csnap(c))
        c.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class CircularIssueView(APIView):
    def post(self, request, pk):
        need(request, "workplace.manage")
        c = _circular(request, pk)
        try:
            c = services.issue_circular(c, request.user)
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages})
        return Response(CircularDetailView()._out(request, c))


class CircularAcknowledgeView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        c = _circular(request, pk)
        try:
            services.acknowledge(c, request.user)
        except DjangoValidationError as exc:
            raise ValidationError({"detail": exc.messages[0]})
        return Response(CircularDetailView()._out(request, c))


class CircularAcknowledgementsView(APIView):
    """Who has and has not acknowledged a circular."""

    def get(self, request, pk):
        need(request, "workplace.manage")
        c = _circular(request, pk)
        return Response(services.ack_report(c))


class CircularPdfView(APIView):
    def get(self, request, pk):
        need(request, "workplace.view")
        c = _circular(request, pk)
        doc = c.issued_document
        if doc is None:
            raise NotFound("This circular has not been issued yet.")
        stem = doc.reference_number.replace("/", "-")
        if request.query_params.get("variant") == "print" and doc.print_file:
            return private_file_response(doc.print_file, filename=f"{stem}-print.pdf", attachment=not flag(request, "inline"),
                                         mime_type="application/pdf")
        return private_file_response(doc.file, filename=f"{stem}.pdf", attachment=not flag(request, "inline"), mime_type="application/pdf")


# --------------------------------------------------------------------------- assets

def money_of(value, key):
    if value in (None, ""):
        return None
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValidationError({key: "Enter a number."})
    if not d.is_finite() or d < 0:
        raise ValidationError({key: "The value cannot be negative."})
    if d.as_tuple().exponent < -2:
        raise ValidationError({key: "Use at most 2 decimal places."})
    if d >= Decimal("10") ** 14:
        raise ValidationError({key: "The value is too large."})
    return d.quantize(Decimal("0.01"), ROUND_HALF_UP)


def asset_dict(a, today):
    warranty = None
    if a.warranty_expiry:
        warranty = "expired" if a.warranty_expiry < today else "expiring" if (a.warranty_expiry - today).days <= 60 else "ok"
    return {"id": str(a.id), "tag": a.tag, "name": a.name, "category": a.category, "serial_number": a.serial_number,
            "assigned_to": person(a.assigned_to), "location": a.location, "purchase_date": a.purchase_date,
            "purchase_value": str(a.purchase_value) if a.purchase_value is not None else None, "status": a.status,
            "status_label": a.get_status_display(), "warranty_expiry": a.warranty_expiry, "warranty_state": warranty, "notes": a.notes}


def parse_asset(data, existing=None):
    creating = existing is None
    f = {}
    if creating or "name" in data:
        f["name"] = text(data, "name", "The asset name", 200, required=True)
    for key, label, limit in (("category", "The category", 80), ("serial_number", "The serial number", 100),
                              ("location", "The location", 150), ("notes", "The notes", 3000)):
        if creating or key in data:
            f[key] = text(data, key, label, limit)
    if creating or "assigned_to" in data:
        f["assigned_to"] = user_of(data.get("assigned_to"), "assigned_to")
    if creating or "purchase_date" in data:
        f["purchase_date"] = date_of(data.get("purchase_date"), "purchase_date", label="purchase date")
    if creating or "purchase_value" in data:
        f["purchase_value"] = money_of(data.get("purchase_value"), "purchase_value")
    if creating or "warranty_expiry" in data:
        f["warranty_expiry"] = date_of(data.get("warranty_expiry"), "warranty_expiry", label="warranty expiry date")
    if creating or "status" in data:
        f["status"] = choice(data.get("status") or Asset.Status.IN_STOCK, "status", Asset.Status, "status")
    purchase = f.get("purchase_date", existing.purchase_date if existing else None)
    warranty = f.get("warranty_expiry", existing.warranty_expiry if existing else None)
    if purchase and warranty and warranty < purchase:
        raise ValidationError({"warranty_expiry": "The warranty cannot expire before the purchase date."})
    return f


def _asnap(a):
    return {"tag": a.tag, "name": a.name, "category": a.category, "status": a.status, "assigned_to": name_of(a.assigned_to),
            "purchase_value": str(a.purchase_value) if a.purchase_value is not None else "", "location": a.location}


class AssetCollectionView(APIView):
    def get(self, request):
        need(request, "workplace.manage")
        qs = Asset.objects.filter(company=current_company()).select_related("assigned_to")
        p, today = request.query_params, datetime.date.today()
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("category"):
            qs = qs.filter(category__iexact=p["category"])
        if p.get("assigned_to"):
            qs = qs.filter(assigned_to_id=p["assigned_to"])
        if flag(request, "warranty_expiring"):
            qs = qs.filter(warranty_expiry__lte=today + datetime.timedelta(days=60))
        if p.get("q"):
            qs = qs.filter(Q(name__icontains=p["q"]) | Q(tag__icontains=p["q"]) | Q(serial_number__icontains=p["q"]) |
                           Q(category__icontains=p["q"]))
        total = qs.aggregate(t=Sum("purchase_value"))["t"] or Decimal("0.00")
        categories = sorted(set(Asset.objects.filter(company=current_company()).exclude(category="").values_list("category", flat=True)))
        return Response({"count": qs.count(), "results": [asset_dict(a, today) for a in qs[:MAX_ROWS]],
                         "total_value": str(total), "categories": categories})

    @transaction.atomic
    def post(self, request):
        need(request, "workplace.manage")
        company = current_company()
        a = Asset(company=company, **parse_asset(request.data))
        services.save_new_asset(a, datetime.date.today())
        log(company, request.user, "asset", a.pk, Action.CREATED, new=_asnap(a))
        return Response(asset_dict(a, datetime.date.today()), status=http.HTTP_201_CREATED)


class AssetDetailView(APIView):
    def _get(self, pk):
        return get_or_404(Asset.objects.filter(company=current_company()).select_related("assigned_to"), pk, "asset")

    def get(self, request, pk):
        need(request, "workplace.manage")
        return Response(asset_dict(self._get(pk), datetime.date.today()))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "workplace.manage")
        a = self._get(pk)
        before = _asnap(a)
        for k, v in parse_asset(request.data, a).items():
            setattr(a, k, v)
        a.save()
        if before != _asnap(a):
            log(a.company, request.user, "asset", a.pk, Action.UPDATED, previous=before, new=_asnap(a))
        return Response(asset_dict(a, datetime.date.today()))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "workplace.manage")
        a = self._get(pk)
        log(a.company, request.user, "asset", a.pk, Action.DELETED, previous=_asnap(a))
        a.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- visitors

def datetime_of(value, key, *, required=False):
    if value in (None, ""):
        if required:
            raise ValidationError({key: "Enter the date and time."})
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError({key: "Use a date and time such as 2026-10-27T10:30."})
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def visitor_dict(v):
    return {"id": str(v.id), "name": v.name, "visitor_company": v.visitor_company, "phone": v.phone, "purpose": v.purpose,
            "host": person(v.host), "host_name": name_of(v.host) or v.host_name, "time_in": v.time_in, "time_out": v.time_out,
            "inside": v.time_out is None, "notes": v.notes, "recorded_by": person(v.recorded_by)}


def parse_visitor(data, existing=None):
    creating = existing is None
    f = {}
    if creating or "name" in data:
        f["name"] = text(data, "name", "The visitor's name", 200, required=True)
    if creating or "purpose" in data:
        f["purpose"] = text(data, "purpose", "The purpose of the visit", 300, required=True)
    for key, label, limit in (("visitor_company", "The company", 200), ("phone", "The phone number", 30),
                              ("host_name", "The host name", 200), ("notes", "The notes", 300)):
        if creating or key in data:
            f[key] = text(data, key, label, limit)
    if creating or "host" in data:
        f["host"] = user_of(data.get("host"), "host")
    if creating or "time_in" in data:
        f["time_in"] = datetime_of(data.get("time_in"), "time_in") or timezone.now()
    if "time_out" in data:
        f["time_out"] = datetime_of(data.get("time_out"), "time_out")
    t_in = f.get("time_in", existing.time_in if existing else None)
    t_out = f.get("time_out", existing.time_out if existing else None)
    if t_in and t_out and t_out < t_in:
        raise ValidationError({"time_out": "The time out cannot be before the time in."})
    return f


def _vsnap(v):
    return {"name": v.name, "visitor_company": v.visitor_company, "phone": v.phone, "purpose": v.purpose,
            "host": name_of(v.host) or v.host_name, "time_in": v.time_in.isoformat(),
            "time_out": v.time_out.isoformat() if v.time_out else ""}


class VisitorCollectionView(APIView):
    def get(self, request):
        need(request, "workplace.manage")
        qs = Visitor.objects.filter(company=current_company()).select_related("host", "recorded_by")
        p = request.query_params
        if p.get("date"):
            d = date_of(p["date"], "date")
            qs = qs.filter(time_in__date=d)
        if p.get("from"):
            qs = qs.filter(time_in__date__gte=date_of(p["from"], "from"))
        if p.get("to"):
            qs = qs.filter(time_in__date__lte=date_of(p["to"], "to"))
        if flag(request, "inside"):
            qs = qs.filter(time_out__isnull=True)
        if p.get("q"):
            qs = qs.filter(Q(name__icontains=p["q"]) | Q(visitor_company__icontains=p["q"]) | Q(purpose__icontains=p["q"]))
        return Response({"count": qs.count(), "inside": qs.filter(time_out__isnull=True).count(),
                         "results": [visitor_dict(v) for v in qs[:MAX_ROWS]]})

    @transaction.atomic
    def post(self, request):
        need(request, "workplace.manage")
        company = current_company()
        v = Visitor(company=company, recorded_by=request.user, **parse_visitor(request.data))
        v.save()
        log(company, request.user, "visitor", v.pk, Action.CREATED, new=_vsnap(v))
        return Response(visitor_dict(v), status=http.HTTP_201_CREATED)


class VisitorDetailView(APIView):
    def _get(self, pk):
        return get_or_404(Visitor.objects.filter(company=current_company()).select_related("host", "recorded_by"), pk, "visitor")

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "workplace.manage")
        v = self._get(pk)
        before = _vsnap(v)
        for k, val in parse_visitor(request.data, v).items():
            setattr(v, k, val)
        v.save()
        if before != _vsnap(v):
            log(v.company, request.user, "visitor", v.pk, Action.UPDATED, previous=before, new=_vsnap(v))
        return Response(visitor_dict(v))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "workplace.manage")
        v = self._get(pk)
        log(v.company, request.user, "visitor", v.pk, Action.DELETED, previous=_vsnap(v))
        v.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class VisitorCheckoutView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "workplace.manage")
        v = VisitorDetailView()._get(pk)
        if v.time_out:
            raise ValidationError("This visitor has already signed out.")
        v.time_out = max(timezone.now(), v.time_in)
        v.save(update_fields=["time_out", "updated_at"])
        log(v.company, request.user, "visitor", v.pk, Action.UPDATED, previous={"time_out": ""}, new={"time_out": v.time_out.isoformat()})
        return Response(visitor_dict(v))


# --------------------------------------------------------------------------- dashboard

class DashboardView(APIView):
    """The module's home. Open to every signed-in user: each list is limited to what that user may see."""

    def get(self, request):
        today = datetime.date.today()
        company, user = current_company(), request.user
        tasks = task_qs(request)
        mine_open = tasks.filter(assignee=user, status__in=Task.OPEN)
        nxt = F("due_date").asc(nulls_last=True)
        week = today + datetime.timedelta(days=7)
        out = {"today": today}

        if user.has_code("workplace.view"):
            upcoming = meeting_qs(request).filter(date__gte=today, status__in=(Meeting.Status.SCHEDULED, Meeting.Status.RESCHEDULED))
            rows = list(upcoming.order_by("date", "start_time")[:6])
            out["upcoming_meetings"] = [meeting_dict(m) for m in rows]
            out["upcoming_count"] = upcoming.filter(date__lte=week).count()
            first = today.replace(day=1)
            by_month = []
            for k in range(-5, 1):
                start = services.add_months(first, k)
                end = services.add_months(first, k + 1) - datetime.timedelta(days=1)
                mq = meeting_qs(request).filter(date__gte=start, date__lte=end)
                by_month.append({"label": start.strftime("%b"), "held": mq.filter(status="held").count(),
                                 "planned": mq.filter(status__in=("scheduled", "rescheduled")).count()})
            out["meetings_by_month"] = by_month
        else:
            out.update({"upcoming_meetings": [], "upcoming_count": 0, "meetings_by_month": []})

        out["my_tasks_due"] = [task_dict(t, today=today) for t in mine_open.order_by(nxt, "-created_at")[:6]]
        out["my_open_count"] = mine_open.count()
        out["my_due_soon"] = mine_open.filter(due_date__lte=week, due_date__gte=today).count()
        out["my_overdue"] = mine_open.filter(due_date__lt=today).count()
        overdue = tasks.filter(status__in=Task.OPEN, due_date__lt=today)
        out["overdue_total"] = overdue.count()
        out["overdue_tasks"] = [task_dict(t, today=today) for t in overdue.order_by("due_date")[:6]]
        out["analytics"] = services.task_analytics(tasks, today)

        feed = services.published(Announcement.objects.filter(company=company), today).filter(services.audience_q(user))
        out["announcements"] = [announcement_dict(a, today) for a in feed.select_related("created_by")[:5]]

        pending = [c for c in Circular.objects.filter(company=company, status=Circular.Status.ISSUED).filter(
            services.audience_q(user)).select_related("issued_document") if not c.acknowledgements.filter(user=user).exists()]
        out["pending_acknowledgements"] = [{"id": str(c.id), "number": c.number, "subject": c.subject, "issue_date": c.issue_date}
                                           for c in pending[:6]]
        out["pending_acknowledgement_count"] = len(pending)
        return Response(out)
