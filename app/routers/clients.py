from datetime import datetime, timezone
from typing import Optional, List, Union
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import RequestContext, get_request_context
from app.schemas import (
    ClientBalanceResponse,
    PeriodSummaryResponse,
    SupportClientLookupResponse,
    TransactionResponse,
)
from app.services.ledger_service import LedgerService
from app.services.audit_service import AuditService
from app.models import LedgerTransaction

router = APIRouter(prefix="/api/v1/clients", tags=["Clients"])


@router.get("/{client_ref}/balance", response_model=ClientBalanceResponse)
def get_balance(
    client_ref: str,
    currency: str = Query("USD", min_length=3, max_length=3, description="Currency code"),
    as_of: Optional[datetime] = Query(None, description="Point-in-time timestamp (e.g. quarter-end 2026-03-31T23:59:59Z)"),
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
):
    """
    Query client balance at current time or historically as-of a specific quarter-end or date.
    Strictly isolated to caller's firm.
    """
    balance, tx_count = LedgerService.get_client_balance(
        db=db,
        tenant_id=ctx.tenant_id,
        client_ref=client_ref,
        currency=currency,
        as_of=as_of,
    )
    effective_as_of = as_of or datetime.now(timezone.utc)
    return ClientBalanceResponse(
        tenant_id=ctx.tenant_id,
        client_ref=client_ref,
        currency=currency.upper(),
        balance=balance,
        as_of=effective_as_of,
        transaction_count=tx_count,
    )


@router.get("/{client_ref}/summary", response_model=PeriodSummaryResponse)
def get_period_summary(
    client_ref: str,
    start_date: datetime = Query(..., description="Start of period (inclusive)"),
    end_date: datetime = Query(..., description="End of period (inclusive)"),
    currency: str = Query("USD", min_length=3, max_length=3),
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
):
    """
    Compute fast periodic summaries (monthly/quarterly) for analysts:
    inflows, outflows, fees, net change, starting and ending balance.
    """
    if start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be earlier than or equal to end_date.",
        )

    summary = LedgerService.get_period_summary(
        db=db,
        tenant_id=ctx.tenant_id,
        client_ref=client_ref,
        currency=currency,
        start_date=start_date,
        end_date=end_date,
    )
    return PeriodSummaryResponse(**summary)


@router.get("/{client_ref}/history", response_model=List[TransactionResponse])
def get_client_history(
    client_ref: str,
    currency: Optional[str] = Query(None, min_length=3, max_length=3),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
):
    """
    Full exportable ledger transaction history for advisors and compliance.
    Support staff are restricted from bulk history dumps.
    """
    if ctx.user_role == "support":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Support role is not permitted to access full transaction history. Use client lookup instead.",
        )

    query = db.query(LedgerTransaction).filter(
        LedgerTransaction.tenant_id == ctx.tenant_id,
        LedgerTransaction.client_ref == client_ref,
    )
    if currency:
        query = query.filter(LedgerTransaction.currency == currency.upper())

    txs = query.order_by(LedgerTransaction.effective_at.desc()).offset(offset).limit(limit).all()
    return txs


@router.get("/{client_ref}")
def lookup_client(
    client_ref: str,
    currency: str = Query("USD", min_length=3, max_length=3),
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
):
    """
    Look up client account.
    - Support staff: receives least-privilege view (active status, balance, recent count).
    - Advisors/Admins: receives complete account summary.
    """
    balance, tx_count = LedgerService.get_client_balance(
        db=db,
        tenant_id=ctx.tenant_id,
        client_ref=client_ref,
        currency=currency,
    )

    last_tx = db.query(LedgerTransaction).filter(
        LedgerTransaction.tenant_id == ctx.tenant_id,
        LedgerTransaction.client_ref == client_ref,
        LedgerTransaction.currency == currency.upper(),
    ).order_by(LedgerTransaction.effective_at.desc()).first()

    status_str = "ACTIVE" if tx_count > 0 else "NEW_OR_EMPTY"

    # Audit lookup
    AuditService.log_event(
        db=db,
        tenant_id=ctx.tenant_id,
        actor_id=ctx.user_id,
        actor_role=ctx.user_role,
        action="CLIENT_LOOKUP",
        resource_type="client",
        resource_id=client_ref,
        details={"role": ctx.user_role, "currency": currency.upper()},
    )
    db.commit()

    if ctx.user_role == "support":
        return SupportClientLookupResponse(
            tenant_id=ctx.tenant_id,
            client_ref=client_ref,
            currency=currency.upper(),
            balance=balance,
            account_status=status_str,
            last_active=last_tx.effective_at if last_tx else None,
            recent_transaction_count=min(tx_count, 10),
        )

    # Advisor / Admin / Compliance view
    return {
        "tenant_id": ctx.tenant_id,
        "client_ref": client_ref,
        "currency": currency.upper(),
        "balance": balance,
        "account_status": status_str,
        "total_transactions": tx_count,
        "last_active": last_tx.effective_at if last_tx else None,
        "last_transaction": TransactionResponse.model_validate(last_tx) if last_tx else None,
    }
