from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import MICROS_PER_CENT
from app.models import Tenant, UsageEvent
from app.services.pricing import calculate_cost, format_usd
from app.services.quota import month_bounds


def get_usage_report(db: Session, tenant: Tenant, now: datetime | None = None) -> dict:
    """Everything about this tenant's usage and cost in the current UTC month."""
    now = now or datetime.now(timezone.utc)
    start, end = month_bounds(now)

    row = db.execute(
        select(
            func.coalesce(func.sum(UsageEvent.calls), 0),
            func.coalesce(func.sum(UsageEvent.fresh_input_tokens), 0),
            func.coalesce(func.sum(UsageEvent.cached_input_tokens), 0),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0),
            func.coalesce(func.sum(UsageEvent.reasoning_tokens), 0),
        ).where(
            UsageEvent.tenant_id == tenant.id,  # only THIS tenant's rows
            UsageEvent.recorded_at >= start,
            UsageEvent.recorded_at < end,
        )
    ).one()
    calls, fresh, cached, output, reasoning = (int(value) for value in row)
    tokens_used = fresh + cached + output + reasoning

    plan = tenant.plan
    cost = calculate_cost(calls, fresh, cached, output, reasoning)
    subscription_micros = plan.base_price_cents * MICROS_PER_CENT
    total_micros = cost["usage_micros"] + subscription_micros

    return {
        "tenant_id": tenant.id,
        "plan": plan.id,
        "subscription_status": tenant.subscription_status,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "calls": {
            "used": calls,
            "limit": plan.monthly_call_quota,
            "remaining": max(0, plan.monthly_call_quota - calls),
        },
        "tokens": {
            "used": tokens_used,
            "limit": plan.monthly_token_quota,
            "remaining": max(0, plan.monthly_token_quota - tokens_used),
            "fresh_input": fresh,
            "cached_input": cached,
            "output": output,
            "reasoning": reasoning,
        },
        "cost": {
            "currency": "USD",
            "unit": "micro-dollars (1 USD = 1,000,000)",
            **cost,
            "subscription_micros": subscription_micros,
            "total_micros": total_micros,
            "total_usd": format_usd(total_micros),
        },
    }
