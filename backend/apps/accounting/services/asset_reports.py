"""Fixed-asset reports: the register as on a date, depreciation by financial year, disposals, and the dashboard figures."""
import datetime
from collections import defaultdict
from decimal import Decimal

from ..models import Account, AssetDisposal, DepreciationLine, FixedAsset
from . import assets as asset_service, depreciation as dep, ledger

ZERO = Decimal("0.00")
Status = FixedAsset.Status


def _held_state(asset, disposal, as_on):
    """The status the asset had on `as_on`: a disposal dated after it has not happened yet."""
    if disposal is not None and disposal.date <= as_on:
        return asset.status
    return Status.IN_USE


def register(company, as_on, *, category=None, status=None, q=None, project=None):
    assets = FixedAsset.objects.filter(company=company, purchase_date__lte=as_on).select_related("category")
    if category:
        assets = assets.filter(category_id=category)
    if project:
        assets = assets.filter(project_id=project)
    if q:
        from django.db.models import Q
        assets = assets.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(serial_number__icontains=q) | Q(location__icontains=q))
    assets = list(assets.order_by("code"))
    ids = [a.pk for a in assets]
    disposals = {d.asset_id: d for d in AssetDisposal.objects.filter(asset_id__in=ids, status=AssetDisposal.Status.POSTED)}
    charged = defaultdict(lambda: ZERO)
    for ln in DepreciationLine.objects.filter(asset_id__in=ids, active=True):
        if dep.month_end(ln.period_start) <= as_on:
            charged[ln.asset_id] += ln.amount
    rows, totals, by_category = [], {"cost": ZERO, "accumulated": ZERO, "wdv": ZERO, "count": 0}, {}
    for a in assets:
        disposal = disposals.get(a.pk)
        state = _held_state(a, disposal, as_on)
        if status and state != status:
            continue
        held = state == Status.IN_USE
        accumulated = charged[a.pk] if held else (disposal.accumulated if disposal else charged[a.pk])
        wdv = a.cost - accumulated if held else ZERO
        in_books = a.acquisition != FixedAsset.Acquisition.PENDING
        row = {"id": str(a.pk), "code": a.code, "name": a.name, "category": {"id": str(a.category_id), "name": a.category.name},
               "purchase_date": a.purchase_date, "put_to_use_date": a.put_to_use_date, "cost": a.cost, "accumulated": accumulated,
               "wdv": wdv, "status": state, "status_label": dict(Status.choices)[state], "acquisition": a.acquisition,
               "in_books": in_books, "location": a.location, "serial_number": a.serial_number,
               "project": str(a.project_id) if a.project_id else None, "disposed_on": disposal.date if disposal and not held else None,
               "method": a.method}
        rows.append(row)
        if held:
            totals["cost"] += a.cost
            totals["accumulated"] += accumulated
            totals["wdv"] += wdv
            totals["count"] += 1
            c = by_category.setdefault(str(a.category_id), {"id": str(a.category_id), "name": a.category.name, "count": 0,
                                                            "cost": ZERO, "accumulated": ZERO, "wdv": ZERO})
            c["count"] += 1
            c["cost"] += a.cost
            c["accumulated"] += accumulated
            c["wdv"] += wdv
    return {"as_on": as_on, "rows": rows, "totals": totals, "by_category": sorted(by_category.values(), key=lambda c: c["name"]),
            "ledger_check": _ledger_check(company, as_on, rows)}


def _ledger_check(company, as_on, rows):
    """The register against the ledger accounts the categories use: a difference means something was posted outside the register
    (an opening balance, a manual journal) or an asset's purchase is not in the books yet."""
    from ..models import AssetCategory
    cost_ids, accum_ids = set(), set()
    for c in AssetCategory.objects.filter(company=company):
        cost_ids.add(c.cost_account_id)
        accum_ids.add(c.accumulated_account_id)
    totals = ledger.totals(company, end=as_on, account_ids=list(cost_ids | accum_ids))
    ledger_cost = sum((ledger.money(totals.get(i, (ZERO, ZERO))[0] - totals.get(i, (ZERO, ZERO))[1]) for i in cost_ids), ZERO)
    ledger_acc = sum((ledger.money(totals.get(i, (ZERO, ZERO))[1] - totals.get(i, (ZERO, ZERO))[0]) for i in accum_ids), ZERO)
    held = [r for r in rows if r["status"] == Status.IN_USE and r["in_books"]]
    reg_cost = sum((r["cost"] for r in held), ZERO)
    reg_acc = sum((r["accumulated"] for r in held), ZERO)
    return {"register_cost": reg_cost, "ledger_cost": ledger_cost, "register_accumulated": reg_acc, "ledger_accumulated": ledger_acc,
            "agrees": reg_cost == ledger_cost and reg_acc == ledger_acc}


