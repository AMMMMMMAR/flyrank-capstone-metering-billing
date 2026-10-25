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
