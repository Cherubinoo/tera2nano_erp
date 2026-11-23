"""Dashboard figures for the approvals module."""
import datetime
from collections import defaultdict
from decimal import Decimal

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone

from . import engine, registry
from .models import ApprovalDecision, ApprovalRequest

Status = ApprovalRequest.Status


def _months(now, n=6):
    y, m = now.year, now.month
    out = []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def hours(value):
    return round(value.total_seconds() / 3600, 1) if value is not None else 0


def request_age_hours(req, now):
    end = req.decided_at or now
    return round(max((end - req.submitted_at).total_seconds(), 0) / 3600, 1)


def dashboard(company, now=None):
    now = now or timezone.now()
    engine.sweep(company, now)
    qs = ApprovalRequest.objects.filter(company=company)
    counts = {r["status"]: r["n"] for r in qs.values("status").annotate(n=Count("id"))}
    pending = counts.get(Status.PENDING, 0) + counts.get(Status.ESCALATED, 0)

    decided = qs.filter(status__in=[Status.APPROVED, Status.REJECTED], decisions__isnull=False).distinct()
    turn = decided.annotate(t=ExpressionWrapper(F("decided_at") - F("submitted_at"), output_field=DurationField())).aggregate(a=Avg("t"))["a"]

    # Waiting on whom, and how long the open ones have waited.
    directory = engine.Directory()
    waiting = defaultdict(int)
    names = {}
    overdue = 0
    ages = []
    for req in engine.open_requests(company):
        step = engine.active_step(req)
        if step is None:
            continue
        info = engine.step_info(step, req, now, directory)
        if info.overdue:
            overdue += 1
        ages.append((now - req.submitted_at).total_seconds() / 3600)
        for u in info.base:
            waiting[u.pk] += 1
            names[u.pk] = engine.user_label(u)

    local = timezone.localtime(now)
    months = _months(local)
    first = timezone.make_aware(datetime.datetime(months[0][0], months[0][1], 1))
    sub = {(r["m"].year, r["m"].month): r["n"] for r in qs.filter(submitted_at__gte=first).annotate(m=TruncMonth("submitted_at")).values("m").annotate(n=Count("id"))}
    dec = defaultdict(dict)
    for r in qs.filter(decided_at__gte=first, status__in=[Status.APPROVED, Status.REJECTED]).annotate(m=TruncMonth("decided_at")).values("m", "status").annotate(n=Count("id")):
        dec[(r["m"].year, r["m"].month)][r["status"]] = r["n"]
    monthly = [{
        "label": datetime.date(y, m, 1).strftime("%b %y"), "submitted": sub.get((y, m), 0),
        "approved": dec[(y, m)].get(Status.APPROVED, 0), "rejected": dec[(y, m)].get(Status.REJECTED, 0),
    } for y, m in months]

    per_kind = defaultdict(lambda: defaultdict(int))
    for r in qs.values("kind", "status").annotate(n=Count("id")):
        per_kind[r["kind"]][r["status"]] += r["n"]
    by_kind = [{"kind": k, "label": registry.kind_label(k), "total": sum(v.values()),
                "pending": v[Status.PENDING] + v[Status.ESCALATED], "approved": v[Status.APPROVED],
                "rejected": v[Status.REJECTED], "cancelled": v[Status.CANCELLED]}
               for k, v in sorted(per_kind.items(), key=lambda kv: -sum(kv[1].values()))]

    slowest = (ApprovalDecision.objects.filter(request__company=company, approver__isnull=False)
               .values("approver", "approver_label").annotate(avg=Avg("hours_taken"), n=Count("id")).order_by("-avg")[:8])
    return {
        "tiles": {
            "pending": pending, "approved": counts.get(Status.APPROVED, 0), "rejected": counts.get(Status.REJECTED, 0),
            "cancelled": counts.get(Status.CANCELLED, 0), "escalated": counts.get(Status.ESCALATED, 0), "overdue": overdue,
            "total": sum(counts.values()), "avg_turnaround_hours": hours(turn),
            "avg_pending_age_hours": round(sum(ages) / len(ages), 1) if ages else 0,
        },
        "monthly": monthly,
        "by_kind": by_kind,
        "slowest_approvers": [{"approver": r["approver_label"], "avg_hours": float(round(r["avg"] or Decimal(0), 1)), "decisions": r["n"]} for r in slowest],
        "pending_per_approver": sorted(
            ({"approver": names[pk], "pending": n} for pk, n in waiting.items()), key=lambda r: (-r["pending"], r["approver"]))[:8],
    }
