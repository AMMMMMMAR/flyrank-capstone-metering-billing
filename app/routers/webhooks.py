import json
import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

try:
    from stripe import SignatureVerificationError
except ImportError:  # older versions of the stripe package
    from stripe.error import SignatureVerificationError

from app.config import settings
from app.database import get_db
from app.services.stripe_sync import handle_webhook

router = APIRouter()
logger = logging.getLogger("billing")


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    if not settings.stripe_webhook_secret:
        # Without the secret nothing can be verified, so nothing is accepted.
        logger.error("webhook rejected: STRIPE_WEBHOOK_SECRET is not configured")
        raise HTTPException(
            status_code=400,
            detail="Webhook signature cannot be verified: the webhook secret is not configured.",
        )

    # The signature covers the RAW bytes. Read them BEFORE any JSON parsing.
    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    if not signature:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header.")

    try:
        stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)
    except (ValueError, SignatureVerificationError):
        # Same answer for every cause, so an attacker learns nothing.
        logger.warning("webhook rejected: invalid signature")
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")

    try:
        event = json.loads(payload)
        event["id"], event["type"], int(event["created"])
        if not isinstance(event["data"]["object"], dict):
            raise TypeError("data.object must be an object")
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="Malformed event.")

    outcome = handle_webhook(db, event)
    return {"received": True, "outcome": outcome}
