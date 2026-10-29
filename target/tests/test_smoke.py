"""Smoke test: can the app boot, serve the UI, and answer the API?

This is the mission's final sanity check - if this passes, the app runs
end-to-end. Keep it fast; do not add per-rule assertions here (those belong
in test_parity.py).
"""

import json
import re

from helpers import WebTestCase


class SmokeTests(WebTestCase):
    def test_health_is_ok(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_customers_page_renders(self):
        r = self.client.get("/customers")
        self.assertEqual(r.status_code, 200)
        self.assertIn("CUSTOMER LOOKUP", r.text)
        self.assertIn("ACME HARDWARE", r.text)

    def test_lookup_fragment_found(self):
        r = self.client.get("/customers/lookup", params={"cust_no": "C1001"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("ACME HARDWARE", r.text)

    def test_lookup_fragment_not_found(self):
        r = self.client.get("/customers/lookup", params={"cust_no": "C9999"})
        self.assertEqual(r.status_code, 404)
        self.assertIn("CUSTOMER NOT FOUND", r.text)

    def test_order_entry_page_renders_labeled_form(self):
        r = self.client.get("/orders/new")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ORDER ENTRY", r.text)
        for control in ('for="cust_no"', 'for="sku"', 'for="qty"', "Preview order"):
            self.assertIn(control, r.text)

    def test_htmx_config_swaps_validation_errors(self):
        page = self.client.get("/orders/new").text
        config = json.loads(re.search(r"name=\"htmx-config\" content='([^']+)'", page).group(1))
        four_xx = [h for h in config["responseHandling"] if h["code"] == "4.."][0]
        self.assertTrue(four_xx["swap"])

    def test_customers_page_links_to_order_entry(self):
        r = self.client.get("/customers")
        self.assertIn('href="/orders/new"', r.text)

    def test_order_entry_page_load_writes_nothing(self):
        before = self.snapshot()
        self.client.get("/orders/new")
        self.assertEqual(self.snapshot(), before)

    def test_order_list_page_renders(self):
        r = self.client.get("/orders")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ORDER LIST", r.text)
        self.assertIn('id="order-1"', r.text)

    def test_customers_page_links_to_order_list(self):
        r = self.client.get("/customers")
        self.assertIn('href="/orders"', r.text)

    def test_order_list_page_load_writes_nothing(self):
        before = self.snapshot()
        self.client.get("/orders")
        self.client.get("/api/orders")
        self.assertEqual(self.snapshot(), before)

    def test_order_list_api_end_to_end(self):
        r = self.client.get("/api/orders")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([o["order_no"] for o in r.json()], [4, 3, 2, 1])
        self.assertEqual(self.client.post("/api/orders/1/pick").json()["status"], "P")

    def test_create_order_api_end_to_end(self):
        r = self.client.post("/api/orders", json={"cust_no": "C1001", "sku": "HAM-01", "qty": "3"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["order_no"], 5)

    def test_sales_report_page_renders(self):
        r = self.client.get("/reports/sales")
        self.assertEqual(r.status_code, 200)
        self.assertIn("SALES REPORT", r.text)
        self.assertIn("GRAND TOTAL", r.text)

    def test_customers_page_links_to_sales_report(self):
        r = self.client.get("/customers")
        self.assertIn('href="/reports/sales"', r.text)

    def test_sales_report_api_end_to_end(self):
        r = self.client.get("/api/reports/sales")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["grand_total_cents"], 65720)

    def test_customer_search_page_renders(self):
        r = self.client.get("/customers/search")
        self.assertEqual(r.status_code, 200)
        self.assertIn("CUSTOMER NAME SEARCH", r.text)

    def test_customers_page_links_to_customer_search(self):
        r = self.client.get("/customers")
        self.assertIn('href="/customers/search"', r.text)

    def test_customer_search_api_end_to_end(self):
        r = self.client.get("/api/search/customers", params={"name": "ACME"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c["cust_no"] for c in r.json()["customers"]], ["C1001"])
