# AGENTS.md

## Project
Demo: migrate ORDERSYS, a legacy green-screen (curses) order-entry app, to a modern Python web app with a modern UI. Behavior must be preserved.

## Layout
- `legacy/` - source system. `db.py` (schema + seed), `ordersys.py` (curses screens with business rules inline).
- `target/` - new app goes here (FastAPI backend + web UI). Starts empty.
- `demo-script.md`, `compare-claude-code.md` - presenter material.

## Commands
- Seed legacy DB: `cd legacy && python3 -I db.py`
- Run legacy app: `cd legacy && python3 ordersys.py` (needs a real terminal; F-keys: F3 exit, F5 refresh, F12 back)

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
- Characterization tests in `tests/` must pass against both legacy logic and the new API before a milestone is complete.
- Python 3.11+; use `pytest`.
