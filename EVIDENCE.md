# Evidence

One proof per requirement of the brief (Section 6) and per shared requirement.
Every proof below was produced by running the command shown, on my own machine.
The proof scripts use temporary databases. The "real server" transcripts use my own database.
Run everything again with: python -m scripts.run_all_checks

## Requirements checklist

| # | Requirement (Section 6 of the brief) | Proof |
|---|---|---|
| 1 | A billable action creates exactly one usage event, even under retries | Stage 5 |
| 2 | Usage is checked against the plan; requests over the limit are rejected | Stage 4 |
| 3 | 429 and 402 with a clear message that explains why | Stage 4, Stage 7 |
| 4 | Monthly usage rolls up into a cost figure per tenant | Stage 6 |
| 5 | AI token pricing: cached input, reasoning as output, categories not simply added | Stage 6 |
| 6 | Pricing constants pinned in config, with proof of correct totals | Stage 6 (constants in app/config.py) |
| 7 | Subscription checkout works end-to-end in Stripe test mode | Stage 7 |
| 8 | Webhooks verify signatures, ignore duplicates, update plan and status | Stage 7 |
| 9 | Database with tenants, plans, subscriptions, usage events; data isolated per tenant | Stage 1, Stage 9 (isolation: Stage 5, Stage 6). Subscription state is columns on the tenants row, see README limitations |
| 10 | README, architecture diagram, setup instructions, required files | Stage 10 |

## Acceptance probes

| Probe | What | Proof |
|---|---|---|
| 1 | Same request twice with one key = one usage event, same response | Stage 5 |
| 2 | Exact quota boundary, then 429 / 402 with a clear message | Stage 4 |
| 3 | Stripe test Checkout flips Free to Pro, /usage shows the new limits | Stage 7 |
| 4 | Forged webhook gets 400; a replayed event is processed once | Stage 7 |
| 5 | Pricing rules give exact totals and /usage matches | Stage 6 |

## Shared requirements (every capstone)

| # | Requirement | Proof |
|---|---|---|
| 1 | Layered architecture: data, logic, HTTP separated | README (Architecture, Project layout) |
| 2 | Validation at the boundary: bad input gives a clean 4xx, never a 500 | Stage 3, Stage 7, Stage 8, Stage 10 |
| 3 | At least one background job with retries and a failure alert | Stage 8 |
| 4 | Real persistence: schema as migrations, indexes, isolated tenants | Stage 9 |
| 5 | Idempotency where it matters | Stage 5 (usage), Stage 7 (Stripe events) |
| 6 | Secrets clean: env only, never committed, never logged | Stage 10 |
| 7 | Cost tracked if AI is used | Not applicable: no AI model is called, token counts are simulated. Cost per tenant is in Stage 6 |

## Stage 1: rules enforced by the database

```
GOOD - blocked: event for a tenant that does not exist
       reason: FOREIGN KEY constraint failed
GOOD - blocked: negative number of calls
       reason: CHECK constraint failed: ck_usage_calls_nonneg
GOOD - blocked: same idempotency key twice for one tenant
       reason: UNIQUE constraint failed: usage_events.tenant_id, usage_events.idempotency_key
```

## Stage 2: API key authentication

```
no key:
{"detail":"Missing X-API-Key header."}
HTTP 401
wrong key:
{"detail":"Invalid API key."}
HTTP 401
Alpha key (value hidden):
{"tenant_id":1,"name":"Alpha Corp","plan":"free","monthly_call_quota":1000,"monthly_token_quota":100000,"subscription_status":"active"}
HTTP 200
```

## Stage 3: recording usage (POST /generate)

