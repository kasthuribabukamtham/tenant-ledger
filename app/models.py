import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Numeric,
    DateTime,
    Boolean,
    Text,
    Index,
    UniqueConstraint,
    Enum as SQLEnum,
)
from app.database import Base


class TransactionType(str, enum.Enum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    FEE = "FEE"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    REVERSAL = "REVERSAL"


class TransactionDirection(str, enum.Enum):
    CREDIT = "CREDIT"  # Increases client balance
    DEBIT = "DEBIT"    # Decreases client balance


class LedgerTransaction(Base):
    __tablename__ = "ledger_transactions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(64), nullable=False, index=True)
    client_ref = Column(String(128), nullable=False, index=True)
    external_id = Column(String(128), nullable=True, index=True)
    content_hash = Column(String(64), nullable=True, index=True)

    type = Column(SQLEnum(TransactionType), nullable=False)
    direction = Column(SQLEnum(TransactionDirection), nullable=False)
    amount = Column(Numeric(precision=18, scale=4), nullable=False)
    currency = Column(String(3), nullable=False)

    effective_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    created_by = Column(String(64), nullable=False, default="system")
    description = Column(Text, nullable=True)

    is_reversed = Column(Boolean, default=False, nullable=False)
    reversed_by_id = Column(String(36), nullable=True)
    reverses_transaction_id = Column(String(36), nullable=True)
    transfer_id = Column(String(36), nullable=True, index=True)
    metadata_json = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "external_id", name="uq_tenant_external_id"),
        Index("ix_tenant_client_effective", "tenant_id", "client_ref", "effective_at"),
        Index("ix_tenant_duplicate_check", "tenant_id", "client_ref", "content_hash"),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(64), nullable=False, index=True)
    actor_id = Column(String(64), nullable=False)
    actor_role = Column(String(32), nullable=False, default="advisor")
    action = Column(String(64), nullable=False)  # e.g. TRANSACTION_CREATED, TRANSACTION_REVERSED, BULK_INGEST
    resource_type = Column(String(64), nullable=False)  # e.g. transaction, client, batch
    resource_id = Column(String(64), nullable=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    __table_args__ = (
        Index("ix_tenant_audit_created", "tenant_id", "created_at"),
    )
