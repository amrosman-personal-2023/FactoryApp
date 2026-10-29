"""API parity tests: the new API must answer with the same values the legacy
system produces for the same inputs (AGENTS.md convention).

Rules for missions extending this file:
- Copy expected values from tests/test_characterization.py (the legacy oracle)
  or compute them from the seed data in app/db.py. NEVER invent or edit an
  expected value to make a test pass - that is a parity violation and the
  migration-reviewer droid will catch it.
- One legacy business rule per test, named after the rule.

Covered: customer lookup + open credit (reference endpoint); order entry
pricing/discount/tax, input width, stock check/decrement and credit-limit
rejection (Order*ParityTests below); order list order, status flow N->P->S,
cancel rules and restock on cancel (OrderList/OrderStatusFlow ParityTests).
TO ADD (mission workspace): sales report.
"""
from helpers import WebTestCase

# Expected values computed from the seed data in app/db.py (same as legacy):
# open = SUM(total_cents) over orders with status N or P
SEED_OPEN_CENTS = {
    "C1001": 4218,   # one N order
    "C1002": 5090,   # one P order
    "C1003": 0,      # only S order - shipped does not count toward credit
    "C1004": 0,      # only C order - cancelled
}


class CustomerLookupParityTests(WebTestCase):
    """Parity for legacy customer_screen (the reference endpoint)."""

    def test_lookup_matches_seed_for_every_customer(self):
        expected = {
            "C1001": ("ACME HARDWARE", "DALLAS", "STANDARD", 500000),
            "C1002": ("BRIGHT FUTURES LLC", "AUSTIN", "GOLD", 2000000),
            "C1003": ("CONTOSO SUPPLY", "HOUSTON", "PLATINUM", 5000000),
            "C1004": ("DELTA TOOLWORKS", "EL PASO", "STANDARD", 250000),
        }
        for cust_no, (name, city, tier, credit_cents) in expected.items():
            r = self.client.get(f"/api/customers/{cust_no}")
            self.assertEqual(r.status_code, 200, cust_no)
            body = r.json()
            self.assertEqual(body["cust_no"], cust_no)
            self.assertEqual(body["name"], name, cust_no)
            self.assertEqual(body["city"], city, cust_no)
            self.assertEqual(body["tier"], tier, cust_no)
            self.assertEqual(body["credit_lim_cents"], credit_cents, cust_no)
            self.assertEqual(body["open_total_cents"], SEED_OPEN_CENTS[cust_no], cust_no)

    def test_unknown_customer_is_404(self):
        r = self.client.get("/api/customers/C9999")
        self.assertEqual(r.status_code, 404)

    def test_credit_money_is_integer_cents(self):
        """AGENTS.md convention: money as integer cents, never floats."""
        body = self.client.get("/api/customers/C1002").json()
        self.assertIsInstance(body["credit_lim_cents"], int)
        self.assertIsInstance(body["open_total_cents"], int)


# ---------------------------------------------------------------------------
# Order entry parity (legacy order_entry_screen). Pinned values come from
# tests/test_characterization.py; new boundary cases are compared against the
# real legacy handler run on an identical copy of the fixture (LegacyTwin).
# ---------------------------------------------------------------------------
import os  # noqa: E402

from app.services import pricing  # noqa: E402
from legacy_oracle import LegacyTwin, legacy_price  # noqa: E402


class OrderEntryTestCase(WebTestCase):
    def order(self, cust, sku, qty, **extra):
        return self.client.post("/api/orders", json={"cust_no": cust, "sku": sku, "qty": qty, **extra})

    def twin(self):
        self.twins = getattr(self, "twins", 0) + 1
        return LegacyTwin(os.path.join(self.tmp.name, "legacy-%d.db" % self.twins))

    def assert_matches_legacy(self, cust, sku, qty):
        """Run the same raw input through legacy and target on identical fixtures."""
        twin = self.twin()
        legacy_text = twin.enter_order(cust, sku, qty)
        r = self.order(cust, sku, qty)
        self.assertEqual(self.snapshot(), twin.snapshot(), (cust, sku, qty))
        return r, legacy_text

    def assert_rejected_without_write(self, r, status, detail):
        self.assertEqual(r.status_code, status, r.text)
        self.assertEqual(r.json()["detail"], detail)


