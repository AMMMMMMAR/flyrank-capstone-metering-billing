# Design Document: Usage Metering & Billing Engine

## Problem
A SaaS product must answer three questions for every customer (tenant):
how much did they use, what do they owe, and have they reached their plan limit?
Bugs here cost real money: a retry that is counted twice overcharges a customer,
and a missing limit check gives away free usage.

## Data model (SQLite via SQLAlchemy; schema is code, migrations come later)
- plans: id (free/pro), monthly_call_quota, monthly_token_quota, base_price_cents
- tenants: id, name, email, api_key_hash (SHA-256 of the key), plan_id,
  subscription_status, stripe_customer_id, stripe_subscription_id
- usage_events: one row per billable call. Holds calls and four token counts
  (fresh input, cached input, output, reasoning). UNIQUE (tenant_id, idempotency_key).
- idempotency_records and processed_webhook_events are added in later stages.

## Rules decided up front
- One /generate call = one usage_event (calls + tokens on the same row).
- "Month" = the UTC calendar month, for every tenant.
- Quota boundary: a request is allowed if used + requested <= limit.
- Over quota -> 429 with Retry-After (seconds until the next month starts).
- 402 is used only when a paid subscription is past_due.
  A canceled subscription moves the tenant back to the free plan.
- Money is stored and calculated as integers in micro-dollars
  (1 dollar = 1,000,000 micro-dollars). No floats anywhere.
- Cost is rounded up once, on the monthly totals per token category.
- The ledger is never deleted: foreign keys use RESTRICT.

## API surface (planned)
- GET  /health
- POST /generate            (needs X-API-Key and Idempotency-Key)
- GET  /usage               (needs X-API-Key)
- POST /checkout-session    (needs X-API-Key)
- POST /webhooks/stripe     (verified by Stripe signature)

## Layers
routers (HTTP and status codes) -> services (business rules) -> models (database).

## Non-goals
No invoicing, no proration, no overage billing, no real AI calls, no live Stripe mode.

## Concurrency (added in Stage 4)
- The quota check and the insert happen together in one critical section per tenant.
- PostgreSQL: SELECT ... FOR UPDATE on the tenant row.
- SQLite ignores FOR UPDATE, so a per-tenant Python lock is used (valid for one server process).
- A rejected request (429 or 402) saves nothing, so it does not use up its Idempotency-Key.

## Idempotency (added in Stage 5)
- Table idempotency_records: UNIQUE (tenant_id, key), request_hash, saved response.
- Order inside the tenant lock: 1) look up the key, 2) check the quota, 3) save the usage
  event and the response in ONE transaction.
- Same key + same body -> the saved response again (header Idempotent-Replayed: true).
- Same key + different body -> 422.
- Rejected requests (429, 402) are not saved, so they do not use up their key.
- Keys belong to one tenant. The usage_events UNIQUE (tenant_id, idempotency_key) stays
  as a second safety net.

## Pricing (added in Stage 6)
- Unit: micro-dollars. 1 USD = 1,000,000. 1 cent = 10,000. Integers only.
- Pinned in app/config.py (demo prices, not real provider prices):
  call = 100; fresh input = 300, cached input = 75, output = 1500 (all per 1,000 tokens).
- Reasoning tokens are billed as output tokens (same bucket as output).
- Three token buckets: fresh, cached, output + reasoning. They are never added together first.
- Rounding: sum the month's tokens per bucket, then round UP once per bucket
  ((a + b - 1) // b). Never round per event.
- GET /usage total = usage cost + plan subscription fee (base_price_cents x 10,000).

## Stripe (added in Stage 7)
- POST /checkout-session creates a Stripe Checkout page (test mode, subscription).
  The tenant id is sent as client_reference_id and as subscription metadata.
- The "payment successful" page changes nothing. Only a verified webhook changes the plan.
- POST /webhooks/stripe: read the RAW body, verify the Stripe-Signature (HMAC-SHA256,
  5 minute window). Bad or missing signature -> 400, nothing changes.
- Table processed_webhook_events: PRIMARY KEY event_id. The event id and the tenant change
  are saved in ONE transaction. A replay returns 200 "duplicate" and changes nothing.
- Out of order: an event older than the newest applied one for that tenant is ignored.
- Events we do not understand, or about an unknown tenant, return 200 "ignored", so Stripe
  does not retry them forever. A real failure returns 500 and Stripe retries.
- States: checkout completed / subscription active -> pro, active.
  past_due or unpaid -> pro, past_due (the API answers 402).
  canceled -> free, active (subscription id cleared).

## Background job (added in Stage 8)
- Job: cleanup-old-receipts deletes idempotency receipts older than 24 hours, in batches of 500.
  The webhook table is kept (it is replay protection). Usage events are never deleted.
- It runs in its own thread every CLEANUP_INTERVAL_SECONDS, started and stopped by the app
  lifespan, so no request ever waits for it.
- Retries: up to 3 attempts, with waits of 1s then 2s (backoff). The job is safe to repeat.
- Failure alert: after the last failed attempt, a CRITICAL log line AND a row in job_alerts.
  The alert code never crashes the worker.
- An expired key (event exists, receipt deleted) is refused with 409. It is never counted twice.
- Limit: the scheduler lives inside the server process. Two server processes would both run
  the job; this is harmless because deleting old rows is safe to repeat.

## Migrations (added in Stage 9)
- The schema is built by Alembic migrations (alembic/versions), not by create_all.
  0001_initial_schema creates all 6 tables, the constraints and the 3 indexes.
- A new database: alembic upgrade head, then python -m scripts.seed.
- An existing database that already has the tables (made by the old create_all): alembic stamp head,
  then alembic check must report no differences.
- seed.py no longer creates tables. It stops with a clear message if they are missing.
- Changing a table later: edit models.py, then alembic revision --autogenerate -m "what changed",
  read the new file, then alembic upgrade head. On SQLite, batch mode rebuilds tables for ALTERs.
- The proof scripts (check_*.py) still use create_all on throw-away databases for speed.
  check_migrations.py proves that the migrations build the same schema as models.py.
