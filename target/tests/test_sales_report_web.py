"""Sales report web page and its links to order entry and the order list.

Expected cents come from the frozen legacy ReportTests/PricingTests oracle
(seed grand total 65720; a C1001/HAM-01/3 order is 4218) and are shown as
dollars on the page (65720 -> $657.20). The page and GET /api/reports/sales
share app/services/report.py.
"""

import os
import re
from unittest import mock

from helpers import WebTestCase, appdb

from app.services import report
from app.templating import dollars

HX = {"HX-Request": "true"}
SEED_GRAND_CENTS = 65720  # legacy ReportTests.test_grand_total_excludes_cancelled
HAM3_CENTS = 4218  # legacy PricingTests.test_standard_tier_truncates_tax
ROW = re.compile(
    r'<tr id="report-([^"]+)" data-cust-no="[^"]+">\s*'
    r"<td>[^<]*</td><td>([^<]*)</td>\s*"
    r'<td class="num">(\d+)</td><td class="num">([^<]+)</td>'
)
GRAND = re.compile(r'<tr id="report-grand-total">.*?<strong>([^<]+)</strong>', re.S)

SEED_REPORT_PAGE = [
    ("C1003", "CONTOSO SUPPLY", 1, "$564.12"),
    ("C1002", "BRIGHT FUTURES LLC", 1, "$50.90"),
    ("C1001", "ACME HARDWARE", 1, "$42.18"),
    ("C1004", "DELTA TOOLWORKS", 0, "$0.00"),
]


class SalesReportPageTests(WebTestCase):
    def page_rows(self, html):
        return [(c, n, int(k), s) for c, n, k, s in ROW.findall(html)]

    def grand(self, html):
        return GRAND.search(html).group(1)

    def test_seed_page_lists_every_customer_by_descending_sales(self):
        r = self.client.get("/reports/sales")
        self.assertEqual(r.status_code, 200)
        self.assertIn("SALES REPORT", r.text)
        self.assertEqual(self.page_rows(r.text), SEED_REPORT_PAGE)
        self.assertEqual(self.grand(r.text), "$657.20")

    def test_page_and_api_show_the_same_report(self):
        body = self.client.get("/api/reports/sales").json()
        html = self.client.get("/reports/sales").text
        self.assertEqual(
            [
                (row["cust_no"], row["name"], row["order_count"], dollars(row["sales_cents"]))
                for row in body["rows"]
            ],
            self.page_rows(html),
        )
        self.assertEqual(self.grand(html), dollars(body["grand_total_cents"]))
        self.assertEqual(body["grand_total_cents"], SEED_GRAND_CENTS)

    def test_page_and_api_use_the_shared_report_service(self):
        fake = report.SalesReport((report.ReportRow("X1", "SHARED CO", 7, 123456),), 123456)
        with mock.patch.object(report, "sales_report", return_value=fake) as svc:
            body = self.client.get("/api/reports/sales").json()
            html = self.client.get("/reports/sales").text
        self.assertEqual(svc.call_count, 2)
        self.assertEqual(
            body,
            {
                "rows": [
                    {"cust_no": "X1", "name": "SHARED CO", "order_count": 7, "sales_cents": 123456}
                ],
                "grand_total_cents": 123456,
            },
        )
        self.assertEqual(self.page_rows(html), [("X1", "SHARED CO", 7, "$1234.56")])
        self.assertEqual(self.grand(html), "$1234.56")

    def test_htmx_refresh_returns_only_the_report_fragment(self):
        r = self.client.get("/reports/sales", headers=HX)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("<html", r.text)
        self.assertEqual(self.page_rows(r.text), SEED_REPORT_PAGE)
        full = self.client.get("/reports/sales")
        self.assertIn('hx-get="/reports/sales" hx-target="#sales-report"', full.text)
        self.assertEqual(r.headers["vary"], "HX-Request")
        self.assertEqual(full.headers["vary"], "HX-Request")

    def test_no_order_customer_still_shown_at_zero(self):
        self.execute("DELETE FROM orders WHERE cust_no='C1004'")
        html = self.client.get("/reports/sales").text
        self.assertEqual(self.page_rows(html), SEED_REPORT_PAGE)
        self.assertEqual(self.grand(html), "$657.20")

    def test_all_zero_page_keeps_four_customer_rows(self):
        self.execute("DELETE FROM orders")
        html = self.client.get("/reports/sales").text
        rows = self.page_rows(html)
        self.assertEqual(sorted(r[0] for r in rows), ["C1001", "C1002", "C1003", "C1004"])
        self.assertEqual({(r[2], r[3]) for r in rows}, {(0, "$0.00")})
        self.assertEqual(self.grand(html), "$0.00")

    def test_customer_names_are_html_escaped(self):
        self.execute("INSERT INTO customers VALUES ('X<1','<b>BOLD</b> & CO','TYLER','S',10)")
        html = self.client.get("/reports/sales").text
        self.assertIn("&lt;b&gt;BOLD&lt;/b&gt; &amp; CO", html)
        self.assertNotIn("<b>BOLD", html)

    def test_report_is_linked_from_every_page(self):
        for path in ("/customers", "/orders", "/orders/new", "/reports/sales"):
            with self.subTest(path=path):
                html = self.client.get(path).text
                self.assertIn('<a href="/reports/sales">Sales report</a>', html)
        nav = self.client.get("/reports/sales").text
        for link in ('href="/customers"', 'href="/orders/new"', 'href="/orders"'):
            self.assertIn(link, nav)

    def test_database_failure_is_controlled(self):
        broken = os.path.join(self.tmp.name, "missing-dir", "x.db")
        with mock.patch.object(appdb, "DB_PATH", broken):
            page = self.client.get("/reports/sales")
            frag = self.client.get("/reports/sales", headers=HX)
            api = self.client.get("/api/reports/sales")
        self.assertEqual(page.status_code, 503)
        self.assertIn(report.REPORT_UNAVAILABLE, page.text)
        self.assertNotIn("GRAND TOTAL", page.text)
        self.assertEqual(frag.status_code, 503)
        self.assertIn(report.REPORT_UNAVAILABLE, frag.text)
        self.assertEqual(
            (api.status_code, api.json()), (503, {"detail": report.REPORT_UNAVAILABLE})
        )


