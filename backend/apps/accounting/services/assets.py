"""Fixed assets: categories, assets, the purchase posting, monthly depreciation runs and disposals.

What each action does to the books (every one through `ledger.post` / `ledger.reverse_source`, in the caller's transaction):

    Record the purchase      Dr Fixed asset (cost)                  Cr Bank / cash / accounts payable (cost)
                             (skipped for an asset already in the books through a bill or opening balance)
    Monthly depreciation     Dr Depreciation expense                Cr Accumulated depreciation      (one entry per month)
    Sale / disposal / write-off
                             Dr Bank / cash / receivable (proceeds)  Dr Accumulated depreciation (all of it)
                             Dr Depreciation expense (the part month up to the date)   Dr Loss on sale (if a loss)
                             Cr Fixed asset (cost)                   Cr Profit on sale (if a gain)
    gain or loss = proceeds - written-down value on the disposal date. GST on a sale is not worked out here; the proceeds
    are what reaches the bank (record the GST with a journal entry or an income voucher).

The month-by-month rules are in `depreciation.py`.
"""
import datetime
import uuid
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum

from apps.company.constants import DocType
from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import numbering

from ..models import Account, AssetCategory, AssetDisposal, DepreciationLine, DepreciationRun, FixedAsset, JournalEntry
from . import chart, depreciation as dep, ledger

Action = CompanyProfileAuditEvent.Action
Source = JournalEntry.Source
ZERO = Decimal("0.00")
S = Account.Subtype
Method = AssetCategory.Method
MAX_MONTHS_PER_RUN = 36

# (name, cost account code, method, life years, income-tax rate %) - all with the Companies Act 5% residual value.
STANDARD_CATEGORIES = [
    ("Computers and IT equipment", "1600", 3, 40),
    ("Furniture and fittings", "1610", 10, 10),
    ("Office equipment", "1620", 5, 15),
]


# --------------------------------------------------------------------------- small helpers

def _dec(value, field, label, *, minimum=None, maximum=None, blank_ok=False, min_exclusive=False, max_exclusive=False):
    if value in (None, ""):
        if blank_ok:
            return None
        raise ValidationError({field: f"Enter {label}."})
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValidationError({field: f"{label} must be a number."})
    if not d.is_finite() or d.as_tuple().exponent < -2:
        raise ValidationError({field: f"{label}: use at most 2 decimal places."})
    if minimum is not None and (d < minimum or (min_exclusive and d == minimum)):
        raise ValidationError({field: f"{label} must be {'more than' if min_exclusive else 'at least'} {minimum}."})
    if maximum is not None and (d > maximum or (max_exclusive and d == maximum)):
        raise ValidationError({field: f"{label} must be {'less than' if max_exclusive else 'at most'} {maximum}."})
    return d


def _date(value, field, label="the date", required=True):
    if isinstance(value, datetime.date):
        return value
    if value in (None, ""):
        if required:
            raise ValidationError({field: f"Enter {label}."})
        return None
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError({field: "Use a date in YYYY-MM-DD format."})


def _uuid(value, field, label):
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ValidationError({field: f"Choose a valid {label}."})


def _text(data, key, size, current=""):
    return str(data[key] if data.get(key) is not None else "").strip()[:size] if key in data else current


def _account(company, ref, subtypes, field, label):
    acct = None
    if ref:
        try:
            acct = Account.objects.filter(pk=ref, company=company).first()
        except (ValueError, ValidationError):
            acct = None
    if acct is None or acct.subtype not in subtypes or not acct.is_active:
        raise ValidationError({field: f"Choose the {label} account."})
    return acct


def _audit(company, actor, entity, obj, action, *, new=None, previous=None, reason=""):
    ledger.audit_event(company, actor, entity, obj.pk, action, new=new, previous=previous, reason=reason)


# --------------------------------------------------------------------------- categories

def category_snapshot(c):
    return {"name": c.name, "method": c.method, "useful_life_years": str(c.useful_life_years or ""),
            "rate_percent": str(c.rate_percent or ""), "salvage_percent": str(c.salvage_percent), "half_year_rule": c.half_year_rule,
            "cost_account": c.cost_account.code, "accumulated_account": c.accumulated_account.code,
            "expense_account": c.expense_account.code, "is_active": c.is_active}