```
1) no API key:
{"detail":"Missing X-API-Key header."}
HTTP 401
2) no Idempotency-Key:
{"detail":"Send an Idempotency-Key header (1 to 128 characters)."}
HTTP 400
3) valid request:
{"result":"Simulated response. Usage recorded.","event_id":1,"tenant_id":1,"calls":1,"fresh_input_tokens":100,"cached_input_tokens":50,"output_tokens":500,"reasoning_tokens":200,"total_tokens":850,"recorded_at":"2026-10-25T07:28:09.447911Z"}
HTTP 200
4) negative tokens:
{"detail":[{"type":"greater_than_equal","loc":["body","fresh_input_tokens"],"msg":"Input should be greater than or equal to 0","input":-5,"ctx":{"ge":0}}]}
HTTP 422
5) same key again (temporary 409, Stage 5 improves it):
{"detail":"This Idempotency-Key was already used."}
HTTP 409
6) database rows (id, tenant, key, calls, fresh, cached, output, reasoning):
[(1, 1, 'stage3-test-001', 1, 100, 50, 500, 200)]
```

## Stage 4: quota enforcement (Probe 2)

Command: python -m scripts.check_quota (uses a temporary database)

```
--- Call boundary (Free plan = 1000 calls per month) ---
PASS - calls 1 to 1000 all return 200
PASS - call 1001 returns 429
PASS - 429 has a Retry-After header
   Retry-After: 577102 seconds
   body: {'detail': {'error': 'quota_exceeded', 'quota': 'calls', 'message': 'Monthly calls quota exceeded.', 'limit': 1000, 'used': 1000, 'requested': 1, 'retry_after_seconds': 577102, 'hint': 'Upgrade to the Pro plan for higher limits, or wait until the quota resets.'}}
PASS - exactly 1000 events are stored (the 1001st saved nothing)
--- Token boundary (Free plan = 100000 tokens per month) ---
PASS - a request landing exactly on the token limit returns 200
PASS - one more token returns 429
PASS - the error names the tokens quota
--- Payment lapsed (Pro plan, past_due) ---
PASS - past_due tenant returns 402
PASS - the 402 saved nothing
--- Race: 10 simultaneous requests when 999 of 1000 calls are used ---
   status codes: [200, 429, 429, 429, 429, 429, 429, 429, 429, 429]
PASS - exactly one request got 200
PASS - the other nine got 429
PASS - exactly 1000 events stored (never 1001 or more)

All checks passed.
```

## Stage 5: idempotency (Probe 1)

Command: python -m scripts.check_idempotency (uses a temporary database)

```
--- Same request twice (same key, same body) ---
PASS - first request returns 200
PASS - second request returns 200
PASS - second response is byte-for-byte the first
PASS - first response is not marked as replayed
PASS - second response is marked as replayed
PASS - exactly 1 usage event stored
   first : {'result': 'Simulated response. Usage recorded.', 'event_id': 1000, 'tenant_id': 1, 'calls': 1, 'fresh_input_tokens': 100, 'cached_input_tokens': 0, 'output_tokens': 500, 'reasoning_tokens': 0, 'total_tokens': 600, 'recorded_at': '2026-10-27T13:30:50.613251+00:00'}
   second: {'result': 'Simulated response. Usage recorded.', 'event_id': 1000, 'tenant_id': 1, 'calls': 1, 'fresh_input_tokens': 100, 'cached_input_tokens': 0, 'output_tokens': 500, 'reasoning_tokens': 0, 'total_tokens': 600, 'recorded_at': '2026-10-27T13:30:50.613251+00:00'}
--- Same key, different body ---
PASS - returns 422
PASS - error says idempotency_key_reused
PASS - still exactly 1 usage event
--- Same key text, different tenant ---
PASS - tenant B gets its own 200
PASS - tenant B's event belongs to tenant B
PASS - one event each (A and B)
--- Retry after the quota is used up (999 of 1000 calls used) ---
PASS - the 1000th call returns 200
PASS - a NEW key now returns 429
PASS - the rejected request did not use up its key
PASS - retrying the 1000th call returns 200, not 429
PASS - the retry returns the original answer
PASS - still exactly 1000 events
--- Race: 10 identical requests at the same moment ---
   status codes: [200, 200, 200, 200, 200, 200, 200, 200, 200, 200]
   replayed answers: 9
PASS - all 10 requests returned 200
PASS - only 1 usage event was created
PASS - all 10 answers are identical
PASS - exactly 9 answers were replays

All checks passed.
```

