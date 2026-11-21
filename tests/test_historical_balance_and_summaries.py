from datetime import datetime, timezone


def test_point_in_time_balance_queries(client):
    headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "adv-1", "X-User-Role": "advisor"}

    # Post events across dates
    # Event 1: Jan 10 deposit $10,000
    client.post("/api/v1/transactions", json={
        "client_ref": "c-hist",
        "type": "DEPOSIT",
        "amount": "10000.00",
        "currency": "USD",
        "effective_at": "2026-01-10T12:00:00Z",
        "external_id": "tx-h1",
    }, headers=headers)

    # Event 2: Jan 20 fee $150
    client.post("/api/v1/transactions", json={
        "client_ref": "c-hist",
        "type": "FEE",
        "amount": "150.00",
        "currency": "USD",
        "effective_at": "2026-01-20T12:00:00Z",
        "external_id": "tx-h2",
    }, headers=headers)

    # Event 3: Feb 15 withdrawal $2000
    client.post("/api/v1/transactions", json={
        "client_ref": "c-hist",
        "type": "WITHDRAWAL",
        "amount": "2000.00",
        "currency": "USD",
        "effective_at": "2026-02-15T12:00:00Z",
        "external_id": "tx-h3",
    }, headers=headers)

    # Query 1: As of Jan 15 (after deposit 1, before fee 2)
    bal_jan15 = client.get("/api/v1/clients/c-hist/balance?as_of=2026-01-15T23:59:59Z", headers=headers).json()
    assert bal_jan15["balance"] == "10000.0000"
    assert bal_jan15["transaction_count"] == 1

    # Query 2: As of Jan 31 (after fee 2, before Feb withdrawal)
    bal_jan31 = client.get("/api/v1/clients/c-hist/balance?as_of=2026-01-31T23:59:59Z", headers=headers).json()
    assert bal_jan31["balance"] == "9850.0000"
    assert bal_jan31["transaction_count"] == 2

    # Query 3: As of Feb 28 (after Feb withdrawal)
    bal_feb28 = client.get("/api/v1/clients/c-hist/balance?as_of=2026-02-28T23:59:59Z", headers=headers).json()
    assert bal_feb28["balance"] == "7850.0000"
    assert bal_feb28["transaction_count"] == 3


def test_period_summary_reports(client):
    headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "analyst-1", "X-User-Role": "advisor"}

    # Prior balance before March: $5000 deposit on Jan 1
    client.post("/api/v1/transactions", json={
        "client_ref": "c-summary",
        "type": "DEPOSIT",
        "amount": "5000.00",
        "currency": "USD",
        "effective_at": "2026-01-01T12:00:00Z",
        "external_id": "sum-init",
    }, headers=headers)

    # March transactions
    # Inflow: deposit $3000 on March 5
    client.post("/api/v1/transactions", json={
        "client_ref": "c-summary",
        "type": "DEPOSIT",
        "amount": "3000.00",
        "currency": "USD",
        "effective_at": "2026-03-05T12:00:00Z",
        "external_id": "sum-m1",
    }, headers=headers)

    # Outflow: withdrawal $1000 on March 15
    client.post("/api/v1/transactions", json={
        "client_ref": "c-summary",
        "type": "WITHDRAWAL",
        "amount": "1000.00",
        "currency": "USD",
        "effective_at": "2026-03-15T12:00:00Z",
        "external_id": "sum-m2",
    }, headers=headers)

    # Fee: $50 on March 30
    client.post("/api/v1/transactions", json={
        "client_ref": "c-summary",
        "type": "FEE",
        "amount": "50.00",
        "currency": "USD",
        "effective_at": "2026-03-30T12:00:00Z",
        "external_id": "sum-m3",
    }, headers=headers)

    # Request March monthly summary
    summary_res = client.get(
        "/api/v1/clients/c-summary/summary?start_date=2026-03-01T00:00:00Z&end_date=2026-03-31T23:59:59Z&currency=USD",
        headers=headers,
    )
    assert summary_res.status_code == 200
    data = summary_res.json()

    assert data["starting_balance"] == "5000.0000"
    assert data["total_inflows"] == "3000.0000"
    assert data["total_outflows"] == "1000.0000"
    assert data["total_fees"] == "50.0000"
    # Net change: 3000 - 1000 - 50 = +1950
    assert data["net_change"] == "1950.0000"
    # Ending balance: 5000 + 1950 = 6950
    assert data["ending_balance"] == "6950.0000"
    assert data["transaction_count"] == 3
