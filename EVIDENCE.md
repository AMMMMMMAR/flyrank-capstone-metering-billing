# Evidence

One proof per requirement. Filled in as each stage is finished.

## Probe 1: Idempotency
(todo)

## Probe 2: Quota boundary
(todo)

## Probe 3: Stripe checkout upgrades the plan
(todo)

## Probe 4: Forged webhook and replay
(todo)

## Probe 5: Pricing rules
(todo)

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