class OrderPricingParityTests(OrderEntryTestCase):
    def test_standard_tier_truncates_tax(self):
        # legacy test_standard_tier_truncates_tax: total 4218 (tax 321, not 322)
        r = self.order("C1001", "HAM-01", "3")
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["total_cents"], 4218)
        self.assertEqual((body["gross_cents"], body["discount_pct"], body["tax_cents"]), (3897, 0, 321))
        self.assertEqual(body["status"], "N")
        last = self.query("SELECT order_no,cust_no,sku,qty,total_cents,status FROM orders "
                          "ORDER BY order_no DESC LIMIT 1")[0]
        self.assertEqual(last, (body["order_no"], "C1001", "HAM-01", 3, 4218, "N"))

    def test_gold_discount_truncates(self):
        # legacy test_gold_discount_truncates: 4950 -> discount 247, tax 387, total 5090
        body = self.order("C1002", "SAW-02", "2").json()
        self.assertEqual((body["discount_pct"], body["discount_cents"], body["tax_cents"]), (5, 247, 387))
        self.assertEqual(body["total_cents"], 5090)

    def test_platinum_volume_discount_is_additive(self):
        # legacy test_platinum_volume_discount_is_additive: 13%, total 56412
        body = self.order("C1003", "NAI-04", "100").json()
        self.assertEqual(body["discount_pct"], 13)
        self.assertEqual(body["total_cents"], 56412)

    def test_tier_and_volume_break_match_legacy_at_99_and_100(self):
        for cust, tier_pct in (("C1001", 0), ("C1002", 5), ("C1003", 10)):
            for qty, pct in (("99", tier_pct), ("100", tier_pct + 3)):
                with self.subTest(cust=cust, qty=qty):
                    self.execute("DELETE FROM orders WHERE order_no>4")
                    self.execute("UPDATE products SET stock=400 WHERE sku='NAI-04'")
                    r, legacy_text = self.assert_matches_legacy(cust, "NAI-04", qty)
                    self.assertEqual(r.status_code, 201, r.text)
                    self.assertEqual(r.json()["discount_pct"], pct)
                    self.assertIn("(%d%%)" % pct, legacy_text)

    def test_api_money_is_integer_cents(self):
        body = self.order("C1002", "SAW-02", "2").json()
        for key in ("gross_cents", "discount_cents", "tax_cents", "total_cents"):
            self.assertIs(type(body[key]), int, key)

    def test_api_computes_rather_than_trusts_client_totals(self):
        body = self.order("C1001", "HAM-01", "3", total_cents=1, discount_pct=99).json()
        self.assertEqual(body["total_cents"], 4218)
        self.assertEqual(self.query("SELECT total_cents FROM orders WHERE order_no=?",
                                    body["order_no"])[0][0], 4218)

    def test_integer_pricing_matches_legacy_float_expressions(self):
        for tier in ("S", "G", "P"):
            for qty in (1, 2, 3, 7, 99, 100, 101, 999, 9999):
                for unit in range(1, 20000, 7):
                    p = pricing.price(unit, qty, tier)
                    self.assertEqual((p.discount_pct, p.discount_cents, p.tax_cents, p.total_cents),
                                     legacy_price(unit, qty, tier), (unit, qty, tier))


