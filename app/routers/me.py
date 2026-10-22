from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import get_current_tenant
from app.models import Tenant

router = APIRouter()


class MeResponse(BaseModel):
    tenant_id: int
    name: str
    plan: str
    monthly_call_quota: int
    monthly_token_quota: int
    subscription_status: str


@router.get("/me", response_model=MeResponse)
def me(tenant: Tenant = Depends(get_current_tenant)):
    """Who am I? Returns the tenant that owns the API key."""
    return MeResponse(
        tenant_id=tenant.id,
        name=tenant.name,
        plan=tenant.plan_id,
        monthly_call_quota=tenant.plan.monthly_call_quota,
        monthly_token_quota=tenant.plan.monthly_token_quota,
        subscription_status=tenant.subscription_status,
    )
