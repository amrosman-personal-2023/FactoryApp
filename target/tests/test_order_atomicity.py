"""Atomicity of order creation against real isolated SQLite fixtures.

Faults are injected with test-only SQLite triggers; there is no production
fault-control endpoint.
"""

import sqlite3
import threading
import time
from unittest import mock

from fastapi.testclient import TestClient
from helpers import WebTestCase, app, appdb

from app.services import orders


class OrderAtomicityTests(WebTestCase):
    def race(self, bodies):
        """Fire requests from separate clients at the same moment."""
        barrier = threading.Barrier(len(bodies))
        results = [None] * len(bodies)

        def run(i, body):
            client = TestClient(app)
            barrier.wait()
            results[i] = client.post("/api/orders", json=body)

        threads = [threading.Thread(target=run, args=(i, b)) for i, b in enumerate(bodies)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        return results

    def test_competing_orders_cannot_oversell(self):
        self.execute("UPDATE products SET stock=10 WHERE sku='DRL-03'")
        results = self.race(
            [
                {"cust_no": "C1001", "sku": "DRL-03", "qty": "6"},
                {"cust_no": "C1003", "sku": "DRL-03", "qty": "6"},
            ]
        )
        codes = sorted(r.status_code for r in results)
        self.assertEqual(codes, [201, 409])
        loser = [r for r in results if r.status_code == 409][0]
        self.assertEqual(loser.json()["detail"], orders.INSUFFICIENT_STOCK)
        self.assertEqual(self.stock("DRL-03"), 4)
        self.assertEqual(
            len(self.query("SELECT 1 FROM orders WHERE sku='DRL-03' AND status='N'")), 1
        )

    def test_competing_orders_cannot_over_credit(self):
        # $100 limit, 4218 already open: room for exactly one more 4218 order.
        self.execute("UPDATE customers SET credit_lim=100 WHERE cust_no='C1001'")
        body = {"cust_no": "C1001", "sku": "HAM-01", "qty": "3"}
        results = self.race([body, body])
        self.assertEqual(sorted(r.status_code for r in results), [201, 409])
        loser = [r for r in results if r.status_code == 409][0]
        self.assertEqual(loser.json()["detail"], orders.CREDIT_EXCEEDED)
        self.assertEqual(
            self.query(
                "SELECT SUM(total_cents) FROM orders WHERE cust_no='C1001' AND status IN ('N','P')"
            )[0][0],
            8436,
        )
        self.assertEqual(self.stock("HAM-01"), 114)

    def test_authoritative_reads_wait_for_the_write_lock(self):
        """A writer holding the lock changes stock; creation must see the new value."""
        blocker = appdb.connect()
        try:
            blocker.execute("BEGIN IMMEDIATE")
            blocker.execute("UPDATE products SET stock=2 WHERE sku='HAM-01'")
            outcome = {}

            def run():
                try:
                    outcome["result"] = orders.create_order("C1001", "HAM-01", "3")
                except Exception as exc:  # noqa: BLE001 - recorded for assertion
                    outcome["error"] = exc

            t = threading.Thread(target=run)
            t.start()
            time.sleep(0.3)
            self.assertTrue(t.is_alive(), "creation read stock without waiting for the write lock")
            blocker.commit()
        finally:
            blocker.close()
        t.join(10)
        self.assertIsInstance(outcome.get("error"), orders.OrderRejected)
        self.assertEqual(outcome["error"].reason, orders.INSUFFICIENT_STOCK)
        self.assertEqual(self.stock("HAM-01"), 2)
        self.assertEqual(len(self.query("SELECT 1 FROM orders")), 4)

    def test_failure_between_order_insert_and_stock_update_rolls_back(self):
        self.execute(
            "CREATE TRIGGER fail_stock BEFORE UPDATE ON products "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
        before = self.snapshot()
        r = self.client.post("/api/orders", json={"cust_no": "C1001", "sku": "HAM-01", "qty": "3"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"], orders.NOT_CREATED)
        self.assertEqual(self.snapshot(), before)

        page = self.client.post(
            "/orders/confirm",
            headers={"HX-Request": "true"},
            data={"cust_no": "C1001", "sku": "HAM-01", "qty": "3"},
        )
        self.assertEqual(page.status_code, 503)
        self.assertIn(orders.NOT_CREATED, page.text)
        self.assertNotIn("CREATED</strong>", page.text.replace(orders.NOT_CREATED, ""))
        self.assertEqual(self.snapshot(), before)

    def test_lock_wait_timeout_is_a_controlled_rejection(self):
        blocker = appdb.connect()
        real_connect = sqlite3.connect
        try:
            blocker.execute("BEGIN IMMEDIATE")
            with mock.patch.object(
                appdb, "connect", lambda: real_connect(appdb.DB_PATH, timeout=0.1)
            ):
                r = self.client.post(
                    "/api/orders", json={"cust_no": "C1001", "sku": "HAM-01", "qty": "3"}
                )
        finally:
            blocker.rollback()
            blocker.close()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"], orders.NOT_CREATED)
        self.assertEqual(len(self.query("SELECT 1 FROM orders")), 4)
        self.assertEqual(self.stock("HAM-01"), 117)

    def test_failure_on_order_insert_leaves_stock_unchanged(self):
        self.execute(
            "CREATE TRIGGER fail_insert BEFORE INSERT ON orders "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
        before = self.snapshot()
        r = self.client.post("/api/orders", json={"cust_no": "C1001", "sku": "HAM-01", "qty": "3"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self.snapshot(), before)
