from sqlalchemy.exc import IntegrityError

from app import models
from app.database import SessionLocal


def expect_blocked(label, events):
    with SessionLocal() as db:
        try:
            db.add_all(events)
            db.commit()
            print(f"BAD  - was accepted: {label}")
        except IntegrityError as error:
            db.rollback()
            print(f"GOOD - blocked: {label}")
            print(f"       reason: {error.orig}")


expect_blocked(
    "event for a tenant that does not exist",
    [models.UsageEvent(tenant_id=9999, idempotency_key="t1")],
)
expect_blocked(
    "negative number of calls",
    [models.UsageEvent(tenant_id=1, idempotency_key="t2", calls=-5)],
)
expect_blocked(
    "same idempotency key twice for one tenant",
    [
        models.UsageEvent(tenant_id=1, idempotency_key="t3"),
        models.UsageEvent(tenant_id=1, idempotency_key="t3"),
    ],
)
