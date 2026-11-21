from decimal import Decimal
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Any
from pydantic import BaseModel, Field, field_validator, model_validator
from app.config import settings
from app.models import TransactionType, TransactionDirection


class TransactionBase(BaseModel):
    client_ref: str = Field(..., min_length=1, max_length=128, description="Opaque client reference identifier")
    type: TransactionType = Field(..., description="Transaction type: DEPOSIT, WITHDRAWAL, FEE")
    amount: Decimal = Field(..., gt=0, decimal_places=4, description="Strictly positive transaction amount")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 3-letter currency code")
    effective_at: datetime = Field(..., description="Date and time when transaction became effective")
    external_id: Optional[str] = Field(None, max_length=128, description="External reference / Idempotency key from custodian or source")
    description: Optional[str] = Field(None, max_length=500)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        v_upper = v.upper()
        if v_upper not in settings.SUPPORTED_CURRENCIES:
            raise ValueError(f"Unsupported currency '{v}'. Must be one of: {sorted(list(settings.SUPPORTED_CURRENCIES))}")
        return v_upper

    @field_validator("effective_at")
    @classmethod
    def validate_effective_at(cls, v: datetime) -> datetime:
        now_utc = datetime.now(timezone.utc)
        # Ensure UTC timezone awareness
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        # Allow max 60 seconds clock skew into the future
        if v > now_utc + timedelta(seconds=60):
            raise ValueError(f"Effective date cannot be in the future: {v.isoformat()} > {now_utc.isoformat()}")
        return v

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: TransactionType) -> TransactionType:
        if v in (TransactionType.TRANSFER_IN, TransactionType.TRANSFER_OUT, TransactionType.REVERSAL):
            raise ValueError(f"Direct posting of type '{v.value}' is restricted. Use transfer or reversal endpoints.")
        return v


class SingleTransactionCreate(TransactionBase):
    pass


class TransferCreate(BaseModel):
    from_client_ref: str = Field(..., min_length=1, max_length=128)
    to_client_ref: str = Field(..., min_length=1, max_length=128)
    amount: Decimal = Field(..., gt=0, decimal_places=4)
    currency: str = Field(..., min_length=3, max_length=3)
    effective_at: datetime = Field(...)
    external_id: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = Field(None, max_length=500)

    @model_validator(mode="after")
    def check_distinct_clients(self) -> "TransferCreate":
        if self.from_client_ref == self.to_client_ref:
            raise ValueError("Transfer sender and recipient client references must be different.")
        return self

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        v_upper = v.upper()
        if v_upper not in settings.SUPPORTED_CURRENCIES:
            raise ValueError(f"Unsupported currency '{v}'. Must be one of: {sorted(list(settings.SUPPORTED_CURRENCIES))}")
        return v_upper

    @field_validator("effective_at")
    @classmethod
    def validate_effective_at(cls, v: datetime) -> datetime:
        now_utc = datetime.now(timezone.utc)
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        if v > now_utc + timedelta(seconds=60):
            raise ValueError("Effective date cannot be in the future.")
        return v


class ReversalCreate(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500, description="Audit reason for reversing this transaction")


class BulkRowError(BaseModel):
    row_index: int
    client_ref: Optional[str] = None
    external_id: Optional[str] = None
    error: str


class TransactionResponse(BaseModel):
    id: str
    tenant_id: str
    client_ref: str
    external_id: Optional[str]
    type: TransactionType
    direction: TransactionDirection
    amount: Decimal
    currency: str
    effective_at: datetime
    created_at: datetime
    created_by: str
    description: Optional[str]
    is_reversed: bool
    reversed_by_id: Optional[str]
    reverses_transaction_id: Optional[str]
    transfer_id: Optional[str]

    model_config = {"from_attributes": True}


class BulkIngestResponse(BaseModel):
    total_records: int
    accepted_count: int
    rejected_count: int
    duplicate_count: int
    accepted: List[TransactionResponse]
    errors: List[BulkRowError]


class ClientBalanceResponse(BaseModel):
    tenant_id: str
    client_ref: str
    currency: str
    balance: Decimal
    as_of: datetime
    transaction_count: int


class PeriodSummaryResponse(BaseModel):
    tenant_id: str
    client_ref: str
    currency: str
    start_date: datetime
    end_date: datetime
    starting_balance: Decimal
    total_inflows: Decimal
    total_outflows: Decimal
    total_fees: Decimal
    net_change: Decimal
    ending_balance: Decimal
    transaction_count: int


class SupportClientLookupResponse(BaseModel):
    tenant_id: str
    client_ref: str
    currency: str
    balance: Decimal
    account_status: str
    last_active: Optional[datetime]
    recent_transaction_count: int


class AuditEventResponse(BaseModel):
    id: str
    tenant_id: str
    actor_id: str
    actor_role: str
    action: str
    resource_type: str
    resource_id: Optional[str]
    details: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}
