"""The sales register (GSTR-1 layout) and the purchase register (input credit), both for a date range.

Sales come from issued sales documents (their stored worked-out lines): customer GSTIN, place of supply, HSN, rate-wise taxable
value. Purchases come from the journal: every entry that debits an input-GST account, with the party and reference on the entry.
Each builder is tolerant: a missing app, table or field gives an empty section, never an error.
"""
import datetime
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.apps import apps as django_apps
from django.db.models import Sum

from apps.core import india

from .. import constants as C
from . import books
from .books import HEADS, ZERO, money

GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")


def _dec(v):
    try:
        return Decimal(str(v if v not in (None, "") else 0))
    except (InvalidOperation, ValueError):
        return ZERO


def pos_label(code):
    return f"{code}-{india.GST_STATES[code]}" if code in india.GST_STATES else (code or "")


def _month(d):
    return f"{d.year}-{d.month:02d}"


def _zero_row():
    return {"taxable": ZERO, "igst": ZERO, "cgst": ZERO, "sgst": ZERO, "cess": ZERO}


def _add(row, taxable, igst, cgst, sgst, sign=1):
    row["taxable"] += sign * taxable
    row["igst"] += sign * igst
    row["cgst"] += sign * cgst
    row["sgst"] += sign * sgst


def _tax_split(tax, supply):
    """(igst, cgst, sgst) of a line's tax: halves of it for an intra-state supply, all IGST for any other."""
    if tax == 0:
        return ZERO, ZERO, ZERO
    if supply == "intra_state":
        half = money(tax / 2)
        return ZERO, half, tax - half
    return tax, ZERO, ZERO


# --------------------------------------------------------------------------- sales register

def _empty_sales(start, end):
    return {"from": start, "to": end, "b2b": [], "b2cl": [], "b2cs": [], "cdnr": [], "cdnur": [], "exports": [], "hsn": [],
            "nil_rated": {"taxable": ZERO, "documents": 0}, "by_month": [], "tax_by_rate": [],
            "totals": {"documents": 0, "taxable": ZERO, "igst": ZERO, "cgst": ZERO, "sgst": ZERO, "cess": ZERO, "tax": ZERO,
                       "invoice_value": ZERO},
            "section_totals": {k: {"count": 0, "taxable": ZERO, "igst": ZERO, "cgst": ZERO, "sgst": ZERO, "tax": ZERO, "value": ZERO}
                               for k in ("b2b", "b2cl", "b2cs", "cdnr", "cdnur", "exports")},
            "gstin": "", "warnings": []}


def sales_register(company, start, end):
    return books.safe(lambda: _sales_register(company, start, end), _empty_sales(start, end), "sales register")