def validate_terms(method, life, rate, salvage):
    """The depreciation terms an asset or category must have for `method`."""
    if method not in Method.values:
        raise ValidationError({"method": "Choose straight line or written-down value."})
    if method == Method.SLM and not life:
        raise ValidationError({"useful_life_years": "Enter the useful life in years for straight-line depreciation."})
    if method == Method.WDV and not rate:
        raise ValidationError({"rate_percent": "Enter the rate % for written-down-value depreciation."})


def _terms_fields(data, current=None):
    g = (lambda k, d=None: data[k] if k in data else (getattr(current, k) if current is not None else d))
    method = g("method", Method.SLM)
    life = _dec(g("useful_life_years"), "useful_life_years", "the useful life", minimum=Decimal("0"), maximum=Decimal("100"),
                blank_ok=True, min_exclusive=True)
    rate = _dec(g("rate_percent"), "rate_percent", "the rate", minimum=Decimal("0"), maximum=Decimal("100"), blank_ok=True,
                min_exclusive=True)
    salvage = _dec(g("salvage_percent", 0), "salvage_percent", "the salvage value %", minimum=Decimal("0"),
                   maximum=Decimal("100"), max_exclusive=True)
    half = g("half_year_rule", False)
    if not isinstance(half, bool):
        raise ValidationError({"half_year_rule": "Send true or false."})
    validate_terms(method, life, rate, salvage)
    return dict(method=method, useful_life_years=life, rate_percent=rate, salvage_percent=salvage, half_year_rule=half)


@transaction.atomic
def save_category(company, actor, data, category=None):
    creating = category is None
    if not creating:
        category = AssetCategory.objects.select_for_update().get(pk=category.pk)
    before = category_snapshot(category) if not creating else {}
    name = _text(data, "name", 120, category.name if category else "")
    if not name:
        raise ValidationError({"name": "Enter the category name."})
    if AssetCategory.objects.filter(company=company, name__iexact=name).exclude(pk=getattr(category, "pk", None)).exists():
        raise ValidationError({"name": "A category with this name already exists."})
    fields = _terms_fields(data, category)
    chart.ensure_chart(company)

    def pick(key, subtypes, label, default):
        if key in data or creating:
            ref = data.get(key) or (default() if default else None)
            return _account(company, ref, subtypes, key, label)
        return getattr(category, key)

    first_fixed = lambda: Account.objects.filter(company=company, subtype=S.FIXED_ASSET, is_active=True).order_by("code").first()
    cost = pick("cost_account", (S.FIXED_ASSET,), "fixed asset (cost)", lambda: first_fixed() and first_fixed().pk)
    accum = pick("accumulated_account", (S.ACCUMULATED_DEPRECIATION,), "accumulated depreciation",
                 lambda: chart.system_account(company, "accumulated_depreciation").pk)
    expense = pick("expense_account", (S.DEPRECIATION, S.EXPENSE, S.OTHER_EXPENSE), "depreciation expense",
                   lambda: chart.system_account(company, "depreciation").pk)
    if not creating and (cost.pk, accum.pk, expense.pk) != (category.cost_account_id, category.accumulated_account_id,
                                                            category.expense_account_id):
        if category.assets.exclude(acquisition=FixedAsset.Acquisition.PENDING).exists() or DepreciationLine.objects.filter(
                asset__category=category).exists():
            raise ValidationError("Assets in this category already have entries in the books, so its ledger accounts cannot change.")
    active = data.get("is_active", category.is_active if category else True)
    if not isinstance(active, bool):
        raise ValidationError({"is_active": "Send true or false."})
    if creating:
        category = AssetCategory(company=company)
    category.name, category.cost_account, category.accumulated_account, category.expense_account = name, cost, accum, expense
    category.is_active = active
    for k, v in fields.items():
        setattr(category, k, v)
    category.save()
    _audit(company, actor, "asset_category", category, Action.CREATED if creating else Action.UPDATED,
           new=category_snapshot(category), previous=before)
    return category


@transaction.atomic
def delete_category(category, actor):
    if category.assets.exists():
        raise ValidationError("This category has assets. Switch it off instead of deleting it.")
    _audit(category.company, actor, "asset_category", category, Action.DELETED, previous=category_snapshot(category))
    category.delete()


