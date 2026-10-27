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
