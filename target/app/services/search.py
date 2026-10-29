"""Customer name search, ported from legacy search_screen.

Shared by GET /api/search/customers and the /customers/search web page.
Legacy rules preserved here:
- the term goes through the legacy prompt rules (field()): uppercased, and
  only the first twenty characters are kept;
- a blank term, or the word EXIT, searches nothing (legacy returned to the
  menu for both);
- the term matches anywhere in the customer name with SQL LIKE, so `%` and
  `_` in the term remain LIKE wildcards;
- at most twelve matches are shown, in table order like the legacy screen.

Intentional difference: legacy pasted the term into the SQL text, so
"' OR '1'='1" listed every customer. Here the SQL text is fixed and the
LIKE pattern is a bound parameter, so the term is always data. As in order
entry, an ESC character in submitted text is ignored rather than treated as
the legacy back key.
"""
import sqlite3
from dataclasses import dataclass

from app import db
from app.services.pricing import field

NAME_WIDTH = 20
MAX_MATCHES = 12
SEARCH_UNAVAILABLE = "CUSTOMER SEARCH UNAVAILABLE - PLEASE RETRY"
# The legacy prompt returned "EXIT" for F3, and the screen could not tell that
# apart from typing the word, so a typed EXIT also left without searching.
EXIT_WORD = "EXIT"

# Legacy had no ORDER BY and showed the first twelve rows of a full scan,
# which for a leading-% LIKE is rowid order; ORDER BY rowid pins that.
_SEARCH_SQL = ("SELECT cust_no,name,city FROM customers WHERE name LIKE ? "
               "ORDER BY rowid LIMIT ?")


@dataclass(frozen=True)
class CustomerMatch:
    cust_no: str
    name: str
    city: str


@dataclass(frozen=True)
class CustomerSearch:
    term: str
    matches: tuple

    @property
    def blank(self):
        return self.term == ""


class SearchUnavailable(Exception):
    reason = SEARCH_UNAVAILABLE


def normalize(raw):
    return field(raw, NAME_WIDTH)


def search_customers(raw):
    term = normalize(raw)
    if not term or term == EXIT_WORD:
        return CustomerSearch("", ())
    try:
        conn = db.connect()
        try:
            rows = conn.execute(_SEARCH_SQL, ("%" + term + "%", MAX_MATCHES)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise SearchUnavailable(SEARCH_UNAVAILABLE) from exc
    return CustomerSearch(term, tuple(CustomerMatch(*r) for r in rows))
