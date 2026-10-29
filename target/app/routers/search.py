"""Customer name search API, ported from legacy search_screen.

GET /api/search/customers?name=ACME returns the normalized term and up to
twelve matching customers. It lives outside /api/customers so the
/api/customers/{cust_no} lookup can never capture it. A blank term returns
no customers rather than a full listing.
"""

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.services import pricing, search

router = APIRouter(prefix="/api/search", tags=["search"])

RawName = Annotated[str, Query(max_length=pricing.MAX_RAW_CHARS)]


def search_body(result):
    return {
        "term": result.term,
        "customers": [
            {"cust_no": m.cust_no, "name": m.name, "city": m.city} for m in result.matches
        ],
    }


@router.get("/customers")
def search_customers(name: RawName = ""):
    try:
        return search_body(search.search_customers(name))
    except search.SearchUnavailable as exc:
        return JSONResponse({"detail": exc.reason}, status_code=503)