def depreciation_by_year(company, fy=None):
    """Depreciation posted for each financial year, with what the schedules still have to charge: by category and by asset."""
    assets = list(FixedAsset.objects.filter(company=company).select_related("category").order_by("code"))
    posted = defaultdict(lambda: ZERO)        # (fy, asset id)
    for ln in DepreciationLine.objects.filter(asset__company=company, active=True):
        posted[(dep.fy_label(ln.period_start), ln.asset_id)] += ln.amount
    scheduled = defaultdict(lambda: ZERO)
    for a in assets:
        if a.status != Status.IN_USE:
            continue
        for r in asset_service.schedule_for(a):
            scheduled[(r["fy"], a.pk)] += r["charge"]
    years = sorted({k[0] for k in posted} | {k[0] for k in scheduled})
    if fy:
        years = [y for y in years if y == fy]
    by_asset = {a.pk: a for a in assets}
    out = []
    for y in years:
        start_year = int(y[:4])
        cats = {}
        for source, key in ((posted, "posted"), (scheduled, "scheduled")):
            for (label, aid), amount in source.items():
                if label != y:
                    continue
                a = by_asset[aid]
                c = cats.setdefault(str(a.category_id), {"id": str(a.category_id), "name": a.category.name, "posted": ZERO,
                                                         "scheduled": ZERO})
                c[key] += amount
        out.append({"fy": y, "start": datetime.date(start_year, 4, 1), "end": datetime.date(start_year + 1, 3, 31),
                    "posted": sum((c["posted"] for c in cats.values()), ZERO),
                    "scheduled": sum((c["scheduled"] for c in cats.values()), ZERO),
                    "categories": sorted(cats.values(), key=lambda c: c["name"])})
    rows = []
    for a in assets:
        amounts = {y: posted[(y, a.pk)] for y in years if posted.get((y, a.pk))}
        if amounts:
            rows.append({"id": str(a.pk), "code": a.code, "name": a.name, "category": a.category.name, "by_fy": amounts})
    return {"years": out, "assets": rows}


def disposals_report(company, start, end):
    qs = AssetDisposal.objects.filter(company=company, status=AssetDisposal.Status.POSTED, date__gte=start, date__lte=end) \
        .select_related("asset", "asset__category")
    rows, gain, loss, proceeds = [], ZERO, ZERO, ZERO
    for d in qs.order_by("date", "created_at"):
        rows.append({"id": str(d.pk), "asset": {"id": str(d.asset_id), "code": d.asset.code, "name": d.asset.name},
                     "category": d.asset.category.name, "kind": d.kind, "kind_label": d.get_kind_display(), "date": d.date,
                     "buyer": d.buyer, "cost": d.cost, "accumulated": d.accumulated, "wdv": d.wdv, "proceeds": d.proceeds,
                     "gain_loss": d.gain_loss})
        proceeds += d.proceeds
        if d.gain_loss >= 0:
            gain += d.gain_loss
        else:
            loss += -d.gain_loss
    return {"start": start, "end": end, "rows": rows, "total_proceeds": proceeds, "total_gain": gain, "total_loss": loss,
            "net": gain - loss}


def pending_depreciation(company, today):
    """Months up to this month that assets have not been charged for: how many, how much, and the first and last."""
    cache, due = {}, defaultdict(lambda: ZERO)
    through = dep.month_end(today)
    live = defaultdict(set)
    for aid, ms in DepreciationLine.objects.filter(asset__company=company, active=True).values_list("asset_id", "period_start"):
        live[aid].add(ms)
    for a in FixedAsset.objects.filter(company=company, status=Status.IN_USE).exclude(acquisition=FixedAsset.Acquisition.PENDING):
        for r in asset_service.schedule_for(a, through=through):
            if r["period_start"] not in live[a.pk]:
                due[r["period_start"]] += r["charge"]
    months = sorted(due)
    return {"months": len(months), "amount": sum(due.values(), ZERO), "first": months[0] if months else None,
            "last": months[-1] if months else None}


def dashboard(company, today=None):
    today = today or datetime.date.today()
    reg = register(company, today)
    fy0, fy1 = dep.fy_start(today), dep.fy_end(today)
    months = dep.months_between(fy0, dep.month_start(fy1))
    monthly = {m: ZERO for m in months}
    for ln in DepreciationLine.objects.filter(asset__company=company, active=True, period_start__gte=fy0, period_start__lte=fy1):
        monthly[ln.period_start] += ln.amount
    all_assets = FixedAsset.objects.filter(company=company)
    return {
        "today": today, "financial_year": dep.fy_label(today),
        "totals": reg["totals"], "by_category": reg["by_category"], "ledger_check": reg["ledger_check"],
        "counts": {s: all_assets.filter(status=s).count() for s in Status.values},
        "not_in_books": all_assets.filter(status=Status.IN_USE, acquisition=FixedAsset.Acquisition.PENDING).count(),
        "depreciation_this_year": sum(monthly.values(), ZERO),
        "monthly": [{"month": f"{m:%Y-%m}", "label": m.strftime("%b"), "amount": a} for m, a in monthly.items()],
        "pending": pending_depreciation(company, today),
        "disposals_this_year": disposals_report(company, fy0, fy1),
    }
