"""Run real legacy screen handlers against a copy of a target fixture.

New boundary cases compare the target with what the legacy handler actually
does on an identical disposable database, instead of hand-computed values.
The keystroke harness is reused from the frozen tests/test_characterization.py.
"""
import curses
import os
import shutil
import sqlite3
import sys
from unittest import mock

REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_DIR, "tests"))

from test_characterization import ENTER, ESC, FakeScreen, keys  # noqa: E402

import db as legacy_db  # noqa: E402  (legacy/db.py, put on sys.path by test_characterization)
import ordersys  # noqa: E402

from app import db as appdb  # noqa: E402


class LegacyTwin:
    """A legacy-side copy of the current target database file."""

    def __init__(self, path):
        shutil.copyfile(appdb.DB_PATH, path)
        self.path = path

    def enter_order(self, cust, sku, qty, confirm="Y"):
        script = (keys(cust) + [ENTER] + keys(sku) + [ENTER] + keys(qty) + [ENTER]
                  + keys(confirm) + [ENTER, ord(" ")])
        return self.run(ordersys.order_entry_screen, script)

    def order_list(self, downs=0, letters=""):
        """Legacy order_list_screen: move the cursor down, press keys, then ESC."""
        script = [curses.KEY_DOWN] * downs + keys(letters) + [ESC]
        return self.run(ordersys.order_list_screen, script)

    def run(self, handler, script):
        scr = FakeScreen(script)
        opened = []

        def connect():
            opened.append(sqlite3.connect(self.path))
            return opened[-1]

        # The legacy handler never closes its connection; close it here.
        try:
            with mock.patch.object(legacy_db, "connect", connect), \
                    mock.patch.object(curses, "curs_set", lambda *a: 0):
                handler(scr)
        finally:
            for conn in opened:
                conn.close()
        return scr.text

    def snapshot(self):
        conn = sqlite3.connect(self.path)
        try:
            return (conn.execute("SELECT * FROM orders ORDER BY order_no").fetchall(),
                    conn.execute("SELECT * FROM products ORDER BY sku").fetchall())
        finally:
            conn.close()


def legacy_price(unit_cents, qty, tier):
    """The legacy float pricing expressions, verbatim, for unit-level comparison."""
    gross = unit_cents * qty
    disc_pct = {"G": 5, "P": 10}.get(tier, 0)
    if qty >= 100:
        disc_pct += 3
    discount = int(gross * disc_pct / 100)
    net = gross - discount
    tax = int(net * ordersys.TAX_RATE)
    return disc_pct, discount, tax, net + tax
