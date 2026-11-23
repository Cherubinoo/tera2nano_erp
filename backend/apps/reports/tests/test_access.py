"""Who may open which report, the empty database, bad requests and the shape of the CSV files."""
import csv
import io

from apps.accounts import access
from apps.accounts.models import Role, RolePermission

from .base import ALL_REPORTS, EXPECTED, R, ROLES, ReportsBase, TODAY

FY = {"fy": "2026-27"}


def rows_of(response):
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:])))


class PermissionMatrixTests(ReportsBase):
    def test_each_role_sees_only_the_reports_its_permissions_allow(self):
        for role in ROLES:
            for key in sorted(ALL_REPORTS):
                want = 200 if key in EXPECTED[role] else 403
                got = self.clients[role].get(f"{R}/{key}/", FY).status_code
                self.assertEqual(got, want, f"{role} {key}")
                got = self.clients[role].get(f"{R}/{key}/export/", FY).status_code
                self.assertEqual(got, want, f"{role} {key} export")

    def test_home_lists_only_permitted_reports(self):
        for role in ROLES:
            r = self.clients[role].get(f"{R}/home/")
            if role == Role.EMPLOYEE:
                self.assertEqual(r.status_code, 403)
                continue
            self.assertEqual({c["key"] for c in r.json()["reports"]}, EXPECTED[role], role)

    def test_dashboard_tiles_follow_the_source_permissions(self):
        want = {
            Role.ADMIN: {"revenue", "expenses", "profit", "cash", "receivables", "payables", "headcount", "deals"},
            Role.MANAGING_PARTNER: {"revenue", "expenses", "profit", "cash", "receivables", "payables", "headcount", "deals"},
            Role.PARTNER: {"revenue", "expenses", "profit", "cash", "receivables", "payables", "headcount", "deals"},
            Role.FINANCE_MANAGER: {"revenue", "expenses", "profit", "cash", "receivables", "payables", "headcount"},
            Role.HR_MANAGER: {"headcount"},
            Role.PROJECT_MANAGER: {"receivables", "payables", "headcount", "deals"},
        }
        for role, keys in want.items():
            d = self.clients[role].get(f"{R}/dashboard/").json()
            self.assertEqual({t["key"] for t in d["tiles"]}, keys, role)
            self.assertEqual(d["trend"] is not None, "revenue" in keys, role)
        self.assertEqual(self.clients[Role.EMPLOYEE].get(f"{R}/dashboard/").status_code, 403)

    def test_reports_view_alone_or_the_source_permission_alone_is_not_enough(self):
        # HR holds reports.view but not accounting.view
        self.assertEqual(self.clients[Role.HR_MANAGER].get(f"{R}/revenue/", FY).status_code, 403)
        # finance holds accounting.view; take reports.view away and the report closes
        RolePermission.objects.create(role=Role.FINANCE_MANAGER, code="reports.view", granted=False)
        access.matrix_changed()
        self.assertEqual(self.clients[Role.FINANCE_MANAGER].get(f"{R}/revenue/", FY).status_code, 403)
        self.assertEqual(self.clients[Role.FINANCE_MANAGER].get(f"{R}/home/").status_code, 403)

    def test_anonymous_is_refused(self):
        for path in ("home/", "dashboard/", "revenue/", "revenue/export/"):
            self.assertIn(self.anon.get(f"{R}/{path}").status_code, (401, 403))

    def test_the_server_decides_even_for_odd_keys(self):
        self.assertEqual(self.clients[Role.EMPLOYEE].get(f"{R}/nonsense/").status_code, 403)  # no reports.view: no hint either way


class EmptyDatabaseTests(ReportsBase):
    def test_every_report_opens_with_no_data(self):
        for key in sorted(ALL_REPORTS):
            for params in (FY, {}, {"fy": "all"}):
                if params == {"fy": "all"} and key in ("revenue", "salary", "gst", "cash-flow"):
                    self.get(key, expect=400, **params)
                    continue
                d = self.get(key, **params)
                self.assertEqual(d["key"], key)
                self.assertIn("kpis", d)
                self.assertEqual(self.export(key, **params).status_code, 200)

    def test_empty_figures_are_zero_not_missing(self):
        d = self.get("revenue", **FY)
        self.assertEqual((d["kpis"]["revenue"], d["kpis"]["change_percent"], d["kpis"]["best_month"], d["by_customer"]), ("0.00", None, None, []))
        self.assertEqual(len(d["months"]), 12)
        r = self.get("receivables")
        self.assertEqual((r["kpis"]["outstanding"], r["kpis"]["largest"], r["customers"]), ("0.00", None, []))
        s = self.get("salary", **FY)
        self.assertEqual((s["kpis"]["gross"], s["kpis"]["headcount"], s["departments"]), ("0.00", 0, []))
        self.assertEqual(self.get("partner-capital")["partners"], [])
        self.assertEqual(self.get("project-profitability")["projects"], [])
        g = self.get("gst", **FY)
        self.assertEqual((g["kpis"]["output"], g["kpis"]["net_payable"]), ("0.00", "0.00"))
        c = self.get("cash-flow", **FY)
        self.assertEqual((c["kpis"]["closing_cash"], c["statement"]["ties"]), ("0.00", True))

    def test_hub_and_dashboard_with_no_data(self):
        home = self.get("home")
        self.assertEqual(len(home["reports"]), 8)
        for card in home["reports"]:
            self.assertEqual(card["headline"]["value"], "0.00", card["key"])
        d = self.get("dashboard")
        self.assertEqual({t["key"]: t["value"] for t in d["tiles"]},
                         {"revenue": "0.00", "expenses": "0.00", "profit": "0.00", "cash": "0.00", "receivables": "0.00",
                          "payables": "0.00", "headcount": 0, "deals": 0})
        self.assertEqual([t["revenue"] for t in d["trend"]], ["0.00"] * 6)