@transaction.atomic
def setup_standard_categories(company, actor):
    """Add the usual categories that do not exist yet. Straight line over the Companies Act life, with the income-tax rate kept
    on the category so it can be switched to written-down value."""
    chart.ensure_chart(company)
    made = []
    for name, code, life, rate in STANDARD_CATEGORIES:
        if AssetCategory.objects.filter(company=company, name__iexact=name).exists():
            continue
        cost = Account.objects.filter(company=company, code=code, subtype=S.FIXED_ASSET).first() or \
            Account.objects.filter(company=company, subtype=S.FIXED_ASSET, is_active=True).order_by("code").first()
        if cost is None:
            continue
        made.append(save_category(company, actor, {
            "name": name, "method": Method.SLM, "useful_life_years": life, "rate_percent": rate, "salvage_percent": 5,
            "cost_account": str(cost.pk)}))
    return made


# --------------------------------------------------------------------------- assets

def asset_snapshot(a):
    return {"code": a.code, "name": a.name, "category": a.category.name, "cost": str(a.cost), "purchase_date": str(a.purchase_date),
            "put_to_use_date": str(a.put_to_use_date), "method": a.method, "useful_life_years": str(a.useful_life_years or ""),
            "rate_percent": str(a.rate_percent or ""), "salvage_percent": str(a.salvage_percent), "half_year_rule": a.half_year_rule,
            "status": a.status, "acquisition": a.acquisition, "vendor": a.vendor_name, "bill": a.bill_number, "location": a.location,
            "serial_number": a.serial_number, "project": str(a.project_id or "")}


def _vendor_and_bill(company, data, asset=None):
    """Resolve the optional soft links to a vendor and a bill (purchases module), keeping their names on the asset."""
    out = {}
    if "vendor" in data or "vendor_id" in data:
        vid = _uuid(data.get("vendor", data.get("vendor_id")), "vendor", "vendor")
        out["vendor_id"], out["vendor_name"] = vid, ""
        if vid:
            try:
                from apps.purchases.models import Vendor
                v = Vendor.objects.filter(company=company, pk=vid).first()
            except ImportError:
                v = None
            if v is None:
                raise ValidationError({"vendor": "No such vendor."})
            out["vendor_name"] = v.name
    if "bill" in data or "bill_id" in data:
        bid = _uuid(data.get("bill", data.get("bill_id")), "bill", "bill")
        out["bill_id"], out["bill_number"] = bid, ""
        if bid:
            try:
                from apps.purchases.models import VendorBill
                b = VendorBill.objects.filter(company=company, pk=bid).select_related("vendor").first()
            except ImportError:
                b = None
            if b is None:
                raise ValidationError({"bill": "No such bill."})
            out["bill_number"] = b.number or b.vendor_bill_no
            if not out.get("vendor_id") and not (asset and asset.vendor_id):
                out["vendor_id"], out["vendor_name"] = b.vendor_id, b.vendor.name
    return out


def _next_code(company, actor, on_date):
    return numbering.allocate_number(company, DocType.FIXED_ASSET, actor=actor, on_date=on_date).reference_number


@transaction.atomic
def create_asset(company, actor, data, *, acquisition="later", paid_from=None):
    """Save an asset. `acquisition`: "later" (not in the books yet), "post" (record the purchase now, paid from
    `paid_from`: a bank or cash account, or None for accounts payable) or "existing" (the cost is already in the books)."""
    name = _text(data, "name", 200)
    if not name:
        raise ValidationError({"name": "Enter the asset name."})
    category = None
    try:
        category = AssetCategory.objects.filter(pk=data.get("category"), company=company).first() if data.get("category") else None
    except (ValueError, ValidationError):
        category = None
    if category is None or not category.is_active:
        raise ValidationError({"category": "Choose the asset category."})
    purchase = _date(data.get("purchase_date"), "purchase_date", "the purchase date")
    put = _date(data.get("put_to_use_date") or purchase, "put_to_use_date", "the date it was put to use")
    if put < purchase:
        raise ValidationError({"put_to_use_date": "An asset cannot be put to use before it was bought."})
    cost = _dec(data.get("cost"), "cost", "the cost", minimum=Decimal("0"), min_exclusive=True, maximum=Decimal("100000000000"))
    if acquisition not in ("later", "post", "existing"):
        raise ValidationError({"acquisition": "Choose how the purchase reaches the books."})
    terms = _terms_fields({k: data[k] for k in ("method", "useful_life_years", "rate_percent", "salvage_percent", "half_year_rule")
                           if k in data}, category)
    project = _uuid(data.get("project", data.get("project_id")), "project", "project")
    links = _vendor_and_bill(company, data)
    asset = FixedAsset(company=company, name=name, category=category, purchase_date=purchase, put_to_use_date=put, cost=cost,
                       vendor_id=links.get("vendor_id"), vendor_name=links.get("vendor_name", ""), bill_id=links.get("bill_id"),
                       bill_number=links.get("bill_number", ""), location=_text(data, "location", 200),
                       serial_number=_text(data, "serial_number", 120), notes=_text(data, "notes", 500), project_id=project,
                       created_by=actor if getattr(actor, "is_authenticated", False) else None, **terms)
    asset.code = _next_code(company, actor, purchase)
    asset.save()
    _audit(company, actor, "fixed_asset", asset, Action.CREATED, new=asset_snapshot(asset))
    if acquisition == "post":
        record_acquisition(asset, actor, paid_from)
    elif acquisition == "existing":
        mark_existing(asset, actor)
    return asset


