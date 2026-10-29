# FactoryApp

ORDERSYS is a small order-management demo. The original system is a green-screen terminal app in `legacy/`. It has been migrated to a FastAPI + HTMX web app in `target/` that keeps the legacy business rules.

## Layout

- `legacy/` - the original curses app. `ordersys.py` holds the screens and their business rules; `db.py` holds the SQLite schema and seed data.
- `tests/test_characterization.py` - characterization tests that pin what the legacy app does. They are the parity oracle for the migration and must not be changed.
- `target/` - the web app.
  - `app/main.py` - app entry point, health endpoint and HTML pages.
  - `app/routers/` - JSON API routes.
  - `app/services/` - business rules shared by the API and the pages (pricing, order entry, order lifecycle, sales report, customer search).
  - `app/templates/` - Jinja2 pages and HTMX fragments.
  - `tests/` - `unittest` smoke, parity and web tests. Parity tests compare the API with the frozen oracle and with the real legacy screens run on identical copies of each test database.
- `run_checks.sh` - runs both test suites.
- `start.sh` - installs the app into `.venv/` and starts or stops it in the background (see below).

## Setup

Python 3.11 or newer is required. The quickest way is the launcher script in the repository root:

```sh
./start.sh -install
```

It creates a virtualenv in `.venv/` at the repository root (or reuses an existing one) and installs the pinned packages from `target/requirements.txt` into it. Your global Python is not changed. Run it again at any time to repair or update the environment. Set `PYTHON=/path/to/python3` if your default `python3` is older than 3.11.

To install into your current Python instead:

```sh
python3 -m pip install -r target/requirements.txt
```

The web pages load HTMX 2.0.4 from unpkg.com, so the browser needs internet access.

## Run the web app

With the launcher (works from any directory, for example `~/FactoryApp/start.sh -start`):

```sh
./start.sh -start    # start in the background on http://127.0.0.1:8000
./start.sh -stop     # stop it
```

- `-start` uses `.venv/bin/python`, runs the app in the background bound to 127.0.0.1:8000 only, and returns once `/health` answers. Then open http://127.0.0.1:8000/customers. If the app is already running it says so and does not start a second copy.
- `-start` fails with a message and a non-zero exit code if `.venv` is missing (run `./start.sh -install`), if another process is already using port 8000, or if the app does not start. It never takes over or stops the other process.
- Server output goes to `.venv/ordersys-server.log`; check it when a start fails. The running instance is recorded in `.venv/ordersys-server.pid` (PID, start time, URL, command, log path). Both are ignored by Git.
- `-stop` stops only the instance recorded by `-start`, after checking that the recorded PID is still that server. If nothing is running, or the record is stale, it says so, removes the record and stops nothing.

To run the server in the foreground without the launcher:

```sh
cd target
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

On first start the app creates and seeds `target/app/orders.db` (ignored by Git). Delete that file to start again from the seed data.

Pages (all linked from the navigation bar):

| Page | URL |
|------|-----|
| Customer lookup | http://127.0.0.1:8000/customers |
| Order entry (preview, then confirm) | http://127.0.0.1:8000/orders/new |
| Order list with Pick / Ship / Cancel | http://127.0.0.1:8000/orders |
| Sales report | http://127.0.0.1:8000/reports/sales |
| Customer name search | http://127.0.0.1:8000/customers/search |

JSON API (money is always integer cents):

| Method and path | Purpose |
|-----------------|---------|
| `GET /health` | Health check |
| `GET /api/customers/{cust_no}` | Customer lookup with open (N/P) order total |
| `POST /api/orders` | Create an order from `{"cust_no", "sku", "qty"}` |
| `GET /api/orders` | All orders, newest first |
| `GET /api/orders/{order_no}` | One order |
| `POST /api/orders/{order_no}/pick`, `/ship`, `/cancel` | Status change (N -> P -> S; cancel from N or P) |
| `GET /api/reports/sales` | Sales by customer, cancelled orders excluded, with grand total |
| `GET /api/search/customers?name=ACME` | Customers whose name contains the term (at most 12) |

Example:

```sh
curl 'http://127.0.0.1:8000/api/search/customers?name=ACME'
```

## Run the legacy app

The legacy app needs a real terminal:

```sh
cd legacy
python3 -I db.py        # create and seed legacy/orders.db
python3 ordersys.py     # F3 exit, F12 or ESC back
```

## Tests

Run every check from the repository root:

```sh
./run_checks.sh
```

It runs the legacy characterization tests and then the target smoke, parity, web and launcher tests, and exits non-zero if either suite fails or if FastAPI is not installed. To run only the target tests:

```sh
cd target
python3 -m unittest discover -s tests -v
```

The launcher tests in `target/tests/test_launcher.py` run the real `start.sh` in throwaway copies of the repository under the system temp directory, so they never touch your `.venv` or `target/app/orders.db`. The tests that start a server need 127.0.0.1:8000 and are skipped if another process is using it.

## Intentional differences from the legacy app

The web app keeps the legacy rules, including their quirks (truncated discount and tax, field widths, twelve visible search results). The main deliberate change is that customer name search binds the search text as a SQL parameter, so the legacy SQL injection (`' OR '1'='1` listing every customer) no longer works. Other small differences, such as order creation rechecking stock and credit in one transaction, are listed in the migration pull request.
