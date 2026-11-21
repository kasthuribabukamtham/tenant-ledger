from datetime import datetime, timezone


def test_compliance_audit_trail(client):
    headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "compliance-officer-9", "X-User-Role": "compliance"}
    advisor_headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "adv-john", "X-User-Role": "advisor"}
    now = datetime.now(timezone.utc).isoformat()

    # Advisor posts deposit
    client.post("/api/v1/transactions", json={
        "client_ref": "c-audit",
        "type": "DEPOSIT",
        "amount": "1500.00",
        "currency": "USD",
        "effective_at": now,
        "external_id": "audit-tx-1",
    }, headers=advisor_headers)

    # Compliance queries audit trail
    events_res = client.get("/api/v1/audit/events", headers=headers)
    assert events_res.status_code == 200
    events = events_res.json()
    assert len(events) >= 1

    deposit_event = next(e for e in events if e["action"] == "TRANSACTION_DEPOSIT")
    assert deposit_event["tenant_id"] == "northbridge"
    assert deposit_event["actor_id"] == "adv-john"
    assert deposit_event["actor_role"] == "advisor"
    assert "1500.00" in deposit_event["details"]


def test_support_role_least_privilege_access(client):
    support_headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "support-alice", "X-User-Role": "support"}
    advisor_headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "adv-john", "X-User-Role": "advisor"}
    now = datetime.now(timezone.utc).isoformat()

    # Setup client transactions
    client.post("/api/v1/transactions", json={
        "client_ref": "c-support-view",
        "type": "DEPOSIT",
        "amount": "25000.00",
        "currency": "USD",
        "effective_at": now,
        "external_id": "sup-tx-1",
        "description": "Sensitive confidential high-net-worth deposit memo",
    }, headers=advisor_headers)

    # 1. Support performs account lookup
    res = client.get("/api/v1/clients/c-support-view", headers=support_headers)
    assert res.status_code == 200
    data = res.json()
    # Support sees high-level status & balance without confidential details
    assert data["account_status"] == "ACTIVE"
    assert data["balance"] == "25000.0000"
    assert data["currency"] == "USD"
    # Sensitive fields are absent in support view
    assert "last_transaction" not in data
    assert "total_transactions" not in data

    # 2. Support attempts to fetch full transaction history -> 403 Forbidden
    hist_res = client.get("/api/v1/clients/c-support-view/history", headers=support_headers)
    assert hist_res.status_code == 403
    assert "Support role is not permitted" in hist_res.json()["detail"]

    # 3. Support attempts to read audit logs -> 403 Forbidden
    audit_res = client.get("/api/v1/audit/events", headers=support_headers)
    assert audit_res.status_code == 403
    assert "Support role is not authorized" in audit_res.json()["detail"]
