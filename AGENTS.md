# AGENTS.md

## Project
Demo: migrate ORDERSYS, a legacy green-screen (curses) order-entry app, to a modern Python web app with a modern UI. Behavior must be preserved.

## Layout
- `legacy/` - source system. `db.py` (schema + seed), `ordersys.py` (curses screens with business rules inline).
- `target/` - the new app: FastAPI backend + HTMX web UI.
  - `app/main.py` - FastAPI entry point, health endpoint, HTML pages
  - `app/db.py` - same schema/seed as legacy; SQLite, integer cents
  - `app/routers/` - thin API routers (I/O + status codes only)
  - `app/services/` - business rules live HERE, never in routers/templates
  - `app/templates/` - Jinja2 + HTMX (server-rendered fragments)
  - `tests/` - `test_smoke.py` (boots the app end-to-end) and `test_parity.py` (API vs legacy oracle)
- `run_checks.sh` - THE gate (see Commands).

## Commands
- Install web deps: `python3 -m pip install -r target/requirements.txt`
- Seed legacy DB: `cd legacy && python3 -I db.py`
- Run legacy app: `cd legacy && python3 ordersys.py` (needs a real terminal; F-keys: F3 exit, F5 refresh, F12 back)
- Run web app: `cd target && python3 -m uvicorn app.main:app --port 8000` (then open http://localhost:8000/customers; API at /api/customers/{cust_no}; health at /health)
- ALL CHECKS: `./run_checks.sh` - runs legacy characterization tests + target smoke/parity tests. A milestone is complete only when this exits 0.

## Business rules that MUST be preserved (see `order_entry_screen`)
- Tier discount: Gold 5%, Platinum 10%, Standard 0%. Qty >= 100 adds 3 points (stacks).
- Discount is truncated with `int()`, not rounded.
- Tax is 8.25% on net, truncated to the cent (rounds down).
- Credit check: open orders (status N or P) + new total must not exceed `credit_lim * 100` cents; otherwise reject.
- Stock must cover qty; stock decrements on create, increments on cancel.
- Status flow: N -> P -> S. Cancel (C) allowed only from N or P.
- Sales report excludes cancelled orders.

## Conventions
- Money is stored as integer cents. Never use floats for stored amounts.
- Port the rules pinned in `tests/test_characterization.py` into equivalent checks against the new API (same inputs, same expected values). The legacy tests stay as the reference and must keep passing. All checks must pass before a milestone is complete.
- Python 3.11+; use `unittest`.

## Rules for migration missions (enforced by CI and the migration-reviewer droid)
- NEVER edit expected values in `tests/test_characterization.py` or the seed-derived constants in `target/tests/test_parity.py`. New behavior gets new tests; modified expectations are a parity violation.
- Copy expected values from the legacy oracle; do not recompute them by hand.
- Every migrated rule gets a row in the PR template's rule -> test map.
- All SQL parameterized (legacy `search_screen` is the anti-pattern to fix, not copy).
- Check-then-write on shared data (stock, credit) must be atomic.
- Follow the reference pattern: `target/app/routers/customers.py` + `customer_lookup_fragment` shows the endpoint/page/fragment split.
