# Usage Metering & Billing Engine

A small backend service that answers the three questions every SaaS product must answer:
**How much has this customer used? What do they owe? Have they reached their plan limit?**

Built for the FlyRank Backend Internship capstone with Python, FastAPI, SQLite, Alembic and
Stripe (test mode only). Everything is free: no credit card, no real money, and no AI key
(the token counts are simulated).

What it does:

- **Meters usage per tenant, exactly once.** A retried request (same `Idempotency-Key`) never creates a second usage event.
- **Enforces monthly plan quotas** with honest status codes: `429` when the allowance is used up, `402` when a payment failed.
- **Calculates cost with exact integer math**, including the cached-input and reasoning-token pricing rules.
- **Syncs plans with Stripe** through signed, de-duplicated webhooks (Free to Pro on payment, back to Free on cancel).
- **Runs a background job** that cleans up old data, retries when it fails, and raises an alert when it gives up.

## Architecture

```
                         +------------------------------------------+
  client  ------------>  |  HTTP layer  (app/routers)               |
  (X-API-Key,            |  auth, validation, status codes only     |
   Idempotency-Key)      |  me | generate | usage | billing | hooks |
                         +--------------------+---------------------+
                                              |
                         +--------------------v---------------------+
                         |  Business layer  (app/services)          |
                         |  metering (idempotency + per-tenant lock)|
                         |  quota  | pricing | usage | stripe_sync   |
                         +--------------------+---------------------+
                                              |
                         +--------------------v---------------------+      +---------------------+
                         |  Data layer  (app/models, SQLAlchemy)    | <--> |  SQLite database    |
                         +------------------------------------------+      |  built by Alembic   |
                                                                           +---------------------+

  Stripe --(signed webhook)--> POST /webhooks/stripe
        verify signature on the RAW body -> claim the event id -> change the tenant (ONE transaction)

  Background thread (started with the app) --> cleanup job: deletes old idempotency receipts,
        retries 3 times (1s, 2s), then writes a CRITICAL log line and a row in job_alerts
```

How `POST /generate` works:

1. The `X-API-Key` header is hashed and matched to a tenant. The tenant never comes from the request body.
2. The body and the `Idempotency-Key` header are validated (bad input is a clean 4xx, never a 500).
3. Inside a per-tenant lock:
   - a saved receipt for this key exists: replay the first answer (or `422` if the body is different);
   - otherwise check the quota (`429` or `402`, and nothing is saved);
   - otherwise save the usage event and the receipt in ONE transaction.

## How to run

You need Python 3.10 or newer (tested on 3.12). No Docker and no database server are needed.

**Windows PowerShell**

```powershell
git clone https://github.com/AMMMMMMAR/flyrank-capstone-metering-billing.git
cd flyrank-capstone-metering-billing
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
python -m scripts.start
```

**macOS / Linux**

```bash
git clone https://github.com/AMMMMMMAR/flyrank-capstone-metering-billing.git
cd flyrank-capstone-metering-billing
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.start
```

`python -m scripts.start` is the one run command. It applies the database migrations, then starts
the API at http://127.0.0.1:8000. Interactive docs: http://127.0.0.1:8000/docs. Stop it with Ctrl+C.

**Seed step** (in a second terminal, with the virtual environment active):

```
python -m scripts.seed --demo-keys
```

This creates the two plans and two demo tenants. `--demo-keys` uses fixed PUBLIC demo keys so you
can copy and paste the commands below. They are for demo databases only. Without the flag the seed
prints random keys once. Running the seed twice is safe.

| Tenant | Plan | Demo API key |
|---|---|---|
| Alpha Corp | Free | `mk_demo_alpha_free_0000` |
| Beta LLC | Pro | `mk_demo_beta_pro_0000` |

Settings come from environment variables or a `.env` file (copy `.env.example`). The core features work
without a `.env`. Only Stripe needs one.

## Quick tour

macOS / Linux:

```bash
curl -s -H "X-API-Key: mk_demo_alpha_free_0000" http://127.0.0.1:8000/me
curl -si -X POST http://127.0.0.1:8000/generate -H "X-API-Key: mk_demo_alpha_free_0000" -H "Idempotency-Key: demo-1" -H "Content-Type: application/json" -d '{"fresh_input_tokens":100,"output_tokens":500}'
# Run the same command again: same answer, header "idempotent-replayed: true", still ONE usage event.
curl -s -H "X-API-Key: mk_demo_alpha_free_0000" http://127.0.0.1:8000/usage
```

Windows PowerShell:

