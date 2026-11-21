from typing import List, Union
from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import RequestContext, get_request_context
from app.schemas import (
    SingleTransactionCreate,
    TransactionResponse,
    TransferCreate,
    ReversalCreate,
    BulkIngestResponse,
)
from app.services.ledger_service import LedgerService
from app.models import LedgerTransaction

router = APIRouter(prefix="/api/v1/transactions", tags=["Transactions"])


@router.post("", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
def create_transaction(
    tx_data: SingleTransactionCreate,
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
):
    """
    Post a single money movement (deposit, withdrawal, or fee).
    Enforces tenant isolation, idempotency on external_id, and duplicate detection.
    """
    tx, is_duplicate = LedgerService.post_single_transaction(
        db=db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        user_role=ctx.user_role,
        tx_data=tx_data,
    )
    return tx


@router.post("/bulk", response_model=BulkIngestResponse, status_code=status.HTTP_200_OK)
def ingest_bulk_transactions(
    rows: List[dict] = Body(..., description="List of transaction dictionaries from spreadsheets or custodians"),
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
):
    """
    Bulk ingestion endpoint designed for custodian feeds and spreadsheet imports.
    Fault-tolerant: valid rows are accepted; bad rows are reported with line number and reason.
    One bad row does NOT fail the entire batch.
    """
    accepted, errors, duplicate_count = LedgerService.process_bulk_transactions(
        db=db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        user_role=ctx.user_role,
        rows=rows,
    )
    return BulkIngestResponse(
        total_records=len(rows),
        accepted_count=len(accepted),
        rejected_count=len(errors),
        duplicate_count=duplicate_count,
        accepted=accepted,
        errors=errors,
    )


@router.post("/transfers", status_code=status.HTTP_201_CREATED)
def transfer_funds(
    transfer_data: TransferCreate,
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
):
    """
    Atomic transfer of funds between two client accounts in the same advisory firm.
    Debits sender and credits recipient within a single ACID transaction.
    """
    tx_out, tx_in = LedgerService.transfer_between_clients(
        db=db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        user_role=ctx.user_role,
        transfer_data=transfer_data,
    )
    return {
        "status": "COMPLETED",
        "transfer_id": tx_out.transfer_id,
        "from_transaction": TransactionResponse.model_validate(tx_out),
        "to_transaction": TransactionResponse.model_validate(tx_in),
    }


@router.post("/{transaction_id}/reverse", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
def reverse_transaction(
    transaction_id: str,
    reversal_data: ReversalCreate,
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
):
    """
    Staff mistake correction endpoint.
    Ledger history is never deleted or altered. A compensatory reversal transaction is created.
    """
    reversal_tx = LedgerService.reverse_transaction(
        db=db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        user_role=ctx.user_role,
        transaction_id=transaction_id,
        reason=reversal_data.reason,
    )
    return reversal_tx


@router.get("/{transaction_id}", response_model=TransactionResponse)
def get_transaction(
    transaction_id: str,
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
):
    """
    Retrieve transaction details strictly isolated to caller's tenant.
    """
    tx = db.query(LedgerTransaction).filter(
        LedgerTransaction.id == transaction_id,
        LedgerTransaction.tenant_id == ctx.tenant_id,
    ).first()
    if not tx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction '{transaction_id}' not found.",
        )
    return tx
