from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth import get_current_tenant
from app.database import get_db
from app.models import Tenant
from app.services.metering import (
    DuplicateKeyError,
    IdempotencyKeyReuseError,
    record_usage,
)
from app.services.quota import PaymentRequiredError, QuotaExceededError

router = APIRouter()

MAX_TOKENS = 10_000_000  # a sanity limit for one request


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown fields are rejected

    fresh_input_tokens: int = Field(default=0, ge=0, le=MAX_TOKENS)
    cached_input_tokens: int = Field(default=0, ge=0, le=MAX_TOKENS)
    output_tokens: int = Field(default=0, ge=0, le=MAX_TOKENS)
    reasoning_tokens: int = Field(default=0, ge=0, le=MAX_TOKENS)


class GenerateResponse(BaseModel):
    result: str
    event_id: int
    tenant_id: int
    calls: int
    fresh_input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    recorded_at: str


@router.post("/generate", response_model=GenerateResponse)
def generate(
    body: GenerateRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None),
    tenant: Tenant = Depends(get_current_tenant),
    db: Session = Depends(get_db),
):
    """The dummy billable endpoint. Safe to retry: the same key counts once."""
    key = (idempotency_key or "").strip()
    if not key or len(key) > 128:
        raise HTTPException(
            status_code=400,
            detail="Send an Idempotency-Key header (1 to 128 characters).",
        )

    try:
        result, replayed = record_usage(db, tenant, key, body.model_dump())
    except IdempotencyKeyReuseError:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "idempotency_key_reused",
                "message": "This Idempotency-Key was already used with a "
                "different request body. Use a new key for a new request.",
            },
        )
    except PaymentRequiredError:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "payment_required",
                "message": "Your last subscription payment failed. "
                "Update your billing details to continue.",
            },
        )
    except QuotaExceededError as error:
        if error.plan_id == "free":
            hint = "Upgrade to the Pro plan for higher limits, or wait until the quota resets."
        else:
            hint = "Wait until the quota resets at the start of the next UTC month."
        raise HTTPException(
            status_code=429,
            detail={
                "error": "quota_exceeded",
                "quota": error.quota,
                "message": f"Monthly {error.quota} quota exceeded.",
                "limit": error.limit,
                "used": error.used,
                "requested": error.requested,
                "retry_after_seconds": error.retry_after_seconds,
                "hint": hint,
            },
            headers={"Retry-After": str(error.retry_after_seconds)},
        )
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="This Idempotency-Key was used before idempotency records "
            "existed. Use a new key.",
        )

    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return result
