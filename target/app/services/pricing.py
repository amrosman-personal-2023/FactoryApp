"""Order pricing and entry validation ported from legacy order_entry_screen.

Legacy rules preserved here (see AGENTS.md):
- Tier discount: Gold 5%, Platinum 10%, Standard 0%. Qty >= 100 adds 3 points (stacks).
- Discount is truncated, not rounded.                       (legacy QUIRK)
- Tax is 8.25% on net, truncated to the cent (rounds down).  (legacy QUIRK)
- Money is integer cents everywhere.

Legacy computes discount and tax with floats and int(); the integer floor
division below yields the same cents for non-negative amounts without
introducing floats (pinned by test_parity.PricingUnitTests).
"""
from dataclasses import dataclass

CUST_WIDTH = 8
SKU_WIDTH = 8
QTY_WIDTH = 4

TIER_DISCOUNT_PCT = {"S": 0, "G": 5, "P": 10}
VOLUME_BREAK_QTY = 100
VOLUME_BREAK_PCT = 3
TAX_RATE_BASIS_POINTS = 825   # 8.25%


@dataclass(frozen=True)
class Price:
    gross_cents: int
    discount_pct: int
    discount_cents: int
    tax_cents: int
    total_cents: int


def field(raw, width):
    """Replay raw text through the legacy curses prompt as keystrokes.

    Enter ends the field, backspace/DEL removes the last character, other
    non-printable characters are ignored, printable ASCII is uppercased and
    keys past the field width are never captured.
    """
    buf = ""
    for ch in str(raw):
        code = ord(ch)
        if code in (10, 13):
            break
        if code in (8, 127):
            buf = buf[:-1]
        elif 32 <= code < 127 and len(buf) < width:
            buf += ch.upper()
    return buf


def parse_qty(raw):
    """Return the legacy quantity (first four characters, positive digits) or None."""
    q = field(raw, QTY_WIDTH)
    if not q.isdigit() or int(q) < 1:
        return None
    return int(q)


def discount_pct(tier, qty):
    pct = TIER_DISCOUNT_PCT.get(tier, 0)
    if qty >= VOLUME_BREAK_QTY:
        pct += VOLUME_BREAK_PCT
    return pct


def price(unit_cents, qty, tier):
    gross = unit_cents * qty
    pct = discount_pct(tier, qty)
    discount = gross * pct // 100
    net = gross - discount
    tax = net * TAX_RATE_BASIS_POINTS // 10000
    return Price(gross, pct, discount, tax, net + tax)
