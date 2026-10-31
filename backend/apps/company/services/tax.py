"""GST calculation helpers.

The rules implemented here are the structural ones (CGST+SGST/UTGST within a state, IGST between
states). Rates themselves come from the company's tax-code configuration, which is validated
against `settings.GST_CURRENT_RATES`; confirm both against current GST notifications.
"""
from decimal import ROUND_HALF_UP, Decimal

from apps.core import india

CENT = Decimal("0.01")
# Union territories without a legislature levy UTGST in place of SGST.
UTGST_CODES = {"04", "26", "31", "35", "38"}


def _money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def place_of_supply_state(code):
    if code not in india.GST_STATES:
        raise ValueError(f"Unknown place-of-supply state code '{code}'.")
    return india.GST_STATES[code]


def supplier_state_code(company):
    """GST state of the company: the GSTIN's state, else the registered office's, else '' when neither is recorded."""
    if company.gstin:
        return company.gstin[:2]
    office = company.addresses.filter(address_type="registered_office", is_active=True).first()
    return office.state_code if office else ""


def calculate_gst(taxable_value, rate, *, supplier_state_code, place_of_supply_code, treatment="regular"):
    """Split GST on `taxable_value` at `rate` percent.

    Returns a dict with cgst/sgst(or utgst)/igst amounts, total tax and grand total.
    Only regular taxpayers charge tax; composition and unregistered suppliers get zero tax.
    """
    taxable = _money(taxable_value)
    rate = Decimal(str(rate))
    if taxable < 0 or not Decimal("0") <= rate <= Decimal("100"):
        raise ValueError("Taxable value and rate must be non-negative and the rate at most 100.")
    place_of_supply_state(place_of_supply_code)
    place_of_supply_state(supplier_state_code)

    result = {
        "taxable_value": taxable, "rate": rate, "cgst": Decimal("0.00"), "sgst": Decimal("0.00"),
        "utgst": Decimal("0.00"), "igst": Decimal("0.00"), "supply_type": "", "tax_charged": False,
    }
    if treatment != "regular":
        result["supply_type"] = "no_tax"
        result["total_tax"] = Decimal("0.00")
        result["grand_total"] = taxable
        return result

    result["tax_charged"] = True
    intra = supplier_state_code == place_of_supply_code and place_of_supply_code != "97"
    if intra:
        half = _money(taxable * rate / Decimal(200))
        result["cgst"] = half
        key = "utgst" if supplier_state_code in UTGST_CODES else "sgst"
        result[key] = half
        result["supply_type"] = "intra_state"
    else:
        result["igst"] = _money(taxable * rate / Decimal(100))
        result["supply_type"] = "inter_state"
    result["total_tax"] = result["cgst"] + result["sgst"] + result["utgst"] + result["igst"]
    result["grand_total"] = taxable + result["total_tax"]
    return result
