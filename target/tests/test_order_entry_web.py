"""Order-entry web flow: preview (no write) -> confirm or decline.

Expected money strings are the legacy oracle cents from
tests/test_characterization.py formatted as dollars (4218 -> $42.18).
"""
import os
from unittest import mock

from helpers import WebTestCase, appdb
from legacy_oracle import LegacyTwin

from app.services import orders

HX = {"HX-Request": "true"}


class OrderEntryWebTests(WebTestCase):
    def preview(self, cust, sku, qty):
        return self.client.post("/orders/preview", data={"cust_no": cust, "sku": sku, "qty": qty}, headers=HX)

    def confirm(self, cust, sku, qty, preview_total_cents=""):
        return self.client.post("/orders/confirm", headers=HX, data={
            "cust_no": cust, "sku": sku, "qty": qty, "preview_total_cents": preview_total_cents})

    def test_standard_preview_truncates_tax_and_writes_nothing(self):
        before = self.snapshot()
        r = self.preview("C1001", "HAM-01", "3")
        self.assertEqual(r.status_code, 200, r.text)
        for shown in ("$38.97", "$0.00 (0%)", "$3.21", "$42.18", "Confirm order", "Decline"):
            self.assertIn(shown, r.text)
        self.assertNotIn("$3.22", r.text)
        self.assertNotIn("<html", r.text)
        self.assertEqual(self.snapshot(), before)

    def test_preview_without_htmx_renders_full_page(self):
        r = self.client.post("/orders/preview", data={"cust_no": "C1001", "sku": "HAM-01", "qty": "3"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("ORDER ENTRY", r.text)
        self.assertIn("$42.18", r.text)

    def test_gold_preview_truncates_discount(self):
        r = self.preview("C1002", "SAW-02", "2")
        for shown in ("$49.50", "$2.47 (5%)", "$3.87", "$50.90"):
            self.assertIn(shown, r.text)

    def test_platinum_volume_preview_shows_13_percent(self):
        r = self.preview("C1003", "NAI-04", "100")
        self.assertIn("(13%)", r.text)
        self.assertIn("$564.12", r.text)

    def test_raw_input_is_normalized_in_preview(self):
        r = self.preview("c1001", "ham-01", "00035")
        self.assertIn('name="cust_no" value="C1001"', r.text)
        self.assertIn('name="sku" value="HAM-01"', r.text)
        self.assertIn('name="qty" value="3"', r.text)
        self.assertIn("$42.18", r.text)

    def test_confirm_creates_one_new_order_and_decrements_stock(self):
        r = self.confirm("C1001", "HAM-01", "3", "4218")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertIn("ORDER 5 CREATED", r.text)
        self.assertIn("$42.18", r.text)
        self.assertEqual(self.query("SELECT cust_no,sku,qty,total_cents,status FROM orders WHERE order_no>4"),
                         [("C1001", "HAM-01", 3, 4218, "N")])
        self.assertEqual(self.stock("HAM-01"), 114)

    def test_confirm_matches_direct_api_on_independent_seed(self):
        self.confirm("C1002", "SAW-02", "2")
        web_rows = self.snapshot()
        appdb.init(reset=True).close()
        self.client.post("/api/orders", json={"cust_no": "C1002", "sku": "SAW-02", "qty": "2"})
        self.assertEqual(self.snapshot(), web_rows)

    def test_confirm_without_htmx_renders_full_page(self):
        ok = self.client.post("/orders/confirm", data={"cust_no": "C1001", "sku": "HAM-01", "qty": "3"})
        self.assertEqual(ok.status_code, 201)
        self.assertIn("ORDER ENTRY", ok.text)
        self.assertIn("ORDER 5 CREATED", ok.text)
        bad = self.client.post("/orders/confirm", data={"cust_no": "C1001", "sku": "DRL-03", "qty": "16"})
        self.assertEqual(bad.status_code, 409)
        self.assertIn("ORDER ENTRY", bad.text)
        self.assertIn("INSUFFICIENT STOCK", bad.text)

    def test_preview_database_failure_is_controlled(self):
        before = self.snapshot()
        broken = os.path.join(self.tmp.name, "missing-dir", "x.db")
        with mock.patch.object(appdb, "DB_PATH", broken):
            r = self.preview("C1001", "HAM-01", "3")
            c = self.confirm("C1001", "HAM-01", "3")
        self.assertEqual(r.status_code, 503)
        self.assertIn(orders.NOT_CREATED, r.text)
        self.assertEqual(c.status_code, 503)
        self.assertIn(orders.NOT_CREATED, c.text)
        self.assertEqual(self.snapshot(), before)

    def test_confirm_button_is_disabled_while_submitting(self):
        r = self.preview("C1001", "HAM-01", "3")
        self.assertIn('hx-disabled-elt=".preview button"', r.text)
        self.assertIn('hx-sync="this:drop"', r.text)

    def test_decline_writes_nothing(self):
        before = self.snapshot()
        self.preview("C1001", "HAM-01", "3")
        r = self.client.post("/orders/decline", headers=HX)
        self.assertEqual(r.status_code, 200)
        self.assertIn("ORDER NOT CREATED", r.text)
        self.assertNotIn("ORDER 5 CREATED", r.text)
        self.assertEqual(self.snapshot(), before)

    def test_rejections_are_visible_and_write_nothing(self):
        before = self.snapshot()
        cases = (
            (("C9999", "HAM-01", "3"), 404, "INVALID CUSTOMER"),
            (("C1001", "BAD-SKU", "3"), 404, "INVALID SKU"),
            (("C1001", "HAM-01", "abc"), 422, "QTY MUST BE A POSITIVE NUMBER"),
            (("C1001", "HAM-01", "00005"), 422, "QTY MUST BE A POSITIVE NUMBER"),
            (("C1001", "DRL-03", "16"), 409, "INSUFFICIENT STOCK"),
        )
        for args, status, message in cases:
            for action in (self.preview, self.confirm):
                with self.subTest(args=args, action=action.__name__):
                    r = action(*args)
                    self.assertEqual(r.status_code, status, r.text)
                    self.assertIn(message, r.text)
                    self.assertIn('role="alert"', r.text)
                    self.assertEqual(self.snapshot(), before)

    def test_credit_rejection_is_visible_and_writes_nothing(self):
        self.execute("UPDATE customers SET credit_lim=100 WHERE cust_no='C1001'")
        before = self.snapshot()
        r = self.preview("C1001", "HAM-01", "5")
        self.assertEqual(r.status_code, 409)
        self.assertIn("CREDIT LIMIT EXCEEDED", r.text)
        self.assertEqual(self.snapshot(), before)

    def test_exact_stock_confirmation_leaves_zero(self):
        r = self.confirm("C1001", "DRL-03", "15")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.stock("DRL-03"), 0)


class StalePreviewTests(WebTestCase):
    """The preview is not a reservation; confirmation recomputes everything."""

    def preview(self):
        r = self.client.post("/orders/preview", headers=HX,
                             data={"cust_no": "C1001", "sku": "HAM-01", "qty": "3"})
        self.assertEqual(r.status_code, 200, r.text)

    def confirm(self):
        return self.client.post("/orders/confirm", headers=HX, data={
            "cust_no": "C1001", "sku": "HAM-01", "qty": "3", "preview_total_cents": "4218"})

    def test_stock_consumed_after_preview_rejects_confirmation(self):
        self.execute("UPDATE products SET stock=3 WHERE sku='HAM-01'")
        self.preview()
        competing = self.client.post("/api/orders", json={"cust_no": "C1002", "sku": "HAM-01", "qty": "3"})
        self.assertEqual(competing.status_code, 201)
        before = self.snapshot()
        r = self.confirm()
        self.assertEqual(r.status_code, 409)
        self.assertIn("INSUFFICIENT STOCK", r.text)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.stock("HAM-01"), 0)

    def test_credit_consumed_after_preview_rejects_confirmation(self):
        self.execute("UPDATE customers SET credit_lim=100 WHERE cust_no='C1001'")
        self.preview()
        competing = self.client.post("/api/orders", json={"cust_no": "C1001", "sku": "HAM-01", "qty": "3"})
        self.assertEqual(competing.status_code, 201)
        before = self.snapshot()
        r = self.confirm()
        self.assertEqual(r.status_code, 409)
        self.assertIn("CREDIT LIMIT EXCEEDED", r.text)
        self.assertEqual(self.snapshot(), before)

    def test_price_change_after_preview_saves_current_oracle_total(self):
        self.preview()
        self.execute("UPDATE products SET price_cents=1399 WHERE sku='HAM-01'")
        twin = LegacyTwin(os.path.join(self.tmp.name, "legacy.db"))
        twin.enter_order("C1001", "HAM-01", "3")
        r = self.confirm()
        self.assertEqual(r.status_code, 201, r.text)
        self.assertIn("current price", r.text)
        self.assertEqual(self.snapshot(), twin.snapshot())
        self.assertNotEqual(self.query("SELECT total_cents FROM orders WHERE order_no=5")[0][0], 4218)
