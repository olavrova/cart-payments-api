# cart-payments-api

The payment part of an online shop: a SQL schema for payments and one HTTP endpoint
that starts the payment for a cart.

Stack: Python 3.11+, Flask, SQLAlchemy 2, PostgreSQL 16 (13+ works).

## Quick start

Requirements: Python 3.11 or later, Docker with Compose.

```bash
# 1. Start PostgreSQL. On first start it creates two databases and applies
#    migrations/ to both: cart_payments (app) and cart_payments_test (tests).
docker compose up -d --wait

# 2. Install dependencies.
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

# 3. Run the app on http://localhost:5000
flask --app app run
```

The database listens on `localhost:5433`, so it does not clash with a local PostgreSQL
on 5432. Set `DATABASE_URL` to use another database.

Pay for the sample cart of the sample user Alice:

```bash
curl -i -X POST \
  -H "X-User-Id: 11111111-1111-1111-1111-111111111111" \
  http://localhost:5000/carts/c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1/payments
```

```http
HTTP/1.1 201 CREATED

{"amount": "70.00", "cart_id": "c1c1c1c1-...", "created_at": "2026-11-18T16:53:59.532174+00:00",
 "currency": "USD", "failure_reason": null, "id": "3d1480bd-...", "last_four": "4242",
 "status": "succeeded"}
```

Running it again returns `409` because the cart is now checked out.

## Running the tests

```bash
docker compose up -d --wait   # if not running yet
pytest
```

Tests use the separate `cart_payments_test` database (override with `TEST_DATABASE_URL`).
Each test runs in a transaction that is rolled back, so tests are independent and leave
no data behind. The endpoint tests use a fake payment provider that records its calls.

**After changing a migration**, recreate the databases (this deletes their data):

```bash
docker compose down -v && docker compose up -d --wait
```

## API

### `POST /carts/<cart_id>/payments`

Charges the user's card for the cart total.

| Part | Value |
|------|-------|
| Header `X-User-Id` | required, UUID of the authenticated user (see assumption A1) |
| Body (optional) | `{"payment_method_id": "<uuid>"}` — the card to use; default: the user's default card |

| Status | When | Body |
|--------|------|------|
| `201` | the card was charged; the cart is now `checked_out` | payment |
| `400` | missing or malformed `X-User-Id`, cart id, `payment_method_id`, or body | `{"error": "..."}` |
| `402` | the provider declined the card; the cart stays `active`, the user can retry | payment, `status: failed` |
| `404` | the cart does not exist or belongs to another user | `{"error": "..."}` |
| `409` | the cart is not `active`, or a payment for it is already in progress | `{"error": "..."}` |
| `422` | the cart is empty, its total is zero, it mixes currencies, or no usable payment method | `{"error": "..."}` |
| `502` | the provider failed; nothing was charged, the cart stays `active` | payment, `status: failed` |

Payment body: `id, cart_id, amount (decimal string), currency, status, last_four,
failure_reason, created_at`.

### Mock payment provider

`app/payment_provider.py` decides the outcome by the card token:

| Token contains | Outcome |
|----------------|---------|
| `decline` | declined, `failure_reason: "card_declined"` → `402` |
| `error` | provider failure, `failure_reason: "provider_error"` → `502` |
| anything else | success |

To try a decline by hand, add a card for Alice and pay with it:

```bash
docker compose exec db psql -U shop -d cart_payments -c "
  INSERT INTO user_payment_methods (id, user_id, provider_token, last_four)
  VALUES ('99999999-9999-9999-9999-999999999999',
          '11111111-1111-1111-1111-111111111111', 'tok_decline_visa', '0002');"

curl -i -X POST -H "X-User-Id: 11111111-1111-1111-1111-111111111111" \
  -H "Content-Type: application/json" \
  -d '{"payment_method_id": "99999999-9999-9999-9999-999999999999"}' \
  http://localhost:5000/carts/c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1/payments
```

