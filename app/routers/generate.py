from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth import get_current_tenant
from app.database import get_db
from app.models import Tenant
from app.services.metering import DuplicateKeyError, record_usage

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
    recorded_at: datetime


@router.post("/generate", response_model=GenerateResponse)
def generate(
    body: GenerateRequest,
    idempotency_key: str | None = Header(default=None),
    tenant: Tenant = Depends(get_current_tenant),
    db: Session = Depends(get_db),
):
    """The dummy billable endpoint: records one usage event."""
    key = (idempotency_key or "").strip()
    if not key or len(key) > 128:
        raise HTTPException(
            status_code=400,
            detail="Send an Idempotency-Key header (1 to 128 characters).",
        )

    try:
        event = record_usage(
            db=db,
            tenant=tenant,
            idempotency_key=key,
            fresh_input_tokens=body.fresh_input_tokens,
            cached_input_tokens=body.cached_input_tokens,
            output_tokens=body.output_tokens,
            reasoning_tokens=body.reasoning_tokens,
        )
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="This Idempotency-Key was already used.",
        )

    total = (
        event.fresh_input_tokens
        + event.cached_input_tokens
        + event.output_tokens
        + event.reasoning_tokens
    )
    return GenerateResponse(
        result="Simulated response. Usage recorded.",
        event_id=event.id,
        tenant_id=event.tenant_id,
        calls=event.calls,
        fresh_input_tokens=event.fresh_input_tokens,
        cached_input_tokens=event.cached_input_tokens,
        output_tokens=event.output_tokens,
        reasoning_tokens=event.reasoning_tokens,
        total_tokens=total,
        recorded_at=event.recorded_at,
    )
