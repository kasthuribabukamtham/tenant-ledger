import hashlib
import uuid
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from fastapi import HTTPException, status

from app.config import settings
from app.models import LedgerTransaction, TransactionType, TransactionDirection
from app.schemas import SingleTransactionCreate, TransferCreate, BulkRowError
from app.services.audit_service import AuditService


class LedgerService:
    @staticmethod
    def compute_content_hash(
        client_ref: str,
        amount: Decimal,
        currency: str,
        tx_type: str,
        effective_at: datetime,
    ) -> str:
        data = f"{client_ref}:{amount:.4f}:{currency.upper()}:{tx_type}:{effective_at.isoformat()}"
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    @staticmethod
    def get_client_balance(
        db: Session,
        tenant_id: str,
        client_ref: str,
        currency: str,
        as_of: Optional[datetime] = None,
    ) -> Tuple[Decimal, int]:
        """
        Calculate client balance strictly isolated to tenant_id and currency as of a given timestamp.
        Balance = SUM(CREDIT) - SUM(DEBIT)
        """
        query = db.query(LedgerTransaction).filter(
            LedgerTransaction.tenant_id == tenant_id,
            LedgerTransaction.client_ref == client_ref,
            LedgerTransaction.currency == currency.upper(),
        )
        if as_of:
            if as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=timezone.utc)
            query = query.filter(LedgerTransaction.effective_at <= as_of)

        credits = query.filter(LedgerTransaction.direction == TransactionDirection.CREDIT).with_entities(
            func.coalesce(func.sum(LedgerTransaction.amount), 0)
        ).scalar() or Decimal("0")

        debits = query.filter(LedgerTransaction.direction == TransactionDirection.DEBIT).with_entities(
            func.coalesce(func.sum(LedgerTransaction.amount), 0)
        ).scalar() or Decimal("0")

        total_tx = query.count()
        balance = Decimal(str(credits)) - Decimal(str(debits))
        return balance, total_tx

    @staticmethod
    def post_single_transaction(
        db: Session,
        tenant_id: str,
        user_id: str,
        user_role: str,
        tx_data: SingleTransactionCreate,
    ) -> Tuple[LedgerTransaction, bool]:
        """
        Post a single transaction with idempotency and duplicate checking.
        Returns (transaction, is_duplicate).
        """
        currency = tx_data.currency.upper()
        content_hash = LedgerService.compute_content_hash(
            tx_data.client_ref,
            tx_data.amount,
            currency,
            tx_data.type.value,
            tx_data.effective_at,
        )

        # 1. Idempotency Check by external_id
        if tx_data.external_id:
            existing = db.query(LedgerTransaction).filter(
                LedgerTransaction.tenant_id == tenant_id,
                LedgerTransaction.external_id == tx_data.external_id,
            ).first()
            if existing:
                return existing, True
        else:
            # 2. Heuristic duplicate check when no external_id is provided (e.g. double-click / network retry)
            window_start = datetime.now(timezone.utc) - timedelta(seconds=settings.DUPLICATE_WINDOW_SECONDS)
            dup_candidate = db.query(LedgerTransaction).filter(
                LedgerTransaction.tenant_id == tenant_id,
                LedgerTransaction.client_ref == tx_data.client_ref,
                LedgerTransaction.content_hash == content_hash,
                LedgerTransaction.created_at >= window_start,
            ).first()

            if dup_candidate:
                return dup_candidate, True

        # 3. Determine Direction
        if tx_data.type == TransactionType.DEPOSIT:
            direction = TransactionDirection.CREDIT
        elif tx_data.type in (TransactionType.WITHDRAWAL, TransactionType.FEE):
            direction = TransactionDirection.DEBIT
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported transaction type '{tx_data.type.value}' for single posting.",
            )

        # 4. Solvency check for debits (prevent negative balance)
        if direction == TransactionDirection.DEBIT:
            current_balance, _ = LedgerService.get_client_balance(db, tenant_id, tx_data.client_ref, currency)
            if current_balance < tx_data.amount:
                raise HTTPException(
                    status_code=422,
                    detail=f"Insufficient funds for client '{tx_data.client_ref}'. Current balance: {current_balance:.2f} {currency}, requested: {tx_data.amount:.2f} {currency}",
                )

        # 5. Create transaction
        tx = LedgerTransaction(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            client_ref=tx_data.client_ref,
            external_id=tx_data.external_id,
            content_hash=content_hash,
            type=tx_data.type,
            direction=direction,
            amount=tx_data.amount,
            currency=currency,
            effective_at=tx_data.effective_at,
            created_at=datetime.now(timezone.utc),
            created_by=user_id,
            description=tx_data.description,
            is_reversed=False,
        )
        db.add(tx)

        # 6. Audit Trail
        AuditService.log_event(
            db=db,
            tenant_id=tenant_id,
            actor_id=user_id,
            actor_role=user_role,
            action=f"TRANSACTION_{tx_data.type.value}",
            resource_type="transaction",
            resource_id=tx.id,
            details={
                "client_ref": tx.client_ref,
                "amount": str(tx.amount),
                "currency": tx.currency,
                "direction": direction.value,
                "external_id": tx.external_id,
            },
        )
        db.commit()
        return tx, False

    @staticmethod
    def process_bulk_transactions(
        db: Session,
        tenant_id: str,
        user_id: str,
        user_role: str,
        rows: List[dict],
    ) -> Tuple[List[LedgerTransaction], List[BulkRowError], int]:
        """
        Process bulk spreadsheet / custodian transactions with fault tolerance.
        Valid rows succeed; invalid rows are collected with line numbers and reasons.
        Does NOT block valid entries when a bad row occurs.
        """
        accepted: List[LedgerTransaction] = []
        errors: List[BulkRowError] = []
        duplicate_count = 0

        # Pre-cache balances for performance and concurrency consistency in batch
        client_currency_balances: dict[tuple[str, str], Decimal] = {}

        for idx, row in enumerate(rows):
            try:
                # 1. Validate required fields presence
                if not isinstance(row, dict):
                    errors.append(BulkRowError(row_index=idx, error="Row must be a JSON object."))
                    continue

                client_ref = row.get("client_ref")
                if not client_ref or not str(client_ref).strip():
                    errors.append(BulkRowError(row_index=idx, error="Missing required field 'client_ref'."))
                    continue
                client_ref = str(client_ref).strip()

                raw_amount = row.get("amount")
                if raw_amount is None:
                    errors.append(BulkRowError(row_index=idx, client_ref=client_ref, error="Missing required field 'amount'."))
                    continue
                try:
                    amount = Decimal(str(raw_amount))
                except Exception:
                    errors.append(BulkRowError(row_index=idx, client_ref=client_ref, error=f"Invalid amount '{raw_amount}'. Must be a valid numeric decimal."))
                    continue

                if amount <= Decimal("0"):
                    errors.append(BulkRowError(row_index=idx, client_ref=client_ref, error=f"Amount must be strictly positive (got {amount})."))
                    continue

                currency_raw = row.get("currency")
                if not currency_raw:
                    errors.append(BulkRowError(row_index=idx, client_ref=client_ref, error="Missing required field 'currency'."))
                    continue
                currency = str(currency_raw).strip().upper()
                if currency not in settings.SUPPORTED_CURRENCIES:
                    errors.append(BulkRowError(row_index=idx, client_ref=client_ref, error=f"Unsupported currency '{currency}'. Must be one of: {sorted(list(settings.SUPPORTED_CURRENCIES))}"))
                    continue

                raw_type = row.get("type")
                if not raw_type:
                    errors.append(BulkRowError(row_index=idx, client_ref=client_ref, error="Missing required field 'type'."))
                    continue
                type_str = str(raw_type).strip().upper()
                if type_str not in (TransactionType.DEPOSIT.value, TransactionType.WITHDRAWAL.value, TransactionType.FEE.value):
                    errors.append(BulkRowError(row_index=idx, client_ref=client_ref, error=f"Invalid transaction type '{type_str}'. Permitted: DEPOSIT, WITHDRAWAL, FEE."))
                    continue
                tx_type = TransactionType(type_str)

                raw_effective_at = row.get("effective_at")
                if not raw_effective_at:
                    errors.append(BulkRowError(row_index=idx, client_ref=client_ref, error="Missing required field 'effective_at'."))
                    continue
                try:
                    if isinstance(raw_effective_at, datetime):
                        effective_at = raw_effective_at
                    else:
                        effective_at = datetime.fromisoformat(str(raw_effective_at).replace("Z", "+00:00"))
                    if effective_at.tzinfo is None:
                        effective_at = effective_at.replace(tzinfo=timezone.utc)
                except Exception as e:
                    errors.append(BulkRowError(row_index=idx, client_ref=client_ref, error=f"Invalid datetime format for 'effective_at': {e}"))
                    continue

                now_utc = datetime.now(timezone.utc)
                if effective_at > now_utc + timedelta(seconds=60):
                    errors.append(BulkRowError(row_index=idx, client_ref=client_ref, error=f"Effective date cannot be in the future: {effective_at.isoformat()} > {now_utc.isoformat()}"))
                    continue

                external_id = row.get("external_id")
                if external_id:
                    external_id = str(external_id).strip()

                content_hash = LedgerService.compute_content_hash(
                    client_ref, amount, currency, tx_type.value, effective_at
                )

                # Check Idempotency by external_id
                if external_id:
                    existing = db.query(LedgerTransaction).filter(
                        LedgerTransaction.tenant_id == tenant_id,
                        LedgerTransaction.external_id == external_id,
                    ).first()
                    if existing:
                        duplicate_count += 1
                        continue

                # Direction
                if tx_type == TransactionType.DEPOSIT:
                    direction = TransactionDirection.CREDIT
                else:
                    direction = TransactionDirection.DEBIT

                # Solvency tracking for debits
                bal_key = (client_ref, currency)
                if bal_key not in client_currency_balances:
                    curr_bal, _ = LedgerService.get_client_balance(db, tenant_id, client_ref, currency)
                    client_currency_balances[bal_key] = curr_bal

                if direction == TransactionDirection.DEBIT:
                    if client_currency_balances[bal_key] < amount:
                        errors.append(BulkRowError(
                            row_index=idx,
                            client_ref=client_ref,
                            external_id=external_id,
                            error=f"Insufficient funds: current balance {client_currency_balances[bal_key]:.2f} {currency} is less than requested debit of {amount:.2f} {currency}."
                        ))
                        continue
                    client_currency_balances[bal_key] -= amount
                else:
                    client_currency_balances[bal_key] += amount

                # Create transaction
                tx = LedgerTransaction(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    client_ref=client_ref,
                    external_id=external_id,
                    content_hash=content_hash,
                    type=tx_type,
                    direction=direction,
                    amount=amount,
                    currency=currency,
                    effective_at=effective_at,
                    created_at=datetime.now(timezone.utc),
                    created_by=user_id,
                    description=row.get("description"),
                    is_reversed=False,
                )
                db.add(tx)
                accepted.append(tx)

            except Exception as ex:
                errors.append(BulkRowError(row_index=idx, error=f"Unexpected error processing row: {str(ex)}"))

        if accepted:
            AuditService.log_event(
                db=db,
                tenant_id=tenant_id,
                actor_id=user_id,
                actor_role=user_role,
                action="BULK_INGEST",
                resource_type="batch",
                details={
                    "total_input": len(rows),
                    "accepted": len(accepted),
                    "rejected": len(errors),
                    "duplicates": duplicate_count,
                },
            )
            db.commit()

        return accepted, errors, duplicate_count

    @staticmethod
    def transfer_between_clients(
        db: Session,
        tenant_id: str,
        user_id: str,
        user_role: str,
        transfer_data: TransferCreate,
    ) -> Tuple[LedgerTransaction, LedgerTransaction]:
        """
        Execute an atomic transfer between two clients within the same advisory firm.
        """
        currency = transfer_data.currency.upper()
        amount = transfer_data.amount

        # Check sender balance
        sender_balance, _ = LedgerService.get_client_balance(
            db, tenant_id, transfer_data.from_client_ref, currency
        )
        if sender_balance < amount:
            raise HTTPException(
                status_code=422,
                detail=f"Insufficient funds in sender account '{transfer_data.from_client_ref}'. Available: {sender_balance:.2f} {currency}, required: {amount:.2f} {currency}",
            )

        transfer_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # 1. Debit sender
        tx_out = LedgerTransaction(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            client_ref=transfer_data.from_client_ref,
            external_id=f"{transfer_data.external_id}-out" if transfer_data.external_id else None,
            type=TransactionType.TRANSFER_OUT,
            direction=TransactionDirection.DEBIT,
            amount=amount,
            currency=currency,
            effective_at=transfer_data.effective_at,
            created_at=now,
            created_by=user_id,
            description=f"Transfer to {transfer_data.to_client_ref}: {transfer_data.description or ''}".strip(),
            transfer_id=transfer_id,
            is_reversed=False,
        )

        # 2. Credit recipient
        tx_in = LedgerTransaction(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            client_ref=transfer_data.to_client_ref,
            external_id=f"{transfer_data.external_id}-in" if transfer_data.external_id else None,
            type=TransactionType.TRANSFER_IN,
            direction=TransactionDirection.CREDIT,
            amount=amount,
            currency=currency,
            effective_at=transfer_data.effective_at,
            created_at=now,
            created_by=user_id,
            description=f"Transfer from {transfer_data.from_client_ref}: {transfer_data.description or ''}".strip(),
            transfer_id=transfer_id,
            is_reversed=False,
        )

        db.add(tx_out)
        db.add(tx_in)

        # Audit
        AuditService.log_event(
            db=db,
            tenant_id=tenant_id,
            actor_id=user_id,
            actor_role=user_role,
            action="TRANSFER_EXECUTED",
            resource_type="transfer",
            resource_id=transfer_id,
            details={
                "from_client_ref": transfer_data.from_client_ref,
                "to_client_ref": transfer_data.to_client_ref,
                "amount": str(amount),
                "currency": currency,
            },
        )

        db.commit()
        return tx_out, tx_in

    @staticmethod
    def reverse_transaction(
        db: Session,
        tenant_id: str,
        user_id: str,
        user_role: str,
        transaction_id: str,
        reason: str,
    ) -> LedgerTransaction:
        """
        Reverse a transaction immutably.
        History is never deleted or mutated. A compensatory transaction is created and linked.
        """
        original = db.query(LedgerTransaction).filter(
            LedgerTransaction.id == transaction_id,
            LedgerTransaction.tenant_id == tenant_id,
        ).first()

        if not original:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Transaction '{transaction_id}' not found in this firm's ledger.",
            )

        if original.is_reversed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Transaction '{transaction_id}' has already been reversed by transaction '{original.reversed_by_id}'.",
            )

        if original.type == TransactionType.REVERSAL:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot reverse a transaction that is itself a reversal.",
            )

        # Inverse direction
        reversal_direction = (
            TransactionDirection.DEBIT
            if original.direction == TransactionDirection.CREDIT
            else TransactionDirection.CREDIT
        )

        # If reversing a credit, verify the client has enough balance to deduct the amount
        if reversal_direction == TransactionDirection.DEBIT:
            current_balance, _ = LedgerService.get_client_balance(
                db, tenant_id, original.client_ref, original.currency
            )
            if current_balance < original.amount:
                raise HTTPException(
                    status_code=422,
                    detail=f"Cannot reverse transaction: client balance ({current_balance:.2f} {original.currency}) is insufficient to deduct {original.amount:.2f} {original.currency}.",
                )

        reversal_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        reversal_tx = LedgerTransaction(
            id=reversal_id,
            tenant_id=tenant_id,
            client_ref=original.client_ref,
            external_id=f"rev-{original.id[:8]}",
            type=TransactionType.REVERSAL,
            direction=reversal_direction,
            amount=original.amount,
            currency=original.currency,
            effective_at=now,
            created_at=now,
            created_by=user_id,
            description=f"Correction reversal of tx {original.id}. Reason: {reason}",
            reverses_transaction_id=original.id,
            is_reversed=False,
        )

        original.is_reversed = True
        original.reversed_by_id = reversal_id

        db.add(reversal_tx)

        # Audit
        AuditService.log_event(
            db=db,
            tenant_id=tenant_id,
            actor_id=user_id,
            actor_role=user_role,
            action="TRANSACTION_REVERSED",
            resource_type="transaction",
            resource_id=reversal_id,
            details={
                "original_transaction_id": original.id,
                "client_ref": original.client_ref,
                "amount": str(original.amount),
                "currency": original.currency,
                "reason": reason,
            },
        )

        db.commit()
        return reversal_tx

    @staticmethod
    def get_period_summary(
        db: Session,
        tenant_id: str,
        client_ref: str,
        currency: str,
        start_date: datetime,
        end_date: datetime,
    ) -> dict:
        """
        Fast aggregated monthly / period summary:
        inflows, outflows, fees, net change, starting balance, ending balance.
        """
        currency = currency.upper()
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        # Starting balance (all transactions before start_date)
        starting_bal, _ = LedgerService.get_client_balance(
            db, tenant_id, client_ref, currency, as_of=start_date - timedelta(microseconds=1)
        )

        # Transactions in period
        period_txs = db.query(LedgerTransaction).filter(
            LedgerTransaction.tenant_id == tenant_id,
            LedgerTransaction.client_ref == client_ref,
            LedgerTransaction.currency == currency,
            LedgerTransaction.effective_at >= start_date,
            LedgerTransaction.effective_at <= end_date,
        ).all()

        inflows = Decimal("0")
        outflows = Decimal("0")
        fees = Decimal("0")
        reversals_credit = Decimal("0")
        reversals_debit = Decimal("0")

        for tx in period_txs:
            if tx.type in (TransactionType.DEPOSIT, TransactionType.TRANSFER_IN):
                inflows += Decimal(str(tx.amount))
            elif tx.type in (TransactionType.WITHDRAWAL, TransactionType.TRANSFER_OUT):
                outflows += Decimal(str(tx.amount))
            elif tx.type == TransactionType.FEE:
                fees += Decimal(str(tx.amount))
            elif tx.type == TransactionType.REVERSAL:
                if tx.direction == TransactionDirection.CREDIT:
                    reversals_credit += Decimal(str(tx.amount))
                else:
                    reversals_debit += Decimal(str(tx.amount))

        net_change = inflows - outflows - fees + reversals_credit - reversals_debit
        ending_bal = starting_bal + net_change

        return {
            "tenant_id": tenant_id,
            "client_ref": client_ref,
            "currency": currency,
            "start_date": start_date,
            "end_date": end_date,
            "starting_balance": starting_bal,
            "total_inflows": inflows,
            "total_outflows": outflows,
            "total_fees": fees,
            "net_change": net_change,
            "ending_balance": ending_bal,
            "transaction_count": len(period_txs),
        }
