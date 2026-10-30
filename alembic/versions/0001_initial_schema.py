"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-10-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("monthly_call_quota", sa.Integer(), nullable=False),
        sa.Column("monthly_token_quota", sa.Integer(), nullable=False),
        sa.Column("base_price_cents", sa.Integer(), nullable=False),
        sa.CheckConstraint("monthly_call_quota >= 0", name="ck_plans_calls_nonneg"),
        sa.CheckConstraint("monthly_token_quota >= 0", name="ck_plans_tokens_nonneg"),
        sa.CheckConstraint("base_price_cents >= 0", name="ck_plans_price_nonneg"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("api_key_hash", sa.String(length=64), nullable=False),
        sa.Column("plan_id", sa.String(length=20), nullable=False),
        sa.Column("subscription_status", sa.String(length=20), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=100), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("api_key_hash"),
        sa.UniqueConstraint("stripe_customer_id"),
        sa.UniqueConstraint("stripe_subscription_id"),
    )

    op.create_table(
        "usage_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("fresh_input_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("calls >= 0", name="ck_usage_calls_nonneg"),
        sa.CheckConstraint("fresh_input_tokens >= 0", name="ck_usage_fresh_nonneg"),
        sa.CheckConstraint("cached_input_tokens >= 0", name="ck_usage_cached_nonneg"),
        sa.CheckConstraint("output_tokens >= 0", name="ck_usage_output_nonneg"),
        sa.CheckConstraint("reasoning_tokens >= 0", name="ck_usage_reasoning_nonneg"),
        # RESTRICT: a tenant with billing history can never be deleted.
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_usage_tenant_key"),
    )
    op.create_index(
        "idx_usage_tenant_recorded", "usage_events", ["tenant_id", "recorded_at"]
    )

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # CASCADE is fine here: receipts are only a disposable cache.
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "key", name="uq_idempotency_tenant_key"),
    )
    op.create_index("idx_idempotency_created", "idempotency_records", ["created_at"])

    op.create_table(
        "processed_webhook_events",
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("event_created", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "idx_webhook_tenant_created",
        "processed_webhook_events",
        ["tenant_id", "event_created"],
    )

    op.create_table(
        "job_alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_name", sa.String(length=100), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    # WARNING: this deletes every table, and all the data in them.
    op.drop_table("job_alerts")
    op.drop_index("idx_webhook_tenant_created", table_name="processed_webhook_events")
    op.drop_table("processed_webhook_events")
    op.drop_index("idx_idempotency_created", table_name="idempotency_records")
    op.drop_table("idempotency_records")
    op.drop_index("idx_usage_tenant_recorded", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_table("tenants")
    op.drop_table("plans")