Same check against the real server (curl transcript, API key hidden):

```
1) first request:
HTTP/1.1 200 OK
date: Tue, 27 Oct 2026 13:32:47 GMT
server: uvicorn
content-length: 243
content-type: application/json

{"result":"Simulated response. Usage recorded.","event_id":3,"tenant_id":1,"calls":1,"fresh_input_tokens":100,"cached_input_tokens":0,"output_tokens":500,"reasoning_tokens":0,"total_tokens":600,"recorded_at":"2026-10-27T13:32:48.141716+00:00"}
2) same request again:
HTTP/1.1 200 OK
date: Tue, 27 Oct 2026 13:32:47 GMT
server: uvicorn
content-length: 243
content-type: application/json
idempotent-replayed: true

{"result":"Simulated response. Usage recorded.","event_id":3,"tenant_id":1,"calls":1,"fresh_input_tokens":100,"cached_input_tokens":0,"output_tokens":500,"reasoning_tokens":0,"total_tokens":600,"recorded_at":"2026-10-27T13:32:48.141716+00:00"}
3) same key, different body:
HTTP/1.1 422 Unprocessable Entity
date: Tue, 27 Oct 2026 13:32:47 GMT
server: uvicorn
content-length: 159
content-type: application/json

{"detail":{"error":"idempotency_key_reused","message":"This Idempotency-Key was already used with a different request body. Use a new key for a new request."}}
4) database:
events with this key: 1
saved responses with this key: 1
```

## Stage 6: GET /usage and pricing rules (Probe 5)

Command: python -m scripts.check_pricing (uses a temporary database)

```
```

Real server: GET /usage for tenant Alpha (API key hidden), with the raw sums used to check it by hand:

```
```

## Stage 7: Stripe Checkout and signed webhooks (Probes 3 and 4)

Command: python -m scripts.check_stripe (temporary database, signed test events, no real Stripe call)

```
--- Probe 3: Checkout flips the tenant Free -> Pro ---
PASS - checkout without an API key returns 401
PASS - checkout returns 200 and a payment url
PASS - the session asks for a subscription
PASS - the tenant id travels in client_reference_id
PASS - the tenant id is also copied onto the subscription metadata
PASS - without a price id configured, checkout returns 503
PASS - before the upgrade the Free tenant is blocked (429)
PASS - /usage shows the Free limit (1000 calls)
   webhook answer: {'received': True, 'outcome': 'applied'}
PASS - the signed checkout.session.completed returns 200
PASS - its outcome is applied
PASS - the tenant is now on the pro plan
PASS - the Stripe subscription id was saved
PASS - /usage shows the Pro plan
PASS - /usage shows the Pro limit (50000 calls)
PASS - /usage shows the 29.00 subscription fee
PASS - the same tenant can now make calls (200)
PASS - a Pro tenant asking for checkout again gets 409
--- Probe 4: forged and replayed webhooks ---
PASS - a forged signature returns 400
PASS - nothing changed after the forged event
PASS - a missing signature header returns 400
PASS - (test setup) the tampered body really is different
PASS - a body changed after signing returns 400
PASS - the tampered event upgraded nobody
PASS - a correctly signed but 1-hour-old delivery returns 400
PASS - a signed body that is not JSON returns 400, never 500
PASS - first delivery returns 200 and is applied
PASS - the tenant is Pro after the first delivery
   replay answer: {'received': True, 'outcome': 'duplicate'}
PASS - replaying the same event id returns 200
PASS - the replay is reported as a duplicate
PASS - the replay changed nothing (tenant is still free)
PASS - the event is stored exactly once
--- Events that must not break anything ---
PASS - an event for an unknown tenant returns 200 and is ignored
PASS - an event type we do not use returns 200 and is ignored
PASS - news about an OLDER subscription does not downgrade the tenant
--- Events arriving out of order ---
PASS - E1 checkout completed is applied (plan pro)
PASS - E2 past_due is applied
PASS - a past_due tenant is blocked with 402
PASS - E3 an older event arriving late is ignored (stale_ignored)
PASS - the tenant is still past_due (402)
PASS - E4 active is applied
PASS - the tenant can use the API again (200)
PASS - E5 subscription deleted is applied
PASS - the tenant is back on the free plan
PASS - the subscription id was cleared
PASS - E6 an older 'active' event cannot bring Pro back

All checks passed.
```

