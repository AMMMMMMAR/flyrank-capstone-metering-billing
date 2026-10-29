import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.config import settings
from app.database import SessionLocal
from app.models import IdempotencyRecord

logger = logging.getLogger("jobs")

BATCH_SIZE = 500  # delete in small batches, so the table is never locked for long


def cleanup_old_receipts(
    retention_hours: int | None = None, now: datetime | None = None
) -> int:
    """Delete idempotency receipts older than the retention time. Returns how many."""
    hours = settings.receipt_retention_hours if retention_hours is None else retention_hours
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    total = 0
    with SessionLocal() as db:
        while True:
            ids = db.scalars(
                select(IdempotencyRecord.id)
                .where(IdempotencyRecord.created_at < cutoff)
                .limit(BATCH_SIZE)
            ).all()
            if not ids:
                break
            db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.id.in_(ids)))
            db.commit()  # one small transaction per batch
            total += len(ids)
    return total