def has_live_depreciation(asset):
    return DepreciationLine.objects.filter(asset=asset, active=True).exists()


@transaction.atomic
def update_asset(asset, actor, data):
    asset = FixedAsset.objects.select_for_update().select_related("category").get(pk=asset.pk)
    company, before = asset.company, asset_snapshot(asset)
    asset.name = _text(data, "name", 200, asset.name)
    if not asset.name:
        raise ValidationError({"name": "Enter the asset name."})
    for key, size in (("location", 200), ("serial_number", 120), ("notes", 500)):
        setattr(asset, key, _text(data, key, size, getattr(asset, key)))
    if "project" in data or "project_id" in data:
        asset.project_id = _uuid(data.get("project", data.get("project_id")), "project", "project")
    for k, v in _vendor_and_bill(company, data, asset).items():
        setattr(asset, k, v)
    money_keys = ("category", "purchase_date", "cost")
    term_keys = ("put_to_use_date", "method", "useful_life_years", "rate_percent", "salvage_percent", "half_year_rule")
    touching = [k for k in money_keys + term_keys if k in data]
    if touching:
        if asset.status != FixedAsset.Status.IN_USE:
            raise ValidationError("A disposed asset's cost and depreciation terms can no longer be changed.")
        if has_live_depreciation(asset):
            raise ValidationError("Depreciation has been posted for this asset. Reverse the depreciation runs to change its cost, dates or terms.")
        if asset.acquisition == FixedAsset.Acquisition.POSTED and any(k in data for k in money_keys):
            raise ValidationError("The purchase has been posted to the books. Undo the purchase posting to change the category, "
                                  "purchase date or cost.")
        if "category" in data:
            cat = AssetCategory.objects.filter(pk=data["category"], company=company).first()
            if cat is None or not cat.is_active:
                raise ValidationError({"category": "Choose the asset category."})
            asset.category = cat
        if "purchase_date" in data:
            asset.purchase_date = _date(data["purchase_date"], "purchase_date", "the purchase date")
        if "put_to_use_date" in data:
            asset.put_to_use_date = _date(data["put_to_use_date"], "put_to_use_date", "the date it was put to use")
        if "cost" in data:
            asset.cost = _dec(data["cost"], "cost", "the cost", minimum=Decimal("0"), min_exclusive=True, maximum=Decimal("100000000000"))
        if asset.put_to_use_date < asset.purchase_date:
            raise ValidationError({"put_to_use_date": "An asset cannot be put to use before it was bought."})
        for k, v in _terms_fields({k: data[k] for k in term_keys[1:] if k in data}, asset).items():
            setattr(asset, k, v)
    asset.save()
    after = asset_snapshot(asset)
    if before != after:
        _audit(company, actor, "fixed_asset", asset, Action.UPDATED, previous=before, new=after)
    return asset


@transaction.atomic
def delete_asset(asset, actor):
    asset = FixedAsset.objects.select_for_update().get(pk=asset.pk)
    if asset.acquisition != FixedAsset.Acquisition.PENDING or DepreciationLine.objects.filter(asset=asset).exists() \
            or asset.disposals.exists():
        raise ValidationError("This asset has entries in the books. Only an asset with nothing posted can be deleted.")
    _audit(asset.company, actor, "fixed_asset", asset, Action.DELETED, previous=asset_snapshot(asset))
    asset.delete()


# --------------------------------------------------------------------------- the purchase

def _credit_account(company, paid_from):
    if paid_from in (None, "", "payable"):
        return chart.system_account(company, "payable")
    return _account(company, paid_from, (S.CASH, S.BANK, S.PAYABLE), "paid_from", "bank, cash or payable")