class OrderInputParityTests(OrderEntryTestCase):
    def test_input_is_uppercased(self):
        # legacy test_input_is_uppercased: lowercase input creates the 5th order
        r = self.order("c1001", "ham-01", "3")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(len(self.query("SELECT 1 FROM orders")), 5)
        self.assertEqual(self.query("SELECT cust_no,sku FROM orders ORDER BY order_no DESC LIMIT 1")[0],
                         ("C1001", "HAM-01"))

    def test_field_width_silently_truncates_quantity(self):
        # legacy test_field_width_silently_truncates_quantity: "00035" -> qty 3
        r = self.order("C1001", "HAM-01", "00035")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.query("SELECT qty,total_cents FROM orders ORDER BY order_no DESC LIMIT 1")[0],
                         (3, 4218))

    def test_integer_qty_follows_four_character_field(self):
        self.assertEqual(pricing.parse_qty(12345), 1234)
        self.assertEqual(pricing.parse_qty("00035"), 3)
        r = self.order("C1001", "NAI-04", 12345)
        self.assert_rejected_without_write(r, 409, "INSUFFICIENT STOCK")

    def test_control_keys_replay_like_legacy_prompt(self):
        # Legacy ENTER ends the field; target text after a newline is never read.
        for raw, legacy_keys in (("c1001\nX", "c1001"), ("c1002\x7f1", "c1002\x7f1"),
                                 ("c10\x0801\x01", "c10\x0801")):
            with self.subTest(raw=raw):
                self.execute("DELETE FROM orders WHERE order_no>4")
                self.execute("UPDATE products SET stock=117 WHERE sku='HAM-01'")
                twin = self.twin()
                twin.enter_order(legacy_keys, "HAM-01", "1")
                self.order(raw, "HAM-01", "1")
                self.assertEqual(self.snapshot(), twin.snapshot())

    def test_escape_character_is_ignored_text_not_a_cancel_key(self):
        # Intentional difference: legacy ESC/F12 is a terminal key that abandons
        # entry. Submitted text has no key events; Decline is the web cancel.
        self.assertEqual(pricing.field("c10\x1b01", pricing.CUST_WIDTH), "C1001")
        r = self.order("C1001\x1b", "HAM-01\x1b", "3\x1b")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual((r.json()["cust_no"], r.json()["sku"], r.json()["qty"]), ("C1001", "HAM-01", 3))

    def test_oversized_raw_fields_are_rejected_before_replay(self):
        before = self.snapshot()
        limit = pricing.MAX_RAW_CHARS
        at_limit = self.order("C1001" + "\x08" * (limit - 5), "HAM-01", "3")
        self.assert_rejected_without_write(at_limit, 404, "INVALID CUSTOMER")
        int_at_limit = self.order("C1001", "NAI-04", 10 ** limit - 1)
        self.assert_rejected_without_write(int_at_limit, 409, "INSUFFICIENT STOCK")
        for qty in (10 ** limit, -10 ** (limit - 1)):
            self.assertEqual(self.order("C1001", "HAM-01", qty).status_code, 422)
        for body in ({"cust_no": "C1001" + "X" * limit, "sku": "HAM-01", "qty": "3"},
                     {"cust_no": "C1001", "sku": "HAM-01" + "X" * limit, "qty": "3"},
                     {"cust_no": "C1001", "sku": "HAM-01", "qty": "3" * (limit + 1)}):
            self.assertEqual(self.client.post("/api/orders", json=body).status_code, 422)
            for page in ("/orders/preview", "/orders/confirm"):
                self.assertEqual(self.client.post(page, data=body).status_code, 422, page)
        self.assertEqual(self.snapshot(), before)

    def test_every_rejection_reason_has_an_http_status(self):
        from app.routers.orders import REJECTION_STATUS
        from app.services import orders
        reasons = {orders.INVALID_CUSTOMER, orders.INVALID_SKU, orders.INVALID_QTY,
                   orders.INSUFFICIENT_STOCK, orders.CREDIT_EXCEEDED, orders.NOT_CREATED}
        self.assertEqual(set(REJECTION_STATUS), reasons)
        self.assertEqual(orders.OrderUnavailable.reason, orders.NOT_CREATED)

    def test_eight_character_identifier_width_matches_legacy(self):
        self.execute("INSERT INTO customers VALUES ('WIDE1234','WIDE CO','TYLER','G',5000)")
        self.execute("INSERT INTO customers VALUES ('WIDE12345','OVERWIDTH CO','TYLER','S',5000)")
        self.execute("INSERT INTO products VALUES ('PART5678','WIDE PART',1000,50)")
        self.execute("INSERT INTO products VALUES ('PART56789','OVERWIDTH PART',1,50)")
        r, _ = self.assert_matches_legacy("wide12345xyz", "part56789zz", "2")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual((r.json()["cust_no"], r.json()["sku"]), ("WIDE1234", "PART5678"))
        self.assertEqual(self.query("SELECT COUNT(*) FROM orders WHERE cust_no='WIDE12345' "
                                    "OR sku='PART56789'")[0][0], 0)


