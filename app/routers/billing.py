from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from app.auth import get_current_tenant
from app.models import Tenant
from app.services.stripe_sync import (
    AlreadyProError,
    CheckoutCreationError,
    StripeNotConfiguredError,
    create_checkout_session,
)

router = APIRouter()


@router.post("/checkout-session")
def checkout_session(tenant: Tenant = Depends(get_current_tenant)):
    """Create a Stripe Checkout page (test mode) to upgrade the caller to Pro."""
    try:
        return create_checkout_session(tenant)
    except StripeNotConfiguredError:
        raise HTTPException(
            status_code=503,
            detail="Stripe is not configured. Set STRIPE_SECRET_KEY and STRIPE_PRICE_ID in .env.",
        )
    except AlreadyProError:
        raise HTTPException(status_code=409, detail="This tenant is already on the Pro plan.")
    except CheckoutCreationError:
        raise HTTPException(
            status_code=502, detail="Stripe could not create the checkout session."
        )


@router.get("/checkout/success", response_class=HTMLResponse)
def checkout_success():
    # This page changes NOTHING. Only a verified webhook changes the plan.
    return (
        "<h1>Payment received</h1>"
        "<p>Your plan is switched by a verified Stripe webhook within a few seconds. "
        "Check GET /usage.</p>"
    )


@router.get("/checkout/cancel", response_class=HTMLResponse)
def checkout_cancel():
    return "<h1>Checkout canceled</h1><p>Nothing was charged.</p>"
