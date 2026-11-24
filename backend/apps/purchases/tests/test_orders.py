import datetime
from decimal import Decimal
from unittest import mock

import fitz

from apps.accounts.models import Role
from apps.company.models import CompanyProfileAuditEvent, IssuedDocument, IssuedReferenceNumber
from apps.company.tests.base import client_for, make_user

from .base import ITEMS, P, PurchasesBase

TODAY = datetime.date.today()


def iso(d):
    return d.isoformat()


class PricingTests(PurchasesBase):
    def test_intra_state_purchase_splits_cgst_and_sgst(self):
        o = self.order(self.local)
        t = o["record"]["totals"]
        self.assertEqual((t["gross"], t["discount"], t["taxable"]), ("10000.00", "1000.00", "9000.00"))
        self.assertEqual((t["cgst"], t["sgst"], t["igst"], t["tax_total"], t["grand_total"]),
                         ("810.00", "810.00", "0.00", "1620.00", "10620.00"))
        self.assertEqual(o["record"]["supply_type"], "intra_state")
        self.assertEqual([r["label"] for r in t["tax"]], ["CGST @ 9%", "SGST @ 9%"])
        self.assertEqual(o["record"]["amount_in_words"], "INR Ten Thousand Six Hundred Twenty Only")
        self.assertEqual((o["taxable_total"], o["tax_total"], o["grand_total"]), ("9000.00", "1620.00", "10620.00"))

    def test_inter_state_purchase_charges_igst(self):
        o = self.order(self.far)
        t = o["record"]["totals"]
        self.assertEqual((t["cgst"], t["sgst"], t["igst"], t["grand_total"]), ("0.00", "0.00", "1620.00", "10620.00"))
        self.assertEqual(o["record"]["supply_type"], "inter_state")
        self.assertEqual([r["label"] for r in t["tax"]], ["IGST @ 18%"])

    def test_mixed_rates_and_rounding_are_worked_out_per_line(self):
        items = [{"description": "A", "quantity": "3", "rate": "333.33", "gst_rate": "5"},       # 999.99 -> 5% = 50.00 (25.00 + 25.00)
                 {"description": "B", "quantity": "1", "rate": "100.01", "gst_rate": "18"},     # 100.01 -> 9% = 9.00 each (9.0009)
                 {"description": "C", "quantity": "2", "rate": "50", "gst_rate": "0"}]          # exempt
        r = self.fin.post(f"{P}/working/", {"vendor": self.local["id"], "items": items})
        t = r.json()["record"]["totals"]
        self.assertEqual((t["taxable"], t["cgst"], t["sgst"], t["tax_total"], t["grand_total"]),
                         ("1200.00", "34.00", "34.00", "68.00", "1268.00"))
        self.assertEqual([x["label"] for x in t["tax"]], ["CGST @ 2.5%", "CGST @ 9%", "SGST @ 2.5%", "SGST @ 9%"])

    def test_unregistered_vendor_charges_no_gst(self):
        u = self.vendor("Unregistered Shop")
        items = [{"description": "Chairs", "quantity": "2", "rate": "500"}]
        t = self.order(u, items=items)["record"]
        self.assertEqual((t["supply_type"], t["totals"]["tax_total"], t["totals"]["grand_total"]), ("none", "0.00", "1000.00"))

    def test_item_validation(self):
        bad = [([], "Add at least one item"), ([{"description": "", "quantity": 1, "rate": 1, "gst_rate": 18}], "description"),
               ([{"description": "x", "quantity": 0, "rate": 1, "gst_rate": 18}], "quantity"),
               ([{"description": "x", "quantity": 1, "rate": -1, "gst_rate": 18}], "rate"),
               ([{"description": "x", "quantity": 1, "rate": 1, "gst_rate": 12}], "currently applicable"),
               ([{"description": "x", "quantity": 1, "rate": 1}], "choose a GST rate"),
               ([{"description": "x", "quantity": 1, "rate": 1, "gst_rate": 18, "hsn_sac": "AB"}], "HSN"),
               ([{"description": "x", "quantity": 1, "rate": 1, "gst_rate": 18, "discount_percent": 101}], "discount"),
               ([{"description": "x", "quantity": "1.2345", "rate": 1, "gst_rate": 18}], "decimal places")]
        for items, text in bad:
            r = self.fin.post(f"{P}/working/", {"vendor": self.local["id"], "items": items})
            self.assertEqual(r.status_code, 400, items)
            self.assertIn(text, str(r.json()["items"]), items)


