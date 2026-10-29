"""Order list and status actions API, ported from legacy order_list_screen.

GET /api/orders lists every order newest first; POST
/api/orders/{order_no}/{pick|ship|cancel} applies one status change. Money is
integer cents. An action that does not apply to the current status changes
nothing and answers 409 with the unchanged order.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Path
from fastapi.responses import JSONResponse

from app.services import lifecycle

router = APIRouter(prefix="/api/orders", tags=["orders"])

LIFECYCLE_STATUS = {
    lifecycle.ORDER_NOT_FOUND: 404,
    lifecycle.TRANSITION_NOT_ALLOWED: 409,
    lifecycle.NOT_UPDATED: 503,
    lifecycle.LIST_UNAVAILABLE: 503,
}

# mypy cannot evaluate Literal[...] from a runtime tuple, so the allowed
# actions are spelled out here; test_parity asserts they stay in sync with
# lifecycle.ACTIONS.
Action = Literal["pick", "ship", "cancel"]
# SQLite INTEGER is 64-bit; binding a larger value raises OverflowError.
OrderNo = Annotated[int, Path(ge=1, le=2**63 - 1)]


def order_body(order):
    return {
        "order_no": order.order_no,
        "cust_no": order.cust_no,
        "sku": order.sku,
        "qty": order.qty,
        "total_cents": order.total_cents,
        "status": order.status,
    }


def _error(exc):
    body = {"detail": exc.reason}
    if exc.order is not None:
        body["order"] = order_body(exc.order)
    return JSONResponse(body, status_code=LIFECYCLE_STATUS[exc.reason])


@router.get("")
def list_orders():
    try:
        return [order_body(o) for o in lifecycle.list_orders()]
    except lifecycle.LifecycleError as exc:
        return _error(exc)


@router.get("/{order_no}")
def get_order(order_no: OrderNo):
    try:
        return order_body(lifecycle.get_order(order_no))
    except lifecycle.LifecycleError as exc:
        return _error(exc)


@router.post("/{order_no}/{action}")
def apply_action(order_no: OrderNo, action: Action):
    try:
        return order_body(lifecycle.apply_action(order_no, action))
    except lifecycle.LifecycleError as exc:
        return _error(exc)
