"""Customer name search web page and its HTMX results fragment.

Expected tuples come from the frozen legacy SearchTests (ACME finds
C1001 / ACME HARDWARE / DALLAS, not CONTOSO) and the app/db.py seed. The
page and GET /api/search/customers share app/services/search.py.
"""

import os
import re
from unittest import mock

from helpers import WebTestCase, appdb

from app.services import search

HX = {"HX-Request": "true"}
ACME = ("C1001", "ACME HARDWARE", "DALLAS")
ROW = re.compile(
    r'<tr class="search-match" data-cust-no="[^"]*">\s*'
    r"<td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td>"
)
PAGES = ("/customers", "/orders/new", "/orders", "/reports/sales", "/customers/search")


class CustomerSearchPageTests(WebTestCase):
    def rows(self, html):
        return ROW.findall(html)

    def fragment(self, name):
        r = self.client.get("/customers/search", params={"name": name}, headers=HX)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("<html", r.text)
        return r.text

    def api(self, name):
        body = self.client.get("/api/search/customers", params={"name": name}).json()
        return [(c["cust_no"], c["name"], c["city"]) for c in body["customers"]]

    def test_page_renders_labeled_form_and_prompt_without_rows(self):
        r = self.client.get("/customers/search")
        self.assertEqual(r.status_code, 200)
        self.assertIn("CUSTOMER NAME SEARCH", r.text)
        self.assertIn('<label for="name">Name contains:</label>', r.text)
        self.assertRegex(r.text, r'<input id="name" name="name" maxlength="20"')
        self.assertIn('hx-get="/customers/search" hx-target="#search-results"', r.text)
        self.assertIn('hx-sync="this:replace"', r.text)
        self.assertIn('id="search-blank"', r.text)
        self.assertEqual(self.rows(r.text), [])

    def test_htmx_search_returns_only_the_results_fragment(self):
        html = self.fragment("ACME")
        self.assertEqual(self.rows(html), [ACME])
        self.assertNotIn("CONTOSO", html)
        r = self.client.get("/customers/search", params={"name": "ACME"}, headers=HX)
        self.assertEqual(r.headers["vary"], "HX-Request")

    def test_plain_get_renders_results_in_the_full_page(self):
        r = self.client.get("/customers/search", params={"name": "hard"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("<html", r.text)
        self.assertEqual(self.rows(r.text), [ACME])
        self.assertIn('value="HARD"', r.text)
        self.assertEqual(r.headers["vary"], "HX-Request")

    def test_no_match_and_blank_fragments_show_states_without_rows(self):
        no_match = self.fragment("ZZZNOMATCH")
        self.assertIn('id="search-no-matches"', no_match)
        self.assertIn("NO MATCHES", no_match)
        self.assertEqual(self.rows(no_match), [])
        blank = self.fragment("")
        self.assertIn('id="search-blank"', blank)
        self.assertEqual(self.rows(blank), [])
        for name in ("ACME HARDWARE", "BRIGHT FUTURES", "CONTOSO", "DELTA"):
            self.assertNotIn(name, blank)

    def test_page_shows_the_same_twelve_matches_as_the_api(self):
        for n in range(1, 14):
            self.execute(
                "INSERT INTO customers VALUES (?,?,?,'S',1000)",
                "L%02d" % n,
                "LIMITCHECK %02d" % n,
                "CITY %02d" % n,
            )
        rows = self.rows(self.fragment("limitcheck"))
        self.assertEqual(len(rows), 12)
        self.assertEqual(len(set(rows)), 12)
        self.assertEqual(rows, self.api("limitcheck"))

    def test_twenty_character_width_and_wildcards_on_the_page(self):
        self.execute(
            "INSERT INTO customers VALUES ('W2001','ABCDEFGHIJKLMNOPQRST','WIDTHVILLE','S',1)"
        )
        self.execute("INSERT INTO customers VALUES ('K1','WILDAXB','ONE','S',1)")
        self.execute("INSERT INTO customers VALUES ('K2','WILDAXXB','TWO','S',1)")
        for term in ("ABCDEFGHIJKLMNOPQRST", "ABCDEFGHIJKLMNOPQRSTZ"):
            self.assertEqual(
                self.rows(self.fragment(term)), [("W2001", "ABCDEFGHIJKLMNOPQRST", "WIDTHVILLE")]
            )
        self.assertEqual([r[0] for r in self.rows(self.fragment("WILDA_B"))], ["K1"])
        self.assertEqual([r[0] for r in self.rows(self.fragment("WILDA%B"))], ["K1", "K2"])

    def test_injection_payload_shows_no_matches_then_acme_still_works(self):
        html = self.fragment("' OR '1'='1")
        self.assertIn("NO MATCHES", html)
        self.assertEqual(self.rows(html), [])
        self.assertIn("&#39; OR &#39;1&#39;=&#39;1", html)
        self.assertEqual(self.rows(self.fragment("ACME")), [ACME])

    def test_customer_data_and_term_are_html_escaped(self):
        self.execute("INSERT INTO customers VALUES ('X<1','<B>BOLD</B> & CO','<I>TOWN','S',10)")
        html = self.fragment("<b>")
        self.assertIn("&lt;B&gt;BOLD&lt;/B&gt; &amp; CO", html)
        self.assertIn("&lt;I&gt;TOWN", html)
        self.assertNotIn("<B>", html)
        self.assertNotIn("<I>", html)
        self.assertIn('data-cust-no="X&lt;1"', html)

    def test_page_and_api_use_the_shared_search_service(self):
        fake = search.CustomerSearch("SHARED", (search.CustomerMatch("X1", "SHARED CO", "TYLER"),))
        with mock.patch.object(search, "search_customers", return_value=fake) as svc:
            body = self.client.get("/api/search/customers", params={"name": "x"}).json()
            html = self.fragment("x")
        self.assertEqual(svc.call_count, 2)
        self.assertEqual(
            body,
            {
                "term": "SHARED",
                "customers": [{"cust_no": "X1", "name": "SHARED CO", "city": "TYLER"}],
            },
        )
        self.assertEqual(self.rows(html), [("X1", "SHARED CO", "TYLER")])

    def test_search_api_coexists_with_customer_lookup(self):
        found = self.client.get("/api/search/customers", params={"name": "ACME"})
        self.assertEqual(found.status_code, 200)
        self.assertEqual(
            found.json(),
            {
                "term": "ACME",
                "customers": [{"cust_no": "C1001", "name": "ACME HARDWARE", "city": "DALLAS"}],
            },
        )
        lookup = self.client.get("/api/customers/C1001")
        self.assertEqual(lookup.status_code, 200)
        self.assertEqual(lookup.json()["name"], "ACME HARDWARE")
        self.assertEqual(lookup.json()["open_total_cents"], 4218)
        self.assertEqual(
            self.client.get("/customers/lookup", params={"cust_no": "C1001"}).status_code, 200
        )

    def test_every_page_links_to_all_five_screens(self):
        links = (
            '<a href="/customers">Customers</a>',
            '<a href="/orders/new">Order entry</a>',
            '<a href="/orders">Order list</a>',
            '<a href="/reports/sales">Sales report</a>',
            '<a href="/customers/search">Customer search</a>',
        )
        for path in PAGES:
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200)
                for link in links:
                    self.assertIn(link, r.text)

    def test_database_failure_is_controlled(self):
        broken = os.path.join(self.tmp.name, "missing-dir", "x.db")
        with mock.patch.object(appdb, "DB_PATH", broken):
            page = self.client.get("/customers/search", params={"name": "ACME"})
            frag = self.client.get("/customers/search", params={"name": "ACME"}, headers=HX)
            api = self.client.get("/api/search/customers", params={"name": "ACME"})
        self.assertEqual(page.status_code, 503)
        self.assertIn(search.SEARCH_UNAVAILABLE, page.text)
        self.assertIn('value="ACME"', page.text)
        self.assertEqual(frag.status_code, 503)
        self.assertIn(search.SEARCH_UNAVAILABLE, frag.text)
        self.assertEqual(
            (api.status_code, api.json()), (503, {"detail": search.SEARCH_UNAVAILABLE})
        )