## How a payment works

`app/payments/service.py`, `start_payment()`:

1. Load the cart with `SELECT ... FOR UPDATE`. Payments for one cart run one at a time.
2. Check the cart: it belongs to the user, is `active`, has items, and has no payment
   in progress.
3. Find the payment method: the given one (it must belong to the user) or the default.
4. Get the total from the total calculation service (`app/cart_total.py`).
5. Save the payment as `pending` and **commit before calling the provider**. If the
   process crashes during the call, the attempt is still recorded.
6. Call the provider with the card token, amount, currency, and the payment id as the
   idempotency key.
7. Save the outcome: `succeeded` (cart → `checked_out`) or `failed` (cart stays `active`).

## Database schema

The base schema is `migrations/001_base_schema.sql`, unchanged. The payment part is the
new migration `migrations/002_payments.sql`: one `payments` table with one row per
payment attempt.

| Rule | How the database enforces it |
|------|------------------------------|
| A cart is paid at most once, even with concurrent requests | partial unique index on `cart_id` where status is `pending` or `succeeded` |
| Failed attempts are kept and do not block a retry | the unique index ignores `failed` rows |
| The cart and the card belong to the paying user | composite foreign keys `(cart_id, user_id)` and `(payment_method_id, user_id)` |
| Only a succeeded payment has a provider id; only a failed one has a reason | two-way `CHECK` constraints |
| One provider charge maps to one payment | unique index on `provider_payment_id` |
| The charged amount is fixed at payment time | `amount` and `currency` stored on the row, like `cart_items.unit_price` |
| Money is exact | `NUMERIC(12, 2)` in SQL, `Decimal` in Python |

Migrations are plain SQL files applied in name order, so the schema is readable as SQL.
With two files, a migration tool such as Alembic would add more than it gives.

## Assumptions

- **A1. Authentication** exists elsewhere. The authenticated user id arrives in the
  `X-User-Id` header, standing in for a session or JWT.
- **A2. Total calculation service** exists. `app/cart_total.py` stands in for it: the
  sum of `quantity * unit_price`. All items in a cart must have one currency.
- **A3. The charge is synchronous.** The mock answers at once, so there are no webhooks;
  `pending` lasts only for the request.
- **A4. Stock** reservation belongs to order fulfilment and is out of scope.
- **A5. Retries.** A failed attempt is kept; the cart stays `active` so the user can pay
  again, with the same or another card.
- **A6. Out of scope:** refunds, partial payments, order creation.
- **A7. Two decimal places** for amounts, like the base schema. Currencies with 0 or 3
  minor digits (JPY, KWD) would need integer minor units.
- **A8. Stuck payments.** A `pending` payment older than 5 minutes was left by a crash.
  The next attempt marks it `failed` (`stale_pending`) so the cart is not blocked
  forever. A real system would first check with the provider, by idempotency key,
  that the card was not charged.
- **A9. Default card.** Without `payment_method_id`, the user's default card is used. If
  a user has several cards marked default, the newest one wins.
- **A10. Provider errors** mean "not charged". A real adapter must map ambiguous
  outcomes such as timeouts to "unknown" and leave the payment `pending` for
  reconciliation, instead of raising `PaymentProviderError`.

## Project layout

```
app/
├── __init__.py          create_app(): config, DB, error handlers, provider, routes
├── config.py            DATABASE_URL from the environment
├── db.py                one SQLAlchemy session per request
├── models.py            ORM models for the existing tables and payments
├── errors.py            API errors and their HTTP status codes
├── payment_provider.py  provider interface and the mock
├── cart_total.py        stand-in for the total calculation service
└── payments/
    ├── routes.py        HTTP layer: parse request, call service, build response
    └── service.py       start_payment(): the payment flow
migrations/              SQL schema, applied in name order
docker/initdb.sh         creates the app and test databases, applies migrations
tests/                   pytest; endpoint tests run against PostgreSQL
```