@transaction.atomic
def record_acquisition(asset, actor, paid_from=None):
    asset = FixedAsset.objects.select_for_update().select_related("category", "company").get(pk=asset.pk)
    company = asset.company
    if asset.acquisition != FixedAsset.Acquisition.PENDING:
        raise ValidationError("The purchase of this asset is already in the books.")
    if asset.status != FixedAsset.Status.IN_USE:
        raise ValidationError("This asset has been disposed of.")
    credit = _credit_account(company, paid_from)
    party = {"type": "vendor", "id": asset.vendor_id} if asset.vendor_id and credit.subtype == S.PAYABLE else None
    label = f"Purchase of {asset.name} ({asset.code})"
    seq = asset.acquisition_seq + 1
    lines = [{"account": asset.category.cost_account, "debit": asset.cost, "memo": label, "project": asset.project_id},
             {"account": credit, "credit": asset.cost, "memo": label, **({"party": party} if party else {})}]
    ledger.post(company, date=asset.purchase_date, lines=lines, narration=label, source=Source.FIXED_ASSET,
                source_id=f"acq:{asset.pk}:{seq}", reference=asset.code, actor=actor)
    asset.acquisition_seq, asset.acquisition = seq, FixedAsset.Acquisition.POSTED
    asset.save(update_fields=["acquisition_seq", "acquisition", "updated_at"])
    _audit(company, actor, "fixed_asset", asset, Action.UPDATED, previous={"acquisition": "pending"},
           new={"acquisition": "posted", "paid_from": credit.name}, reason="Purchase posted to the books")
    return asset


@transaction.atomic
def mark_existing(asset, actor):
    """The cost is already in the books (a vendor bill, an opening balance): no entry is made, so nothing is posted twice."""
    asset = FixedAsset.objects.select_for_update().get(pk=asset.pk)
    if asset.acquisition != FixedAsset.Acquisition.PENDING:
        raise ValidationError("The purchase of this asset is already in the books.")
    asset.acquisition = FixedAsset.Acquisition.EXISTING
    asset.save(update_fields=["acquisition", "updated_at"])
    _audit(asset.company, actor, "fixed_asset", asset, Action.UPDATED, previous={"acquisition": "pending"},
           new={"acquisition": "existing"}, reason="Cost already in the books; no entry made")
    return asset


@transaction.atomic
def undo_acquisition(asset, actor, reason):
    asset = FixedAsset.objects.select_for_update().select_related("company").get(pk=asset.pk)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"change_reason": "Give a reason."})
    if asset.acquisition == FixedAsset.Acquisition.PENDING:
        raise ValidationError("The purchase of this asset is not in the books from here.")
    if asset.status != FixedAsset.Status.IN_USE or asset.disposals.filter(status=AssetDisposal.Status.POSTED).exists():
        raise ValidationError("This asset has been disposed of. Reverse the disposal first.")
    if has_live_depreciation(asset):
        raise ValidationError("Depreciation has been posted for this asset. Reverse the depreciation runs first.")
    was = asset.acquisition
    if was == FixedAsset.Acquisition.POSTED:
        ledger.reverse_source(asset.company, Source.FIXED_ASSET, f"acq:{asset.pk}:{asset.acquisition_seq}", actor, reason)
    asset.acquisition = FixedAsset.Acquisition.PENDING
    asset.save(update_fields=["acquisition", "updated_at"])
    _audit(asset.company, actor, "fixed_asset", asset, Action.UPDATED, previous={"acquisition": was},
           new={"acquisition": "pending"}, reason=reason)
    return asset


# --------------------------------------------------------------------------- depreciation runs

def _schedule_cache(cache, asset, through):
    key = asset.pk
    if key not in cache:
        cache[key] = dep.schedule(dep.terms_of(asset), through=through)
    return cache[key]


