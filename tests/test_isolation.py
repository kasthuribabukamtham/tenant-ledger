from datetime import datetime, timezone


def test_missing_tenant_header_rejected(client):
    """Requests without X-Tenant-ID must be rejected immediately."""
    res = client.get("/api/v1/clients/c-123/balance")
    assert res.status_code == 400
    assert "Missing required header 'X-Tenant-ID'" in res.json()["detail"]


def test_tenant_data_isolation(client):
    """Firm A (Northbridge) data must never be visible to Firm B (Meridian)."""
    firm_a_headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "adv-1", "X-User-Role": "advisor"}
    firm_b_headers = {"X-Tenant-ID": "meridian", "X-User-ID": "adv-2", "X-User-Role": "advisor"}

    # Firm A deposits $1,000 for client c-100
    deposit_payload = {
        "client_ref": "c-100",
        "type": "DEPOSIT",
        "amount": "1000.00",
        "currency": "USD",
        "effective_at": datetime.now(timezone.utc).isoformat(),
        "external_id": "ext-nb-1",
        "description": "Initial funding",
    }
    res = client.post("/api/v1/transactions", json=deposit_payload, headers=firm_a_headers)
    assert res.status_code == 201
    tx_a_id = res.json()["id"]

    # Firm A sees balance $1,000
    res_a = client.get("/api/v1/clients/c-100/balance", headers=firm_a_headers)
    assert res_a.status_code == 200
    assert res_a.json()["balance"] == "1000.0000"

    # Firm B queries same client_ref -> balance must be 0 and 0 transactions
    res_b = client.get("/api/v1/clients/c-100/balance", headers=firm_b_headers)
    assert res_b.status_code == 200
    assert res_b.json()["balance"] == "0"
    assert res_b.json()["transaction_count"] == 0

    # Firm B attempts to fetch Firm A's transaction directly by ID -> 404 Not Found
    res_b_tx = client.get(f"/api/v1/transactions/{tx_a_id}", headers=firm_b_headers)
    assert res_b_tx.status_code == 404

    # Firm B attempts to reverse Firm A's transaction -> 404 Not Found
    res_b_rev = client.post(
        f"/api/v1/transactions/{tx_a_id}/reverse",
        json={"reason": "Malicious cross-firm reversal attempt"},
        headers=firm_b_headers,
    )
    assert res_b_rev.status_code == 404
