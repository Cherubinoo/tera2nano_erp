"""Purchase orders to the vendor and payslips to the employee, each with its PDF, and only for people who may do that work."""
from django.core import mail

from apps.accounts.models import Role
from apps.company.tests.base import client_for, make_user
from apps.emails.models import EmailMessage
from apps.hr.tests.base import H
from apps.hr.tests.test_payroll import PayrollBase
from apps.purchases.tests.base import PurchasesBase

E = "/api/v1/email"


class PurchaseOrderEmailTests(PurchasesBase):
    def test_an_issued_order_goes_to_the_vendor_with_its_pdf(self):
        vendor = self.vendor("Mail Supplies", email="orders@mailsupplies.example", contact_person="Ravi")
        draft = self.order(vendor)
        r = self.fin.get(f"{E}/draft/", {"kind": "purchase_order", "id": draft["id"]})
        self.assertEqual(r.status_code, 400)  # not issued yet: no PDF
        order = self.issue_order(draft)
        d = self.fin.get(f"{E}/draft/", {"kind": "purchase_order", "id": order["id"]}).json()
        self.assertEqual(d["to"], ["orders@mailsupplies.example"])
        self.assertIn(order["number"], d["subject"])
        self.assertIn("Dear Ravi", d["body"])
        mail.outbox = []
        r = self.fin.post(f"{E}/send/", {"kind": "purchase_order", "id": order["id"], "to": d["to"], "subject": d["subject"],
                                         "body": d["body"]}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        name, content, mime = mail.outbox[0].attachments[0]
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertEqual(name, order["number"].replace("/", "-") + ".pdf")
        self.assertEqual(EmailMessage.objects.get().url, f"/purchases/orders/{order['id']}")
        viewer = client_for(make_user("pm1", Role.PROJECT_MANAGER))  # purchases.view only
        self.assertEqual(viewer.get(f"{E}/draft/", {"kind": "purchase_order", "id": order["id"]}).status_code, 403)
        self.assertEqual(viewer.post(f"{E}/send/", {"kind": "purchase_order", "id": order["id"], "to": ["a@b.example"],
                                                    "subject": "s", "body": "b"}, format="json").status_code, 403)


class PayslipEmailTests(PayrollBase):
    def test_a_payslip_is_emailed_to_the_employee_once_the_run_is_approved(self):
        from apps.hr.models import Employee

        Employee.objects.filter(pk=self.asha["id"]).update(work_email="asha@example.com")
        run = self.run_payroll()
        slip = self.slip(run, self.asha)
        self.assertEqual(self.fin.get(f"{E}/draft/", {"kind": "payslip", "id": slip["id"]}).status_code, 400)  # still a draft run
        self.step(run, "lock")
        self.step(run, "submit")
        self.decide(run)
        d = self.fin.get(f"{E}/draft/", {"kind": "payslip", "id": slip["id"]}).json()
        self.assertEqual(d["to"], ["asha@example.com"])
        self.assertIn("payslip for August 2026", d["body"])
        mail.outbox = []
        r = self.fin.post(f"{E}/send/", {"kind": "payslip", "id": slip["id"], "to": d["to"], "subject": d["subject"], "body": d["body"]},
                          format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["status"], "saved")
        name, content, _ = mail.outbox[0].attachments[0]
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertTrue(name.startswith("payslip-") and name.endswith("-2026-08.pdf"))
        # people without payroll access cannot send pay details
        for client in (self.api_pm, self.api_staff):
            self.assertEqual(client.get(f"{E}/draft/", {"kind": "payslip", "id": slip["id"]}).status_code, 403)
        self.assertEqual(self.fin.get(f"{H}/payroll/payslips/{slip['id']}/pdf/").status_code, 200)  # the same numbered PDF
