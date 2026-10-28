from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_tenant
from app.database import get_db
from app.models import Tenant
from app.services.usage import get_usage_report

router = APIRouter()


class Period(BaseModel):
    start: str
    end: str


class CallsUsage(BaseModel):
    used: int
    limit: int
    remaining: int


class TokensUsage(BaseModel):
    used: int
    limit: int
    remaining: int
    fresh_input: int
    cached_input: int
    output: int
    reasoning: int


class Cost(BaseModel):
    currency: str
    unit: str
    calls_micros: int
    fresh_input_micros: int
    cached_input_micros: int
    output_and_reasoning_micros: int
    usage_micros: int
    subscription_micros: int
    total_micros: int
    total_usd: str


class UsageResponse(BaseModel):
    tenant_id: int
    plan: str
    subscription_status: str
    period: Period
    calls: CallsUsage
    tokens: TokensUsage
    cost: Cost


@router.get("/usage", response_model=UsageResponse)
def usage(
    tenant: Tenant = Depends(get_current_tenant),
    db: Session = Depends(get_db),
):
    """This month's usage, limits and exact cost for the calling tenant."""
    return get_usage_report(db, tenant)
