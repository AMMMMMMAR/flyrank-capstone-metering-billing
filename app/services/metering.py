import hashlib
import json
import threading

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import IdempotencyRecord, Tenant, UsageEvent
from app.services.quota import PaymentRequiredError, QuotaExceededError, check_quota


class DuplicateKeyError(Exception):
    """The key is in usage_events but has no saved response (data from before Stage 5)."""


class IdempotencyKeyReuseError(Exception):
    """The same key was sent again with a DIFFERENT request body."""


# One Python lock per tenant (used with SQLite, which has no row locks).
_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _tenant_lock(tenant_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(tenant_id, threading.Lock())


def make_request_hash(payload: dict) -> str:
    """A fingerprint of the request body. Same body -> same fingerprint."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(("POST /generate " + canonical).encode("utf-8")).hexdigest()


def _find_record(db: Session, tenant_id: int, key: str) -> IdempotencyRecord | None:
    return db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.key == key,
        )
    )


def record_usage(
    db: Session, tenant: Tenant, idempotency_key: str, payload: dict
) -> tuple[dict, bool]:
    """Meter one call exactly once.

    Returns (response, replayed). replayed is True when we returned a saved
    response instead of doing the work again.
    """
    tenant_id = tenant.id
    request_hash = make_request_hash(payload)
    fresh = payload["fresh_input_tokens"]
    cached = payload["cached_input_tokens"]
    output = payload["output_tokens"]
    reasoning = payload["reasoning_tokens"]
    requested_tokens = fresh + cached + output + reasoning

    with _tenant_lock(tenant_id):
        # PostgreSQL: lock this tenant's row until we commit or roll back.
        # SQLite ignores FOR UPDATE; the Python lock above protects us instead.
        db.scalar(
            select(Tenant)
            .where(Tenant.id == tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        # 1) Idempotency first: have we already answered this key?
        existing = _find_record(db, tenant_id, idempotency_key)
        if existing is not None:
            stored_hash = existing.request_hash
            stored_body = existing.response_body
            db.rollback()  # release the lock, we change nothing
            if stored_hash != request_hash:
                raise IdempotencyKeyReuseError(idempotency_key)
            return json.loads(stored_body), True

        # 2) New key: is the tenant allowed to do this?
        try:
            check_quota(db, tenant, requested_tokens)
        except (QuotaExceededError, PaymentRequiredError):
            db.rollback()  # save nothing, the key is not used up
            raise

        # 3) Save the usage event AND the response in ONE transaction.
        event = UsageEvent(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            calls=1,  # the server decides this, never the client
            fresh_input_tokens=fresh,
            cached_input_tokens=cached,
            output_tokens=output,
            reasoning_tokens=reasoning,
        )
        db.add(event)
        db.flush()  # gives the event its id and recorded_at

        result = {
            "result": "Simulated response. Usage recorded.",
            "event_id": event.id,
            "tenant_id": tenant_id,
            "calls": event.calls,
            "fresh_input_tokens": fresh,
            "cached_input_tokens": cached,
            "output_tokens": output,
            "reasoning_tokens": reasoning,
            "total_tokens": requested_tokens,
            "recorded_at": event.recorded_at.isoformat(),
        }
        db.add(
            IdempotencyRecord(
                tenant_id=tenant_id,
                key=idempotency_key,
                request_hash=request_hash,
                response_status=200,
                response_body=json.dumps(result),
            )
        )

        try:
            db.commit()
        except IntegrityError:
            # The database refused a duplicate. Normally the lock prevents this.
            db.rollback()
            existing = _find_record(db, tenant_id, idempotency_key)
            if existing is None:
                raise DuplicateKeyError(idempotency_key)
            stored_hash = existing.request_hash
            stored_body = existing.response_body
            db.rollback()
            if stored_hash != request_hash:
                raise IdempotencyKeyReuseError(idempotency_key)
            return json.loads(stored_body), True

    return result, False
