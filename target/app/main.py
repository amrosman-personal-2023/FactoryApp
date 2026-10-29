"""FastAPI application entry point.

Run locally (from target/):  python3 -m uvicorn app.main:app --port 8000
Health:                       curl http://localhost:8000/health
"""
from contextlib import asynccontextmanager

from app import db
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse

from .routers import customers, orders
from .services import orders as order_service
from .templating import TEMPLATES


@asynccontextmanager
async def lifespan(_app):
    db.init()   # idempotent: creates schema + seeds if empty, like legacy
    yield


app = FastAPI(title="ORDERSYS web", version="0.1.0", lifespan=lifespan)
app.include_router(customers.router)
app.include_router(orders.router)


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


# Order-entry page: form -> preview fragment -> explicit confirmation.
# Preview never writes. Confirmation re-runs the shared create service, which
# revalidates and reprices inside one write transaction: the preview is not a
# reservation, and a price change after preview is saved at the current price
# and reported on the success screen.

def _order_render(request, fragment, context, status_code=200):
    """HTMX gets the fragment; a plain form post gets the full page around it."""
    if request.headers.get("HX-Request") == "true":
        return TEMPLATES.TemplateResponse(request, fragment, context, status_code=status_code)
    page = dict(context, result_template=fragment)
    return TEMPLATES.TemplateResponse(request, "order_entry.html", page, status_code=status_code)


def _order_rejected(request, exc, form):
    return _order_render(request, "order_result_fragment.html",
                         {"error": exc.reason, "form": form},
                         status_code=orders.REJECTION_STATUS[exc.reason])


@app.get("/orders/new", response_class=HTMLResponse)
def order_entry_page(request: Request):
    return TEMPLATES.TemplateResponse(request, "order_entry.html", {"form": {}})


@app.post("/orders/preview", response_class=HTMLResponse)
def order_preview(request: Request, cust_no: str = Form(""), sku: str = Form(""),
                  qty: str = Form("")):
    form = {"cust_no": cust_no, "sku": sku, "qty": qty}
    try:
        quote = order_service.preview_order(cust_no, sku, qty)
    except (order_service.OrderRejected, order_service.OrderUnavailable) as exc:
        return _order_rejected(request, exc, form)
    return _order_render(request, "order_preview_fragment.html", {"q": quote, "form": form})


@app.post("/orders/confirm", response_class=HTMLResponse)
def order_confirm(request: Request, cust_no: str = Form(""), sku: str = Form(""),
                  qty: str = Form(""), preview_total_cents: str = Form("")):
    form = {"cust_no": cust_no, "sku": sku, "qty": qty}
    try:
        order_no, quote, repriced = order_service.confirm_order(
            cust_no, sku, qty, preview_total_cents)
    except (order_service.OrderRejected, order_service.OrderUnavailable) as exc:
        return _order_rejected(request, exc, form)
    return _order_render(request, "order_result_fragment.html",
                         {"order_no": order_no, "q": quote, "repriced": repriced, "form": {}},
                         status_code=201)


@app.post("/orders/decline", response_class=HTMLResponse)
def order_decline(request: Request):
    return _order_render(request, "order_result_fragment.html", {"declined": True, "form": {}})
