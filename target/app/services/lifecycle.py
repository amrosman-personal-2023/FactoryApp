"""Order list and status lifecycle, ported from legacy order_list_screen.

Shared by the /api/orders list/action endpoints and the /orders web page so
both apply identical rules:
- every order is listed, newest first (order_no DESC), including cancelled;
- Pick moves N -> P, Ship moves P -> S, Cancel moves N or P -> C;
- any other action/status pairing changes nothing (legacy silent no-op);
- cancelling returns the order quantity to its SKU's stock.
"""
import sqlite3
from dataclasses import dataclass

from app import db

ORDER_NOT_FOUND = "ORDER NOT FOUND"
TRANSITION_NOT_ALLOWED = "STATUS CHANGE NOT ALLOWED"
NOT_UPDATED = "ORDER NOT UPDATED - PLEASE RETRY"
LIST_UNAVAILABLE = "ORDERS UNAVAILABLE - PLEASE RETRY"

STATUS_NAMES = {"N": "NEW", "P": "PICKED", "S": "SHIPPED", "C": "CANCELLED"}

# action -> (statuses it applies to, resulting status); legacy keys P, S, C.
TRANSITIONS = {
    "pick": (("N",), "P"),
    "ship": (("P",), "S"),
    "cancel": (("N", "P"), "C"),
}
ACTIONS = tuple(TRANSITIONS)

_COLUMNS = "order_no,cust_no,sku,qty,total_cents,status"


@dataclass(frozen=True)
class Order:
    order_no: int
    cust_no: str
    sku: str
    qty: int
    total_cents: int
    status: str

    @property
    def status_name(self):
        return STATUS_NAMES.get(self.status, self.status)

    @property
    def actions(self):
        """Actions that would change this order's status."""
        return tuple(a for a, (sources, _) in TRANSITIONS.items() if self.status in sources)


class LifecycleError(Exception):
    """Base for lifecycle failures. ``reason`` is one of the module message constants."""

    reason = NOT_UPDATED

    def __init__(self, reason=None, order=None):
        super().__init__(reason or self.reason)
        if reason:
            self.reason = reason
        self.order = order


class OrderNotFound(LifecycleError):
    reason = ORDER_NOT_FOUND


class TransitionRejected(LifecycleError):
    """The action does not apply to the order's current status; nothing changed."""

    reason = TRANSITION_NOT_ALLOWED


class LifecycleUnavailable(LifecycleError):
    """The database failed mid-operation; nothing was written."""

    reason = NOT_UPDATED


def _fetch(conn, order_no):
    row = conn.execute("SELECT " + _COLUMNS + " FROM orders WHERE order_no=?",
                       (order_no,)).fetchone()
    return Order(*row) if row else None


def list_orders():
    try:
        conn = db.connect()
        try:
            rows = conn.execute("SELECT " + _COLUMNS + " FROM orders "
                                "ORDER BY order_no DESC").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise LifecycleUnavailable(LIST_UNAVAILABLE) from exc
    return [Order(*r) for r in rows]


def get_order(order_no):
    try:
        conn = db.connect()
        try:
            order = _fetch(conn, order_no)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise LifecycleUnavailable(LIST_UNAVAILABLE) from exc
    if order is None:
        raise OrderNotFound()
    return order


def apply_action(order_no, action):
    """Apply pick/ship/cancel to one order and return the updated Order.

    BEGIN IMMEDIATE takes the SQLite write lock before the status is read, so
    the status check, the status write and the cancel restock form one atomic
    unit: competing or repeated cancels serialize and only the first one that
    still sees N/P restocks. Any failure rolls back both writes.
    """
    sources, target = TRANSITIONS[action]
    try:
        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                order = _fetch(conn, order_no)
                if order is None:
                    raise OrderNotFound()
                if order.status not in sources:
                    raise TransitionRejected(order=order)
                cur = conn.execute("UPDATE orders SET status=? WHERE order_no=? AND status=?",
                                   (target, order.order_no, order.status))
                if cur.rowcount != 1:
                    raise LifecycleUnavailable()
                if target == "C":
                    conn.execute("UPDATE products SET stock=stock+? WHERE sku=?",
                                 (order.qty, order.sku))
                updated = _fetch(conn, order.order_no)
                conn.commit()
                return updated
            except BaseException:
                conn.rollback()
                raise
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise LifecycleUnavailable() from exc
