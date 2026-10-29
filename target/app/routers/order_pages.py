"""HTML order-entry page: form -> preview fragment -> explicit confirmation.

Preview never writes. Confirmation re-runs the shared create_order service,
which revalidates and reprices inside one write transaction: the preview is
not a reservation, and a price change after preview is saved at the current
price and reported on the success screen.
"""
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.routers.orders import REJECTION_STATUS
from app.services import orders
from app.templating import TEMPLATES

router = APIRouter(tags=["order pages"])


def _render(request, fragment, context, status_code=200):
    """HTMX gets the fragment; a plain form post gets the full page around it."""
    if request.headers.get("HX-Request") == "true":
        return TEMPLATES.TemplateResponse(request, fragment, context, status_code=status_code)
    page = dict(context, result_template=fragment)
    return TEMPLATES.TemplateResponse(request, "order_entry.html", page, status_code=status_code)


def _rejected(request, exc, form):
    return _render(request, "order_result_fragment.html",
                   {"error": exc.reason, "form": form},
                   status_code=REJECTION_STATUS.get(exc.reason, 503))


@router.get("/orders/new", response_class=HTMLResponse)
def order_entry_page(request: Request):
    return TEMPLATES.TemplateResponse(request, "order_entry.html", {"form": {}})


@router.post("/orders/preview", response_class=HTMLResponse)
def order_preview(request: Request, cust_no: str = Form(""), sku: str = Form(""),
                  qty: str = Form("")):
    form = {"cust_no": cust_no, "sku": sku, "qty": qty}
    try:
        quote = orders.preview_order(cust_no, sku, qty)
    except (orders.OrderRejected, orders.OrderUnavailable) as exc:
        return _rejected(request, exc, form)
    return _render(request, "order_preview_fragment.html", {"q": quote, "form": form})


@router.post("/orders/confirm", response_class=HTMLResponse)
def order_confirm(request: Request, cust_no: str = Form(""), sku: str = Form(""),
                  qty: str = Form(""), preview_total_cents: str = Form("")):
    form = {"cust_no": cust_no, "sku": sku, "qty": qty}
    try:
        order_no, quote = orders.create_order(cust_no, sku, qty)
    except (orders.OrderRejected, orders.OrderUnavailable) as exc:
        return _rejected(request, exc, form)
    repriced = preview_total_cents.isdigit() and int(preview_total_cents) != quote.price.total_cents
    return _render(request, "order_result_fragment.html",
                   {"order_no": order_no, "q": quote, "repriced": repriced, "form": {}},
                   status_code=201)


@router.post("/orders/decline", response_class=HTMLResponse)
def order_decline(request: Request):
    return _render(request, "order_result_fragment.html", {"declined": True, "form": {}})
