"""Dashboard and report figures. Money stays Decimal; every figure comes from one query per measure."""
import datetime
from collections import defaultdict
from decimal import Decimal

from django.utils import timezone
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce

from ..models import Activity, Deal, DealStageHistory, Lead, LeadSource, PipelineStage
from . import common
from .common import ZERO, money
from .deals import weighted

AGING_BUCKETS = [("d0_30", "0-30 days", 30), ("d31_60", "31-60 days", 60), ("d61_90", "61-90 days", 90), ("d91_plus", "Over 90 days", None)]


def month_list(today, back=0, forward=0):
    out = []
    y, m = today.year, today.month
    for _ in range(back):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    for i in range(back + forward + 1):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def month_key(d):
    return (d.year, d.month)


def label(ym):
    return datetime.date(ym[0], ym[1], 1).strftime("%b %y")


def proposal_stage(company):
    stages = list(PipelineStage.objects.filter(company=company, is_active=True, kind="open").order_by("position"))
    for s in stages:
        if "proposal" in s.name.lower() or "quotation" in s.name.lower():
            return s
    return stages[2] if len(stages) > 2 else (stages[-1] if stages else None)


def funnel(company):
    """Leads -> qualified -> proposal -> won. Proposal counts deals that ever reached the proposal stage or beyond."""
    leads = Lead.objects.filter(company=company)
    n_leads = leads.count()
    n_qualified = leads.filter(status__in=("qualified", "converted")).count()
    stage = proposal_stage(company)
    deals = Deal.objects.filter(company=company)
    if stage is not None:
        ids = DealStageHistory.objects.filter(deal__company=company, to_stage__position__gte=stage.position,
                                              to_stage__kind="open").values("deal_id")
        reached = deals.filter(Q(pk__in=ids) | Q(status="won"))
    else:
        reached = deals.filter(status="won")
    won = deals.filter(status="won")
    won_value = won.aggregate(t=Coalesce(Sum("amount"), ZERO))["t"]
    reached_value = reached.aggregate(t=Coalesce(Sum("amount"), ZERO))["t"]
    return [
        {"key": "leads", "label": "Leads", "count": n_leads, "value": None},
        {"key": "qualified", "label": "Qualified", "count": n_qualified, "value": None},
        {"key": "proposal", "label": stage.name if stage else "Proposal", "count": reached.count(), "value": reached_value},
        {"key": "won", "label": "Won", "count": won.count(), "value": won_value},
    ]


