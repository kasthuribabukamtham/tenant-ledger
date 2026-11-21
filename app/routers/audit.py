from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import RequestContext, get_request_context
from app.schemas import AuditEventResponse
from app.services.audit_service import AuditService

router = APIRouter(prefix="/api/v1/audit", tags=["Audit"])


@router.get("/events", response_model=List[AuditEventResponse])
def get_audit_events(
    actor_id: Optional[str] = Query(None, description="Filter by user ID"),
    resource_id: Optional[str] = Query(None, description="Filter by resource ID (transaction/client/batch)"),
    action: Optional[str] = Query(None, description="Filter by action name"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
):
    """
    Query compliance audit trail: who changed what, when, and with what parameters.
    Accessible to compliance, admin, and advisory personnel.
    """
    if ctx.user_role == "support":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Support role is not authorized to inspect audit logs.",
        )

    events = AuditService.get_events(
        db=db,
        tenant_id=ctx.tenant_id,
        actor_id=actor_id,
        resource_id=resource_id,
        action=action,
        limit=limit,
        offset=offset,
    )
    return events