def plan_month(company, ms, *, cache=None, last_month=None):
    """What a run for the month starting `ms` would post: rows, assets skipped (and why), and the total."""
    cache = {} if cache is None else cache
    me = dep.month_end(ms)
    horizon = dep.month_end(last_month or ms)
    assets = FixedAsset.objects.filter(company=company, status=FixedAsset.Status.IN_USE, put_to_use_date__lte=me).select_related(
        "category", "category__expense_account", "category__accumulated_account")
    live = set(DepreciationLine.objects.filter(asset__company=company, period_start=ms, active=True).values_list("asset_id", flat=True))
    rows, skipped, already = [], [], 0
    for a in assets.order_by("code"):
        if a.pk in live:
            already += 1
            continue
        due = next((r for r in _schedule_cache(cache, a, horizon) if r["period_start"] == ms), None)
        if due is None:
            continue
        if a.acquisition == FixedAsset.Acquisition.PENDING:
            skipped.append({"asset": a, "reason": "The purchase is not in the books yet. Record the purchase first."})
            continue
        rows.append({"asset": a, "amount": due["charge"], "accumulated": due["accumulated"], "wdv": due["wdv"]})
    return {"period_start": ms, "period_end": me, "rows": rows, "skipped": skipped, "already": already,
            "total": sum((r["amount"] for r in rows), ZERO)}


def _months(first, last, today):
    first, last = dep.month_start(_date(first, "period", "the month")), dep.month_start(_date(last or first, "to", "the month"))
    if last < first:
        raise ValidationError({"to": "The last month is before the first month."})
    if last > dep.month_start(today):
        raise ValidationError({"period": "Depreciation cannot be run for a month that has not started."})
    months = dep.months_between(first, last)
    if len(months) > MAX_MONTHS_PER_RUN:
        raise ValidationError({"to": f"Run at most {MAX_MONTHS_PER_RUN} months at a time."})
    return months


def preview(company, first, last=None, today=None):
    months = _months(first, last, today or datetime.date.today())
    cache = {}
    return [plan_month(company, ms, cache=cache, last_month=months[-1]) for ms in months]


def _post_month(company, actor, plan):
    ms, me, rows = plan["period_start"], plan["period_end"], plan["rows"]
    groups = {}
    for r in rows:
        a = r["asset"]
        key = (a.category.expense_account_id, a.category.accumulated_account_id, a.project_id)
        g = groups.setdefault(key, {"expense": a.category.expense_account, "accum": a.category.accumulated_account, "amount": ZERO,
                                    "name": a.category.name})
        g["amount"] += r["amount"]
    label = f"Depreciation for {ms:%B %Y}"
    lines = []
    for (_, _, project), g in groups.items():
        memo = f"{label}: {g['name']}"
        lines.append({"account": g["expense"], "debit": g["amount"], "memo": memo, "project": project})
        lines.append({"account": g["accum"], "credit": g["amount"], "memo": memo})
    run = DepreciationRun.objects.create(company=company, period_start=ms, period_end=me, total=plan["total"], asset_count=len(rows),
                                         created_by=actor if getattr(actor, "is_authenticated", False) else None)
    ledger.post(company, date=me, lines=lines, narration=label, source=Source.DEPRECIATION, source_id=str(run.pk),
                reference=f"DEP-{ms:%Y-%m}", actor=actor)
    DepreciationLine.objects.bulk_create([DepreciationLine(asset=r["asset"], run=run, period_start=ms, amount=r["amount"],
                                                           accumulated=r["accumulated"]) for r in rows])
    _audit(company, actor, "depreciation_run", run, Action.CREATED,
           new={"month": f"{ms:%Y-%m}", "assets": len(rows), "total": str(plan["total"])})
    return run


@transaction.atomic
def run_depreciation(company, actor, first, last=None, today=None):
    """Post depreciation for each month from `first` to `last`. A month an asset was already charged for is skipped for that
    asset, so running the same period again posts nothing."""
    months = _months(first, last, today or datetime.date.today())
    # Lock the assets so two runs at once cannot both charge the same month (the unique line is the last line of defence).
    list(FixedAsset.objects.select_for_update().filter(company=company, status=FixedAsset.Status.IN_USE).order_by("pk")
         .values_list("pk", flat=True))
    cache, runs, skipped = {}, [], []
    for ms in months:
        plan = plan_month(company, ms, cache=cache, last_month=months[-1])
        skipped += plan["skipped"]
        if plan["rows"]:
            runs.append(_post_month(company, actor, plan))
    return {"runs": runs, "total": sum((r.total for r in runs), ZERO), "skipped": skipped, "months": len(months)}


