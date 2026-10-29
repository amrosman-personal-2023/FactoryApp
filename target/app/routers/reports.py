"""Sales report API, ported from legacy report_screen.

GET /api/reports/sales returns every customer with its non-cancelled order
count and sales, highest sales first, plus the grand total. Money is integer
cents; the web page formats dollars.
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services import report

router = APIRouter(prefix="/api/reports", tags=["reports"])


def report_body(sales):
    return {
        "rows": [{"cust_no": r.cust_no, "name": r.name, "order_count": r.order_count,
                  "sales_cents": r.sales_cents} for r in sales.rows],
        "grand_total_cents": sales.grand_total_cents,
    }


@router.get("/sales")
def sales_report():
    try:
        return report_body(report.sales_report())
    except report.ReportUnavailable as exc:
        return JSONResponse({"detail": exc.reason}, status_code=503)
