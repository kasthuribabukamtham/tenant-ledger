from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor


def test_duplicate_content_hash_without_external_id(client):
    headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "adv-1", "X-User-Role": "advisor"}
    now = datetime.now(timezone.utc).isoformat()

    payload = {
        "client_ref": "c-dup-hash",
        "type": "DEPOSIT",
        "amount": "250.00",
        "currency": "USD",
        "effective_at": now,
        # No external_id provided
    }

    # First submission
    res1 = client.post("/api/v1/transactions", json=payload, headers=headers)
    assert res1.status_code == 201
    tx1_id = res1.json()["id"]

    # Immediate second identical submission (network glitch re-send)
    res2 = client.post("/api/v1/transactions", json=payload, headers=headers)
    assert res2.status_code == 201
    tx2_id = res2.json()["id"]

    # Must return the existing transaction without creating duplicate
    assert tx1_id == tx2_id

    # Balance must be $250.00, NOT $500.00
    bal_res = client.get("/api/v1/clients/c-dup-hash/balance", headers=headers)
    assert bal_res.json()["balance"] == "250.0000"
    assert bal_res.json()["transaction_count"] == 1


def test_rapid_sequential_deposits_integrity(client):
    """Ensure rapid successive operations don't lose updates or corrupt balance."""
    headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "load-tester", "X-User-Role": "admin"}
    now = datetime.now(timezone.utc).isoformat()

    for i in range(20):
        res = client.post("/api/v1/transactions", json={
            "client_ref": "c-concurrent",
            "type": "DEPOSIT",
            "amount": "10.00",
            "currency": "USD",
            "effective_at": now,
            "external_id": f"conc-dep-{i}",
        }, headers=headers)
        assert res.status_code == 201

    # 20 deposits of $10.00 = exactly $200.00
    bal_res = client.get("/api/v1/clients/c-concurrent/balance", headers=headers)
    assert bal_res.json()["balance"] == "200.0000"
    assert bal_res.json()["transaction_count"] == 20


def test_unsupported_currency_rejected(client):
    headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "adv-1", "X-User-Role": "advisor"}
    now = datetime.now(timezone.utc).isoformat()
    res = client.post("/api/v1/transactions", json={
        "client_ref": "c-curr-err",
        "type": "DEPOSIT",
        "amount": "100.00",
        "currency": "FAKECOIN",
        "effective_at": now,
    }, headers=headers)
    assert res.status_code == 422


def test_future_date_rejected(client):
    headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "adv-1", "X-User-Role": "advisor"}
    future_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    res = client.post("/api/v1/transactions", json={
        "client_ref": "c-date-err",
        "type": "DEPOSIT",
        "amount": "100.00",
        "currency": "USD",
        "effective_at": future_time,
    }, headers=headers)
    assert res.status_code == 422
