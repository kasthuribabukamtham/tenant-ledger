from datetime import datetime, timezone
from decimal import Decimal


def test_money_decimal_precision(client):
    headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "adv-1", "X-User-Role": "advisor"}
    now = datetime.now(timezone.utc).isoformat()

    # Deposit 0.10
    client.post("/api/v1/transactions", json={
        "client_ref": "c-prec",
        "type": "DEPOSIT",
        "amount": "0.1000",
        "currency": "USD",
        "effective_at": now,
        "external_id": "tx-p1",
    }, headers=headers)

    # Deposit 0.20
    client.post("/api/v1/transactions", json={
        "client_ref": "c-prec",
        "type": "DEPOSIT",
        "amount": "0.2000",
        "currency": "USD",
        "effective_at": now,
        "external_id": "tx-p2",
    }, headers=headers)

    # Balance must be exactly 0.3000, never floating point imprecision like 0.30000000000000004
    bal_res = client.get("/api/v1/clients/c-prec/balance?currency=USD", headers=headers)
    assert bal_res.status_code == 200
    assert bal_res.json()["balance"] == "0.3000"


def test_insufficient_funds_overdraft_prevention(client):
    headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "adv-1", "X-User-Role": "advisor"}
    now = datetime.now(timezone.utc).isoformat()

    # Deposit $100
    client.post("/api/v1/transactions", json={
        "client_ref": "c-solvency",
        "type": "DEPOSIT",
        "amount": "100.00",
        "currency": "USD",
        "effective_at": now,
        "external_id": "tx-sol-1",
    }, headers=headers)

    # Attempt to withdraw $150 -> 422 Unprocessable Entity
    res = client.post("/api/v1/transactions", json={
        "client_ref": "c-solvency",
        "type": "WITHDRAWAL",
        "amount": "150.00",
        "currency": "USD",
        "effective_at": now,
        "external_id": "tx-sol-2",
    }, headers=headers)
    assert res.status_code == 422
    assert "Insufficient funds" in res.json()["detail"]

    # Balance must still be $100
    bal_res = client.get("/api/v1/clients/c-solvency/balance?currency=USD", headers=headers)
    assert bal_res.json()["balance"] == "100.0000"


def test_atomic_transfer_between_clients(client):
    headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "adv-1", "X-User-Role": "advisor"}
    now = datetime.now(timezone.utc).isoformat()

    # Fund sender client c-alpha with $500
    client.post("/api/v1/transactions", json={
        "client_ref": "c-alpha",
        "type": "DEPOSIT",
        "amount": "500.00",
        "currency": "USD",
        "effective_at": now,
        "external_id": "alpha-fund",
    }, headers=headers)

    # Transfer $200 from c-alpha to c-beta
    transfer_payload = {
        "from_client_ref": "c-alpha",
        "to_client_ref": "c-beta",
        "amount": "200.00",
        "currency": "USD",
        "effective_at": now,
        "external_id": "xfer-001",
        "description": "Family trust distribution",
    }
    res = client.post("/api/v1/transactions/transfers", json=transfer_payload, headers=headers)
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "COMPLETED"
    assert data["transfer_id"] is not None

    # Verify sender balance: $500 - $200 = $300
    bal_alpha = client.get("/api/v1/clients/c-alpha/balance?currency=USD", headers=headers).json()
    assert bal_alpha["balance"] == "300.0000"

    # Verify recipient balance: $200
    bal_beta = client.get("/api/v1/clients/c-beta/balance?currency=USD", headers=headers).json()
    assert bal_beta["balance"] == "200.0000"

    # Attempt transfer exceeding balance ($350 from c-alpha)
    fail_transfer = {
        "from_client_ref": "c-alpha",
        "to_client_ref": "c-beta",
        "amount": "350.00",
        "currency": "USD",
        "effective_at": now,
        "external_id": "xfer-002",
    }
    fail_res = client.post("/api/v1/transactions/transfers", json=fail_transfer, headers=headers)
    assert fail_res.status_code == 422
    assert "Insufficient funds in sender account" in fail_res.json()["detail"]


def test_transfer_to_same_client_rejected(client):
    headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "adv-1", "X-User-Role": "advisor"}
    now = datetime.now(timezone.utc).isoformat()
    transfer_payload = {
        "from_client_ref": "c-same",
        "to_client_ref": "c-same",
        "amount": "50.00",
        "currency": "USD",
        "effective_at": now,
    }
    res = client.post("/api/v1/transactions/transfers", json=transfer_payload, headers=headers)
    assert res.status_code == 422
