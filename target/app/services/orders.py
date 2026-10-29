"""Order entry: validation, preview and atomic creation.

Shared by POST /api/orders and the HTML order-entry page so both paths apply
identical rules. Validation order and messages follow legacy order_entry_screen.
"""
import sqlite3
from dataclasses import dataclass

from app import db
from app.services import pricing

INVALID_CUSTOMER = "INVALID CUSTOMER"
INVALID_SKU = "INVALID SKU"
INVALID_QTY = "QTY MUST BE A POSITIVE NUMBER"
INSUFFICIENT_STOCK = "INSUFFICIENT STOCK"
CREDIT_EXCEEDED = "CREDIT LIMIT EXCEEDED - ORDER REJECTED"
NOT_CREATED = "ORDER NOT CREATED - PLEASE RETRY"


class OrderRejected(Exception):
    """Business-rule rejection. ``reason`` is one of the module message constants."""

    def __init__(self, reason, field=None):
        super().__init__(reason)
        self.reason = reason
        self.field = field


class OrderUnavailable(Exception):
    """The database failed mid-operation; nothing was written."""

    reason = NOT_CREATED


@dataclass(frozen=True)
class Quote:
    cust_no: str
    cust_name: str
    sku: str
    descr: str
    unit_cents: int
    stock: int
    qty: int
    price: pricing.Price


def _quote(conn, raw_cust, raw_sku, raw_qty):
    cust = pricing.field(raw_cust, pricing.CUST_WIDTH)
    c = conn.execute(
        "SELECT tier,credit_lim,name FROM customers WHERE cust_no=?", (cust,)
    ).fetchone()
    if not c:
        raise OrderRejected(INVALID_CUSTOMER, "cust_no")
    sku = pricing.field(raw_sku, pricing.SKU_WIDTH)
    p = conn.execute(
        "SELECT descr,price_cents,stock FROM products WHERE sku=?", (sku,)
    ).fetchone()
    if not p:
        raise OrderRejected(INVALID_SKU, "sku")
    qty = pricing.parse_qty(raw_qty)
    if qty is None:
        raise OrderRejected(INVALID_QTY, "qty")
    if qty > p[2]:
        raise OrderRejected(INSUFFICIENT_STOCK, "qty")
    priced = pricing.price(p[1], qty, c[0])
    used = conn.execute(
        "SELECT COALESCE(SUM(total_cents),0) FROM orders "
        "WHERE cust_no=? AND status IN ('N','P')", (cust,)
    ).fetchone()[0]
    if used + priced.total_cents > c[1] * 100:
        raise OrderRejected(CREDIT_EXCEEDED)
    return Quote(cust, c[2], sku, p[0], p[1], p[2], qty, priced)


def preview_order(raw_cust, raw_sku, raw_qty):
    """Validate and price without writing anything."""
    try:
        conn = db.connect()
        try:
            return _quote(conn, raw_cust, raw_sku, raw_qty)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise OrderUnavailable(NOT_CREATED) from exc


def create_order(raw_cust, raw_sku, raw_qty):
    """Revalidate, reprice and create an N order, decrementing stock.

    BEGIN IMMEDIATE takes the SQLite write lock before the authoritative
    price/stock/credit reads, so competing creations serialize and a stale
    preview can never oversell or over-credit. Returns (order_no, Quote).
    """
    try:
        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                quote = _quote(conn, raw_cust, raw_sku, raw_qty)
                cur = conn.execute(
                    "INSERT INTO orders (cust_no,sku,qty,total_cents,status) "
                    "VALUES (?,?,?,?,'N')",
                    (quote.cust_no, quote.sku, quote.qty, quote.price.total_cents))
                conn.execute("UPDATE products SET stock=stock-? WHERE sku=?",
                             (quote.qty, quote.sku))
                conn.commit()
                return cur.lastrowid, quote
            except BaseException:
                conn.rollback()
                raise
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise OrderUnavailable(NOT_CREATED) from exc
