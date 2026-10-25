from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Tenant, UsageEvent


class QuotaExceededError(Exception):
    """The tenant used up its monthly allowance (HTTP 429)."""

    def __init__(self, quota, limit, used, requested, retry_after_seconds, plan_id):
        self.quota = quota  # "calls" or "tokens"
        self.limit = limit
        self.used = used
        self.requested = requested
        self.retry_after_seconds = retry_after_seconds
        self.plan_id = plan_id
        super().__init__(f"{quota} quota exceeded")


class PaymentRequiredError(Exception):
    """The tenant's subscription payment failed (HTTP 402)."""


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Start (included) and end (excluded) of the UTC calendar month of `now`."""
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def get_monthly_usage(
    db: Session, tenant_id: int, start: datetime, end: datetime
) -> tuple[int, int]:
    """Return (calls used, tokens used) for this tenant inside [start, end)."""
    all_tokens = (
        UsageEvent.fresh_input_tokens
        + UsageEvent.cached_input_tokens
        + UsageEvent.output_tokens
        + UsageEvent.reasoning_tokens
    )
    row = db.execute(
        select(
            func.coalesce(func.sum(UsageEvent.calls), 0),
            func.coalesce(func.sum(all_tokens), 0),
        ).where(
            UsageEvent.tenant_id == tenant_id,
            UsageEvent.recorded_at >= start,
            UsageEvent.recorded_at < end,
        )
    ).one()
    return int(row[0]), int(row[1])


def check_quota(
    db: Session, tenant: Tenant, requested_tokens: int, now: datetime | None = None
) -> None:
    """Raise an error if this request must be refused. Return nothing if it is allowed."""
    if tenant.subscription_status == "past_due":
        raise PaymentRequiredError()

    now = now or datetime.now(timezone.utc)
    start, end = month_bounds(now)
    used_calls, used_tokens = get_monthly_usage(db, tenant.id, start, end)

    plan = tenant.plan
    retry_after = max(1, int((end - now).total_seconds()))

    # Boundary rule: allowed when used + requested <= limit.
    if used_calls + 1 > plan.monthly_call_quota:
        raise QuotaExceededError(
            "calls", plan.monthly_call_quota, used_calls, 1, retry_after, plan.id
        )
    if used_tokens + requested_tokens > plan.monthly_token_quota:
        raise QuotaExceededError(
            "tokens",
            plan.monthly_token_quota,
            used_tokens,
            requested_tokens,
            retry_after,
            plan.id,
        )