@transaction.atomic
def reverse_run(run, actor, reason):
    run = DepreciationRun.objects.select_for_update().select_related("company").get(pk=run.pk)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"change_reason": "Give a reason for reversing this depreciation."})
    if run.status == DepreciationRun.Status.REVERSED:
        raise ValidationError("This depreciation run has already been reversed.")
    sold = FixedAsset.objects.filter(depreciation_lines__run=run, depreciation_lines__active=True).exclude(
        status=FixedAsset.Status.IN_USE).values_list("code", flat=True)
    if sold:
        raise ValidationError(f"{', '.join(sold)} has been disposed of since. Reverse the disposal first.")
    ledger.reverse_source(run.company, Source.DEPRECIATION, run.pk, actor, reason)
    run.lines.filter(active=True).update(active=False)
    run.status, run.reverse_reason = DepreciationRun.Status.REVERSED, reason[:300]
    run.save(update_fields=["status", "reverse_reason", "updated_at"])
    _audit(run.company, actor, "depreciation_run", run, Action.DEACTIVATED, previous={"status": "posted"},
           new={"status": "reversed"}, reason=reason)
    return run


# --------------------------------------------------------------------------- disposal

def posted_depreciation(asset):
    return DepreciationLine.objects.filter(asset=asset, active=True).aggregate(t=Sum("amount"))["t"] or ZERO


def schedule_for(asset, *, stop=None, through=None):
    return dep.schedule(dep.terms_of(asset), stop=stop, through=through)


def disposal_figures(asset, date, proceeds):
    """Everything a disposal on `date` would post: the part-period depreciation still to charge, the written-down value and the
    gain or loss. Raises when depreciation already posted does not fit the date."""
    first_of_month = dep.month_start(date)
    live = {l.period_start: l.amount for l in DepreciationLine.objects.filter(asset=asset, active=True)}
    if any(m > first_of_month for m in live):
        raise ValidationError({"date": "Depreciation has been posted for months after this date. Reverse those runs first, or "
                                       "choose a later date."})
    rows = schedule_for(asset, stop=date)
    scheduled = {r["period_start"]: r for r in rows}
    if first_of_month in live and live[first_of_month] != (scheduled.get(first_of_month) or {"charge": ZERO})["charge"]:
        raise ValidationError({"date": f"A full month's depreciation for {first_of_month:%B %Y} has been posted, but the asset "
                                       "was disposed of before its end. Reverse that run, or choose the last day of the month."})
    new_rows = [r for r in rows if r["period_start"] not in live]
    final = sum((r["charge"] for r in new_rows), ZERO)
    accumulated = sum(live.values(), ZERO) + final
    wdv = asset.cost - accumulated
    return {"rows": new_rows, "final_depreciation": final, "accumulated": accumulated, "wdv": wdv, "gain_loss": proceeds - wdv,
            "posted_depreciation": sum(live.values(), ZERO)}


KIND_STATUS = {AssetDisposal.Kind.SOLD: FixedAsset.Status.SOLD, AssetDisposal.Kind.DISPOSED: FixedAsset.Status.DISPOSED,
               AssetDisposal.Kind.WRITTEN_OFF: FixedAsset.Status.WRITTEN_OFF}


def _validate_disposal(asset, kind, date, proceeds, account_ref):
    if kind not in AssetDisposal.Kind.values:
        raise ValidationError({"kind": "Choose sold, scrapped or written off."})
    if date < asset.put_to_use_date:
        raise ValidationError({"date": "The disposal cannot be before the asset was put to use."})
    if kind == AssetDisposal.Kind.WRITTEN_OFF and proceeds != 0:
        raise ValidationError({"proceeds": "A write-off has no sale proceeds."})
    if kind == AssetDisposal.Kind.SOLD and proceeds <= 0:
        raise ValidationError({"proceeds": "Enter the sale amount."})
    if proceeds > 0:
        return _account(asset.company, account_ref, (S.CASH, S.BANK, S.RECEIVABLE), "proceeds_account",
                        "bank, cash or receivable the money goes to")
    return None