def dashboard(company, user=None, today=None):
    today = today or datetime.date.today()
    deals = Deal.objects.filter(company=company)
    open_deals = deals.filter(status="open")
    open_rows = list(open_deals.values_list("amount", "probability", "stage_id", "expected_close_date", "created_at", "owner_id"))
    open_value = sum((r[0] for r in open_rows), ZERO)
    weighted_total = sum((weighted(r[0], r[1]) for r in open_rows), ZERO)

    year_ago = today - datetime.timedelta(days=365)
    closed = deals.filter(closed_on__gte=year_ago)
    won_n, lost_n = closed.filter(status="won").count(), closed.filter(status="lost").count()
    win_rate = round(won_n * 100 / (won_n + lost_n), 1) if won_n + lost_n else None
    cycles = [(closed_on - timezone.localtime(created).date()).days for closed_on, created in
              closed.filter(status="won").values_list("closed_on", "created_at")]
    avg_cycle = round(sum(cycles) / len(cycles), 1) if cycles else None

    month_start = today.replace(day=1)
    leads = Lead.objects.filter(company=company)

    # Pipeline by stage (open stages in board order).
    stages = list(PipelineStage.objects.filter(company=company, is_active=True, kind="open").order_by("position"))
    by_stage = {s.pk: {"count": 0, "value": ZERO, "weighted": ZERO} for s in stages}
    for amount, prob, stage_id, *_ in open_rows:
        if stage_id in by_stage:
            b = by_stage[stage_id]
            b["count"] += 1
            b["value"] += amount
            b["weighted"] += weighted(amount, prob)
    pipeline = [{"key": str(s.pk), "label": s.name, "colour": s.colour, "probability": s.probability, **by_stage[s.pk]} for s in stages]

    # Lead sources.
    names = dict(LeadSource.objects.filter(company=company).values_list("key", "name"))
    sources = [{"key": r["source"], "label": names.get(r["source"], r["source"]), "count": r["n"]}
               for r in leads.values("source").annotate(n=Count("id")).order_by("-n")]

    # Won/lost per close month, last six months.
    months = month_list(today, back=5)
    trend = {m: {"won": 0, "lost": 0, "won_value": ZERO, "lost_value": ZERO} for m in months}
    first = datetime.date(months[0][0], months[0][1], 1)
    for status, closed_on, amount in deals.filter(closed_on__gte=first, status__in=("won", "lost")).values_list("status", "closed_on", "amount"):
        t = trend.get(month_key(closed_on))
        if t:
            t[status] += 1
            t[f"{status}_value"] += amount

    # Owners.
    from django.contrib.auth import get_user_model
    names_by_id = {u.pk: (u.get_full_name() or u.get_username()) for u in get_user_model().objects.filter(
        pk__in={r[5] for r in open_rows if r[5]} | set(deals.filter(status="won", closed_on__gte=year_ago).values_list("owner_id", flat=True)))}
    owners = defaultdict(lambda: {"open_value": ZERO, "won_value": ZERO, "open_count": 0, "won_count": 0})
    for amount, _p, _s, _d, _c, owner_id in open_rows:
        o = owners[owner_id]
        o["open_value"] += amount
        o["open_count"] += 1
    for owner_id, amount in deals.filter(status="won", closed_on__gte=year_ago).values_list("owner_id", "amount"):
        o = owners[owner_id]
        o["won_value"] += amount
        o["won_count"] += 1
    top_owners = sorted(({"key": str(k) if k else "none", "label": names_by_id.get(k, "Unassigned"), **v} for k, v in owners.items()),
                        key=lambda r: -(r["won_value"] + r["open_value"]))[:6]

    # Ageing of open deals by days since created.
    aging = {k: {"count": 0, "value": ZERO} for k, _l, _m in AGING_BUCKETS}
    for amount, _p, _s, _d, created, _o in open_rows:
        days = (today - created.date()).days
        key = next(k for k, _l, limit in AGING_BUCKETS if limit is None or days <= limit)
        aging[key]["count"] += 1
        aging[key]["value"] += amount

    # Forecast by expected close month: this month and the next five, plus past-due and undated.
    fmonths = month_list(today, forward=5)
    fc = {m: {"value": ZERO, "weighted": ZERO, "count": 0} for m in fmonths}
    past_due = {"value": ZERO, "weighted": ZERO, "count": 0}
    undated = {"value": ZERO, "weighted": ZERO, "count": 0}
    later = {"value": ZERO, "weighted": ZERO, "count": 0}
    for amount, prob, _s, close, *_ in open_rows:
        if close is None:
            slot = undated
        elif close < today:
            slot = past_due
        else:
            slot = fc.get(month_key(close), later)
        slot["value"] += amount
        slot["weighted"] += weighted(amount, prob)
        slot["count"] += 1

    mine = {"overdue": 0, "today": 0}
    if user is not None:
        acts = Activity.objects.filter(company=company, owner=user, status="open")
        mine = {"overdue": acts.filter(due_date__lt=today).count(), "today": acts.filter(due_date=today).count()}

    return {
        "today": today.isoformat(),
        "tiles": {
            "open_deals": len(open_rows), "open_value": open_value, "weighted_forecast": weighted_total,
            "win_rate": win_rate, "won_12m": won_n, "lost_12m": lost_n, "avg_cycle_days": avg_cycle,
            "leads_this_month": leads.filter(created_at__date__gte=month_start).count(),
            "open_leads": leads.exclude(status__in=("converted", "unqualified")).count(),
            "won_value_12m": money(deals.filter(status="won", closed_on__gte=year_ago).aggregate(t=Coalesce(Sum("amount"), ZERO))["t"]),
        },
        "funnel": funnel(company),
        "pipeline": pipeline,
        "sources": sources,
        "trend": [{"month": f"{y}-{m:02d}", "label": label((y, m)), **{k: (str(v) if isinstance(v, Decimal) else v) for k, v in trend[(y, m)].items()}}
                  for y, m in months],
        "owners": top_owners,
        "aging": [{"key": k, "label": lbl, **aging[k]} for k, lbl, _m in AGING_BUCKETS],
        "forecast": [{"key": f"{y}-{m:02d}", "label": label((y, m)), **fc[(y, m)]} for y, m in fmonths],
        "forecast_other": {"past_due": past_due, "undated": undated, "later": later},
        "mine": mine,
    }


def reports_summary(company, today=None):
    """Lead conversion by source and the sales team's results."""
    today = today or datetime.date.today()
    names = dict(LeadSource.objects.filter(company=company).values_list("key", "name"))
    by_source = []
    for r in Lead.objects.filter(company=company).values("source").annotate(
            n=Count("id"), converted=Count("id", filter=Q(status="converted")),
            qualified=Count("id", filter=Q(status__in=("qualified", "converted")))).order_by("-n"):
        by_source.append({"key": r["source"], "label": names.get(r["source"], r["source"]), "leads": r["n"], "qualified": r["qualified"],
                          "converted": r["converted"], "rate": round(r["converted"] * 100 / r["n"], 1) if r["n"] else 0})
    from django.contrib.auth import get_user_model
    users = {u.pk: (u.get_full_name() or u.get_username()) for u in get_user_model().objects.all()}
    team = []
    for r in Deal.objects.filter(company=company).values("owner").annotate(
            won=Count("id", filter=Q(status="won")), lost=Count("id", filter=Q(status="lost")), open=Count("id", filter=Q(status="open")),
            won_value=Coalesce(Sum("amount", filter=Q(status="won")), ZERO), open_value=Coalesce(Sum("amount", filter=Q(status="open")), ZERO)):
        closed = r["won"] + r["lost"]
        team.append({"key": str(r["owner"]) if r["owner"] else "none", "label": users.get(r["owner"], "Unassigned"), "open": r["open"],
                     "open_value": r["open_value"], "won": r["won"], "lost": r["lost"], "won_value": r["won_value"],
                     "win_rate": round(r["won"] * 100 / closed, 1) if closed else None})
    team.sort(key=lambda t: -t["won_value"])
    lost = [{"reason": r["lost_reason"] or "(none given)", "count": r["n"], "value": r["v"]} for r in
            Deal.objects.filter(company=company, status="lost").values("lost_reason").annotate(
                n=Count("id"), v=Coalesce(Sum("amount"), ZERO)).order_by("-n")]
    return {"by_source": by_source, "team": team, "lost_reasons": lost, "funnel": funnel(company)}
