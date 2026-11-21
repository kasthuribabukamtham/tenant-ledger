from typing import Optional
from fastapi import Header, HTTPException, status
from pydantic import BaseModel


class RequestContext(BaseModel):
    tenant_id: str
    user_id: str
    user_role: str


async def get_request_context(
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID", description="Unique firm / tenant identifier"),
    x_user_id: Optional[str] = Header("system", alias="X-User-ID", description="Acting user identifier"),
    x_user_role: Optional[str] = Header("advisor", alias="X-User-Role", description="User role: advisor, admin, compliance, support"),
) -> RequestContext:
    if not x_tenant_id or not x_tenant_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required header 'X-Tenant-ID'. Multi-tenant isolation requires a valid firm identifier.",
        )
    
    tenant_id = x_tenant_id.strip().lower()
    user_role = (x_user_role or "advisor").strip().lower()
    valid_roles = {"advisor", "admin", "compliance", "support"}
    if user_role not in valid_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Invalid user role '{user_role}'. Must be one of: {', '.join(sorted(valid_roles))}",
        )

    return RequestContext(
        tenant_id=tenant_id,
        user_id=(x_user_id or "system").strip(),
        user_role=user_role,
    )
