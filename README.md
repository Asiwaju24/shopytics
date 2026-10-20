# Shopytics — Shop Manager

A complete shop-management system for small businesses: point of sale, stock control,
purchasing, customers & debts, expenses and profit reports.

```
backend/    Django REST API (JWT auth, multi-shop, roles)
frontend/   Plain HTML / CSS / JavaScript dashboard — no build step, no external CDNs
deploy/     nginx config used by docker-compose
```

## What it does

| Area | Features |
|---|---|
| **Sell** | Fast till with product search and barcode scanning (type/scan the SKU + Enter), multi-item carts, editable prices, cash / transfer / card / credit, part-payments, VAT, printable receipts, receipt numbers, voiding a sale |
| **Stock** | Stock goes down automatically on every sale and up when purchase orders are received. Every change is recorded in **Stock History** (before/after, who, why). Manual corrections, damaged/expired write-offs, low-stock alerts with days-of-stock-left |
| **Buy** | Suppliers, purchase orders with a full lifecycle (draft → sent via WhatsApp → confirmed → received in part or full → delivered), cost price updates |
| **Customers** | Customer list, credit sales create debts automatically, repayments with history, WhatsApp reminder messages (you press send), overdue tracking |
| **Money** | Expenses by category, profit & loss, revenue by day, best sellers, payment-method breakdown, tax collected, Excel / CSV downloads |
| **Team** | Owner, Manager, Cashier, Accountant, Viewer roles — enforced by the API, not just hidden in the UI |
| **Import** | CSV / Excel sales import with preview, duplicate protection and an optional "also reduce stock" switch |
| **Insights** | Dashboard with health score, trend chart, stock alerts, expected sales for the next 7 days (a transparent trailing-average estimate), suggestions |

## Quick start (local development)

Requires Python 3.11+.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                    # DJANGO_DEBUG=True is already set for local use
python manage.py migrate
python manage.py seed_demo_data                         # optional: demo shop (login: demo_owner / DemoPassword123)
python manage.py runserver                              # API on http://localhost:8000
```

In a second terminal serve the frontend (any static server works):

```bash
cd frontend
python -m http.server 5500                              # open http://localhost:5500
```

The frontend automatically talks to `http://localhost:8000/api` when opened on a dev port
(3000, 5173, 5500, 8080…). Create an account on the sign-in screen; the first screen then sets up your shop.

## Run everything with Docker (PostgreSQL + nginx)

```bash
cp backend/.env.example .env      # then set DJANGO_SECRET_KEY and DB_PASSWORD in this root .env
docker compose up --build
# open http://localhost:8080
```

nginx serves the frontend and proxies `/api` to Django, so no CORS setup is needed.

## Verify the install (recommended first run)

```bash
cd backend
python manage.py makemigrations --check --dry-run   # must say "No changes detected"
python manage.py test                               # runs the whole test suite
```

## Daily jobs

Run once a day (cron / scheduler). It rebuilds daily sales summaries, regenerates the 7-day
forecast and refreshes suggestions for every shop:

```bash
python manage.py run_daily_jobs
# crontab:  0 2 * * *  cd /app/backend && python manage.py run_daily_jobs
```

## Configuration (backend/.env)

| Variable | Meaning |
|---|---|
| `DJANGO_SECRET_KEY` | **Required** unless `DJANGO_DEBUG=True`. Keep it secret; changing it signs everyone out. |
| `DJANGO_DEBUG` | `True` for local development only. Defaults to `False`. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames in production. |
| `DATABASE_URL` | `postgres://user:pass@host:5432/db`. Empty = SQLite file. |
| `CORS_ALLOWED_ORIGINS` | Only if the frontend is on a different origin than the API. |
| `DJANGO_SECURE_SSL` | `True` when serving over HTTPS. |

## Things this version deliberately does NOT do

Being upfront so nobody is surprised:

* **Messages are not sent automatically.** Debt reminders and purchase orders open WhatsApp with the message
  filled in; a person presses send. Automatic sending needs a WhatsApp Business API account.
* **No online payments** (Paystack / Flutterwave) — payments are recorded, not collected.
* **No offline mode.** The till needs a connection to the server.
* **Forecasts are simple averages**, labelled as such — not a trained machine-learning model.
* **No automatic payment of debts via bank integration, no barcode *generation*, no product photos upload.**
  (Barcode *scanning* works with any USB/Bluetooth scanner that types into a field.)
