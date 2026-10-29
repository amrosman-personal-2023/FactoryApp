"""Order entry API, ported from legacy order_entry_screen.

The client sends only raw customer/SKU/quantity text; price, stock and credit
are always computed server-side. Money is returned as integer cents. Every
field, including an integer qty, goes through the legacy field-width rules,
so qty 12345 is read as 1234 exactly as the four-character terminal field did.
"""
from typing import Union

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictInt, StrictStr

from app.services import orders

router = APIRouter(prefix="/api/orders", tags=["orders"])

REJECTION_STATUS = {
    orders.INVALID_CUSTOMER: 404,
    orders.INVALID_SKU: 404,
    orders.INVALID_QTY: 422,
    orders.INSUFFICIENT_STOCK: 409,
    orders.CREDIT_EXCEEDED: 409,
    orders.NOT_CREATED: 503,
}


class OrderRequest(BaseModel):
    cust_no: StrictStr
    sku: StrictStr
    qty: Union[StrictStr, StrictInt]


def quote_body(quote):
    p = quote.price
    return {
        "cust_no": quote.cust_no,
        "sku": quote.sku,
        "qty": quote.qty,
        "gross_cents": p.gross_cents,
        "discount_pct": p.discount_pct,
        "discount_cents": p.discount_cents,
        "tax_cents": p.tax_cents,
        "total_cents": p.total_cents,
    }


@router.post("", status_code=201)
def create_order(req: OrderRequest):
    try:
        order_no, quote = orders.create_order(req.cust_no, req.sku, str(req.qty))
    except (orders.OrderRejected, orders.OrderUnavailable) as exc:
        return JSONResponse({"detail": exc.reason}, status_code=REJECTION_STATUS[exc.reason])
    return {"order_no": order_no, "status": "N", **quote_body(quote)}