class OrderValidationParityTests(OrderEntryTestCase):
    def test_unknown_customer_rejected_without_write(self):
        before = self.snapshot()
        r, legacy_text = self.assert_matches_legacy("C9999", "HAM-01", "3")
        self.assert_rejected_without_write(r, 404, "INVALID CUSTOMER")
        self.assertIn("INVALID CUSTOMER", legacy_text)
        self.assertEqual(self.snapshot(), before)

    def test_unknown_sku_rejected_without_write(self):
        before = self.snapshot()
        r, legacy_text = self.assert_matches_legacy("C1001", "BAD-SKU", "3")
        self.assert_rejected_without_write(r, 404, "INVALID SKU")
        self.assertIn("INVALID SKU", legacy_text)
        self.assertEqual(self.snapshot(), before)

    def test_quantity_must_be_positive_digits_after_truncation(self):
        before = self.snapshot()
        for raw in ("", "0", "-5", "1.5", "abc", "00005", " 3", "3x"):
            with self.subTest(qty=raw):
                r, legacy_text = self.assert_matches_legacy("C1001", "HAM-01", raw)
                self.assert_rejected_without_write(r, 422, "QTY MUST BE A POSITIVE NUMBER")
                self.assertIn("QTY MUST BE A POSITIVE NUMBER", legacy_text)
                self.assertEqual(self.snapshot(), before)
        for raw in (0, -5):
            r = self.order("C1001", "HAM-01", raw)
            self.assert_rejected_without_write(r, 422, "QTY MUST BE A POSITIVE NUMBER")
        self.assertEqual(self.snapshot(), before)

    def test_malformed_body_rejected_without_write(self):
        before = self.snapshot()
        for body in ({}, {"cust_no": "C1001", "sku": "HAM-01"}, {"cust_no": "C1001", "sku": "HAM-01", "qty": 1.5}):
            self.assertEqual(self.client.post("/api/orders", json=body).status_code, 422, body)
        self.assertEqual(self.snapshot(), before)


class OrderStockParityTests(OrderEntryTestCase):
    def test_stock_decrements_on_create(self):
        before = self.stock("HAM-01")
        self.order("C1001", "HAM-01", "3")
        self.assertEqual(self.stock("HAM-01"), before - 3)

    def test_insufficient_stock_rejected(self):
        # legacy test_insufficient_stock_rejected: DRL-03 x16 vs stock 15, 4 orders remain
        before = self.snapshot()
        r = self.order("C1001", "DRL-03", "16")
        self.assert_rejected_without_write(r, 409, "INSUFFICIENT STOCK")
        self.assertEqual(len(self.query("SELECT 1 FROM orders")), 4)
        self.assertEqual(self.snapshot(), before)

    def test_exact_stock_is_allowed_and_matches_legacy(self):
        r, _ = self.assert_matches_legacy("C1001", "DRL-03", "15")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.stock("DRL-03"), 0)


