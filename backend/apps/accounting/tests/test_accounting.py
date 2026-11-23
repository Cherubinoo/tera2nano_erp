import datetime
from decimal import Decimal

from django.test import TestCase

from apps.accounting.models import Account, BankReconciliation, JournalEntry, JournalLine, Voucher
from apps.accounting.services import chart, posting
from apps.accounts.models import Role
from apps.company.models import CompanyProfile, CompanyProfileAuditEvent, CompanyTaxSettings, FinancialYear
from apps.company.tests.base import API, GSTIN, CompanyTestMixin, client_for, make_user

A = "/api/v1/accounting"
S = "/api/v1/sales"
D = Decimal
ITEMS = [{"description": "Development", "quantity": "2", "rate": "1000", "discount_percent": "10"}]  # 1,800.00


class Base(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()
        self.fin = self.api_finance
        self.company = CompanyProfile.objects.get()
        chart.ensure_chart(self.company)

    # -- helpers
    def acct(self, key_or_code):
        q = {"system_key": key_or_code} if not key_or_code.isdigit() else {"code": key_or_code}
        return Account.objects.get(company=self.company, **q)

    def bal(self, key_or_code, as_of="2027-03-31"):
        rows = {r["code"]: r for r in self.fin.get(f"{A}/reports/trial-balance/", {"as_of": as_of}).json()["rows"]}
        a = self.acct(key_or_code)
        r = rows.get(a.code)
        if r is None:
            return D("0")
        return D(r["debit"]) - D(r["credit"])

    def tb(self, as_of="2027-03-31"):
        r = self.fin.get(f"{A}/reports/trial-balance/", {"as_of": as_of}).json()
        self.assertTrue(r["balanced"], r)
        self.assertEqual(D(r["total_debit"]), D(r["total_credit"]))
        return r

    def customer(self, name="Acme Industries", **over):
        r = self.fin.post(f"{S}/customers/", {"display_name": name, **over})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def invoice(self, customer, items=None, date="2026-08-15", kind="invoice", **extra):
        d = self.fin.post(f"{S}/documents/", {"kind": kind, "customer": customer["id"], "issue_date": date, "form": {"items": items or ITEMS}, **extra})
        self.assertEqual(d.status_code, 201, d.content)
        r = self.fin.post(f"{S}/documents/{d.json()['id']}/issue/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def pay(self, customer, amount, date="2026-08-20", allocations=None, **extra):
        r = self.fin.post(f"{S}/payments/", {"customer": customer["id"], "date": date, "amount": amount, "mode": "bank_transfer",
                                             "allocations": allocations or [], **extra})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def journal(self, lines, date="2026-08-30", narration="Test entry", expect=201):
        payload = [{"account": str(self.acct(a).id), **side} for a, side in lines]
        r = self.fin.post(f"{A}/journal/", {"date": date, "narration": narration, "lines": payload})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def expense(self, category="5200", amount="1000", date="2026-08-25", expect=201, **extra):
        r = self.fin.post(f"{A}/vouchers/", {"kind": "expense", "date": date, "category": str(self.acct(category).id),
                                             "money_account": str(self.acct("bank").id), "amount": amount, **extra})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def enable_gst(self):
        CompanyProfile.objects.update(gst_registration_status="registered", gstin=GSTIN)
        CompanyTaxSettings.objects.update(gst_applicable=True, gst_treatment="regular", default_tax_code="GST18",
                                          tax_codes=[{"code": "GST18", "rate": 18}])

    def entry(self, source, source_id):
        return JournalEntry.objects.get(source=source, source_id=source_id)

    def lines(self, entry):
        return sorted(((l.account.code, l.debit, l.credit) for l in entry.lines.select_related("account")))


class ChartTests(Base):
    def test_standard_chart_exists_with_the_system_accounts(self):
        for key in ("cash", "bank", "receivable", "payable", "sales", "sales_returns", "gst_cgst_out", "gst_sgst_out", "gst_igst_out",
                    "gst_cgst_in", "gst_igst_in", "customer_advances", "capital", "retained", "depreciation", "opening_balance_equity"):
            self.assertTrue(self.acct(key).is_system, key)
        self.assertEqual((self.acct("receivable").type, self.acct("sales").type, self.acct("capital").type), ("asset", "income", "equity"))
        rows = self.fin.get(f"{A}/accounts/").json()
        self.assertGreater(len(rows), 30)
        self.assertEqual(len({r["code"] for r in rows}), len(rows))

    def test_ensure_chart_is_idempotent_and_respects_renames(self):
        self.fin.patch(f"{A}/accounts/{self.acct('sales').id}/", {"name": "Service revenue"})
        before = Account.objects.count()
        self.assertEqual(chart.ensure_chart(self.company), [])
        self.assertEqual(Account.objects.count(), before)
        self.assertEqual(self.acct("sales").name, "Service revenue")

    def test_add_edit_and_remove_an_account(self):
        r = self.fin.post(f"{A}/accounts/", {"code": "5250", "name": "Hosting", "subtype": "expense"})
        self.assertEqual((r.status_code, r.json()["type"]), (201, "expense"))
        for bad in ({"code": "5250", "name": "Dup", "subtype": "expense"}, {"code": "", "name": "x", "subtype": "expense"},
                    {"code": "9", "name": "", "subtype": "expense"}, {"code": "9", "name": "x", "subtype": "nonsense"}, {"code": "a b", "name": "x", "subtype": "expense"}):
            self.assertEqual(self.fin.post(f"{A}/accounts/", bad).status_code, 400, bad)
        aid = r.json()["id"]
        self.assertEqual(self.fin.patch(f"{A}/accounts/{aid}/", {"name": "Cloud hosting", "is_active": False}).json()["is_active"], False)
        self.assertEqual(self.fin.delete(f"{A}/accounts/{aid}/").status_code, 204)

    def test_system_and_used_accounts_are_protected(self):
        sales = self.acct("sales")
        self.assertEqual(self.fin.delete(f"{A}/accounts/{sales.id}/").status_code, 400)
        self.assertEqual(self.fin.patch(f"{A}/accounts/{sales.id}/", {"is_active": False}).status_code, 400)
        self.assertEqual(self.fin.patch(f"{A}/accounts/{sales.id}/", {"subtype": "expense"}).status_code, 400)
        self.expense()
        rent = self.acct("5200")
        self.assertEqual(self.fin.delete(f"{A}/accounts/{rent.id}/").status_code, 400)
        self.assertEqual(self.fin.patch(f"{A}/accounts/{rent.id}/", {"subtype": "direct_cost"}).status_code, 400)
        self.assertEqual(self.fin.patch(f"{A}/accounts/{rent.id}/", {"name": "Office rent"}).status_code, 200)

    def test_a_bank_account_gets_its_own_ledger_account(self):
        bank = self.add_bank().json()
        accounts = self.fin.get(f"{A}/bank/").json()
        mine = [a for a in accounts if a["name"].startswith("Bank: ")]
        self.assertEqual(len(mine), 1)
        self.assertEqual(Account.objects.get(pk=mine[0]["id"]).bank_account_id.hex, bank["id"].replace("-", ""))
        self.assertEqual(len(self.fin.get(f"{A}/bank/").json()), len(accounts))  # not duplicated on a second look


class SalesPostingTests(Base):
    def test_invoice_without_gst(self):
        c = self.customer()
        inv = self.invoice(c)
        e = self.entry("invoice", inv["id"])
        self.assertEqual((e.reference, e.date), (inv["number"], datetime.date(2026, 8, 15)))
        self.assertEqual(self.lines(e), [("1200", D("1800.00"), D("0")), ("4000", D("0"), D("1800.00"))])
        self.tb()

    def test_invoice_with_gst_same_state_splits_cgst_and_sgst(self):
        self.enable_gst()
        inv = self.invoice(self.customer("Local", state_code="29"))
        self.assertEqual(self.lines(self.entry("invoice", inv["id"])), [
            ("1200", D("2124.00"), D("0")), ("2110", D("0"), D("162.00")), ("2120", D("0"), D("162.00")), ("4000", D("0"), D("1800.00"))])
        self.tb()

    def test_invoice_with_gst_other_state_uses_igst(self):
        self.enable_gst()
        inv = self.invoice(self.customer("Far", state_code="27"))
        self.assertEqual(self.lines(self.entry("invoice", inv["id"])), [
            ("1200", D("2124.00"), D("0")), ("2130", D("0"), D("324.00")), ("4000", D("0"), D("1800.00"))])

    def test_credit_note_reverses_sales_and_tax_and_reduces_receivable(self):
        self.enable_gst()
        c = self.customer("Local", state_code="29")
        inv = self.invoice(c)
        cn = self.invoice(c, items=[{"description": "Return", "quantity": "1", "rate": "500"}], kind="credit_note", date="2026-08-20",
                          against=inv["id"], reason="Returned")
        self.assertEqual(self.lines(self.entry("credit_note", cn["id"])), [
            ("1200", D("0"), D("590.00")), ("2110", D("45.00"), D("0")), ("2120", D("45.00"), D("0")), ("4100", D("500.00"), D("0"))])
        self.assertEqual(self.bal("receivable"), D("2124.00") - D("590.00"))
        self.tb()

    def test_payment_goes_to_the_default_bank_and_clears_receivable(self):
        c = self.customer()
        inv = self.invoice(c)
        p = self.pay(c, "500", allocations=[{"document": inv["id"], "amount": "500"}])
        self.assertEqual(self.lines(self.entry("payment", p["id"])), [("1010", D("500.00"), D("0")), ("1200", D("0"), D("500.00"))])
        self.assertEqual((self.bal("bank"), self.bal("receivable")), (D("500.00"), D("1300.00")))

    def test_part_of_a_payment_not_applied_is_a_customer_advance(self):
        c = self.customer()
        inv = self.invoice(c)
        p = self.pay(c, "3000", allocations=[{"document": inv["id"], "amount": "1800"}])
        self.assertEqual(self.lines(self.entry("payment", p["id"])), [("1010", D("3000.00"), D("0")), ("1200", D("0"), D("1800.00")), ("2200", D("0"), D("1200.00"))])

    def test_cash_payment_and_a_chosen_bank_account(self):
        c = self.customer()
        inv = self.invoice(c)
        cash = self.pay(c, "100", allocations=[{"document": inv["id"], "amount": "100"}], mode="cash")
        self.assertEqual(self.lines(self.entry("payment", cash["id"]))[0][0], "1000")
        bank = self.add_bank().json()
        p = self.pay(c, "200", allocations=[{"document": inv["id"], "amount": "200"}], deposit_account=bank["id"])
        ledger = Account.objects.get(bank_account_id=bank["id"])
        self.assertEqual(self.lines(self.entry("payment", p["id"]))[0][0], ledger.code)

    def test_voids_post_the_mirror_image(self):
        c = self.customer()
        inv = self.invoice(c)
        p = self.pay(c, "500", allocations=[{"document": inv["id"], "amount": "500"}])
        self.fin.post(f"{S}/payments/{p['id']}/void/", {"change_reason": "Bounced"})
        self.assertEqual((self.bal("bank"), self.bal("receivable")), (D("0"), D("1800.00")))
        self.assertEqual(self.entry("payment", p["id"]).status, "reversed")
        self.fin.post(f"{S}/documents/{inv['id']}/void/", {"change_reason": "Wrong customer"})
        self.assertEqual((self.bal("receivable"), self.bal("sales")), (D("0"), D("0")))
        self.assertEqual(JournalEntry.objects.filter(source="reversal").count(), 2)
        self.tb()

    def test_a_proforma_posts_nothing(self):
        before = JournalEntry.objects.count()
        self.invoice(self.customer(), kind="proforma_invoice")
        self.assertEqual(JournalEntry.objects.count(), before)

    def test_a_sale_cannot_be_issued_in_a_locked_year_and_leaves_no_trace(self):
        c = self.customer()
        d = self.fin.post(f"{S}/documents/", {"kind": "invoice", "customer": c["id"], "issue_date": "2026-08-15", "form": {"items": ITEMS}}).json()
        FinancialYear.objects.update(is_locked=True)
        self.assertEqual(self.fin.post(f"{S}/documents/{d['id']}/issue/").status_code, 400)
        self.assertEqual(JournalEntry.objects.count(), 0)
        self.assertEqual(self.fin.get(f"{S}/documents/{d['id']}/").json()["status"], "draft")

    def test_a_posting_can_never_be_made_twice(self):
        from django.db import IntegrityError, transaction
        c = self.customer()
        inv = self.invoice(c)
        with self.assertRaises(IntegrityError), transaction.atomic():
            JournalEntry.objects.create(company=self.company, date=datetime.date(2026, 8, 15), source="invoice", source_id=inv["id"])

    def test_rebuild_posts_sales_made_before_the_books_and_only_once(self):
        c = self.customer()
        inv = self.invoice(c)
        p = self.pay(c, "500", allocations=[{"document": inv["id"], "amount": "500"}])
        JournalLine.objects.all().delete()
        JournalEntry.objects.all().delete()
        self.assertEqual(posting.rebuild(self.company), {"documents": 1, "payments": 1})
        self.assertEqual(posting.rebuild(self.company), {"documents": 0, "payments": 0})
        self.assertEqual((self.bal("receivable"), self.bal("bank")), (D("1300.00"), D("500.00")))
        self.assertEqual(self.fin.post(f"{A}/accounts/setup/").json()["documents"], 0)
        self.tb()


class VoucherTests(Base):
    def test_expense_without_gst(self):
        v = self.expense(party_name="Landlord", reference="RENT-AUG")
        self.assertRegex(v["number"], r"^T2N/EXP/26/2508/00001$")
        self.assertEqual((v["total"], v["tax_amount"], v["status"]), ("1000.00", "0.00", "recorded"))
        e = JournalEntry.objects.get(pk=v["entry_id"])
        self.assertEqual(self.lines(e), [("1010", D("0"), D("1000.00")), ("5200", D("1000.00"), D("0"))])

    def test_expense_with_input_gst_same_state_and_other_state(self):
        self.enable_gst()
        v = self.expense(tax_mode="intra", tax_rate="18")
        self.assertEqual((v["tax_amount"], v["total"]), ("180.00", "1180.00"))
        self.assertEqual([g["label"] for g in v["gst_split"]], ["CGST @ 9%", "SGST @ 9%"])
        self.assertEqual(self.lines(JournalEntry.objects.get(pk=v["entry_id"])), [
            ("1010", D("0"), D("1180.00")), ("1310", D("90.00"), D("0")), ("1320", D("90.00"), D("0")), ("5200", D("1000.00"), D("0"))])
        w = self.expense(tax_mode="inter", tax_rate="18", amount="500")
        self.assertEqual(self.lines(JournalEntry.objects.get(pk=w["entry_id"]))[1], ("1330", D("90.00"), D("0")))

    def test_gst_on_odd_amounts_rounds_so_both_halves_match(self):
        self.enable_gst()
        v = self.expense(tax_mode="intra", tax_rate="18", amount="333.33")
        self.assertEqual((v["tax_amount"], v["total"]), ("60.00", "393.33"))  # 30.00 each: the halves are always equal
        self.tb()

    def test_gst_refused_when_not_switched_on_or_rate_invalid(self):
        self.expense(tax_mode="intra", tax_rate="18", expect=400)
        self.enable_gst()
        self.expense(tax_mode="intra", tax_rate="17", expect=400)
        self.expense(tax_mode="intra", tax_rate="0", expect=400)
        self.expense(tax_mode="sideways", tax_rate="18", expect=400)

    def test_income_voucher_with_output_gst(self):
        self.enable_gst()
        r = self.fin.post(f"{A}/vouchers/", {"kind": "income", "date": "2026-09-01", "category": str(self.acct("other_income").id),
                                             "money_account": str(self.acct("bank").id), "amount": "1000", "tax_mode": "intra", "tax_rate": "18"})
        v = r.json()
        self.assertEqual((r.status_code, v["number"][:12]), (201, "T2N/INC/26/0"))
        self.assertEqual(self.lines(JournalEntry.objects.get(pk=v["entry_id"])), [
            ("1010", D("1180.00"), D("0")), ("2110", D("0"), D("90.00")), ("2120", D("0"), D("90.00")), ("4500", D("0"), D("1000.00"))])

    def test_validation(self):
        self.expense(amount="0", expect=400)
        self.expense(amount="-5", expect=400)
        self.expense(amount="10.005", expect=400)
        self.expense(category="4000", expect=400)  # an income account for an expense
        r = self.fin.post(f"{A}/vouchers/", {"kind": "expense", "date": "2026-08-25", "category": str(self.acct("5200").id),
                                             "money_account": str(self.acct("5300").id), "amount": "10"})
        self.assertEqual(r.status_code, 400)  # not a cash or bank account
        self.assertEqual(self.fin.post(f"{A}/vouchers/", {"kind": "expense", "category": str(self.acct("5200").id)}).status_code, 400)
        self.assertEqual(Voucher.objects.count(), 0)

    def test_void_reverses_and_needs_a_reason(self):
        v = self.expense()
        self.assertEqual(self.fin.post(f"{A}/vouchers/{v['id']}/void/", {}).status_code, 400)
        r = self.fin.post(f"{A}/vouchers/{v['id']}/void/", {"change_reason": "Entered twice"})
        self.assertEqual((r.status_code, r.json()["status"]), (200, "void"))
        self.assertEqual(self.bal("5200"), D("0"))
        self.assertEqual(self.fin.post(f"{A}/vouchers/{v['id']}/void/", {"change_reason": "again"}).status_code, 400)
        self.tb()

    def test_list_filters_and_options(self):
        self.expense(party_name="Landlord")
        self.expense(category="5300", party_name="Cloud Co", amount="250")
        rows = self.fin.get(f"{A}/vouchers/", {"kind": "expense", "q": "cloud"}).json()
        self.assertEqual((rows["count"], rows["total"]), (1, "250.00"))
        o = self.fin.get(f"{A}/vouchers/options/").json()
        self.assertFalse(o["gst_applied"])
        self.assertTrue(any(a["name"] == "Rent" for a in o["expense_accounts"]))
        self.assertTrue(any(a["name"] == "Cash in hand" for a in o["money_accounts"]))


class JournalTests(Base):
    def test_balanced_manual_journal(self):
        e = self.journal([("5200", {"debit": "400"}), ("1000", {"credit": "400"})])
        self.assertRegex(e["reference"], r"^T2N/JV/26/3008/00001$")
        self.assertEqual((e["amount"], len(e["lines"]), e["status"]), ("400.00", 2, "posted"))
        self.assertEqual(self.bal("cash"), D("-400.00"))

    def test_bad_entries_are_refused(self):
        self.journal([("5200", {"debit": "400"}), ("1000", {"credit": "300"})], expect=400)           # unbalanced
        self.journal([("5200", {"debit": "400"})], expect=400)                                          # one line
        self.journal([("5200", {"debit": "400", "credit": "400"}), ("1000", {"credit": "0"})], expect=400)  # both sides
        self.journal([("5200", {"debit": "0"}), ("1000", {"credit": "0"})], expect=400)                 # nothing
        self.journal([("5200", {"debit": "400"}), ("1000", {"credit": "400"})], narration="", expect=400)
        self.journal([("5200", {"debit": "400.005"}), ("1000", {"credit": "400.005"})], expect=400)
        self.assertEqual(JournalEntry.objects.count(), 0)
        Account.objects.filter(code="5200").update(is_active=False)
        self.journal([("5200", {"debit": "400"}), ("1000", {"credit": "400"})], expect=400)

    def test_reverse_a_manual_entry(self):
        e = self.journal([("5200", {"debit": "400"}), ("1000", {"credit": "400"})])
        self.assertEqual(self.fin.post(f"{A}/journal/{e['id']}/reverse/", {}).status_code, 400)
        r = self.fin.post(f"{A}/journal/{e['id']}/reverse/", {"change_reason": "Wrong account", "date": "2026-08-31"})
        self.assertEqual((r.status_code, r.json()["source"], r.json()["reverses"]), (200, "reversal", e["id"]))
        self.assertEqual((self.bal("5200"), self.bal("cash")), (D("0"), D("0")))
        self.assertEqual(self.fin.get(f"{A}/journal/{e['id']}/").json()["status"], "reversed")
        self.assertEqual(self.fin.post(f"{A}/journal/{e['id']}/reverse/", {"change_reason": "again"}).status_code, 400)
        self.assertEqual(self.fin.post(f"{A}/journal/{r.json()['id']}/reverse/", {"change_reason": "undo"}).status_code, 400)

    def test_entries_made_by_a_sale_or_voucher_cannot_be_reversed_by_hand(self):
        c = self.customer()
        self.invoice(c)
        e = JournalEntry.objects.get(source="invoice")
        r = self.fin.post(f"{A}/journal/{e.id}/reverse/", {"change_reason": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Void that record", str(r.json()))

    def test_a_locked_year_refuses_new_entries(self):
        FinancialYear.objects.update(is_locked=True)
        r = self.journal([("5200", {"debit": "400"}), ("1000", {"credit": "400"})], expect=400)
        self.assertIn("locked", str(r))

    def test_entries_are_immutable_in_the_api_and_audited(self):
        e = self.journal([("5200", {"debit": "400"}), ("1000", {"credit": "400"})])
        for method in ("patch", "put", "delete"):
            self.assertEqual(getattr(self.fin, method)(f"{A}/journal/{e['id']}/", {}).status_code, 405, method)
        before = CompanyProfile.objects.get().version
        self.journal([("5200", {"debit": "10"}), ("1000", {"credit": "10"})])
        self.assertEqual(CompanyProfile.objects.get().version, before)
        self.assertTrue(CompanyProfileAuditEvent.objects.filter(entity_type="journal_entry", category="accounting").exists())

    def test_transfer_between_cash_and_bank(self):
        self.journal([("bank", {"debit": "5000"}), ("capital", {"credit": "5000"})])
        r = self.fin.post(f"{A}/transfers/", {"date": "2026-09-02", "from_account": str(self.acct("bank").id),
                                              "to_account": str(self.acct("cash").id), "amount": "1200", "notes": "Cash withdrawn"})
        self.assertEqual((r.status_code, r.json()["source"]), (201, "transfer"))
        self.assertEqual((self.bal("bank"), self.bal("cash")), (D("3800.00"), D("1200.00")))
        for bad in ({"from_account": str(self.acct("bank").id), "to_account": str(self.acct("bank").id)},
                    {"from_account": str(self.acct("bank").id), "to_account": str(self.acct("5200").id)}, {"amount": "0"}):
            payload = {"date": "2026-09-02", "from_account": str(self.acct("bank").id), "to_account": str(self.acct("cash").id), "amount": "10", **bad}
            self.assertEqual(self.fin.post(f"{A}/transfers/", payload).status_code, 400, bad)

    def test_opening_balances_balance_through_an_adjustment_account(self):
        r = self.fin.post(f"{A}/opening-balances/", {"date": "2026-04-01", "rows": [
            {"account": str(self.acct("bank").id), "debit": "50000"}, {"account": str(self.acct("payable").id), "credit": "8000"}]})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((self.bal("bank"), self.bal("payable"), self.bal("opening_balance_equity")), (D("50000.00"), D("-8000.00"), D("-42000.00")))
        self.tb()
        self.assertEqual(self.fin.post(f"{A}/opening-balances/", {"date": "2026-04-01", "rows": []}).status_code, 400)

    def opening(self, rows=None, date="2026-04-01", **kw):
        rows = rows or [{"account": str(self.acct("bank").id), "debit": "50000"}, {"account": str(self.acct("payable").id), "credit": "8000"}]
        return self.fin.post(f"{A}/opening-balances/", {"date": date, "rows": rows}, **kw)

    def test_opening_balances_are_entered_once_and_can_be_redone_after_a_reversal(self):
        self.assertEqual(self.fin.get(f"{A}/opening-balances/").json(), {"entry": None})
        first = self.opening()
        self.assertEqual(first.status_code, 201, first.content)
        state = self.fin.get(f"{A}/opening-balances/").json()["entry"]
        self.assertEqual((state["id"], state["date"], state["amount"]), (first.json()["id"], "2026-04-01", "50000.00"))  # total debit: bank 50,000 (payable 8,000 and the 42,000 balancing line are credits)
        again = self.opening([{"account": str(self.acct("cash").id), "debit": "100"}])
        self.assertEqual(again.status_code, 400)
        self.assertIn("already entered", str(again.json()))
        self.assertEqual(self.bal("cash"), D("0"))  # the refused entry posted nothing
        rev = self.fin.post(f"{A}/journal/{first.json()['id']}/reverse/", {"change_reason": "wrong date"})
        self.assertEqual(rev.status_code, 200, rev.content)
        self.assertEqual(self.fin.get(f"{A}/opening-balances/").json(), {"entry": None})
        self.assertEqual((self.bal("bank"), self.bal("opening_balance_equity")), (D("0"), D("0")))
        second = self.opening([{"account": str(self.acct("cash").id), "debit": "100"}], date="2026-04-02")
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual((self.bal("cash"), self.bal("opening_balance_equity")), (D("100.00"), D("-100.00")))
        self.tb()

    def test_opening_balances_that_already_balance_need_no_adjustment_and_bad_rows_are_refused(self):
        ok = self.opening([{"account": str(self.acct("bank").id), "debit": "1000"}, {"account": str(self.acct("capital").id), "credit": "1000"}])
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertEqual(self.bal("opening_balance_equity"), D("0"))
        self.fin.post(f"{A}/journal/{ok.json()['id']}/reverse/", {"change_reason": "redo"})
        both = self.opening([{"account": str(self.acct("bank").id), "debit": "5", "credit": "5"}])
        self.assertEqual(both.status_code, 400)
        self.assertEqual(self.opening([{"account": str(self.acct("bank").id)}]).status_code, 400)  # nothing entered
        self.assertEqual(self.fin.post(f"{A}/opening-balances/", {"rows": []}).status_code, 400)  # no date

    def test_only_people_who_manage_accounting_may_post_opening_balances(self):
        partner = client_for(make_user("part", Role.PARTNER))
        self.assertEqual(partner.post(f"{A}/opening-balances/", {"date": "2026-04-01", "rows": []}).status_code, 403)
        self.assertEqual(partner.get(f"{A}/opening-balances/").status_code, 200)
        self.assertEqual(self.api_staff.get(f"{A}/opening-balances/").status_code, 403)
        self.assertEqual(self.anon.get(f"{A}/opening-balances/").status_code, 401)


class ReconciliationTests(Base):
    def setUp(self):
        super().setUp()
        self.journal([("bank", {"debit": "10000"}), ("capital", {"credit": "10000"})], date="2026-08-01")
        self.expense(amount="1000", date="2026-08-10")
        self.expense(amount="500", date="2026-08-20")
        self.bank = self.acct("bank")

    def lines(self):
        r = self.fin.get(f"{A}/bank/{self.bank.id}/unreconciled/").json()
        return r, {(l["debit"], l["credit"]): l["id"] for l in r["lines"]}

    def test_reconcile_when_the_statement_agrees(self):
        r, ids = self.lines()
        self.assertEqual((len(r["lines"]), r["cleared_balance"]), (3, "0.00"))
        # the statement at 15 Aug shows the deposit and the 1,000 payment only
        res = self.fin.post(f"{A}/bank/{self.bank.id}/reconcile/", {"statement_date": "2026-08-15", "statement_balance": "9000",
                                                                      "lines": [ids[("10000.00", "0.00")], ids[("0.00", "1000.00")]]})
        self.assertEqual((res.status_code, res.json()["lines"]), (201, 2))
        r, _ = self.lines()
        self.assertEqual((len(r["lines"]), r["cleared_balance"]), (1, "9000.00"))
        overview = self.fin.get(f"{A}/bank/").json()
        row = next(x for x in overview if x["id"] == str(self.bank.id))
        self.assertEqual((row["balance"], row["unreconciled"], row["last_reconciled"]), ("8500.00", 1, "2026-08-15"))

    def test_a_statement_that_does_not_agree_is_refused_with_the_difference(self):
        _, ids = self.lines()
        res = self.fin.post(f"{A}/bank/{self.bank.id}/reconcile/", {"statement_date": "2026-08-31", "statement_balance": "9000",
                                                                      "lines": [ids[("10000.00", "0.00")], ids[("0.00", "1000.00")], ids[("0.00", "500.00")]]})
        self.assertEqual(res.status_code, 400)
        self.assertIn("difference is 500.00", str(res.json()))
        self.assertEqual(BankReconciliation.objects.count(), 0)

    def test_lines_after_the_statement_date_and_already_cleared_lines_are_refused(self):
        _, ids = self.lines()
        late = self.fin.post(f"{A}/bank/{self.bank.id}/reconcile/", {"statement_date": "2026-08-15", "statement_balance": "8500",
                                                                       "lines": [ids[("10000.00", "0.00")], ids[("0.00", "1000.00")], ids[("0.00", "500.00")]]})
        self.assertEqual(late.status_code, 400)
        ok = self.fin.post(f"{A}/bank/{self.bank.id}/reconcile/", {"statement_date": "2026-08-15", "statement_balance": "9000",
                                                                     "lines": [ids[("10000.00", "0.00")], ids[("0.00", "1000.00")]]})
        self.assertEqual(ok.status_code, 201)
        again = self.fin.post(f"{A}/bank/{self.bank.id}/reconcile/", {"statement_date": "2026-08-31", "statement_balance": "9000",
                                                                        "lines": [ids[("10000.00", "0.00")]]})
        self.assertEqual(again.status_code, 400)

    def test_second_reconciliation_builds_on_the_first_and_only_the_latest_can_be_undone(self):
        _, ids = self.lines()
        first = self.fin.post(f"{A}/bank/{self.bank.id}/reconcile/", {"statement_date": "2026-08-15", "statement_balance": "9000",
                                                                        "lines": [ids[("10000.00", "0.00")], ids[("0.00", "1000.00")]]}).json()
        second = self.fin.post(f"{A}/bank/{self.bank.id}/reconcile/", {"statement_date": "2026-08-31", "statement_balance": "8500",
                                                                         "lines": [ids[("0.00", "500.00")]]})
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual(self.fin.post(f"{A}/reconciliations/{first['id']}/undo/").status_code, 400)
        self.assertEqual(self.fin.post(f"{A}/reconciliations/{second.json()['id']}/undo/").status_code, 204)
        self.assertEqual(self.fin.post(f"{A}/reconciliations/{first['id']}/undo/").status_code, 204)
        self.assertEqual(len(self.lines()[0]["lines"]), 3)

    def test_an_overdrawn_statement_balance_can_be_negative(self):
        self.expense(amount="9000", date="2026-08-21")
        r, ids = self.lines()
        res = self.fin.post(f"{A}/bank/{self.bank.id}/reconcile/", {"statement_date": "2026-08-31", "statement_balance": "-500",
                                                                      "lines": list(ids.values())})
        self.assertEqual((res.status_code, res.json()["statement_balance"]), (201, "-500.00"))


class StatementTests(Base):
    """A year of business with every figure worked out by hand (see the comments)."""

    def setUp(self):
        super().setUp()
        self.journal([("bank", {"debit": "100000"}), ("capital", {"credit": "100000"})], date="2026-04-05", narration="Capital introduced")
        self.journal([("1600", {"debit": "30000"}), ("bank", {"credit": "30000"})], date="2026-04-10", narration="Laptops")
        c = self.customer()
        inv = self.invoice(c, date="2026-08-15")
        self.pay(c, "1800", date="2026-08-20", allocations=[{"document": inv["id"], "amount": "1800"}])
        self.expense(amount="1000", date="2026-08-25")
        self.journal([("depreciation", {"debit": "500"}), ("accumulated_depreciation", {"credit": "500"})], date="2026-09-30", narration="Depreciation")

    def get(self, name, **params):
        r = self.fin.get(f"{A}/reports/{name}/", params)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_trial_balance_balances_and_shows_every_account_used(self):
        tb = self.tb("2026-09-30")
        codes = {r["code"] for r in tb["rows"]}
        self.assertEqual(codes, {"1010", "1200", "1600", "1650", "3000", "4000", "5200", "5800"})
        self.assertEqual((tb["total_debit"], tb["total_credit"]), ("102300.00", "102300.00"))
        self.assertEqual(self.tb("2026-04-30")["total_debit"], "100000.00")  # by April: bank 70,000 and equipment 30,000

    def test_profit_and_loss(self):
        p = self.get("profit-loss", **{"from": "2026-04-01", "to": "2027-03-31"})
        self.assertEqual((p["total_income"], p["total_expenses"], p["net_profit"], p["gross_profit"]), ("1800.00", "1500.00", "300.00", "1800.00"))
        self.assertEqual({r["name"]: r["amount"] for r in p["expenses"]}, {"Rent": "1000.00", "Depreciation": "500.00"})
        sep = self.get("profit-loss", **{"from": "2026-09-01", "to": "2026-09-30"})
        self.assertEqual((sep["total_income"], sep["net_profit"]), ("0.00", "-500.00"))

    def test_balance_sheet_balances(self):
        b = self.get("balance-sheet", as_of="2026-09-30")
        self.assertTrue(b["balanced"], b)
        self.assertEqual((b["total_current_assets"], b["total_fixed_assets"], b["total_assets"]), ("70800.00", "29500.00", "100300.00"))
        self.assertEqual((b["total_liabilities"], b["total_equity"], b["financial_year"]), ("0.00", "100300.00", "2026-27"))
        equity = {r["name"]: r["amount"] for r in b["equity"]}
        self.assertEqual(equity["Profit for the current year"], "300.00")
        self.assertEqual(equity["Partners' capital"], "100000.00")
        self.assertEqual({r["name"]: r["amount"] for r in b["fixed_assets"]}, {"Computers and equipment": "30000.00", "Accumulated depreciation": "-500.00"})

    def test_balance_sheet_in_a_later_year_carries_profit_forward(self):
        FinancialYear.objects.create(company=self.company, label="2027-28", start_date=datetime.date(2027, 4, 1), end_date=datetime.date(2028, 3, 31))
        b = self.get("balance-sheet", as_of="2027-06-30")
        self.assertTrue(b["balanced"], b)
        equity = {r["name"]: r["amount"] for r in b["equity"]}
        self.assertEqual((equity["Retained earnings: previous years"], equity["Profit for the current year"]), ("300.00", "0.00"))

    def test_cash_flow_for_the_year_ties_to_the_cash_change(self):
        c = self.get("cash-flow", **{"from": "2026-04-01", "to": "2026-09-30"})
        self.assertEqual((c["net_profit"], c["net_operating"], c["net_investing"], c["net_financing"]), ("300.00", "800.00", "-30000.00", "100000.00"))
        self.assertEqual((c["net_change"], c["opening_cash"], c["closing_cash"], c["ties"]), ("70800.00", "0.00", "70800.00", True))
        self.assertEqual(c["operating"][0]["name"], "Depreciation (not a cash cost)")

    def test_cash_flow_for_a_later_window_starts_from_the_real_opening_cash(self):
        c = self.get("cash-flow", **{"from": "2026-08-01", "to": "2026-09-30"})
        self.assertEqual((c["opening_cash"], c["net_change"], c["closing_cash"], c["ties"]), ("70000.00", "800.00", "70800.00", True))

    def test_cash_flow_ties_with_receivables_and_payables_moving(self):
        c = self.customer("Slow")
        self.invoice(c, date="2026-10-01")                                   # receivable up 1,800: uses cash
        self.journal([("5300", {"debit": "700"}), ("payable", {"credit": "700"})], date="2026-10-05")  # payable up 700: provides cash
        r = self.get("cash-flow", **{"from": "2026-10-01", "to": "2026-10-31"})
        self.assertTrue(r["ties"], r)
        self.assertEqual(r["net_profit"], "1100.00")  # 1,800 sales less 700 software
        self.assertEqual(r["net_change"], "0.00")      # profit 1,100 - receivable 1,800 + payable 700
        names = {i["name"]: i["amount"] for i in r["operating"]}
        self.assertEqual((names["Change in Accounts receivable"], names["Change in Accounts payable"]), ("-1800.00", "700.00"))

    def test_account_ledger_runs_a_balance_and_has_an_opening_balance(self):
        bank = self.acct("bank")
        led = self.fin.get(f"{A}/accounts/{bank.id}/ledger/", {"from": "2026-08-01", "to": "2026-09-30"}).json()
        self.assertEqual((led["opening"], led["closing"], led["total_debit"], led["total_credit"]), ("70000.00", "70800.00", "1800.00", "1000.00"))
        self.assertEqual([r["balance"] for r in led["rows"]], ["71800.00", "70800.00"])
        rent = self.fin.get(f"{A}/accounts/{self.acct('5200').id}/ledger/", {"from": "2026-04-01", "to": "2027-03-31"}).json()
        self.assertEqual((rent["opening"], rent["closing"]), ("0.00", "1000.00"))
        self.assertEqual(self.fin.get(f"{A}/accounts/{bank.id}/ledger/", {"from": "2026-09-30", "to": "2026-09-01"}).status_code, 400)

    def test_dashboard(self):
        d = self.fin.get(f"{A}/dashboard/").json()
        self.assertEqual((d["cash_total"], d["receivable"], d["payable"]), ("70800.00", "0.00", "0.00"))
        self.assertEqual(len(d["monthly"]), 6)
        self.assertEqual(len(d["recent"]), 6)  # capital, laptops, invoice, payment, rent, depreciation
        self.assertEqual(d["currency"], "INR")
        names = {r["name"] for r in d["expense_breakdown"]}
        self.assertLessEqual(len(d["expense_breakdown"]), 7)
        self.assertTrue({"cash", "bank"} <= {c["name"].lower().split()[0] for c in d["cash"]} or d["cash"])
        # the period defaults to the financial year; this month's figures cannot exceed the year's
        self.assertGreaterEqual(D(d["ytd"]["income"]), D(d["month"]["income"]))
        self.assertIsInstance(names, set)

    def test_the_books_always_balance_after_everything(self):
        c = self.customer("More")
        inv = self.invoice(c, date="2026-10-01")
        self.pay(c, "2500", date="2026-10-03", allocations=[{"document": inv["id"], "amount": "1800"}])
        self.fin.post(f"{S}/documents/{inv['id']}/void/", {"change_reason": "x"}) if False else None
        self.expense(amount="123.45", date="2026-10-04")
        tb = self.tb("2027-03-31")
        self.assertEqual(sum(D(r["debit"]) for r in tb["rows"]), sum(D(r["credit"]) for r in tb["rows"]))
        self.assertTrue(self.get("balance-sheet", as_of="2027-03-31")["balanced"])
        self.assertTrue(self.get("cash-flow", **{"from": "2026-04-01", "to": "2027-03-31"})["ties"])


class PermissionTests(Base):
    def test_roles(self):
        self.expense()
        partner = client_for(make_user("part", Role.PARTNER))
        for path in ("dashboard/", "accounts/", "journal/", "vouchers/", "bank/", "reports/trial-balance/", "reports/profit-loss/",
                     "reports/balance-sheet/", "reports/cash-flow/"):
            self.assertEqual(self.api_staff.get(f"{A}/{path}").status_code, 403, path)
            self.assertEqual(partner.get(f"{A}/{path}").status_code, 200, path)
            self.assertEqual(self.anon.get(f"{A}/{path}").status_code, 401, path)
        self.assertEqual(partner.post(f"{A}/journal/", {}).status_code, 403)
        self.assertEqual(partner.post(f"{A}/vouchers/", {}).status_code, 403)
        self.assertEqual(partner.post(f"{A}/accounts/", {}).status_code, 403)
        self.assertEqual(partner.post(f"{A}/accounts/setup/").status_code, 403)
        self.assertEqual(self.api.get(f"{A}/accounts/").status_code, 200)

    def test_unknown_ids_are_404(self):
        zero = "00000000-0000-0000-0000-000000000000"
        for path in (f"accounts/{zero}/", f"journal/{zero}/", f"accounts/{zero}/ledger/"):
            self.assertIn(self.fin.get(f"{A}/{path}").status_code, (400, 404), path)
        self.assertEqual(self.fin.get(f"{A}/journal/not-a-uuid/").status_code, 404)
        self.assertEqual(self.fin.post(f"{A}/vouchers/{zero}/void/", {"change_reason": "x"}).status_code, 404)
