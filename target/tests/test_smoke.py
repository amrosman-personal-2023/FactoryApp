"""Smoke test: can the app boot, serve the UI, and answer the API?

This is the mission's final sanity check - if this passes, the app runs
end-to-end. Keep it fast; do not add per-rule assertions here (those belong
in test_parity.py).
"""
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
