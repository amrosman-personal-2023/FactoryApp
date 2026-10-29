"""REFERENCE ENDPOINT - customer lookup, the pattern for all migrated screens.

Ported from legacy customer_screen. Note the deliberate differences from the
legacy curses handler:
- parameterized SQL (legacy search_screen built SQL from strings -> injection)
- integer cents in the response; the UI formats dollars for display only
- open_total_cents mirrors the legacy "used" credit computation:
  SUM(total_cents) over orders with status N or P.
"""
from app import db
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/customers", tags=["customers"])

TIER_NAMES = {"S": "STANDARD", "G": "GOLD", "P": "PLATINUM"}


@router.get("/{cust_no}")
def get_customer(cust_no: str):
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT cust_no,name,city,tier,credit_lim FROM customers WHERE cust_no=?",
            (cust_no,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="CUSTOMER NOT FOUND")
        used = conn.execute(
            "SELECT COALESCE(SUM(total_cents),0) FROM orders "
            "WHERE cust_no=? AND status IN ('N','P')",
            (row[0],),
        ).fetchone()[0]
        return {
            "cust_no": row[0],
            "name": row[1],
            "city": row[2],
            "tier": TIER_NAMES[row[3]],
            "credit_lim_cents": row[4] * 100,
            "open_total_cents": used,
        }
    finally:
        conn.close()