def _sales_register(company, start, end):
    from apps.sales.models import SalesDocument

    reg = _empty_sales(start, end)
    reg["gstin"] = getattr(company, "gstin", "") or ""
    K = SalesDocument.Kind
    docs = SalesDocument.objects.filter(
        company=company, status=SalesDocument.Status.ISSUED, kind__in=[K.INVOICE, K.DEBIT_NOTE, K.CREDIT_NOTE],
        issue_date__gte=start, issue_date__lte=end).select_related("customer", "against").order_by("issue_date", "number")

    b2cs = defaultdict(lambda: {"taxable": ZERO, "igst": ZERO, "cgst": ZERO, "sgst": ZERO, "docs": set()})
    hsn = defaultdict(lambda: {"description": "", "uqc": "", "quantity": ZERO, "value": ZERO, "taxable": ZERO, "igst": ZERO,
                               "cgst": ZERO, "sgst": ZERO})
    by_month = defaultdict(lambda: {"documents": 0, **_zero_row(), "tax": ZERO})
    by_rate = defaultdict(lambda: {"taxable": ZERO, "tax": ZERO})
    totals, section_totals = reg["totals"], reg["section_totals"]

    for doc in docs:
        rec = doc.record or {}
        cust = rec.get("customer") or {}
        gstin = (cust.get("gstin") or doc.customer.gstin or "").strip().upper()
        pos = (rec.get("place_of_supply") or {}).get("code") or cust.get("state_code") or (gstin[:2] if gstin else "") or doc.customer.state_code
        supply = rec.get("supply_type") or ("inter_state" if rec.get("gst_applied") else "")
        sign = -1 if doc.kind == K.CREDIT_NOTE else 1
        rates = defaultdict(_zero_row)
        for line in rec.get("lines") or []:
            taxable, tax = _dec(line.get("taxable")), _dec(line.get("tax"))
            rate = _dec(line.get("gst_rate"))
            igst, cgst, sgst = _tax_split(tax, supply)
            _add(rates[rate], taxable, igst, cgst, sgst)
            if rate == 0:
                reg["nil_rated"]["taxable"] += sign * taxable
            h = hsn[(line.get("hsn_sac") or "NA", rate)]
            h["description"] = h["description"] or (line.get("description") or "")[:60]
            h["uqc"] = h["uqc"] or (line.get("unit") or "NOS").upper()[:10]
            h["quantity"] += sign * _dec(line.get("quantity"))
            h["value"] += sign * (taxable + tax)
            _add(h, taxable, igst, cgst, sgst, sign)
            by_rate[rate]["taxable"] += sign * taxable
            by_rate[rate]["tax"] += sign * tax
        taxable_t = sum((r["taxable"] for r in rates.values()), ZERO)
        igst_t = sum((r["igst"] for r in rates.values()), ZERO)
        cgst_t = sum((r["cgst"] for r in rates.values()), ZERO)
        sgst_t = sum((r["sgst"] for r in rates.values()), ZERO)
        tax_t = igst_t + cgst_t + sgst_t
        value = money(doc.grand_total)
        row = {
            "id": str(doc.id), "kind": doc.kind, "number": doc.number, "date": doc.issue_date, "customer": cust.get("name") or doc.customer.display_name,
            "gstin": gstin, "place_of_supply": pos_label(pos), "place_code": pos, "supply_type": supply,
            "against": doc.against.number if doc.against_id else "", "reason": doc.reason,
            "taxable": taxable_t, "igst": igst_t, "cgst": cgst_t, "sgst": sgst_t, "cess": ZERO, "tax": tax_t, "invoice_value": value,
            "rates": [{"rate": r, **vals, "cess": ZERO} for r, vals in sorted(rates.items())],
            "note_type": {"credit_note": "C", "debit_note": "D"}.get(doc.kind, ""),
        }
        overseas = doc.customer.gst_treatment == "overseas"
        if doc.kind == K.INVOICE:
            if overseas:
                section = "exports"
            elif gstin:
                section = "b2b"
            elif supply == "inter_state" and value > C.B2CL_THRESHOLD:
                section = "b2cl"
            else:
                section = "b2cs"
        else:
            if gstin:
                section = "cdnr"
            else:
                basis = doc.against.grand_total if doc.against_id else value
                section = "cdnur" if supply == "inter_state" and basis > C.B2CL_THRESHOLD else "b2cs"
        if section == "b2cs":
            for rate, vals in rates.items():
                _add(b2cs[(pos, rate)], vals["taxable"], vals["igst"], vals["cgst"], vals["sgst"], sign)
                b2cs[(pos, rate)]["docs"].add(str(doc.id))
        else:
            reg[section].append(row)
        if section != "b2cs":  # small B2C is totalled from its aggregated rows below
            st = section_totals[section]
            st["count"] += 1
            for k, v in (("taxable", taxable_t), ("igst", igst_t), ("cgst", cgst_t), ("sgst", sgst_t), ("tax", tax_t), ("value", value)):
                st[k] += v  # notes are shown as positive amounts with their type
        for k, v in (("taxable", taxable_t), ("igst", igst_t), ("cgst", cgst_t), ("sgst", sgst_t), ("tax", tax_t)):
            totals[k] += sign * v
        totals["invoice_value"] += sign * value
        totals["documents"] += 1
        m = by_month[_month(doc.issue_date)]
        m["documents"] += 1
        _add(m, taxable_t, igst_t, cgst_t, sgst_t, sign)
        m["tax"] += sign * tax_t

    reg["b2cs"] = [{"place_of_supply": pos_label(pos), "place_code": pos, "rate": rate, "taxable": v["taxable"], "igst": v["igst"],
                    "cgst": v["cgst"], "sgst": v["sgst"], "cess": ZERO, "tax": v["igst"] + v["cgst"] + v["sgst"], "documents": len(v["docs"])}
                   for (pos, rate), v in sorted(b2cs.items())]
    sc = section_totals["b2cs"]
    for r in reg["b2cs"]:
        sc["count"] += r["documents"]
        for k in ("taxable", "igst", "cgst", "sgst", "tax"):
            sc[k] += r[k]
        sc["value"] += r["taxable"] + r["tax"]
    reg["hsn"] = [{"hsn": code, "rate": rate, **v, "cess": ZERO, "tax": v["igst"] + v["cgst"] + v["sgst"]}
                  for (code, rate), v in sorted(hsn.items(), key=lambda kv: (kv[0][0], kv[0][1]))]
    reg["by_month"] = [{"month": k, **v} for k, v in sorted(by_month.items())]
    reg["tax_by_rate"] = [{"rate": r, **v} for r, v in sorted(by_rate.items())]
    if any(d for d in reg["b2b"] if not d["gstin"]):
        reg["warnings"].append("Some B2B documents have no GSTIN.")
    for k in ("taxable", "igst", "cgst", "sgst", "cess", "tax", "invoice_value"):
        totals[k] = money(totals[k])
    return reg


# --------------------------------------------------------------------------- purchase register

def _empty_purchases(start, end):
    return {"from": start, "to": end, "rows": [], "by_month": [], "by_supplier": [], "warnings": [],
            "totals": {"documents": 0, "taxable": ZERO, "igst": ZERO, "cgst": ZERO, "sgst": ZERO, "cess": ZERO, "itc": ZERO,
                       "eligible": ZERO, "ineligible": ZERO, "unverified": ZERO}}


