"""CSV downloads of the registers, laid out like the GST offline tool's GSTR-1 templates so they can be copied across."""
import csv
import io

from django.http import HttpResponse

from .services.common import money


def _d(d):
    return d.strftime("%d-%b-%y") if d else ""


def _n(v):
    return f"{money(v):.2f}"


def _rate(v):
    return f"{v.normalize():f}" if v is not None else ""


def csv_response(filename, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    w.writerows(rows)
    resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def _per_rate(rows, build):
    out = []
    for r in rows:
        for rt in r["rates"] or [{"rate": None, "taxable": 0, "cess": 0}]:
            out.append(build(r, rt))
    return out


SECTIONS = ("b2b", "b2cl", "b2cs", "cdnr", "cdnur", "exp", "hsn")


def sales_csv(reg, section):
    """(filename, header, rows) for one GSTR-1 table of a sales register."""
    tag = f"{reg['from']:%Y%m%d}-{reg['to']:%Y%m%d}"
    if section == "b2b":
        header = ["GSTIN/UIN of Recipient", "Receiver Name", "Invoice Number", "Invoice date", "Invoice Value", "Place Of Supply", "Reverse Charge",
                  "Applicable % of Tax Rate", "Invoice Type", "E-Commerce GSTIN", "Rate", "Taxable Value", "Cess Amount"]
        rows = _per_rate(reg["b2b"], lambda r, t: [r["gstin"], r["customer"], r["number"], _d(r["date"]), _n(r["invoice_value"]), r["place_of_supply"],
                                                   "N", "", "Regular B2B", "", _rate(t["rate"]), _n(t["taxable"]), _n(t["cess"])])
    elif section == "b2cl":
        header = ["Invoice Number", "Invoice date", "Invoice Value", "Place Of Supply", "Applicable % of Tax Rate", "Rate", "Taxable Value",
                  "Cess Amount", "E-Commerce GSTIN"]
        rows = _per_rate(reg["b2cl"], lambda r, t: [r["number"], _d(r["date"]), _n(r["invoice_value"]), r["place_of_supply"], "", _rate(t["rate"]),
                                                    _n(t["taxable"]), _n(t["cess"]), ""])
    elif section == "b2cs":
        header = ["Type", "Place Of Supply", "Applicable % of Tax Rate", "Rate", "Taxable Value", "Cess Amount", "E-Commerce GSTIN"]
        rows = [["OE", r["place_of_supply"], "", _rate(r["rate"]), _n(r["taxable"]), _n(r["cess"]), ""] for r in reg["b2cs"]]
    elif section == "cdnr":
        header = ["GSTIN/UIN of Recipient", "Receiver Name", "Note Number", "Note Date", "Note Type", "Place Of Supply", "Reverse Charge",
                  "Note Supply Type", "Note Value", "Applicable % of Tax Rate", "Rate", "Taxable Value", "Cess Amount"]
        rows = _per_rate(reg["cdnr"], lambda r, t: [r["gstin"], r["customer"], r["number"], _d(r["date"]), r["note_type"], r["place_of_supply"], "N",
                                                    "Regular B2B", _n(r["invoice_value"]), "", _rate(t["rate"]), _n(t["taxable"]), _n(t["cess"])])
    elif section == "cdnur":
        header = ["UR Type", "Note Number", "Note Date", "Note Type", "Place Of Supply", "Note Value", "Applicable % of Tax Rate", "Rate",
                  "Taxable Value", "Cess Amount"]
        rows = _per_rate(reg["cdnur"], lambda r, t: ["B2CL", r["number"], _d(r["date"]), r["note_type"], r["place_of_supply"], _n(r["invoice_value"]), "",
                                                     _rate(t["rate"]), _n(t["taxable"]), _n(t["cess"])])
    elif section == "exp":
        header = ["Export Type", "Invoice Number", "Invoice date", "Invoice Value", "Port Code", "Shipping Bill Number", "Shipping Bill Date",
                  "Applicable % of Tax Rate", "Rate", "Taxable Value", "Cess Amount"]
        rows = _per_rate(reg["exports"], lambda r, t: ["WOPAY", r["number"], _d(r["date"]), _n(r["invoice_value"]), "", "", "", "", _rate(t["rate"]),
                                                       _n(t["taxable"]), _n(t["cess"])])
    elif section == "hsn":
        header = ["HSN", "Description", "UQC", "Total Quantity", "Total Value", "Taxable Value", "Integrated Tax Amount", "Central Tax Amount",
                  "State/UT Tax Amount", "Cess Amount", "Rate"]
        rows = [[r["hsn"], r["description"], r["uqc"], _n(r["quantity"]), _n(r["value"]), _n(r["taxable"]), _n(r["igst"]), _n(r["cgst"]),
                 _n(r["sgst"]), _n(r["cess"]), _rate(r["rate"])] for r in reg["hsn"]]
    else:
        return None
    return f"gstr1-{section}-{tag}.csv", header, rows


def purchases_csv(reg):
    header = ["GSTIN of Supplier", "Supplier Name", "Bill Number", "Date", "Journal Reference", "Taxable Value", "Integrated Tax", "Central Tax",
              "State/UT Tax", "Cess", "Total Input Credit", "ITC Eligibility", "Reason"]
    rows = [[r["gstin"], r["supplier"], r["reference"], _d(r["date"]), r["journal_reference"], _n(r["taxable"]), _n(r["igst"]), _n(r["cgst"]),
             _n(r["sgst"]), _n(r["cess"]), _n(r["itc"]), r["eligibility"], r["reason"]] for r in reg["rows"]]
    return f"purchase-register-{reg['from']:%Y%m%d}-{reg['to']:%Y%m%d}.csv", header, rows
