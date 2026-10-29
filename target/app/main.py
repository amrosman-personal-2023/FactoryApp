"""FastAPI application entry point.

Run locally (from target/):  python3 -m uvicorn app.main:app --port 8000
Health:                       curl http://localhost:8000/health
"""
import os
from contextlib import asynccontextmanager

from app import db
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .routers import customers


@asynccontextmanager
async def lifespan(_app):
    db.init()   # idempotent: creates schema + seeds if empty, like legacy
    yield


app = FastAPI(title="ORDERSYS web", version="0.1.0", lifespan=lifespan)
app.include_router(customers.router)

TEMPLATES = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/customers", response_class=HTMLResponse)
def customers_page(request: Request):
    """Server-rendered customer list; the lookup box posts via HTMX."""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT cust_no,name,city,tier,credit_lim FROM customers ORDER BY cust_no"
        ).fetchall()
    finally:
        conn.close()
    return TEMPLATES.TemplateResponse(
        request,
        "customers.html",
        {"customers": rows, "tier_names": {"S": "STANDARD", "G": "GOLD", "P": "PLATINUM"}},
    )


@app.get("/customers/lookup", response_class=HTMLResponse)
def customer_lookup_fragment(request: Request, cust_no: str = ""):
    """HTMX fragment: same data as GET /api/customers/{cust_no}, rendered as HTML."""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT cust_no,name,city,tier,credit_lim FROM customers WHERE cust_no=?",
            (cust_no.upper(),),
        ).fetchone()
        used = 0
        if row:
            used = conn.execute(
                "SELECT COALESCE(SUM(total_cents),0) FROM orders "
                "WHERE cust_no=? AND status IN ('N','P')",
                (row[0],),
            ).fetchone()[0]
    finally:
        conn.close()
    return TEMPLATES.TemplateResponse(
        request,
        "customer_fragment.html",
        {"c": row, "tier_names": {"S": "STANDARD", "G": "GOLD", "P": "PLATINUM"}, "open": used},
        status_code=404 if not row else 200,
    )
