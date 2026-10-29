"""Database access for the migrated order system.

Mirrors legacy/db.py exactly (same schema, same seed data) so parity tests can
compare both systems on identical inputs.

Conventions (from AGENTS.md):
- Money is stored as integer cents. Never floats.
- All SQL is parameterized. Never build SQL from strings (legacy search_screen
  had a SQL injection quirk; the new app must not).
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orders.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    cust_no     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    city        TEXT,
    tier        TEXT NOT NULL DEFAULT 'S',   -- S=standard, G=gold, P=platinum
    credit_lim  INTEGER NOT NULL DEFAULT 5000
);
CREATE TABLE IF NOT EXISTS products (
    sku         TEXT PRIMARY KEY,
    descr       TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    stock       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    order_no    INTEGER PRIMARY KEY AUTOINCREMENT,
    cust_no     TEXT NOT NULL,
    sku         TEXT NOT NULL,
    qty         INTEGER NOT NULL,
    total_cents INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'N'    -- N=new, P=picked, S=shipped, C=cancelled
);
"""

CUSTOMERS = [
    ("C1001", "ACME HARDWARE", "DALLAS", "S", 5000),
    ("C1002", "BRIGHT FUTURES LLC", "AUSTIN", "G", 20000),
    ("C1003", "CONTOSO SUPPLY", "HOUSTON", "P", 50000),
    ("C1004", "DELTA TOOLWORKS", "EL PASO", "S", 2500),
]

PRODUCTS = [
    ("HAM-01", "CLAW HAMMER 16OZ", 1299, 120),
    ("SAW-02", "HAND SAW 20IN", 2475, 40),
    ("DRL-03", "CORDLESS DRILL", 8999, 15),
    ("NAI-04", "NAILS 1LB BOX", 599, 500),
]

# (cust_no, sku, qty, total_cents, status) - totals follow the legacy pricing rules
ORDERS = [
    ("C1001", "HAM-01", 3, 4218, "N"),
    ("C1002", "SAW-02", 2, 5090, "P"),
    ("C1003", "NAI-04", 100, 56412, "S"),
    ("C1004", "DRL-03", 1, 9741, "C"),
]


def connect():
    return sqlite3.connect(DB_PATH)


def init(reset=False):
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = connect()
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
        conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", CUSTOMERS)
        conn.executemany("INSERT INTO products VALUES (?,?,?,?)", PRODUCTS)
        conn.executemany(
            "INSERT INTO orders (cust_no,sku,qty,total_cents,status) VALUES (?,?,?,?,?)", ORDERS
        )
        for _, sku, qty, _, status in ORDERS:
            if status != "C":
                conn.execute("UPDATE products SET stock=stock-? WHERE sku=?", (qty, sku))
        conn.commit()
    return conn
