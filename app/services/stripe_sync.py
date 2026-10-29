import logging

import stripe
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

try:
    from stripe import StripeError
except ImportError:  # older versions of the stripe package
    from stripe.error import StripeError

from app.config import settings
from app.models import ProcessedWebhookEvent, Tenant

logger = logging.getLogger("billing")

# What a Stripe subscription status means for us.
ACTIVE_STATUSES = {"active", "trialing"}
PAYMENT_PROBLEM_STATUSES = {"past_due", "unpaid"}
ENDED_STATUSES = {"canceled", "incomplete_expired"}


class StripeNotConfiguredError(Exception):
    """STRIPE_SECRET_KEY or STRIPE_PRICE_ID is missing."""


class AlreadyProError(Exception):
    """The tenant is already on the Pro plan."""


class CheckoutCreationError(Exception):
    """Stripe refused to create the checkout session."""


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------
def create_checkout_session(tenant: Tenant) -> dict:
    """Ask Stripe for a hosted payment page for the Pro plan."""
    if not settings.stripe_secret_key or not settings.stripe_price_id:
        raise StripeNotConfiguredError()
    if tenant.plan_id == "pro":
        raise AlreadyProError()

    stripe.api_key = settings.stripe_secret_key
    params = dict(
        mode="subscription",
        line_items=[{"price": settings.stripe_price_id, "quantity": 1}],
        # The tenant id travels with the payment, so the webhook knows who paid.
        client_reference_id=str(tenant.id),
        subscription_data={"metadata": {"tenant_id": str(tenant.id)}},
        success_url=f"{settings.app_base_url}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.app_base_url}/checkout/cancel",
    )
    if tenant.stripe_customer_id:
        params["customer"] = tenant.stripe_customer_id
    else:
        params["customer_email"] = tenant.email

    try:
        session = stripe.checkout.Session.create(**params)
    except StripeError as error:
        logger.error("Stripe refused the checkout session: %s", type(error).__name__)
        raise CheckoutCreationError()
    return {"checkout_url": session.url, "session_id": session.id}


# ---------------------------------------------------------------------------
# Webhooks (the event is already VERIFIED when it gets here)
# ---------------------------------------------------------------------------
def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _find_tenant_for_subscription(db: Session, sub: dict) -> Tenant | None:
    """Find the tenant a subscription event is about: by subscription id, then by the
    tenant id we put in the metadata, then by the Stripe customer id."""
    tenant = None
    if sub.get("id"):
        tenant = db.scalar(
            select(Tenant).where(Tenant.stripe_subscription_id == sub["id"])
        )
    if tenant is None:
        tenant_id = _to_int((sub.get("metadata") or {}).get("tenant_id"))
        if tenant_id is not None:
            tenant = db.get(Tenant, tenant_id)
    if tenant is None and sub.get("customer"):
        tenant = db.scalar(
            select(Tenant).where(Tenant.stripe_customer_id == sub["customer"])
        )
    return tenant


def _is_stale(db: Session, tenant_id: int, event_created: int) -> bool:
    """True if we already applied a NEWER event for this tenant."""
    latest = db.scalar(
        select(func.max(ProcessedWebhookEvent.event_created)).where(
            ProcessedWebhookEvent.tenant_id == tenant_id,
            ProcessedWebhookEvent.outcome == "applied",
        )
    )
    return latest is not None and event_created < latest


def _link_stripe_ids(tenant: Tenant, customer_id, subscription_id) -> None:
    if customer_id:
        tenant.stripe_customer_id = customer_id
    if subscription_id:
        tenant.stripe_subscription_id = subscription_id


def _apply(db: Session, event: dict, record: ProcessedWebhookEvent) -> str:
    """Change the tenant if the event says so. Returns the outcome text."""
    event_type = event["type"]
    obj = event["data"]["object"]
    created = record.event_created

    if event_type == "checkout.session.completed":
        if obj.get("mode") != "subscription":
            return "ignored"
        if obj.get("payment_status") not in ("paid", "no_payment_required"):
            return "ignored"
        tenant_id = _to_int(obj.get("client_reference_id"))
        tenant = db.get(Tenant, tenant_id) if tenant_id is not None else None
        if tenant is None:
            logger.warning("checkout event for an unknown tenant: %s", tenant_id)
            return "ignored"
        record.tenant_id = tenant.id
        if _is_stale(db, tenant.id, created):
            return "stale_ignored"
        tenant.plan_id = "pro"
        tenant.subscription_status = "active"
        _link_stripe_ids(tenant, obj.get("customer"), obj.get("subscription"))
        return "applied"

    if event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        tenant = _find_tenant_for_subscription(db, obj)
        if tenant is None:
            return "ignored"
        record.tenant_id = tenant.id
        current = tenant.stripe_subscription_id
        if current and obj.get("id") and obj["id"] != current:
            return "ignored"  # news about an OLDER subscription, not the current one
        if _is_stale(db, tenant.id, created):
            return "stale_ignored"

        status = (
            "canceled"
            if event_type == "customer.subscription.deleted"
            else obj.get("status")
        )
        if status in ENDED_STATUSES:
            tenant.plan_id = "free"
            tenant.subscription_status = "active"
            tenant.stripe_subscription_id = None
            return "applied"
        if status in ACTIVE_STATUSES or status in PAYMENT_PROBLEM_STATUSES:
            tenant.plan_id = "pro"
            tenant.subscription_status = (
                "active" if status in ACTIVE_STATUSES else "past_due"
            )
            _link_stripe_ids(tenant, obj.get("customer"), obj.get("id"))
            return "applied"
        return "ignored"  # for example "incomplete" or "paused"

    return "ignored"  # an event type we do not use


def handle_webhook(db: Session, event: dict) -> str:
    """Process one verified event exactly once.

    Returns "duplicate", "applied", "stale_ignored" or "ignored".
    The event id and the tenant change are saved in ONE transaction.
    """
    record = ProcessedWebhookEvent(
        event_id=event["id"],
        event_type=event["type"],
        event_created=int(event["created"]),
    )
    db.add(record)
    try:
        db.flush()  # claim the event id. The PRIMARY KEY refuses a second claim.
    except IntegrityError:
        db.rollback()
        return "duplicate"

    try:
        outcome = _apply(db, event, record)
        record.outcome = outcome
        db.commit()  # the claim and the tenant change are saved together
    except Exception:
        db.rollback()  # the claim is undone too, so Stripe can retry later
        raise
    return outcome