class CrossScreenReportTests(WebTestCase):
    """One web-created order followed through credit exposure, the order list and the report."""

    def create_via_web(self):
        r = self.client.post(
            "/orders/confirm",
            headers=HX,
            data={"cust_no": "C1001", "sku": "HAM-01", "qty": "3", "preview_total_cents": "4218"},
        )
        self.assertEqual(r.status_code, 201, r.text)
        return int(re.search(r"ORDER (\d+)", r.text).group(1))

    def act_via_web(self, order_no, action):
        r = self.client.post(f"/orders/{order_no}/{action}", headers=HX)
        self.assertEqual(r.status_code, 200, r.text)
        return r

    def exposure(self, cust_no="C1001"):
        return self.client.get(f"/api/customers/{cust_no}").json()["open_total_cents"]

    def acme(self, body):
        return [row for row in body["rows"] if row["cust_no"] == "C1001"][0]

    def report(self):
        return self.client.get("/api/reports/sales").json()

    def test_created_order_counts_through_pick_and_ship(self):
        self.assertEqual(self.exposure(), HAM3_CENTS)
        order_no = self.create_via_web()
        for step in ("N", "P", "S"):
            with self.subTest(status=step):
                if step == "P":
                    self.act_via_web(order_no, "pick")
                elif step == "S":
                    self.act_via_web(order_no, "ship")
                self.assertEqual(self.client.get(f"/api/orders/{order_no}").json()["status"], step)
                body = self.report()
                self.assertEqual(
                    (self.acme(body)["order_count"], self.acme(body)["sales_cents"]),
                    (2, 2 * HAM3_CENTS),
                )
                self.assertEqual(body["grand_total_cents"], SEED_GRAND_CENTS + HAM3_CENTS)
                self.assertEqual(
                    [row["cust_no"] for row in body["rows"]], ["C1003", "C1001", "C1002", "C1004"]
                )
                # Shipping ends only the credit exposure, not the sale.
                self.assertEqual(self.exposure(), HAM3_CENTS if step == "S" else 2 * HAM3_CENTS)
                html = self.client.get("/reports/sales").text
                self.assertIn(dollars(2 * HAM3_CENTS), html)
                self.assertIn(dollars(SEED_GRAND_CENTS + HAM3_CENTS), html)
                listed = self.client.get("/orders").text
                self.assertIn(
                    f'<tr id="order-{order_no}" data-order-no="{order_no}" data-status="{step}">',
                    listed,
                )

    def test_cancel_restores_stock_exposure_and_report(self):
        baseline = self.report()
        stock = self.stock("HAM-01")
        others = [row for row in baseline["rows"] if row["cust_no"] != "C1001"]
        order_no = self.create_via_web()
        self.assertEqual(self.stock("HAM-01"), stock - 3)
        self.assertEqual(self.report()["grand_total_cents"], SEED_GRAND_CENTS + HAM3_CENTS)
        self.act_via_web(order_no, "cancel")
        self.assertEqual(self.client.get(f"/api/orders/{order_no}").json()["status"], "C")
        self.assertIn('data-status="C"', self.client.get("/orders").text)
        self.assertEqual(self.stock("HAM-01"), stock)
        self.assertEqual(self.exposure(), HAM3_CENTS)
        after = self.report()
        self.assertEqual(after, baseline)
        self.assertEqual(after["grand_total_cents"], SEED_GRAND_CENTS)
        self.assertEqual([row for row in after["rows"] if row["cust_no"] != "C1001"], others)
        html = self.client.get("/reports/sales").text
        self.assertIn("$657.20", html)
