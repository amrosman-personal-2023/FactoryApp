"""API parity tests: the new API must answer with the same values the legacy
system produces for the same inputs (AGENTS.md convention).

Rules for missions extending this file:
- Copy expected values from tests/test_characterization.py (the legacy oracle)
  or compute them from the seed data in app/db.py. NEVER invent or edit an
  expected value to make a test pass - that is a parity violation and the
  migration-reviewer droid will catch it.
- One legacy business rule per test, named after the rule.

Already covered by the reference endpoint: customer lookup + open credit.
TO ADD (mission workspace): pricing/discount/tax, credit-limit rejection,
stock decrement/restock, status flow N->P->S, cancel rules, sales report.
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