def _vendor_lookup(ids):
    """{vendor id: {"name", "gstin"}} from the purchases module if it exists; empty when it does not."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    try:
        Vendor = django_apps.get_model("purchases", "Vendor")
        found = {}
        for v in Vendor.objects.filter(pk__in=ids):
            found[str(v.pk)] = {"name": getattr(v, "name", None) or getattr(v, "display_name", None) or str(v),
                                "gstin": (getattr(v, "gstin", "") or "").strip().upper()}
        return found
    except Exception:  # noqa: BLE001
        return {}


def purchase_register(company, start, end):
    return books.safe(lambda: _purchase_register(company, start, end), _empty_purchases(start, end), "purchase register")


def _purchase_register(company, start, end):
    from apps.accounting.models import JournalLine, Voucher
    from apps.tax.models import ItcFlag

    reg = _empty_purchases(start, end)
    in_keys = list(books.IN_KEY.values())
    lines = JournalLine.objects.filter(entry__company=company, account__system_key__in=in_keys, entry__date__gte=start,
                                       entry__date__lte=end).select_related("entry", "entry__reverses", "account")
    entries = {}
    for l in lines:
        e = l.entry
        eff = e.reverses.source if e.source == "reversal" and e.reverses_id else e.source
        if eff in (books.SETOFF_SOURCE, books.OPENING_SOURCE):
            continue
        row = entries.setdefault(e.id, {"entry": e, "igst": ZERO, "cgst": ZERO, "sgst": ZERO})
        head = l.account.system_key.split("_")[1]
        row[head] += l.debit - l.credit
    if not entries:
        return reg
    ids = list(entries)
    # Everything else on those entries: who it was with, and the amount before tax.
    party, taxable_by_entry = {}, defaultdict(lambda: ZERO)
    for l in JournalLine.objects.filter(entry_id__in=ids).select_related("account"):
        if l.party_type in ("vendor", "supplier") and l.entry_id not in party:
            party[l.entry_id] = l.party_id
        a = l.account
        if a.system_key in books.ALL_KEYS:
            continue
        if a.type == "expense" or a.subtype in ("fixed_asset", "other_current_asset"):
            taxable_by_entry[l.entry_id] += l.debit - l.credit
    vouchers = {v.entry_id: v for v in Voucher.objects.filter(entry_id__in=ids)}
    vendors = _vendor_lookup({p for p in party.values()})
    flags = {f.entry_id: f for f in ItcFlag.objects.filter(company=company, entry_id__in=ids)}
    by_month, by_supplier = defaultdict(lambda: {"documents": 0, "taxable": ZERO, "igst": ZERO, "cgst": ZERO, "sgst": ZERO, "itc": ZERO}), defaultdict(lambda: ZERO)
    t = reg["totals"]
    for eid, r in sorted(entries.items(), key=lambda kv: (kv[1]["entry"].date, kv[1]["entry"].reference)):
        e = r["entry"]
        voucher = vouchers.get(eid)
        vendor = vendors.get(party.get(eid, ""), {})
        label = e.narration.split(": ", 1)[1] if ": " in e.narration else e.narration
        supplier = vendor.get("name") or (voucher.party_name if voucher else "") or label
        gstin = vendor.get("gstin") or ""
        if not gstin:
            m = GSTIN_RE.search(f"{e.narration} {voucher.notes if voucher else ''}".upper())
            gstin = m.group(0) if m else ""
        sign = -1 if e.source == "reversal" and e.reverses_id else 1
        taxable = money(voucher.amount * sign) if voucher else money(taxable_by_entry[eid])
        itc = money(r["igst"] + r["cgst"] + r["sgst"])
        flag = flags.get(eid)
        if flag and flag.eligibility == "ineligible":
            elig = "ineligible"
        elif gstin:
            elig = "eligible"
        else:
            elig = "unverified"
        row = {"entry_id": str(eid), "date": e.date, "reference": voucher.reference if voucher and voucher.reference else e.reference,
               "journal_reference": e.reference, "source": e.source, "supplier": supplier, "gstin": gstin, "taxable": taxable,
               "igst": money(r["igst"]), "cgst": money(r["cgst"]), "sgst": money(r["sgst"]), "cess": ZERO, "itc": itc,
               "eligibility": elig, "reason": flag.reason if flag else "",
               "supply_type": "inter_state" if r["igst"] else "intra_state", "narration": e.narration}
        reg["rows"].append(row)
        t["documents"] += 1
        for k in ("taxable", "igst", "cgst", "sgst", "itc"):
            t[k] += row[k]
        t[{"eligible": "eligible", "ineligible": "ineligible", "unverified": "unverified"}[elig]] += itc
        m = by_month[_month(e.date)]
        m["documents"] += 1
        for k in ("taxable", "igst", "cgst", "sgst", "itc"):
            m[k] += row[k]
        by_supplier[supplier or "Unknown"] += itc
    reg["by_month"] = [{"month": k, **v} for k, v in sorted(by_month.items())]
    reg["by_supplier"] = [{"supplier": k, "itc": v} for k, v in sorted(by_supplier.items(), key=lambda kv: -kv[1])[:10]]
    if t["unverified"]:
        reg["warnings"].append("Some purchases have no supplier GSTIN, so their input credit is not verified.")
    return reg