Real Stripe test mode (Probe 3). Tenant Alpha before and after paying with the 4242 test card. The upgrade came from a signed webhook, not from the success page:

```
before: {"tenant_id":1,"name":"Alpha Corp","plan":"free","monthly_call_quota":1000,"monthly_token_quota":100000,"subscription_status":"active"}
after:  {"tenant_id":1,"name":"Alpha Corp","plan":"pro","monthly_call_quota":50000,"monthly_token_quota":5000000,"subscription_status":"active"}
{
    "tenant_id": 1,
    "plan": "pro",
    "subscription_status": "active",
    "period": {
        "start": "2026-10-01T00:00:00+00:00",
        "end": "2026-11-01T00:00:00+00:00"
    },
    "calls": {
        "used": 3,
        "limit": 50000,
        "remaining": 49997
    },
    "tokens": {
        "used": 1460,
        "limit": 5000000,
        "remaining": 4998540,
        "fresh_input": 200,
        "cached_input": 50,
        "output": 1010,
        "reasoning": 200
    },
    "cost": {
        "currency": "USD",
        "unit": "micro-dollars (1 USD = 1,000,000)",
        "calls_micros": 300,
        "fresh_input_micros": 60,
        "cached_input_micros": 4,
        "output_and_reasoning_micros": 1815,
        "usage_micros": 2179,
        "subscription_micros": 29000000,
        "total_micros": 29002179,
        "total_usd": "29.002179"
    }
}
processed_webhook_events (event_id, type, tenant_id, outcome):
('evt_1UVoqpQrcVTMuLEC6YzpBIxl', 'checkout.session.completed', None, 'ignored')
('evt_1UVp0AQrcVTMuLEC0E7jUefE', 'checkout.session.completed', 1, 'applied')
```

Forged webhook against the real server (Probe 4):

```
{"detail":"Invalid webhook signature."}
HTTP 400
```

## Stage 8: background job with retries and a failure alert

Command: python -m scripts.check_cleanup (temporary database)

```
--- The cleanup job deletes only OLD receipts ---
PASS - two requests were accepted
PASS - 1202 receipts exist before the cleanup
PASS - the job deleted the 1201 old receipts (in several batches)
PASS - only the recent receipt is left
PASS - no usage event was touched (billing history is safe)
PASS - running the job again deletes nothing (safe to repeat)
PASS - a recent key can still be replayed
PASS - an expired key is refused with 409 (never a 500)
   409 answer: {'detail': 'This Idempotency-Key was already used and its saved response expired (older than 24 hours). Use a new key.'}
PASS - the expired key was NOT counted a second time
--- Retries with backoff, and the failure alert ---
PASS - a job that works the first time runs once and does not wait
PASS - a job that fails twice and then works is retried and succeeds
PASS - the waits grow: 1 second, then 2 seconds (backoff)
PASS - no alert was raised, because it finally worked
PASS - a job that always fails stops after 3 attempts
PASS - it waited only BETWEEN attempts (1s, 2s)
PASS - exactly one failure alert was saved
   alert row: t-broken 3 RuntimeError: simulated failure 3
PASS - the alert names the job, the attempts and the error
PASS - if the alert itself cannot be saved, the worker still does not crash
--- The job runs on a schedule, off the request path ---
PASS - the job ran several times by itself
PASS - it ran in its own thread, not in a request thread
PASS - after stop() it does not run any more
PASS - while a slow job is running, the API still answers fast
--- The job starts and stops with the application ---
PASS - the cleanup job is started with the application
PASS - it is stopped when the application stops
PASS - with BACKGROUND_JOBS_ENABLED=false no job is started

All checks passed.
```