class BadRequestTests(ReportsBase):
    def test_unknown_report_is_404(self):
        self.get("no-such-report", expect=404)
        self.export("no-such-report", expect=404)
        self.get("home/../x", expect=404)

    def test_bad_periods_are_400_with_a_field_message(self):
        cases = [({"fy": "2026"}, "fy"), ({"fy": "2026-28"}, "fy"), ({"fy": "abcd-ef"}, "fy"), ({"fy": "1999-00"}, "fy"),
                 ({"from": "2026-13-01", "to": "2026-12-31"}, "from"), ({"from": "2026-04-01", "to": "soon"}, "to"),
                 ({"from": "2026-04-01"}, "to"), ({"to": "2026-04-01"}, "from"),
                 ({"from": "2026-05-01", "to": "2026-04-01"}, "to"), ({"from": "2015-01-01", "to": "2026-01-01"}, "to")]
        for params, field in cases:
            for key in ("revenue", "receivables", "salary"):
                r = self.clients[Role.ADMIN].get(f"{R}/{key}/", params)
                self.assertEqual(r.status_code, 400, (key, params))
                self.assertIn(field, r.json(), (key, params))
        self.assertEqual(self.clients[Role.ADMIN].get(f"{R}/revenue/export/", {"fy": "nope"}).status_code, 400)

    def test_all_dates_only_where_it_makes_sense(self):
        self.get("revenue", expect=200)                      # defaults to the current financial year
        self.get("revenue", expect=400, fy="all")
        self.get("receivables", fy="all")
        d = self.get("receivables")
        self.assertTrue(d["period"]["all"])
        self.assertEqual(self.get("revenue")["period"]["fy"][:2], "20")
        self.assertEqual(self.get("revenue")["period"]["from"] <= TODAY.isoformat() <= self.get("revenue")["period"]["to"], True)


class CsvTests(ReportsBase):
    def test_revenue_csv_shape_and_values(self):
        self.book("2026-08-10", "bank", "sales", "10000")
        self.book("2026-09-05", "bank", "sales", "5000")
        self.invoice(self.customer("=SUM(1)"), issue_date="2026-08-15")
        r = self.export("revenue", **FY)
        self.assertTrue(r["Content-Type"].startswith("text/csv"))
        self.assertIn('filename="revenue-', r["Content-Disposition"])
        rows = rows_of(r)
        self.assertEqual(rows[0], ["Annual revenue"])
        self.assertEqual(rows[1][0], "Period: FY 2026-27")
        i = rows.index(["Revenue by month", ])
        self.assertEqual(rows[i + 1], ["Month", "Revenue", "Same month last year", "Change %"])
        month_rows = rows[i + 2:i + 14]
        self.assertEqual([m[0] for m in month_rows][:3], ["Apr 26", "May 26", "Jun 26"])
        self.assertTrue(all(len(m) == 4 for m in month_rows))
        aug = next(m for m in month_rows if m[0] == "Aug 26")
        self.assertEqual(aug[1], "11800.00")       # 10,000 booked + 1,800 invoice
        total = rows[i + 14]
        self.assertEqual(total[:2], ["Total", "16800.00"])
        self.assertEqual(rows[i + 15], [])
        j = rows.index(["Revenue by customer"])
        names = [row[0] for row in rows[j + 2:] if row]
        self.assertIn("'=SUM(1)", names)           # a customer called like a formula is made safe
        self.assertNotIn("=SUM(1)", names)

    def test_every_report_csv_has_title_period_and_a_header_per_block(self):
        for key in sorted(ALL_REPORTS):
            rows = rows_of(self.export(key, **FY))
            self.assertEqual(len(rows[0]), 1, key)
            self.assertTrue(rows[1][0].startswith("Period: "), key)
            self.assertEqual(rows[2], [], key)
            # every block: a one-cell title, a header, then rows no wider than the header, ended by a blank row
            i, blocks = 3, 0
            while i < len(rows):
                self.assertEqual(len(rows[i]), 1, (key, i))
                width = len(rows[i + 1])
                self.assertGreater(width, 1)
                j = i + 2
                while j < len(rows) and rows[j] != []:
                    self.assertEqual(len(rows[j]), width, (key, rows[j]))
                    j += 1
                i, blocks = j + 1, blocks + 1
            self.assertGreaterEqual(blocks, 1, key)

    def test_csv_matches_the_screen_for_ageing(self):
        from .base import ago, iso
        self.invoice(issue_date=iso(ago(40)), due_date=iso(ago(10)))
        screen = self.get("receivables")
        rows = rows_of(self.export("receivables"))
        header = next(r for r in rows if r and r[0] == "Customer")
        self.assertEqual(header[1:6], [b["label"] for b in screen["buckets"]])
        line = rows[rows.index(header) + 1]
        self.assertEqual((line[0], line[2]), ("Acme Industries", "1800.00"))  # 1-15 days overdue
        total = rows[rows.index(header) + 2]
        self.assertEqual((total[0], total[6]), ("Total", screen["kpis"]["outstanding"]))
