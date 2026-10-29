"""Sales report, ported from legacy report_screen.

Shared by GET /api/reports/sales and the /reports/sales web page so both show
the same numbers:
- every customer is listed, including customers with no orders;
- N, P and S orders are counted and their stored totals summed; C is not;
- rows are sorted by sales, highest first;
- the grand total is the sum of the listed sales.

The cancelled filter sits in the LEFT JOIN condition, not in WHERE: filtering
after the join would drop customers whose only orders are cancelled. Stored
order totals are summed as saved; nothing is repriced. The report only reads.

Intentional difference: the legacy 24-line terminal silently dropped rows (and
then the GRAND TOTAL line) past the bottom of the screen; the web report lists
every customer.
"""
import sqlite3
from dataclasses import dataclass

from app import db

REPORT_UNAVAILABLE = "SALES REPORT UNAVAILABLE - PLEASE RETRY"

# Legacy sorts by sales only; cust_no makes tied rows (e.g. all zero) stable.
_REPORT_SQL = (
    "SELECT c.cust_no, c.name, COUNT(o.order_no), COALESCE(SUM(o.total_cents),0) "
    "FROM customers c LEFT JOIN orders o "
    "ON o.cust_no=c.cust_no AND o.status<>? "
    "GROUP BY c.cust_no ORDER BY 4 DESC, c.cust_no"
)


@dataclass(frozen=True)
class ReportRow:
    cust_no: str
    name: str
    order_count: int
    sales_cents: int


@dataclass(frozen=True)
class SalesReport:
    rows: tuple
    grand_total_cents: int


class ReportUnavailable(Exception):
    reason = REPORT_UNAVAILABLE


def sales_report():
    try:
        conn = db.connect()
        try:
            rows = conn.execute(_REPORT_SQL, ("C",)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ReportUnavailable(REPORT_UNAVAILABLE) from exc
    report_rows = tuple(ReportRow(*r) for r in rows)
    return SalesReport(report_rows, sum(r.sales_cents for r in report_rows))