The job running on a schedule in the real server (interval 10 seconds, retention 0 hours, set only for this demo):

```
2026-10-29 14:00:03,462 INFO [jobs] job cleanup-old-receipts started (every 10 seconds)
2026-10-29 14:00:13,491 INFO [jobs] job cleanup-old-receipts succeeded on attempt 1: 1
2026-10-29 14:00:23,500 INFO [jobs] job cleanup-old-receipts succeeded on attempt 1: 0
2026-10-29 14:00:33,502 INFO [jobs] job cleanup-old-receipts succeeded on attempt 1: 0
2026-10-29 14:00:43,517 INFO [jobs] job cleanup-old-receipts succeeded on attempt 1: 0
2026-10-29 14:00:53,531 INFO [jobs] job cleanup-old-receipts succeeded on attempt 1: 0
2026-10-29 14:01:03,535 INFO [jobs] job cleanup-old-receipts succeeded on attempt 1: 0
```

## Stage 9: schema as migrations (Alembic)

Command: python -m scripts.check_migrations (temporary database)

```
--- Build a brand new database from the migrations ---
PASS - the migration history has exactly one head
PASS - all 6 tables were created (plus the alembic_version table)
PASS - the 3 indexes were created
PASS - the database is at the latest revision
PASS - the migrated schema has NO differences from models.py
--- The seed script works on a migrated database ---
PASS - the first seed creates 2 plans and 2 tenants
PASS - the second seed creates nothing (safe to repeat)
PASS - exactly 2 plans and 2 tenants exist
--- The database rules exist in the MIGRATED schema ---
PASS - an event for a tenant that does not exist is refused (foreign key)
PASS - negative calls are refused (check constraint)
PASS - a valid usage event is accepted
PASS - the same (tenant, key) twice is refused (unique)
PASS - the same key for ANOTHER tenant is accepted (keys belong to one tenant)
PASS - deleting a tenant that has usage events is refused (RESTRICT protects the ledger)
PASS - deleting a plan that tenants use is refused
PASS - a second tenant with the same email is refused (unique)
PASS - the same receipt (tenant, key) twice is refused (unique)
PASS - the same Stripe event id twice is refused (primary key)
PASS - deleting a tenant with no billing history also removes its receipts (CASCADE)
--- Going back: downgrade to the empty database ---
PASS - downgrade removed every table (only alembic_version is left)
PASS - the database has no revision now
PASS - seed refuses to run without tables, and says what to do
--- An OLD database made by create_all, adopted with stamp ---
PASS - the old-style database has no revision yet
PASS - after stamp, the database is at the latest revision
PASS - upgrade after stamp changes nothing
PASS - the stamped old database matches the models (stamp was safe)
--- Repeatable: down and up again ---
PASS - up again gives the full schema
PASS - and still no differences from models.py

All checks passed.
```

A brand new database, built by the migrations and seeded (the two commands a stranger runs; API keys hidden):

```
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial_schema, initial schema
created plan: free
created plan: pro
created tenant: Alpha Corp (free)  API KEY: (hidden)
created tenant: Beta LLC (pro)  API KEY: (hidden)
second seed run:
plan already exists: free
plan already exists: pro
tenant already exists: Alpha Corp (its key was shown the first time)
tenant already exists: Beta LLC (its key was shown the first time)
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
0001_initial_schema (head)
```

My real database, adopted with 'alembic stamp head', then checked against the models:

```
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
0001_initial_schema (head)
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.schemas
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.tables
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.types
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.constraints
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.defaults
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.comments
No new upgrade operations detected.
```


## Stage 10: submission pack and final checks

All proof scripts together (python -m scripts.run_all_checks):

```
Running 6 proof scripts, one after another...
OK   Schema built from the migrations              28 checks passed    2.7s
OK   Quota boundary, 429 and 402 (Probe 2)         12 checks passed   19.0s
OK   Idempotent metering (Probe 1)                 22 checks passed    2.5s
OK   Pricing rules and /usage (Probe 5)            29 checks passed    2.2s
OK   Stripe checkout and webhooks (Probes 3, 4)    45 checks passed    2.6s
OK   Background job: retries and alert             25 checks passed    4.2s

Total: 161 checks passed, 0 failed.
All checks passed.
```