class OrderCreditParityTests(OrderEntryTestCase):
    def test_credit_limit_rejects(self):
        # legacy test_credit_limit_rejects: 4218 open + 7030 new > 10000
        self.execute("UPDATE customers SET credit_lim=100 WHERE cust_no='C1001'")
        before = self.snapshot()
        r = self.order("C1001", "HAM-01", "5")
        self.assert_rejected_without_write(r, 409, "CREDIT LIMIT EXCEEDED - ORDER REJECTED")
        self.assertEqual(len(self.query("SELECT 1 FROM orders")), 4)
        self.assertEqual(self.snapshot(), before)

    def test_shipped_orders_do_not_count_toward_credit(self):
        # legacy test_shipped_orders_do_not_count_toward_credit: 6 orders afterwards
        self.execute("UPDATE customers SET credit_lim=100 WHERE cust_no='C1001'")
        self.execute("INSERT INTO orders (cust_no,sku,qty,total_cents,status) "
                     "VALUES ('C1001','HAM-01',1,9000,'S')")
        r = self.order("C1001", "HAM-01", "3")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(len(self.query("SELECT 1 FROM orders")), 6)

    def test_credit_equality_allowed_one_cent_over_rejected_matches_legacy(self):
        # $100 limit; new HAM-01 x3 order is 4218, so 5782 of open exposure lands exactly on it.
        for status in ("N", "P"):
            for open_cents, accepted in ((5782, True), (5783, False)):
                with self.subTest(status=status, open_cents=open_cents):
                    self.execute("UPDATE customers SET credit_lim=100 WHERE cust_no='C1001'")
                    self.execute("UPDATE orders SET total_cents=?, status=? WHERE order_no=1",
                                 open_cents, status)
                    self.execute("DELETE FROM orders WHERE order_no>4")
                    r, legacy_text = self.assert_matches_legacy("C1001", "HAM-01", "3")
                    self.assertEqual(r.status_code, 201 if accepted else 409, r.text)
                    self.assertEqual("CREDIT LIMIT EXCEEDED" in legacy_text, not accepted)
                    self.execute("UPDATE products SET stock=117 WHERE sku='HAM-01'")

    def test_other_customers_open_orders_do_not_count(self):
        # C1001 limit $100 with no own exposure; C1002's open orders are irrelevant.
        self.execute("UPDATE customers SET credit_lim=100 WHERE cust_no='C1001'")
        self.execute("DELETE FROM orders WHERE cust_no='C1001'")
        self.execute("INSERT INTO orders (cust_no,sku,qty,total_cents,status) "
                     "VALUES ('C1002','HAM-01',1,999999,'N')")
        r, _ = self.assert_matches_legacy("C1001", "HAM-01", "5")
        self.assertEqual(r.status_code, 201, r.text)

    def test_shipped_and_cancelled_orders_do_not_consume_exposure(self):
        self.execute("UPDATE customers SET credit_lim=100 WHERE cust_no='C1001'")
        self.execute("UPDATE orders SET status='S', total_cents=999999 WHERE order_no=1")
        self.execute("INSERT INTO orders (cust_no,sku,qty,total_cents,status) "
                     "VALUES ('C1001','HAM-01',1,999999,'C')")
        r, _ = self.assert_matches_legacy("C1001", "HAM-01", "3")
        self.assertEqual(r.status_code, 201, r.text)


# ---------------------------------------------------------------------------
# Order list and lifecycle parity (legacy order_list_screen). Expected
# outcomes come from the frozen StatusFlowTests or from the real legacy
# handler run on an identical copy of the fixture (LegacyTwin.order_list).
# ---------------------------------------------------------------------------
import re  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from helpers import app, appdb  # noqa: E402

LEGACY_LIST_ROW = re.compile(r"^(\d+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(\d+\.\d\d)\s+([NPSC])$")
ACTION_KEYS = {"pick": "P", "ship": "S", "cancel": "C"}


def legacy_rows(screen_text):
    """(order_no, cust, sku, qty, total text, status) for each row the legacy list drew."""
    rows = []
    for line in screen_text.split("\n"):
        m = LEGACY_LIST_ROW.match(line)
        if m:
            n, cust, sku, qty, total, status = m.groups()
            rows.append((int(n), cust, sku, int(qty), total, status))
    return rows


class OrderLifecycleTestCase(OrderEntryTestCase):
    def act(self, order_no, action):
        return self.client.post(f"/api/orders/{order_no}/{action}")

    def status(self, order_no):
        return self.query("SELECT status FROM orders WHERE order_no=?", order_no)[0][0]

    def legacy_downs(self, order_no):
        """Cursor moves needed to select order_no in the legacy newest-first list."""
        listed = [r[0] for r in legacy_rows(self.twin().order_list())]
        return listed.index(order_no)


