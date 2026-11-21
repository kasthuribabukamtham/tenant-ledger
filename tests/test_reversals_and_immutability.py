from datetime import datetime, timezone


def test_reversals_immutable_correction(client):
    headers = {"X-Tenant-ID": "northbridge", "X-User-ID": "adv-1", "X-User-Role": "advisor"}
    now = datetime.now(timezone.utc).isoformat()

    # Staff incorrectly records a fee of $200 on client c-rev
    # First, client has $1000 balance
    client.post("/api/v1/transactions", json={
        "client_ref": "c-rev",
        "type": "DEPOSIT",
        "amount": "1000.00",
        "currency": "USD",
        "effective_at": now,
        "external_id": "seed-deposit",
    }, headers=headers)

    res_err_fee = client.post("/api/v1/transactions", json={
        "client_ref": "c-rev",
        "type": "FEE",
        "amount": "200.00",
        "currency": "USD",
        "effective_at": now,
        "external_id": "accidental-fee",
        "description": "Erroneous fee charge",
    }, headers=headers)
    assert res_err_fee.status_code == 201
    err_fee_id = res_err_fee.json()["id"]

    # Balance drops to $800
    bal1 = client.get("/api/v1/clients/c-rev/balance", headers=headers).json()
    assert bal1["balance"] == "800.0000"

    # Staff discovers error and executes reversal
    rev_res = client.post(
        f"/api/v1/transactions/{err_fee_id}/reverse",
        json={"reason": "Incorrect fee assessment - client is fee-exempt under promotional tier"},
        headers=headers,
    )
    assert rev_res.status_code == 201
    rev_data = rev_res.json()

    assert rev_data["type"] == "REVERSAL"
    assert rev_data["direction"] == "CREDIT"  # Fee was DEBIT, so reversal is CREDIT
    assert rev_data["amount"] == "200.0000"
    assert rev_data["reverses_transaction_id"] == err_fee_id

    # Verify history is preserved: original transaction still exists and is marked is_reversed
    orig_res = client.get(f"/api/v1/transactions/{err_fee_id}", headers=headers)
    assert orig_res.status_code == 200
    orig_data = orig_res.json()
    assert orig_data["is_reversed"] is True
    assert orig_data["reversed_by_id"] == rev_data["id"]

    # Balance is restored back to $1000
    bal2 = client.get("/api/v1/clients/c-rev/balance", headers=headers).json()
    assert bal2["balance"] == "1000.0000"

    # Attempting to reverse the same transaction again is forbidden
    double_rev = client.post(
        f"/api/v1/transactions/{err_fee_id}/reverse",
        json={"reason": "Second reversal attempt"},
        headers=headers,
    )
    assert double_rev.status_code == 400
    assert "already been reversed" in double_rev.json()["detail"]

    # Attempting to reverse a reversal transaction itself is forbidden
    rev_of_rev = client.post(
        f"/api/v1/transactions/{rev_data['id']}/reverse",
        json={"reason": "Reversing the reversal"},
        headers=headers,
    )
    assert rev_of_rev.status_code == 400
    assert "Cannot reverse a transaction that is itself a reversal" in rev_of_rev.json()["detail"]