Dress rehearsal of a reviewer's run on a clean setup: temporary database, no Stripe keys, the documented run and seed commands, probes over HTTP (python -m scripts.check_submission):

```
--- The submission pack ---
PASS - README.md exists and is not empty
PASS - capstone.yaml exists and is not empty
PASS - EVIDENCE.md exists and is not empty
PASS - BUILDLOG.md exists and is not empty
PASS - .env.example exists and is not empty
PASS - .gitignore exists and is not empty
PASS - requirements.txt exists and is not empty
PASS - alembic.ini exists and is not empty
PASS - docs/design_doc.md exists and is not empty
PASS - README has an architecture section
PASS - README documents the run command
PASS - README documents the seed command
PASS - README has a limitations section
PASS - capstone.yaml has run:
PASS - capstone.yaml has seed:
PASS - capstone.yaml has test:
PASS - capstone.yaml has base_url:
PASS - capstone.yaml has endpoints:
PASS - EVIDENCE.md has no unfinished (todo) parts
PASS - EVIDENCE.md has the requirements checklist
PASS - .env.example lists every setting the app reads
PASS - .env is not tracked by git
PASS - .env.example is tracked by git
PASS - no database file is tracked by git
--- Start the system with the documented run command (temporary database, no Stripe keys) ---
PASS - the run command boots the system and /health answers
PASS - the seed command works on the fresh database
--- Probe the live system ---
PASS - the interactive docs page answers at /docs
PASS - GET /me without an API key returns 401
PASS - GET /me with a wrong key returns 401
PASS - the demo Free tenant is known
PASS - the demo Pro tenant has the Pro limits
PASS - POST /generate returns 200
PASS - the same request again returns 200 with the IDENTICAL body
PASS - the second answer is marked as a replay
PASS - the first answer is not marked as a replay
PASS - GET /usage shows exactly 1 call (the retry was not counted)
PASS - GET /usage shows the exact cost: 100 + 30 + 750 = 880 micro-dollars
PASS - POST /generate without Idempotency-Key returns 400
PASS - a negative token count returns 422, never 500
PASS - the same key with a different body returns 422
PASS - a forged webhook returns 400
PASS - the forged webhook changed nothing
PASS - checkout says 'not configured' (503) when there are no Stripe keys

All checks passed.
```

Secrets are clean (requirement 6). Scan of every file in every commit for Stripe secret keys and webhook secrets:

```
no Stripe secret key and no webhook secret in any file of any commit
```

Scan for my own API keys (mk_...). One old commit had a local demo key in a comment. It only ever opened my local database, and I rotated it:

```
commit f3a9af1, file .env.example (value not shown; this key was rotated, see BUILDLOG.md)
```

The only environment file tracked by git:

```
.env.example
```

Every log statement in the application, to show that no secret is logged (only error type names, ids and job names):

```
app/jobs/runner.py:29:    logger.critical(
app/jobs/runner.py:37:        logger.critical("could not save the alert row: %s", alert_error)
app/jobs/runner.py:55:            logger.info("job %s succeeded on attempt %d: %s", job_name, attempt, result)
app/jobs/runner.py:59:            logger.warning(
app/jobs/runner.py:84:        logger.info("job %s started (every %s seconds)", self.name, self.interval_seconds)
app/jobs/runner.py:90:        logger.info("job %s stopped", self.name)
app/jobs/runner.py:100:                logger.exception("unexpected error in the loop of job %s", self.name)
app/routers/webhooks.py:25:        logger.error("webhook rejected: STRIPE_WEBHOOK_SECRET is not configured")
app/routers/webhooks.py:41:        logger.warning("webhook rejected: invalid signature")
app/services/stripe_sync.py:64:        logger.error("Stripe refused the checkout session: %s", type(error).__name__)
app/services/stripe_sync.py:130:            logger.warning("checkout event for an unknown tenant: %s", tenant_id)
```
