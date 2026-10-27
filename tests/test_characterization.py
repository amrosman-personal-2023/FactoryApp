"""Characterization tests for legacy/ordersys.py.

These pin what the legacy app DOES today (including quirks), by driving the
real curses handlers with a fake screen and scripted keystrokes against a
throwaway database. They are the parity oracle for the migration: the new
API must produce the same results for the same business inputs.

Run: python3 -m unittest discover -s tests -v   (pytest also works)
"""
import curses
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "legacy"))

import db  # noqa: E402
import ordersys  # noqa: E402

ENTER, ESC = 10, 27


def keys(text):
    return [ord(c) for c in text]


class FakeScreen:
    """Minimal curses window: scripted input, recorded output."""

    def __init__(self, script):
        self.script = list(script)
        self.out = []

    def getch(self):
        if not self.script:
            raise AssertionError("test script ran out of keys (handler wanted more input)")
        return self.script.pop(0)

    def addstr(self, y, x, text, attr=0):
        self.out.append(text)

    def getmaxyx(self):
        return (24, 80)

    def erase(self):
        pass

    def move(self, y, x):
        pass

    def refresh(self):
        pass

    @property
    def text(self):
        return "\n".join(self.out)


class LegacyTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(db, "DB_PATH", os.path.join(self.tmp.name, "t.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        mock.patch.object(curses, "curs_set", lambda *a: 0).start()
        self.addCleanup(mock.patch.stopall)
        db.init(reset=True)

    def q(self, sql, *args):
        conn = db.connect()
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def run_screen(self, handler, script):
        scr = FakeScreen(script)
        handler(scr)
        return scr

    def enter_order(self, cust, sku, qty, confirm="Y"):
        script = (keys(cust) + [ENTER] + keys(sku) + [ENTER] + keys(qty) + [ENTER]
                  + keys(confirm) + [ENTER, ord(" ")])
        return self.run_screen(ordersys.order_entry_screen, script)


class PricingTests(LegacyTestCase):
    def test_standard_tier_truncates_tax(self):
        # 3 x 12.99 = 3897; tax int(3897*.0825)=321 (a rounding port gives 322)
        self.enter_order("C1001", "HAM-01", "3")
        self.assertEqual(self.q("SELECT total_cents FROM orders ORDER BY order_no DESC LIMIT 1")[0][0], 4218)

    def test_platinum_volume_discount_is_additive(self):
        # Platinum 10% + volume 3% = 13% once (not 10% then 3%)
        scr = self.enter_order("C1003", "NAI-04", "100")
        self.assertIn("(13%)", scr.text)
        self.assertEqual(self.q("SELECT total_cents FROM orders ORDER BY order_no DESC LIMIT 1")[0][0], 56412)

    def test_gold_discount_truncates(self):
        # Gold 5% of 2 x 24.75 = 4950 -> 247 (int of 247.5), net 4703, tax 387
        self.enter_order("C1002", "SAW-02", "2")
        self.assertEqual(self.q("SELECT total_cents FROM orders ORDER BY order_no DESC LIMIT 1")[0][0], 5090)

    def test_input_is_uppercased(self):
        self.enter_order("c1001", "ham-01", "3")
        self.assertEqual(len(self.q("SELECT 1 FROM orders")), 5)

    def test_field_width_silently_truncates_quantity(self):
        # qty field is 4 wide: "00035" is read as "0003"
        self.enter_order("C1001", "HAM-01", "00035")
        self.assertEqual(self.q("SELECT qty FROM orders ORDER BY order_no DESC LIMIT 1")[0][0], 3)


class StockAndCreditTests(LegacyTestCase):
    def test_stock_decrements_on_create(self):
        before = self.q("SELECT stock FROM products WHERE sku='HAM-01'")[0][0]
        self.enter_order("C1001", "HAM-01", "3")
        self.assertEqual(self.q("SELECT stock FROM products WHERE sku='HAM-01'")[0][0], before - 3)

    def test_insufficient_stock_rejected(self):
        scr = self.enter_order("C1001", "DRL-03", "16", confirm="")
        self.assertIn("INSUFFICIENT STOCK", scr.text)
        self.assertEqual(len(self.q("SELECT 1 FROM orders")), 4)

    def test_credit_limit_rejects(self):
        conn = db.connect()
        conn.execute("UPDATE customers SET credit_lim=100 WHERE cust_no='C1001'")
        conn.commit()
        conn.close()
        # open N order 4218 + new 7030 = 11248 > 10000
        script = (keys("C1001") + [ENTER] + keys("HAM-01") + [ENTER] + keys("5") + [ENTER, ord(" ")])
        scr = self.run_screen(ordersys.order_entry_screen, script)
        self.assertIn("CREDIT LIMIT EXCEEDED", scr.text)
        self.assertEqual(len(self.q("SELECT 1 FROM orders")), 4)

    def test_shipped_orders_do_not_count_toward_credit(self):
        conn = db.connect()
        conn.execute("UPDATE customers SET credit_lim=100 WHERE cust_no='C1001'")
        conn.execute("INSERT INTO orders (cust_no,sku,qty,total_cents,status) VALUES ('C1001','HAM-01',1,9000,'S')")
        conn.commit()
        conn.close()
        # exposure = N 4218 only; + new 4218 = 8436 <= 10000 -> accepted
        self.enter_order("C1001", "HAM-01", "3")
        self.assertEqual(len(self.q("SELECT 1 FROM orders")), 6)


class StatusFlowTests(LegacyTestCase):
    def press(self, downs, letters):
        script = [curses.KEY_DOWN] * downs + keys(letters) + [ESC]
        self.run_screen(ordersys.order_list_screen, script)

    def status(self, order_no):
        return self.q("SELECT status FROM orders WHERE order_no=?", order_no)[0][0]

    def test_invalid_transition_is_silent_noop(self):
        self.press(3, "S")           # order 1 is N; S needs P first
        self.assertEqual(self.status(1), "N")

    def test_new_to_picked_to_shipped(self):
        self.press(3, "PS")
        self.assertEqual(self.status(1), "S")

    def test_cannot_cancel_shipped(self):
        self.press(1, "C")           # order 3 is already shipped
        self.assertEqual(self.status(3), "S")

    def test_cancel_restocks(self):
        before = self.q("SELECT stock FROM products WHERE sku='SAW-02'")[0][0]
        self.press(2, "C")           # order 2 is P, qty 2
        self.assertEqual(self.status(2), "C")
        self.assertEqual(self.q("SELECT stock FROM products WHERE sku='SAW-02'")[0][0], before + 2)

    def test_empty_list_message(self):
        conn = db.connect()
        conn.execute("DELETE FROM orders")
        conn.commit()
        conn.close()
        scr = self.run_screen(ordersys.order_list_screen, [ESC])
        self.assertIn("NO ORDERS ON FILE", scr.text)


class ReportTests(LegacyTestCase):
    def test_zero_order_customer_still_listed(self):
        # C1004 has only a cancelled order: listed with 0 orders and 0.00
        scr = self.run_screen(ordersys.report_screen, [ESC])
        line = [t for t in scr.out if "DELTA TOOLWORKS" in t][0]
        self.assertRegex(line, r"DELTA TOOLWORKS\s+0\s+0\.00")

    def test_grand_total_excludes_cancelled(self):
        scr = self.run_screen(ordersys.report_screen, [ESC])
        # 4218 + 5090 + 56412 = 65720 -> 657.20
        self.assertTrue(any("GRAND TOTAL" in t and "657.20" in t for t in scr.out))


class SearchTests(LegacyTestCase):
    def search(self, term):
        script = keys(term) + [ENTER, ord(" ")]
        return self.run_screen(ordersys.search_screen, script)

    def test_name_search(self):
        scr = self.search("ACME")
        self.assertIn("ACME HARDWARE", scr.text)
        self.assertNotIn("CONTOSO", scr.text)

    def test_sql_injection_present_in_legacy(self):
        # Documents the planted vulnerability; the migrated app must NOT keep it.
        scr = self.search("' OR '1'='1")
        for name in ("ACME HARDWARE", "CONTOSO SUPPLY", "DELTA TOOLWORKS"):
            self.assertIn(name, scr.text)


if __name__ == "__main__":
    unittest.main()
