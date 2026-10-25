import threading

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Tenant, UsageEvent
from app.services.quota import PaymentRequiredError, QuotaExceededError, check_quota


class DuplicateKeyError(Exception):
    """This tenant already used this idempotency key."""


# One Python lock per tenant (used with SQLite, which has no row locks).
_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _tenant_lock(tenant_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(tenant_id, threading.Lock())


def record_usage(
    db: Session,
    tenant: Tenant,
    idempotency_key: str,
    fresh_input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
) -> UsageEvent:
    """Check the quota, then save ONE usage event. Both steps happen under one lock."""
    requested_tokens = (
        fresh_input_tokens + cached_input_tokens + output_tokens + reasoning_tokens
    )

    with _tenant_lock(tenant.id):
        # PostgreSQL: lock this tenant's row until we commit or roll back.
        # SQLite ignores FOR UPDATE; the Python lock above protects us instead.
        db.scalar(
            select(Tenant)
            .where(Tenant.id == tenant.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        try:
            check_quota(db, tenant, requested_tokens)
        except (QuotaExceededError, PaymentRequiredError):
            db.rollback()  # release the lock, save nothing
            raise

        event = UsageEvent(
            tenant_id=tenant.id,
            idempotency_key=idempotency_key,
            calls=1,  # the server decides this, never the client
            fresh_input_tokens=fresh_input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        db.add(event)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise DuplicateKeyError(idempotency_key)

    return event