```powershell
'{"fresh_input_tokens":100,"output_tokens":500}' | Set-Content "$env:TEMP\body.json" -Encoding ascii
curl.exe -si -X POST http://127.0.0.1:8000/generate -H "X-API-Key: mk_demo_alpha_free_0000" -H "Idempotency-Key: demo-1" -H "Content-Type: application/json" -d "@$env:TEMP\body.json"
curl.exe -s -H "X-API-Key: mk_demo_alpha_free_0000" http://127.0.0.1:8000/usage
```

## Endpoints

| Method | Path | Needs | What it does |
|---|---|---|---|
| GET | `/health` | nothing | Is the server alive? |
| GET | `/me` | `X-API-Key` | The calling tenant, its plan and its limits |
| POST | `/generate` | `X-API-Key`, `Idempotency-Key` | The billable endpoint: checks the quota, records ONE usage event |
| GET | `/usage` | `X-API-Key` | This month's usage, limits and exact cost |
| POST | `/checkout-session` | `X-API-Key` | Creates a Stripe Checkout page (test mode) to upgrade to Pro |
| GET | `/checkout/success`, `/checkout/cancel` | nothing | Landing pages. They change nothing. |
| POST | `/webhooks/stripe` | `Stripe-Signature` | Signed Stripe events: plan upgrades, downgrades, payment problems |

## Plans, quotas and status codes

| Plan | Calls per month | Tokens per month | Price |
|---|---|---|---|
| Free | 1,000 | 100,000 | $0 |
| Pro | 50,000 | 5,000,000 | $29.00 per month |

- **Month** = the UTC calendar month. Tokens counted = fresh input + cached input + output + reasoning.
- **Boundary rule:** a request is allowed when `used + requested <= limit`. Call 1,000 is allowed. Call 1,001 is refused.
- **429** = the plan is active but the allowance is used up. The body says which quota, the limit, how much was used, how much was requested, the seconds until it resets (also in the `Retry-After` header) and a hint (Free tenants are told to upgrade).
- **402** = a Pro payment failed (`past_due` or `unpaid`). A canceled subscription simply moves the tenant back to Free.
- A refused request (429 or 402) saves nothing, so it does not use up its `Idempotency-Key`.

| Code | When |
|---|---|
| 200 | Success, or a replay (header `Idempotent-Replayed: true`) |
| 400 | Missing or invalid `Idempotency-Key`; bad or missing webhook signature |
| 401 | Missing or wrong API key |
| 402 | Payment problem (`past_due`) |
| 409 | The key is older than 24 hours and its saved answer is gone; tenant already on Pro |
| 422 | Invalid body; the same key was used with a different body |
| 429 | Monthly quota used up |
| 502 / 503 | Stripe refused the checkout / Stripe is not configured |

## Pricing (pinned in `app/config.py`)

All money is a whole number of **micro-dollars** (1 USD = 1,000,000). No floats anywhere.

| Item | Price |
|---|---|
| One API call | 100 |
| Fresh input tokens | 300 per 1,000 tokens |
| Cached input tokens | 75 per 1,000 tokens (75% cheaper) |
| Output tokens | 1,500 per 1,000 tokens |
| Reasoning tokens | billed as output tokens |
| Pro subscription | 29,000,000 per month |

The token categories are never added together first. Reasoning joins the output bucket. The month's tokens
are summed per bucket and rounded **up once per bucket** (`(a + b - 1) // b`), never per event.
Example: 1 call + 1,000 fresh + 1,000 cached + 1,000 output + 1,000 reasoning tokens = 100 + 300 + 75 + 3,000 =
3,475 micro-dollars = $0.003475. These are demo prices chosen for this project, not real provider prices.

## Idempotency (exactly-once metering)

Every `POST /generate` needs an `Idempotency-Key`. After the first success the server saves the key, a
fingerprint of the request body and the exact response (table `idempotency_records`). The same key and body again
returns the saved response byte for byte. The same key with a different body is a client mistake (422). Keys
belong to one tenant. Two requests at the same moment are serialized by a per-tenant lock, and the database
`UNIQUE (tenant_id, idempotency_key)` on `usage_events` is a second safety net. Receipts are deleted after 24
hours by the background job.

## Stripe (test mode only)

1. Create a free Stripe account and stay in **test mode**. Never use live keys.
2. Create a product "Pro plan" with a recurring monthly price of $29.00. Copy its Price ID (`price_...`).
3. Copy your test secret key (`sk_test_...`).
4. Install the Stripe CLI and run `stripe login`.
5. Start the listener BEFORE you pay (the CLI only forwards events while it runs). It prints a `whsec_...` secret:
   `stripe listen --events checkout.session.completed,customer.subscription.updated,customer.subscription.deleted --forward-to 127.0.0.1:8000/webhooks/stripe`
