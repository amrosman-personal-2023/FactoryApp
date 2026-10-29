"""Order pricing - MISSION WORKSPACE.

Implement to match legacy order_entry_screen exactly. The parity tests in
tests/test_parity.py hold the expected values; copy them from
tests/test_characterization.py, never recompute by hand.

Legacy rules that MUST be preserved (see AGENTS.md):
- Tier discount: Gold 5%, Platinum 10%, Standard 0%. Qty >= 100 adds 3 points (stacks).
- Discount is truncated with int(), not rounded.           (legacy QUIRK)
- Tax is 8.25% on net, truncated to the cent (rounds down). (legacy QUIRK)
- Credit check: open orders (status N or P) + new total must not exceed
  credit_lim * 100 cents; otherwise reject.
- Money is integer cents everywhere.
"""