class OrderTests(PurchasesBase):
    def test_draft_has_no_number_until_issued_and_issue_makes_a_pdf_on_the_letterhead(self):
        o = self.order(self.local, expected_delivery="2026-09-01", reference="QT-77", delivery_address="Plot 5, Hosur")
        self.assertEqual((o["status"], o["number"], o["issued"]), ("draft", "", None))
        r = self.fin.patch(f"{P}/orders/{o['id']}/", {"form": {"items": [{**ITEMS[0], "quantity": "20"}], "terms": "Delivery within 14 days."}})
        self.assertEqual(r.json()["grand_total"], "21240.00")
        o = self.issue_order(o)
        self.assertEqual(o["status"], "issued")
        self.assertRegex(o["number"], r"^T2N/PO/26/1508/00001$")
        self.assertEqual(o["issued"]["reference_number"], o["number"])
        doc = IssuedDocument.objects.get(pk=o["issued"]["id"])
        self.assertEqual((doc.document_type, doc.source_module, doc.source_id), ("purchase_order", "purchases", o["id"]))
        r = self.fin.get(f"{P}/orders/{o['id']}/pdf/")
        self.assertEqual(r.status_code, 200)
        data = b"".join(r.streaming_content)
        self.assertTrue(data.startswith(b"%PDF"))
        text = "".join(page.get_text() for page in fitz.open(stream=data, filetype="pdf"))
        for expected in ("Purchase Order", "Local Supplies", "T2N/PO/26/1508/00001", "21,240.00", "Delivery within 14 days.", "CGST @ 9%"):
            self.assertIn(expected, text)
        plain = self.fin.get(f"{P}/orders/{o['id']}/pdf/", {"variant": "print"})
        self.assertTrue(b"".join(plain.streaming_content).startswith(b"%PDF"))
        # an issued order is frozen
        r = self.fin.patch(f"{P}/orders/{o['id']}/", {"reference": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.fin.delete(f"{P}/orders/{o['id']}/").status_code, 400)
        self.assertEqual(self.fin.post(f"{P}/orders/{o['id']}/issue/").status_code, 400)

    def test_pdf_of_a_draft_is_404(self):
        o = self.order()
        self.assertEqual(self.fin.get(f"{P}/orders/{o['id']}/pdf/").status_code, 404)

    def test_numbers_are_sequential_and_carry_year_and_date(self):
        a, b = self.issue_order(self.order()), self.issue_order(self.order(order_date="2026-09-03"))
        self.assertEqual((a["number"], b["number"]), ("T2N/PO/26/1508/00001", "T2N/PO/26/0309/00002"))

    def test_validation(self):
        r = self.fin.post(f"{P}/orders/", {"order_date": "2026-08-15", "form": {"items": ITEMS}})
        self.assertIn("vendor", r.json())
        self.order(order_date="2026-08-15", expected_delivery="2026-08-01", expect=400)
        r = self.fin.post(f"{P}/orders/", {"vendor": self.local["id"], "order_date": "15-08-2026", "form": {"items": ITEMS}})
        self.assertIn("order_date", r.json())
        r = self.fin.post(f"{P}/orders/", {"vendor": "00000000-0000-0000-0000-000000000000", "order_date": "2026-08-15", "form": {"items": ITEMS}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("vendor", r.json())

    def test_approval_flow_and_rejection_without_the_approvals_module(self):
        with mock.patch("apps.purchases.services.orders._approvals", return_value=None):
            o = self.order()
            self.assertEqual(self.fin.post(f"{P}/orders/{o['id']}/approve/").status_code, 400)  # not waiting for approval
            o = self.fin.post(f"{P}/orders/{o['id']}/submit/").json()
            self.assertEqual(o["status"], "pending_approval")
            self.assertEqual(self.fin.patch(f"{P}/orders/{o['id']}/", {"reference": "x"}).status_code, 400)
            self.assertEqual(self.fin.post(f"{P}/orders/{o['id']}/issue/").status_code, 400)  # must be approved first
            r = self.fin.post(f"{P}/orders/{o['id']}/reject/", {})
            self.assertEqual(r.status_code, 400)
            self.assertIn("change_reason", r.json())
            o = self.fin.post(f"{P}/orders/{o['id']}/reject/", {"change_reason": "Rate too high"}).json()
            self.assertEqual((o["status"], o["decision_note"]), ("draft", "Rejected: Rate too high"))
            self.fin.post(f"{P}/orders/{o['id']}/submit/")
            o = self.fin.post(f"{P}/orders/{o['id']}/approve/", {"note": "OK"}).json()
            self.assertEqual((o["status"], o["decision_note"], o["approved_by"]), ("approved", "OK", "finance"))
            o = self.issue_order(o)
            self.assertEqual(o["status"], "issued")
        reasons = [e.reason for e in CompanyProfileAuditEvent.objects.filter(entity_type="purchase_order", entity_id=o["id"]).order_by("occurred_at")]
        self.assertEqual(reasons, ["", "Sent for approval", "Rejected: Rate too high", "Sent for approval", "OK", "Issued"])

    def test_a_draft_covered_by_an_approval_rule_cannot_be_issued_directly(self):
        from apps.approvals import api as approvals
        o = self.order()
        with mock.patch.object(approvals, "requires_approval", return_value=True):
            d = self.fin.get(f"{P}/orders/{o['id']}/").json()
            self.assertTrue(d["needs_approval"])
            r = self.fin.post(f"{P}/orders/{o['id']}/issue/")
            self.assertEqual(r.status_code, 400)
            self.assertIn("Send it for approval first", str(r.json()))
            self.assertEqual(self.fin.get(f"{P}/orders/{o['id']}/").json()["status"], "draft")
        with mock.patch.object(approvals, "requires_approval", return_value=False):
            self.assertFalse(self.fin.get(f"{P}/orders/{o['id']}/").json()["needs_approval"])
            self.assertEqual(self.issue_order(o)["status"], "issued")  # no rule: the direct flow still works

    def test_with_the_approvals_module_an_order_nobody_needs_to_approve_is_approved_at_once(self):
        o = self.order()
        o = self.fin.post(f"{P}/orders/{o['id']}/submit/").json()
        self.assertEqual(o["status"], "approved")
        self.assertEqual(self.fin.post(f"{P}/orders/{o['id']}/approve/").status_code, 400)
        self.assertEqual(self.fin.post(f"{P}/orders/{o['id']}/reject/", {"change_reason": "no"}).status_code, 400)
        self.assertEqual(self.issue_order(o)["status"], "issued")

    def test_hooks_called_by_the_approvals_module_approve_and_reject_the_order(self):
        from apps.purchases import approval_hooks
        from apps.purchases.models import PurchaseOrder
        o = self.order()
        with mock.patch("apps.purchases.services.orders._approvals", return_value=None):
            self.fin.post(f"{P}/orders/{o['id']}/submit/")
        request_obj = mock.MagicMock(source_id=o["id"], requested_by=self.finance)
        request_obj.decisions.select_related.return_value.order_by.return_value.first.return_value = None
        approval_hooks.on_approved(request_obj)
        self.assertEqual(PurchaseOrder.objects.get(pk=o["id"]).status, "approved")
        o2 = self.order()
        with mock.patch("apps.purchases.services.orders._approvals", return_value=None):
            self.fin.post(f"{P}/orders/{o2['id']}/submit/")
        approval_hooks.on_rejected(mock.MagicMock(source_id=o2["id"], **{"decisions.select_related.return_value.order_by.return_value.first.return_value": None}))
        self.assertEqual(PurchaseOrder.objects.get(pk=o2["id"]).status, "draft")
        info = approval_hooks.describe(o["id"])
        self.assertEqual((info["amount"], info["url"]), (Decimal("10620.00"), f"/purchases/orders/{o['id']}"))
        self.assertIsNone(approval_hooks.describe("00000000-0000-0000-0000-000000000000"))

    def test_receiving_goods_moves_the_order_through_partial_to_received(self):
        o = self.issue_order(self.order(items=[{**ITEMS[0]}, {"description": "Cables", "quantity": "4", "rate": "50", "gst_rate": "18"}]))
        r = self.fin.post(f"{P}/orders/{o['id']}/receive/", {"date": "2026-08-20", "lines": [{"line": 0, "quantity": "6"}]})
        self.assertEqual(r.status_code, 200, r.content)
        o = r.json()
        self.assertEqual(o["status"], "partially_received")
        self.assertEqual([l["received"] for l in o["record"]["lines"]], ["6", "0"])
        # more than is still expected
        r = self.fin.post(f"{P}/orders/{o['id']}/receive/", {"date": "2026-08-21", "lines": [{"line": 0, "quantity": "5"}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("lines", r.json())
        for bad in ([], [{"line": 9, "quantity": "1"}], [{"line": 0, "quantity": "0"}], [{"line": "x", "quantity": "1"}],
                    [{"line": 0, "quantity": "NaN"}], [{"line": 0, "quantity": "Infinity"}],
                    [{"line": 0, "quantity": "1"}, {"line": 0, "quantity": "1"}]):
            self.assertEqual(self.fin.post(f"{P}/orders/{o['id']}/receive/", {"date": "2026-08-21", "lines": bad}).status_code, 400, bad)
        self.assertEqual(self.fin.post(f"{P}/orders/{o['id']}/receive/", {"date": "2026-08-01", "lines": [{"line": 0, "quantity": "1"}]}).status_code, 400)
        r = self.fin.post(f"{P}/orders/{o['id']}/receive/", {"date": "2026-08-22", "lines": [{"line": 0, "quantity": "4"}, {"line": 1, "quantity": "4"}]})
        self.assertEqual(r.json()["status"], "received")
        self.assertEqual(len(r.json()["receipts"]), 2)
        self.assertEqual(self.fin.post(f"{P}/orders/{o['id']}/receive/", {"date": "2026-08-23", "lines": [{"line": 0, "quantity": "1"}]}).status_code, 400)
        self.assertEqual(self.fin.get(f"{P}/orders/", {"status": "received"}).json()["count"], 1)

    def test_close_needs_a_reason_unless_fully_received(self):
        o = self.issue_order(self.order())
        r = self.fin.post(f"{P}/orders/{o['id']}/close/", {})
        self.assertIn("change_reason", r.json())
        self.assertEqual(self.fin.post(f"{P}/orders/{o['id']}/close/", {"change_reason": "Vendor stopped supplying"}).json()["status"], "closed")
        o2 = self.issue_order(self.order())
        self.fin.post(f"{P}/orders/{o2['id']}/receive/", {"date": "2026-08-20", "lines": [{"line": 0, "quantity": "10"}]})
        self.assertEqual(self.fin.post(f"{P}/orders/{o2['id']}/close/", {}).json()["status"], "closed")

    def test_cancel_voids_the_number_and_is_refused_after_receipt_or_billing(self):
        o = self.issue_order(self.order())
        self.assertEqual(self.fin.post(f"{P}/orders/{o['id']}/cancel/", {}).status_code, 400)
        r = self.fin.post(f"{P}/orders/{o['id']}/cancel/", {"change_reason": "Ordered twice"})
        self.assertEqual(r.json()["status"], "cancelled")
        self.assertEqual(IssuedReferenceNumber.objects.get(reference_number=o["number"]).status, "void")
        self.assertEqual(IssuedDocument.objects.get(pk=o["issued"]["id"]).status, "void")
        o2 = self.issue_order(self.order())
        self.fin.post(f"{P}/orders/{o2['id']}/receive/", {"date": "2026-08-20", "lines": [{"line": 0, "quantity": "1"}]})
        self.assertEqual(self.fin.post(f"{P}/orders/{o2['id']}/cancel/", {"change_reason": "x"}).status_code, 400)
        o3 = self.issue_order(self.order())
        self.fin.post(f"{P}/orders/{o3['id']}/bill/")
        self.assertEqual(self.fin.post(f"{P}/orders/{o3['id']}/cancel/", {"change_reason": "x"}).status_code, 400)

    def test_delete_only_a_draft(self):
        o = self.order()
        self.assertEqual(self.fin.delete(f"{P}/orders/{o['id']}/").status_code, 204)
        self.assertEqual(self.fin.get(f"{P}/orders/{o['id']}/").status_code, 404)
        self.assertTrue(CompanyProfileAuditEvent.objects.filter(entity_type="purchase_order", entity_id=o["id"], action="deleted").exists())

    def test_convert_to_bill_bills_what_was_received_and_never_more_than_ordered(self):
        o = self.issue_order(self.order())
        self.assertEqual(self.fin.post(f"{P}/orders/{o['id']}/receive/", {"date": "2026-08-20", "lines": [{"line": 0, "quantity": "4"}]}).status_code, 200)
        b = self.fin.post(f"{P}/orders/{o['id']}/bill/")
        self.assertEqual(b.status_code, 201, b.content)
        b = b.json()
        # 4 x 1,000 less 10% = 3,600.00 taxable; 18% = 648.00; total 4,248.00
        self.assertEqual((b["status"], b["taxable_total"], b["tax_total"], b["grand_total"]), ("draft", "3600.00", "648.00", "4248.00"))
        self.assertEqual(b["order"]["number"], o["number"])
        self.assertEqual(self.fin.post(f"{P}/orders/{o['id']}/bill/").status_code, 400)  # nothing more received
        self.fin.post(f"{P}/orders/{o['id']}/receive/", {"date": "2026-08-22", "lines": [{"line": 0, "quantity": "6"}]})
        b2 = self.fin.post(f"{P}/orders/{o['id']}/bill/").json()
        self.assertEqual(b2["grand_total"], "6372.00")
        self.assertEqual(self.fin.post(f"{P}/orders/{o['id']}/bill/").status_code, 400)  # fully billed
        detail = self.fin.get(f"{P}/orders/{o['id']}/").json()
        self.assertEqual([x["billed"] for x in detail["record"]["lines"]], ["10"])
        self.assertEqual(len(detail["bills"]), 2)
        # a bill that names too much of an order line is refused
        r = self.fin.post(f"{P}/bills/", {"vendor": self.local["id"], "vendor_bill_no": "X-1", "bill_date": "2026-08-25", "order": o["id"],
                                          "items": [{**ITEMS[0], "quantity": "1", "po_line": 0}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("more than was ordered", str(r.json()))

    def test_a_draft_order_cannot_be_billed(self):
        o = self.order()
        self.assertEqual(self.fin.post(f"{P}/orders/{o['id']}/bill/").status_code, 400)

    def test_list_filters_and_totals(self):
        a = self.issue_order(self.order(self.local))
        self.order(self.far)
        d = self.fin.get(f"{P}/orders/").json()
        self.assertEqual((d["count"], d["total_amount"]), (2, "21240.00"))
        self.assertEqual(d["status_counts"], {"issued": 1, "draft": 1})
        self.assertEqual(self.fin.get(f"{P}/orders/", {"vendor": self.far["id"]}).json()["count"], 1)
        self.assertEqual(self.fin.get(f"{P}/orders/", {"status": "open"}).json()["count"], 1)
        self.assertEqual(self.fin.get(f"{P}/orders/", {"q": a["number"]}).json()["count"], 1)
        self.assertEqual(self.fin.get(f"{P}/orders/", {"from": "2026-09-01"}).json()["count"], 0)
        self.assertEqual(self.fin.get(f"{P}/orders/", {"from": "bad"}).status_code, 400)


class RequestAndQuotationTests(PurchasesBase):
    def request(self, **over):
        payload = {"title": "Laptops for new hires", "request_date": iso(TODAY), "needed_by": iso(TODAY + datetime.timedelta(days=20)),
                   "items": [{"description": "Laptop", "quantity": "5", "est_rate": "50000", "unit": "nos"}], **over}
        r = self.fin.post(f"{P}/requests/", payload)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def test_request_number_totals_and_flow(self):
        r = self.request()
        self.assertRegex(r["number"], r"^T2N/PR/\d{2}/\d{4}/00001$")
        self.assertEqual((r["status"], r["estimated_total"]), ("draft", "250000.00"))
        self.assertEqual(self.fin.post(f"{P}/requests/{r['id']}/approve/").status_code, 400)  # not submitted yet
        self.assertEqual(self.fin.post(f"{P}/requests/{r['id']}/submit/").json()["status"], "submitted")
        self.assertEqual(self.fin.patch(f"{P}/requests/{r['id']}/", {"title": "x", "request_date": iso(TODAY), "items": r["items"]}).status_code, 400)
        rej = self.fin.post(f"{P}/requests/{r['id']}/reject/", {})
        self.assertIn("change_reason", rej.json())
        r2 = self.fin.post(f"{P}/requests/{r['id']}/reject/", {"change_reason": "Budget"}).json()
        self.assertEqual((r2["status"], r2["decision_note"]), ("rejected", "Budget"))
        e = self.fin.patch(f"{P}/requests/{r['id']}/", {"title": "Laptops (revised)", "request_date": iso(TODAY), "items": r["items"]}).json()
        self.assertEqual(e["status"], "draft")

    def test_approved_request_becomes_a_draft_purchase_order(self):
        r = self.request()
        self.fin.post(f"{P}/requests/{r['id']}/submit/")
        self.fin.post(f"{P}/requests/{r['id']}/approve/")
        res = self.fin.post(f"{P}/requests/{r['id']}/convert/", {})
        self.assertEqual(res.status_code, 400)  # needs a vendor
        res = self.fin.post(f"{P}/requests/{r['id']}/convert/", {"vendor": self.local["id"]})
        self.assertEqual(res.status_code, 201, res.content)
        order = res.json()["order"]
        self.assertEqual((order["status"], order["request"]["number"], order["record"]["lines"][0]["quantity"]), ("draft", r["number"], "5"))
        self.assertEqual(order["taxable_total"], "250000.00")
        self.assertEqual(res.json()["request"]["status"], "converted")
        self.assertEqual(self.fin.post(f"{P}/requests/{r['id']}/convert/", {"vendor": self.local["id"]}).status_code, 400)
        self.assertEqual(self.fin.delete(f"{P}/requests/{r['id']}/").status_code, 400)

    def test_request_validation_and_delete(self):
        for bad in ({"title": ""}, {"items": []}, {"items": [{"description": "x", "quantity": "0"}]},
                    {"needed_by": "2000-01-01"}, {"request_date": "nope"}):
            payload = {"title": "T", "request_date": iso(TODAY), "items": [{"description": "x", "quantity": "1"}], **bad}
            self.assertEqual(self.fin.post(f"{P}/requests/", payload).status_code, 400, bad)
        r = self.request()
        self.assertEqual(self.fin.delete(f"{P}/requests/{r['id']}/").status_code, 204)

    def quote(self, vendor, req=None, qty="5", rate="48000", expect=201, **over):
        payload = {"vendor": vendor["id"], "quote_date": iso(TODAY - datetime.timedelta(days=2)),
                   "valid_until": iso(TODAY + datetime.timedelta(days=10)), "vendor_ref": f"Q-{vendor['code']}",
                   "items": [{"description": "Laptop", "quantity": qty, "rate": rate, "gst_rate": "18"}], "delivery_days": 7, **over}
        if req:
            payload["request"] = req["id"]
        r = self.fin.post(f"{P}/quotations/", payload)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def test_quotations_are_numbered_compared_accepted_and_converted(self):
        req = self.request()
        q1 = self.quote(self.local, req, rate="48000")   # 240,000 + 18% = 283,200
        q2 = self.quote(self.far, req, rate="46000")     # 230,000 + 18% = 271,400
        self.assertRegex(q1["number"], r"^T2N/VQ/\d{2}/\d{4}/00001$")
        self.assertEqual((q1["grand_total"], q2["grand_total"]), ("283200.00", "271400.00"))
        cmp = self.fin.get(f"{P}/quotations/compare/", {"request": req["id"]}).json()
        self.assertEqual([q["vendor"]["name"] for q in cmp["quotations"]], ["Far Away Traders", "Local Supplies"])
        self.assertEqual([q["is_lowest"] for q in cmp["quotations"]], [True, False])
        self.assertEqual((cmp["quotations"][1]["difference"], cmp["lowest_total"]), ("11800.00", "271400.00"))
        self.assertEqual(self.fin.get(f"{P}/quotations/compare/").status_code, 400)
        res = self.fin.post(f"{P}/quotations/{q2['id']}/convert/")  # accepts it first
        self.assertEqual(res.status_code, 201, res.content)
        order = res.json()["order"]
        self.assertEqual((res.json()["quotation"]["status"], order["grand_total"], order["quotation"]["number"]),
                         ("accepted", "271400.00", q2["number"]))
        self.assertEqual(order["record"]["supply_type"], "inter_state")
        self.assertEqual(self.fin.post(f"{P}/quotations/{q2['id']}/convert/").status_code, 400)
        self.assertEqual(self.fin.get(f"{P}/requests/{req['id']}/").json()["status"], "converted")

    def test_rejection_needs_a_reason_and_expired_quotations_cannot_be_accepted(self):
        q = self.quote(self.local)
        self.assertIn("change_reason", self.fin.post(f"{P}/quotations/{q['id']}/reject/", {}).json())
        self.assertEqual(self.fin.post(f"{P}/quotations/{q['id']}/reject/", {"change_reason": "Too dear"}).json()["status"], "rejected")
        self.assertEqual(self.fin.post(f"{P}/quotations/{q['id']}/accept/").status_code, 400)
        old = self.quote(self.far, quote_date=iso(TODAY - datetime.timedelta(days=30)), valid_until=iso(TODAY - datetime.timedelta(days=1)))
        self.assertEqual(old["status_key"], "expired")
        self.assertEqual(self.fin.post(f"{P}/quotations/{old['id']}/accept/").status_code, 400)
        self.assertEqual(self.fin.get(f"{P}/quotations/", {"status": "expired"}).json()["count"], 1)

    def test_quotation_validation_edit_and_delete(self):
        self.quote(self.local, valid_until="2000-01-01", expect=400)
        self.quote(self.local, delivery_days="abc", expect=400)
        q = self.quote(self.local)
        e = self.fin.patch(f"{P}/quotations/{q['id']}/", {"quote_date": q["quote_date"], "items": [{"description": "Laptop", "quantity": "1", "rate": "100", "gst_rate": "18"}]})
        self.assertEqual(e.json()["grand_total"], "118.00")
        self.assertEqual(self.fin.delete(f"{P}/quotations/{q['id']}/").status_code, 204)
        self.assertEqual(self.fin.get(f"{P}/quotations/{q['id']}/").status_code, 404)
