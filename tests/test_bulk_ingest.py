from datetime import datetime, timezone, timedelta


def test_bulk_ingest_fault_tolerant_and_deduplication(client):
    headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "custodian-bot", "X-User-Role": "admin"}
    now_utc = datetime.now(timezone.utc)
    future_date = (now_utc + timedelta(days=5)).isoformat()
    valid_date = now_utc.isoformat()

    batch = [
        # Row 0: Valid deposit for c-1
        {
            "client_ref": "c-1",
            "type": "DEPOSIT",
            "amount": "5000.00",
            "currency": "USD",
            "effective_at": valid_date,
            "external_id": "cust-batch-001",
            "description": "Valid deposit",
        },
        # Row 1: Bad row - negative amount
        {
            "client_ref": "c-2",
            "type": "DEPOSIT",
            "amount": "-150.00",
            "currency": "USD",
            "effective_at": valid_date,
            "external_id": "cust-batch-002",
        },
        # Row 2: Bad row - unknown currency
        {
            "client_ref": "c-3",
            "type": "DEPOSIT",
            "amount": "200.00",
            "currency": "BITCOIN",
            "effective_at": valid_date,
            "external_id": "cust-batch-003",
        },
        # Row 3: Bad row - future date
        {
            "client_ref": "c-4",
            "type": "DEPOSIT",
            "amount": "300.00",
            "currency": "USD",
            "effective_at": future_date,
            "external_id": "cust-batch-004",
        },
        # Row 4: Bad row - missing client_ref
        {
            "type": "DEPOSIT",
            "amount": "400.00",
            "currency": "USD",
            "effective_at": valid_date,
            "external_id": "cust-batch-005",
        },
        # Row 5: Valid fee on c-1 (has $5000 balance from Row 0)
        {
            "client_ref": "c-1",
            "type": "FEE",
            "amount": "25.50",
            "currency": "USD",
            "effective_at": valid_date,
            "external_id": "cust-batch-006",
            "description": "Advisory quarterly fee",
        },
    ]

    res = client.post("/api/v1/transactions/bulk", json=batch, headers=headers)
    assert res.status_code == 200
    data = res.json()

    assert data["total_records"] == 6
    assert data["accepted_count"] == 2
    assert data["rejected_count"] == 4
    assert len(data["accepted"]) == 2
    assert len(data["errors"]) == 4

    error_indices = [err["row_index"] for err in data["errors"]]
    assert error_indices == [1, 2, 3, 4]

    # Verify c-1 balance reflects the two accepted rows: $5000.00 - $25.50 = $4974.50
    bal_res = client.get("/api/v1/clients/c-1/balance?currency=USD", headers=headers)
    assert bal_res.status_code == 200
    assert bal_res.json()["balance"] == "4974.5000"

    # Test re-sending the same batch (duplicate resilience)
    # The two valid records should be recognized as duplicates via external_id
    res_resend = client.post("/api/v1/transactions/bulk", json=batch, headers=headers)
    assert res_resend.status_code == 200
    resend_data = res_resend.json()
    assert resend_data["accepted_count"] == 0
    assert resend_data["duplicate_count"] == 2
    assert resend_data["rejected_count"] == 4

    # Balance must remain exactly unchanged!
    bal_res_after = client.get("/api/v1/clients/c-1/balance?currency=USD", headers=headers)
    assert bal_res_after.json()["balance"] == "4974.5000"