class OrderListParityTests(OrderLifecycleTestCase):
    def test_list_is_newest_first_including_cancelled_matches_legacy(self):
        legacy = legacy_rows(self.twin().order_list())
        body = self.client.get("/api/orders").json()
        self.assertEqual([o["order_no"] for o in body], [4, 3, 2, 1])
        self.assertEqual([r[0] for r in legacy], [o["order_no"] for o in body])
        for row, o in zip(legacy, body):
            self.assertEqual(row[:4] + row[5:],
                             (o["order_no"], o["cust_no"], o["sku"], o["qty"], o["status"]))
            self.assertEqual(row[4], "%d.%02d" % divmod(o["total_cents"], 100))
        self.assertEqual([o["status"] for o in body], ["C", "S", "P", "N"])

    def test_list_matches_stored_rows_in_integer_cents(self):
        body = self.client.get("/api/orders").json()
        stored = self.query("SELECT order_no,cust_no,sku,qty,total_cents,status FROM orders "
                            "ORDER BY order_no DESC")
        self.assertEqual([tuple(o.values()) for o in body], stored)
        for o in body:
            self.assertIs(type(o["total_cents"]), int)

    def test_new_order_is_listed_first(self):
        created = self.order("C1001", "HAM-01", "3").json()
        body = self.client.get("/api/orders").json()
        self.assertEqual(body[0]["order_no"], created["order_no"])
        self.assertEqual([o["order_no"] for o in body], [5, 4, 3, 2, 1])

    def test_status_change_does_not_reorder(self):
        self.assertEqual(self.act(1, "pick").status_code, 200)
        self.assertEqual([o["order_no"] for o in self.client.get("/api/orders").json()], [4, 3, 2, 1])

    def test_empty_list_message(self):
        # legacy test_empty_list_message: "NO ORDERS ON FILE" with no rows
        self.execute("DELETE FROM orders")
        self.assertIn("NO ORDERS ON FILE", self.twin().order_list())
        self.assertEqual(self.client.get("/api/orders").json(), [])
        page = self.client.get("/orders")
        self.assertEqual(page.status_code, 200)
        self.assertIn("NO ORDERS ON FILE", page.text)

    def test_get_single_order(self):
        self.assertEqual(self.client.get("/api/orders/2").json(),
                         {"order_no": 2, "cust_no": "C1002", "sku": "SAW-02", "qty": 2,
                          "total_cents": 5090, "status": "P"})
        r = self.client.get("/api/orders/99")
        self.assertEqual((r.status_code, r.json()["detail"]), (404, "ORDER NOT FOUND"))