@transaction.atomic
def dispose(asset, actor, *, kind, date, proceeds=0, proceeds_account=None, buyer="", notes=""):
    asset = FixedAsset.objects.select_for_update().select_related("category", "company").get(pk=asset.pk)
    company = asset.company
    date = _date(date, "date", "the disposal date")
    proceeds = _dec(proceeds if proceeds not in (None, "") else 0, "proceeds", "the proceeds", minimum=Decimal("0"),
                    maximum=Decimal("100000000000"))
    if asset.status != FixedAsset.Status.IN_USE:
        raise ValidationError("This asset has already been disposed of.")
    if asset.acquisition == FixedAsset.Acquisition.PENDING:
        raise ValidationError("The purchase of this asset is not in the books yet, so there is nothing to remove. Record the purchase "
                              "first, or delete the asset.")
    account = _validate_disposal(asset, kind, date, proceeds, proceeds_account)
    fig = disposal_figures(asset, date, proceeds)
    cat, label = asset.category, f"{dict(AssetDisposal.Kind.choices)[kind]}: {asset.name} ({asset.code})"
    disposal = AssetDisposal.objects.create(
        company=company, asset=asset, kind=kind, date=date, proceeds=proceeds, proceeds_account=account, cost=asset.cost,
        accumulated=fig["accumulated"], final_depreciation=fig["final_depreciation"], wdv=fig["wdv"], gain_loss=fig["gain_loss"],
        buyer=(buyer or "").strip()[:200], notes=(notes or "").strip()[:300],
        created_by=actor if getattr(actor, "is_authenticated", False) else None)
    lines = []
    if proceeds > 0:
        lines.append({"account": account, "debit": proceeds, "memo": label})
    if fig["final_depreciation"] > 0:
        lines.append({"account": cat.expense_account, "debit": fig["final_depreciation"], "memo": f"Depreciation up to {date:%d %b %Y}: {asset.name}",
                      "project": asset.project_id})
    # The accumulated depreciation already posted is cleared; the part-period charge above never goes through that account.
    if fig["posted_depreciation"] > 0:
        lines.append({"account": cat.accumulated_account, "debit": fig["posted_depreciation"], "memo": label})
    if fig["gain_loss"] < 0:
        lines.append({"account": chart.system_account(company, "loss_on_disposal"), "debit": -fig["gain_loss"], "memo": label,
                      "project": asset.project_id})
    lines.append({"account": cat.cost_account, "credit": asset.cost, "memo": label, "project": asset.project_id})
    if fig["gain_loss"] > 0:
        lines.append({"account": chart.system_account(company, "gain_on_disposal"), "credit": fig["gain_loss"], "memo": label,
                      "project": asset.project_id})
    ledger.post(company, date=date, lines=lines, narration=label, source=Source.FIXED_ASSET, source_id=f"disp:{disposal.pk}",
                reference=asset.code, actor=actor)
    posted_before = posted_depreciation(asset)
    running = posted_before
    new_lines = []
    for r in fig["rows"]:
        running += r["charge"]
        new_lines.append(DepreciationLine(asset=asset, disposal=disposal, period_start=r["period_start"], amount=r["charge"],
                                          accumulated=running))
    DepreciationLine.objects.bulk_create(new_lines)
    asset.status = KIND_STATUS[kind]
    asset.save(update_fields=["status", "updated_at"])
    _audit(company, actor, "asset_disposal", disposal, Action.CREATED,
           new={"asset": asset.code, "kind": kind, "date": str(date), "proceeds": str(proceeds), "wdv": str(fig["wdv"]),
                "gain_loss": str(fig["gain_loss"])})
    _audit(company, actor, "fixed_asset", asset, Action.UPDATED, previous={"status": "in_use"}, new={"status": asset.status},
           reason=f"{disposal.get_kind_display()} on {date:%d %b %Y}")
    return disposal


@transaction.atomic
def reverse_disposal(disposal, actor, reason):
    disposal = AssetDisposal.objects.select_for_update().select_related("asset", "company").get(pk=disposal.pk)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"change_reason": "Give a reason for reversing this disposal."})
    if disposal.status == AssetDisposal.Status.REVERSED:
        raise ValidationError("This disposal has already been reversed.")
    ledger.reverse_source(disposal.company, Source.FIXED_ASSET, f"disp:{disposal.pk}", actor, reason)
    disposal.lines.filter(active=True).update(active=False)
    disposal.status, disposal.reverse_reason = AssetDisposal.Status.REVERSED, reason[:300]
    disposal.save(update_fields=["status", "reverse_reason", "updated_at"])
    asset = disposal.asset
    asset.status = FixedAsset.Status.IN_USE
    asset.save(update_fields=["status", "updated_at"])
    _audit(disposal.company, actor, "asset_disposal", disposal, Action.DEACTIVATED, previous={"status": "posted"},
           new={"status": "reversed"}, reason=reason)
    _audit(disposal.company, actor, "fixed_asset", asset, Action.UPDATED, previous={"status": disposal.kind},
           new={"status": "in_use"}, reason=f"Disposal reversed: {reason}")
    return disposal
