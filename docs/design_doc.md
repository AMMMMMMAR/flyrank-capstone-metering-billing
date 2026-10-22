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
