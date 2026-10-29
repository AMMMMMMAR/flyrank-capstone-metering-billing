from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # "free" or "pro"
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    monthly_call_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_token_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    base_price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("monthly_call_quota >= 0", name="ck_plans_calls_nonneg"),
        CheckConstraint("monthly_token_quota >= 0", name="ck_plans_tokens_nonneg"),
        CheckConstraint("base_price_cents >= 0", name="ck_plans_price_nonneg"),
    )


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    plan_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
    )
    # "active", "past_due" (payment failed). A canceled subscription simply
    # moves the tenant back to the free plan.
    subscription_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, unique=True
    )
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    plan: Mapped["Plan"] = relationship()


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)

    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    fresh_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_usage_tenant_key"),
        CheckConstraint("calls >= 0", name="ck_usage_calls_nonneg"),
        CheckConstraint("fresh_input_tokens >= 0", name="ck_usage_fresh_nonneg"),
        CheckConstraint("cached_input_tokens >= 0", name="ck_usage_cached_nonneg"),
        CheckConstraint("output_tokens >= 0", name="ck_usage_output_nonneg"),
        CheckConstraint("reasoning_tokens >= 0", name="ck_usage_reasoning_nonneg"),
        Index("idx_usage_tenant_recorded", "tenant_id", "recorded_at"),
    )


class IdempotencyRecord(Base):
    """The saved answer for one (tenant, Idempotency-Key). Used to replay retries."""

    __tablename__ = "idempotency_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # CASCADE is fine here: this table is a disposable cache, not the billing ledger.
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[str] = mapped_column(Text, nullable=False)  # JSON text
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_idempotency_tenant_key"),
        Index("idx_idempotency_created", "created_at"),  # for the cleanup job (later)
    )


class ProcessedWebhookEvent(Base):
    """One row per Stripe event we have seen. The PRIMARY KEY makes a replay impossible."""

    __tablename__ = "processed_webhook_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    event_created: Mapped[int] = mapped_column(Integer, nullable=False)  # Stripe time, in seconds
    tenant_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # "applied", "stale_ignored" or "ignored"
    outcome: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        Index("idx_webhook_tenant_created", "tenant_id", "event_created"),
    )


class JobAlert(Base):
    """A failure alert, saved when a background job fails after all its retries."""

    __tablename__ = "job_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(100), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
