from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Tenant, UsageEvent


class DuplicateKeyError(Exception):
    """This tenant already used this idempotency key."""


def record_usage(
    db: Session,
    tenant: Tenant,
    idempotency_key: str,
    fresh_input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
) -> UsageEvent:
    """Save ONE usage event for this tenant (one call = one event)."""
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
        # The database refused the row: same tenant + same key already exists.
        db.rollback()
        raise DuplicateKeyError(idempotency_key)
    return event
