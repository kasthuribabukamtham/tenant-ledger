import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Any
from sqlalchemy.orm import Session
from app.models import AuditEvent


class AuditService:
    @staticmethod
    def log_event(
        db: Session,
        tenant_id: str,
        actor_id: str,
        actor_role: str,
        action: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> AuditEvent:
        event = AuditEvent(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_role=actor_role,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=json.dumps(details) if details else None,
            created_at=datetime.now(timezone.utc),
        )
        db.add(event)
        # Note: caller will commit along with the transaction
        return event

    @staticmethod
    def get_events(
        db: Session,
        tenant_id: str,
        actor_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        query = db.query(AuditEvent).filter(AuditEvent.tenant_id == tenant_id)
        if actor_id:
            query = query.filter(AuditEvent.actor_id == actor_id)
        if resource_id:
            query = query.filter(AuditEvent.resource_id == resource_id)
        if action:
            query = query.filter(AuditEvent.action == action)
        return query.order_by(AuditEvent.created_at.desc()).offset(offset).limit(limit).all()