class OrderStatusFlowParityTests(OrderLifecycleTestCase):
    def test_invalid_transition_is_silent_noop(self):
        # legacy test_invalid_transition_is_silent_noop: order 1 is N; S needs P first
        before = self.snapshot()
        r = self.act(1, "ship")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["order"]["status"], "N")
        self.assertEqual(self.status(1), "N")
        self.assertEqual(self.snapshot(), before)

    def test_new_to_picked_to_shipped(self):
        # legacy test_new_to_picked_to_shipped
        self.assertEqual(self.act(1, "pick").json()["status"], "P")
        self.assertEqual(self.act(1, "ship").json()["status"], "S")
        self.assertEqual(self.status(1), "S")

    def test_cannot_cancel_shipped(self):
        # legacy test_cannot_cancel_shipped: order 3 is already shipped
        before = self.snapshot()
        self.assertEqual(self.act(3, "cancel").status_code, 409)
        self.assertEqual(self.status(3), "S")
        self.assertEqual(self.snapshot(), before)

    def test_cancel_restocks(self):
        # legacy test_cancel_restocks: order 2 is P, qty 2
        before = self.stock("SAW-02")
        r = self.act(2, "cancel")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.status(2), "C")
        self.assertEqual(self.stock("SAW-02"), before + 2)

    def test_cancel_new_order_restocks_and_matches_legacy(self):
        twin = self.twin()
        twin.order_list(self.legacy_downs(1), "C")
        before = self.stock("HAM-01")
        self.assertEqual(self.act(1, "cancel").status_code, 200)
        self.assertEqual(self.stock("HAM-01"), before + 3)
        self.assertEqual(self.snapshot(), twin.snapshot())

    def test_every_action_status_pairing_matches_legacy(self):
        """All 12 seed order/action pairs: 4 legal changes and 8 silent no-ops."""
        changed = []
        for order_no in (1, 2, 3, 4):
            for action, key in ACTION_KEYS.items():
                with self.subTest(order_no=order_no, action=action):
                    appdb.init(reset=True).close()
                    before = self.snapshot()
                    twin = self.twin()
                    twin.order_list(self.legacy_downs(order_no), key)
                    r = self.act(order_no, action)
                    self.assertEqual(self.snapshot(), twin.snapshot())
                    legacy_changed = twin.snapshot() != before
                    self.assertEqual(r.status_code, 200 if legacy_changed else 409, r.text)
                    if legacy_changed:
                        changed.append((order_no, action))
        self.assertEqual(sorted(changed),
                         [(1, "cancel"), (1, "pick"), (2, "cancel"), (2, "ship")])

    def test_pick_and_ship_do_not_change_stock(self):
        products = self.query("SELECT * FROM products ORDER BY sku")
        self.act(1, "pick")
        self.act(1, "ship")
        self.act(2, "ship")
        self.assertEqual(self.query("SELECT * FROM products ORDER BY sku"), products)

    def test_repeated_cancel_restocks_once_like_legacy(self):
        for order_no, sku, qty in ((1, "HAM-01", 3), (2, "SAW-02", 2)):
            with self.subTest(order_no=order_no):
                appdb.init(reset=True).close()
                before = self.stock(sku)
                twin = self.twin()
                twin.order_list(self.legacy_downs(order_no), "CCC")
                self.assertEqual(self.act(order_no, "cancel").status_code, 200)
                self.assertEqual(self.act(order_no, "cancel").status_code, 409)
                fresh = TestClient(app)
                self.assertEqual(fresh.post(f"/api/orders/{order_no}/cancel").status_code, 409)
                self.assertEqual(self.status(order_no), "C")
                self.assertEqual(self.stock(sku), before + qty)
                self.assertEqual(self.snapshot(), twin.snapshot())

    def test_cancelled_order_does_not_count_toward_credit(self):
        self.act(1, "cancel")
        self.assertEqual(self.client.get("/api/customers/C1001").json()["open_total_cents"], 0)

    def test_unknown_order_or_action_changes_nothing(self):
        before = self.snapshot()
        r = self.act(99, "cancel")
        self.assertEqual((r.status_code, r.json()["detail"]), (404, "ORDER NOT FOUND"))
        self.assertEqual(self.act(1, "delete").status_code, 422)
        self.assertEqual(self.act("abc", "cancel").status_code, 422)
        self.assertEqual(self.snapshot(), before)

    def test_every_lifecycle_failure_has_an_http_status(self):
        from app.routers.order_lifecycle import LIFECYCLE_STATUS
        from app.services import lifecycle
        self.assertEqual(set(LIFECYCLE_STATUS), {lifecycle.ORDER_NOT_FOUND,
                                                 lifecycle.TRANSITION_NOT_ALLOWED,
                                                 lifecycle.NOT_UPDATED,
                                                 lifecycle.LIST_UNAVAILABLE})

    def test_out_of_range_order_numbers_are_rejected_without_write(self):
        before = self.snapshot()
        for order_no in (0, -1, 2 ** 63, 10 ** 20):
            with self.subTest(order_no=order_no):
                self.assertEqual(self.client.get(f"/api/orders/{order_no}").status_code, 422)
                self.assertEqual(self.act(order_no, "cancel").status_code, 422)
                self.assertEqual(self.client.post(f"/orders/{order_no}/cancel").status_code, 422)
        self.assertEqual(self.client.get(f"/api/orders/{2 ** 63 - 1}").status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_cancel_with_missing_product_row_matches_legacy(self):
        # Legacy restocks with an UPDATE that silently matches no product row.
        self.execute("DELETE FROM products WHERE sku='HAM-01'")
        twin = self.twin()
        twin.order_list(self.legacy_downs(1), "C")
        self.assertEqual(self.act(1, "cancel").status_code, 200)
        self.assertEqual(self.snapshot(), twin.snapshot())