6. Copy `.env.example` to `.env` and fill in `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID`. Restart `python -m scripts.start`.
7. `POST /checkout-session` with a tenant key, open the returned `checkout_url`, pay with card `4242 4242 4242 4242`
   (any future expiry, any CVC). Within seconds `GET /me` shows `"plan":"pro"`.

How the webhook stays safe: the signature is checked on the raw body first (forged, missing, tampered or old
deliveries get 400); the event id is saved in the same transaction as the plan change (a replay returns
`duplicate` and changes nothing); events older than the newest one already applied for that tenant are ignored.

## Background job

`cleanup-old-receipts` runs in its own thread every hour (`CLEANUP_INTERVAL_SECONDS`). It deletes idempotency
receipts older than 24 hours (`RECEIPT_RETENTION_HOURS`), in batches of 500. If it fails it retries 3 times (waiting 1 s, then 2 s).
After the last failure it writes a CRITICAL log line and a row in the `job_alerts` table. Usage events are never deleted.

## Tests and proofs

```
python -m scripts.run_all_checks     # 6 proof scripts, about 1 to 2 minutes, temporary databases
python -m scripts.check_submission   # starts the system on a clean setup and probes it like a reviewer
```

| Script | Proves |
|---|---|
| `check_migrations` | The migrations build exactly the schema of the models; downgrade; adopting an old database |
| `check_quota` | Call 1,000 allowed, 1,001 gets 429 with `Retry-After`; token boundary; 402; 10 requests at once |
| `check_idempotency` | Same request twice = one event, same answer; different body = 422; 10 identical requests at once |
| `check_pricing` | The exact pricing rules and `GET /usage` totals; round once; tenant isolation |
| `check_stripe` | Checkout flips Free to Pro; forged, missing, tampered, old webhooks get 400; replays; out-of-order events |
| `check_cleanup` | Cleanup deletes only old receipts; retries with backoff; failure alert; runs off the request path |

Every proof, with the output I got, is in `EVIDENCE.md`. `BUILDLOG.md` is my honest AI-usage log.
`docs/design_doc.md` is the design document.

## Project layout

```
app/
  main.py        the application and the start/stop of the background job
  config.py      settings and the pinned prices
  database.py    engine and sessions
  models.py      the tables
  auth.py        X-API-Key -> tenant
  routers/       HTTP layer
  services/      business layer: metering, quota, pricing, usage, stripe_sync
  jobs/          the background job and its retry/alert runner
alembic/         database migrations
scripts/         start, seed, proof scripts
docs/design_doc.md
```

Changing a table later: edit `app/models.py`, run `alembic revision --autogenerate -m "what changed"`, read the new
file, then `alembic upgrade head`. Useful commands: `alembic current`, `alembic check`, `alembic history`.

## Limitations

1. **SQLite only.** The migrations and tests ran on SQLite. The per-tenant lock is a Python lock, so it only protects one server process. The code also asks PostgreSQL for a row lock (`SELECT ... FOR UPDATE`), but I never tested PostgreSQL.
2. **Subscription state lives on the `tenants` row** (plan, status, Stripe ids) because a tenant has one subscription and there is one paid plan. There is no separate `subscriptions` table.
3. **One paid plan.** Any active Stripe subscription means Pro. The price id is not compared.
4. **No invoices, proration or overage billing.** `GET /usage` is an estimate of this month's bill.
5. **The failure alert is a log line and a database row.** No email or chat message is sent.
6. **The scheduler lives inside the server process.** Two server processes would both run the job (harmless: deleting old rows is safe to repeat).
7. **Idempotency receipts expire after 24 hours.** After that the same key returns 409 and the client must use a new key.
8. **Tokens are simulated and the prices are demo prices.** No AI model is called.
9. **Stripe test mode only.** Creating a checkout session is not idempotent (each call makes a new payment page).
10. **The webhook endpoint reads the body asynchronously but uses blocking database calls.** Fine at this size.
11. **The demo API keys are public on purpose** and only work on a database seeded with `--demo-keys`.

## AI usage

I built this with Claude as a mentor, step by step, and I read and ran every stage. An earlier attempt generated
by another AI agent was discarded. See `BUILDLOG.md` for where AI helped, where it was wrong, and what I changed.