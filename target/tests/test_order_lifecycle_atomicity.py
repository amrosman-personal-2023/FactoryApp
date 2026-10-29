"""Atomicity of order status changes against real isolated SQLite fixtures.

Faults are injected with test-only SQLite triggers; there is no production
fault-control endpoint.
"""

import sqlite3
import threading
import time
from unittest import mock

from fastapi.testclient import TestClient
from helpers import WebTestCase, app, appdb

from app.services import lifecycle

HX = {"HX-Request": "true"}
SEED_OPEN = ((1, "HAM-01", 3, "N"), (2, "SAW-02", 2, "P"))


class LifecycleAtomicityTests(WebTestCase):
    def race(self, paths):
        """POST each path from its own client at the same moment."""
        barrier = threading.Barrier(len(paths))
        results = [None] * len(paths)

        def run(i, path):
            client = TestClient(app)
            barrier.wait()
            results[i] = client.post(path)

        threads = [threading.Thread(target=run, args=(i, p)) for i, p in enumerate(paths)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        return results

    def order_row(self, order_no):
        return self.query("SELECT status FROM orders WHERE order_no=?", order_no)[0][0]

    def test_competing_cancels_restock_once(self):
        for order_no, sku, qty, _ in SEED_OPEN:
            with self.subTest(order_no=order_no):
                appdb.init(reset=True).close()
                before = self.stock(sku)
                results = self.race([f"/api/orders/{order_no}/cancel"] * 2)
                self.assertEqual(sorted(r.status_code for r in results), [200, 409])
                loser = [r for r in results if r.status_code == 409][0].json()
                self.assertEqual(loser["detail"], lifecycle.TRANSITION_NOT_ALLOWED)
                self.assertEqual(loser["order"]["status"], "C")
                self.assertEqual(self.order_row(order_no), "C")
                self.assertEqual(self.stock(sku), before + qty)

    def test_competing_web_and_api_cancels_restock_once(self):
        before = self.stock("HAM-01")
        barrier = threading.Barrier(2)
        results = {}

        def web():
            client = TestClient(app)
            barrier.wait()
            results["web"] = client.post("/orders/1/cancel", headers=HX)

        def api():
            client = TestClient(app)
            barrier.wait()
            results["api"] = client.post("/api/orders/1/cancel")

        threads = [threading.Thread(target=web), threading.Thread(target=api)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(sorted(r.status_code for r in results.values()), [200, 409])
        self.assertEqual(self.order_row(1), "C")
        self.assertEqual(self.stock("HAM-01"), before + 3)

    def test_competing_pick_and_cancel_end_cancelled_with_one_restock(self):
        before = self.stock("HAM-01")
        results = self.race(["/api/orders/1/pick", "/api/orders/1/cancel"])
        pick, cancel = results
        self.assertEqual(cancel.status_code, 200, cancel.text)
        self.assertIn(pick.status_code, (200, 409))
        self.assertEqual(self.order_row(1), "C")
        self.assertEqual(self.stock("HAM-01"), before + 3)

    def test_status_check_waits_for_the_write_lock(self):
        """A writer holding the lock ships order 2; cancel must then see S and do nothing."""
        before = self.stock("SAW-02")
        blocker = appdb.connect()
        try:
            blocker.execute("BEGIN IMMEDIATE")
            blocker.execute("UPDATE orders SET status='S' WHERE order_no=2")
            outcome = {}

            def run():
                try:
                    outcome["result"] = lifecycle.apply_action(2, "cancel")
                except Exception as exc:  # noqa: BLE001 - recorded for assertion
                    outcome["error"] = exc

            t = threading.Thread(target=run)
            t.start()
            time.sleep(0.3)
            self.assertTrue(
                t.is_alive(), "cancel read the status without waiting for the write lock"
            )
            blocker.commit()
        finally:
            blocker.close()
        t.join(10)
        self.assertIsInstance(outcome.get("error"), lifecycle.TransitionRejected)
        self.assertEqual(outcome["error"].order.status, "S")
        self.assertEqual(self.order_row(2), "S")
        self.assertEqual(self.stock("SAW-02"), before)

    def test_failure_between_status_and_stock_writes_rolls_back(self):
        for order_no, sku, qty, status in SEED_OPEN:
            with self.subTest(order_no=order_no):
                appdb.init(reset=True).close()
                self.execute(
                    "CREATE TRIGGER fail_restock BEFORE UPDATE ON products "
                    "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
                )
                before = self.snapshot()
                start = self.stock(sku)
                r = self.client.post(f"/api/orders/{order_no}/cancel")
                self.assertEqual(r.status_code, 503)
                self.assertEqual(r.json()["detail"], lifecycle.NOT_UPDATED)
                self.assertEqual(self.snapshot(), before)

                page = self.client.post(f"/orders/{order_no}/cancel", headers=HX)
                self.assertEqual(page.status_code, 503)
                self.assertIn(lifecycle.NOT_UPDATED, page.text)
                self.assertNotIn("CANCELLED</strong>", page.text)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.order_row(order_no), status)

                self.execute("DROP TRIGGER fail_restock")
                ok = self.client.post(f"/api/orders/{order_no}/cancel")
                self.assertEqual(ok.status_code, 200, ok.text)
                self.assertEqual(self.order_row(order_no), "C")
                self.assertEqual(self.stock(sku), start + qty)
                self.assertEqual(
                    self.client.post(f"/api/orders/{order_no}/cancel").status_code, 409
                )
                self.assertEqual(self.stock(sku), start + qty)

    def test_failure_on_status_write_leaves_everything_unchanged(self):
        self.execute(
            "CREATE TRIGGER fail_status BEFORE UPDATE ON orders "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
        before = self.snapshot()
        for action, order_no in (("pick", 1), ("ship", 2), ("cancel", 1)):
            r = self.client.post(f"/api/orders/{order_no}/{action}")
            self.assertEqual(r.status_code, 503, action)
        self.assertEqual(self.snapshot(), before)

    def test_status_write_that_changes_no_row_rolls_back(self):
        self.execute(
            "CREATE TRIGGER skip_status BEFORE UPDATE ON orders BEGIN SELECT RAISE(IGNORE); END"
        )
        before = self.snapshot()
        r = self.client.post("/api/orders/1/cancel")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"], lifecycle.NOT_UPDATED)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.stock("HAM-01"), 117)

    def test_lock_wait_timeout_is_a_controlled_failure(self):
        blocker = appdb.connect()
        real_connect = sqlite3.connect
        try:
            blocker.execute("BEGIN IMMEDIATE")
            with mock.patch.object(
                appdb, "connect", lambda: real_connect(appdb.DB_PATH, timeout=0.1)
            ):
                r = self.client.post("/api/orders/1/cancel")
        finally:
            blocker.rollback()
            blocker.close()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"], lifecycle.NOT_UPDATED)
        self.assertEqual(self.order_row(1), "N")
        self.assertEqual(self.stock("HAM-01"), 117)
