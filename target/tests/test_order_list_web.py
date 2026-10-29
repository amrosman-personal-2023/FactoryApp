"""Order list web page: newest-first rows with Pick/Ship/Cancel controls.

Expected money strings are the seed totals from app/db.py (the legacy seed)
formatted as dollars (4218 -> $42.18). Row actions share the lifecycle
service with POST /api/orders/{order_no}/{action}.
"""
import os
import re
from unittest import mock

from helpers import WebTestCase, appdb

from app.services import lifecycle

HX = {"HX-Request": "true"}
ROW = re.compile(r'<tr id="order-(\d+)" data-order-no="\d+" data-status="([NPSC])">')


class OrderListWebTests(WebTestCase):
    def rows(self, html):
        return [(int(n), s) for n, s in ROW.findall(html)]

    def row_html(self, html, order_no):
        start = html.index(f'<tr id="order-{order_no}"')
        return html[start:html.index("</tr>", start)]

    def act(self, order_no, action, **kw):
        return self.client.post(f"/orders/{order_no}/{action}", **kw)

    def test_page_lists_all_orders_newest_first_with_formatted_totals(self):
        r = self.client.get("/orders")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ORDER LIST", r.text)
        self.assertEqual(self.rows(r.text), [(4, "C"), (3, "S"), (2, "P"), (1, "N")])
        for order_no, cust, sku, total, status in (
                (1, "C1001", "HAM-01", "$42.18", "NEW (N)"),
                (2, "C1002", "SAW-02", "$50.90", "PICKED (P)"),
                (3, "C1003", "NAI-04", "$564.12", "SHIPPED (S)"),
                (4, "C1004", "DRL-03", "$97.41", "CANCELLED (C)")):
            row = self.row_html(r.text, order_no)
            for shown in (cust, sku, total, status):
                self.assertIn(shown, row)

    def test_only_legal_actions_are_offered_per_row(self):
        page = self.client.get("/orders").text
        expected = {1: {"pick", "cancel"}, 2: {"ship", "cancel"}, 3: set(), 4: set()}
        for order_no, actions in expected.items():
            row = self.row_html(page, order_no)
            offered = set(re.findall(rf'hx-post="/orders/{order_no}/(\w+)"', row))
            self.assertEqual(offered, actions, order_no)
            for a in actions:
                self.assertIn(f'aria-label="{a.capitalize()} order {order_no}"', row)
                self.assertIn(f'action="/orders/{order_no}/{a}" method="post"', row)
            self.assertEqual(row.count("/orders/"), 2 * len(actions))

    def test_pick_then_ship_updates_only_that_row_and_keeps_stock(self):
        products = self.query("SELECT * FROM products ORDER BY sku")
        others = self.query("SELECT * FROM orders WHERE order_no<>1 ORDER BY order_no")
        r = self.act(1, "pick", headers=HX)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("<html", r.text)
        self.assertIn("ORDER 1 PICKED", r.text)
        self.assertEqual(self.rows(r.text), [(4, "C"), (3, "S"), (2, "P"), (1, "P")])
        self.assertIn('hx-post="/orders/1/ship"', self.row_html(r.text, 1))
        self.assertEqual(self.client.get("/api/orders/1").json()["status"], "P")
        r = self.act(1, "ship", headers=HX)
        self.assertIn("ORDER 1 SHIPPED", r.text)
        self.assertEqual(self.rows(self.client.get("/orders").text)[3], (1, "S"))
        self.assertEqual(self.client.get("/api/orders/1").json()["status"], "S")
        self.assertEqual(self.query("SELECT * FROM products ORDER BY sku"), products)
        self.assertEqual(self.query("SELECT * FROM orders WHERE order_no<>1 ORDER BY order_no"), others)

    def test_cancel_new_and_picked_orders_restock(self):
        for order_no, sku, qty in ((1, "HAM-01", 3), (2, "SAW-02", 2)):
            with self.subTest(order_no=order_no):
                appdb.init(reset=True).close()
                before = self.stock(sku)
                r = self.act(order_no, "cancel", headers=HX)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertIn(f"ORDER {order_no} CANCELLED", r.text)
                self.assertIn(f"{qty} {sku} returned to stock", r.text)
                self.assertIn((order_no, "C"), self.rows(r.text))
                self.assertEqual(self.stock(sku), before + qty)

    def test_repeated_web_cancel_is_a_visible_noop(self):
        before = self.stock("HAM-01")
        self.act(1, "cancel", headers=HX)
        snap = self.snapshot()
        r = self.act(1, "cancel", headers=HX)
        self.assertEqual(r.status_code, 409)
        self.assertIn("ORDER 1 IS CANCELLED - CANCEL NOT ALLOWED", r.text)
        self.assertIn('role="alert"', r.text)
        self.assertEqual(self.snapshot(), snap)
        self.assertEqual(self.stock("HAM-01"), before + 3)
        self.assertIn((1, "C"), self.rows(self.client.get("/orders").text))

    def test_invalid_web_actions_change_nothing_and_list_stays_usable(self):
        before = self.snapshot()
        for order_no, action in ((1, "ship"), (2, "pick"), (3, "pick"), (3, "ship"), (3, "cancel"),
                                 (4, "pick"), (4, "ship"), (4, "cancel")):
            with self.subTest(order_no=order_no, action=action):
                r = self.act(order_no, action, headers=HX)
                self.assertEqual(r.status_code, 409)
                self.assertIn("NOT ALLOWED", r.text)
                self.assertIn("Nothing changed.", r.text)
                self.assertEqual(self.rows(r.text), [(4, "C"), (3, "S"), (2, "P"), (1, "N")])
                self.assertEqual(self.snapshot(), before)

    def test_unknown_order_is_reported_and_changes_nothing(self):
        before = self.snapshot()
        r = self.act(99, "cancel", headers=HX)
        self.assertEqual(r.status_code, 404)
        self.assertIn("ORDER 99: ORDER NOT FOUND", r.text)
        self.assertEqual(self.act(1, "delete", headers=HX).status_code, 422)
        self.assertEqual(self.snapshot(), before)

    def test_web_and_api_actions_match_on_independent_seeds(self):
        steps = (("pick", 1), ("cancel", 2), ("ship", 1), ("cancel", 3))
        for action, order_no in steps:
            self.act(order_no, action, headers=HX)
        web = self.snapshot()
        appdb.init(reset=True).close()
        for action, order_no in steps:
            self.client.post(f"/api/orders/{order_no}/{action}")
        self.assertEqual(self.snapshot(), web)

    def test_action_without_htmx_renders_full_page(self):
        r = self.act(1, "pick")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ORDER LIST", r.text)
        self.assertIn("<nav", r.text)
        self.assertIn("ORDER 1 PICKED", r.text)
        bad = self.act(3, "cancel")
        self.assertEqual(bad.status_code, 409)
        self.assertIn("ORDER LIST", bad.text)

    def test_empty_list_shows_message_and_no_controls(self):
        self.execute("DELETE FROM orders")
        r = self.client.get("/orders")
        self.assertEqual(r.status_code, 200)
        self.assertIn("NO ORDERS ON FILE", r.text)
        self.assertIn('href="/orders/new"', r.text)
        self.assertEqual(self.rows(r.text), [])
        self.assertNotIn("<button", r.text)
        self.assertEqual(self.client.get("/api/orders").json(), [])

    def test_row_actions_share_one_request_queue(self):
        page = self.client.get("/orders").text
        self.assertIn('<div id="order-list" hx-sync="this:drop">', page)
        self.assertEqual(page.count('hx-disabled-elt="#order-list button"'), 4)
        self.assertEqual(page.count('hx-target="#order-list"'), 4)

    def test_database_failure_is_controlled(self):
        broken = os.path.join(self.tmp.name, "missing-dir", "x.db")
        with mock.patch.object(appdb, "DB_PATH", broken):
            page = self.client.get("/orders")
            act = self.act(1, "cancel", headers=HX)
            api = self.client.get("/api/orders")
        self.assertEqual(page.status_code, 503)
        self.assertIn(lifecycle.LIST_UNAVAILABLE, page.text)
        self.assertNotIn("NO ORDERS ON FILE", page.text)
        self.assertEqual(act.status_code, 503)
        self.assertIn(lifecycle.NOT_UPDATED, act.text)
        self.assertEqual(api.status_code, 503)
        self.assertEqual(api.json()["detail"], lifecycle.LIST_UNAVAILABLE)
        self.assertEqual(self.stock("HAM-01"), 117)

    def test_rejected_action_keeps_its_status_when_the_list_reload_fails(self):
        unavailable = lifecycle.LifecycleUnavailable(lifecycle.LIST_UNAVAILABLE)
        with mock.patch.object(lifecycle, "list_orders", side_effect=unavailable):
            r = self.act(3, "cancel", headers=HX)
        self.assertEqual(r.status_code, 409)
        self.assertIn("NOT ALLOWED", r.text)
        self.assertIn(lifecycle.LIST_UNAVAILABLE, r.text)

    def test_saved_change_is_reported_when_the_list_reload_fails(self):
        unavailable = lifecycle.LifecycleUnavailable(lifecycle.LIST_UNAVAILABLE)
        with mock.patch.object(lifecycle, "list_orders", side_effect=unavailable):
            r = self.act(1, "cancel", headers=HX)
        self.assertEqual(r.status_code, 200)
        self.assertIn("ORDER 1 CANCELLED", r.text)
        self.assertIn(lifecycle.LIST_UNAVAILABLE, r.text)
        self.assertNotIn("NO ORDERS ON FILE", r.text)
        self.assertEqual(self.stock("HAM-01"), 120)
