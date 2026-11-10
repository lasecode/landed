"""Brief section 9: signatures, idempotency, verify-before-trust, key encryption, merchant scoping, roles."""
import json

from sqlalchemy import select

from app import db as dbmod
from app import models
from app.providers.mock import DEFAULT_SECRET, sign

from .conftest import make_invoice, pay, signup


def _webhook(client, token, payload, secret=DEFAULT_SECRET, signature=None):
    raw = json.dumps(payload).encode()
    sig = signature if signature is not None else sign(secret, raw)
    return client.post(f"/webhooks/mock/{token}", content=raw, headers={"x-mock-signature": sig})


def _token():
    with dbmod.session_scope() as s:
        return s.scalar(select(models.Merchant.webhook_token))


def test_bad_signature_rejected_and_nothing_paid(client, owner):
    inv = make_invoice(client, owner, 45000)
    r = _webhook(client, _token(), {"event": "payment.received", "reference": "X1"}, signature="deadbeef")
    assert r.status_code == 401
    r = _webhook(client, _token(), {"event": "payment.received", "reference": "X1"}, secret="wrong-secret")
    assert r.status_code == 401
    with dbmod.session_scope() as s:
        assert s.query(models.Payment).count() == 0
        assert s.query(models.WebhookEvent).filter_by(status="rejected").count() == 2
    assert client.get(f"/api/invoices/{inv['id']}", headers=owner).json()["status"] == "unpaid"


def test_signed_webhook_for_unverifiable_transaction_does_nothing(client, owner):
    """A correctly signed call whose reference the provider doesn't confirm must not create money."""
    make_invoice(client, owner, 45000)
    r = _webhook(client, _token(), {"event": "payment.received", "reference": "NOT-REAL", "amount": 45000})
    assert r.status_code == 200
    with dbmod.session_scope() as s:
        assert s.query(models.Payment).count() == 0
        assert s.scalar(select(models.WebhookEvent.status)) == "ignored"


def test_amount_comes_from_verify_not_webhook_body(client, owner):
    inv = make_invoice(client, owner, 45000)
    ref = pay(client, owner, inv["account"]["account_number"], 1000)  # real transaction is ₦1,000
    with dbmod.session_scope() as s:
        s.query(models.LedgerEntry).delete()
        s.query(models.Payment).delete()
        i = s.get(models.Invoice, inv["id"])
        i.amount_paid, i.status = 0, models.InvoiceStatus.unpaid
    # a replayed body claiming ₦45,000 still only credits what the provider confirms
    _webhook(client, _token(), {"event": "payment.received", "reference": ref, "amount": 45000})
    d = client.get(f"/api/invoices/{inv['id']}", headers=owner).json()
    assert d["amount_paid"] == 100_000 and d["status"] == "part_paid"


def test_duplicate_webhook_is_ignored(client, owner):
    inv = make_invoice(client, owner, 45000)
    ref = pay(client, owner, inv["account"]["account_number"], 20000)
    for _ in range(3):
        assert _webhook(client, _token(), {"event": "payment.received", "reference": ref}).status_code == 200
    d = client.get(f"/api/invoices/{inv['id']}", headers=owner).json()
    assert d["amount_paid"] == 2_000_000 and len(d["payments"]) == 1
    with dbmod.session_scope() as s:
        assert s.query(models.WebhookEvent).filter_by(status="duplicate").count() == 3


def test_merchants_only_see_their_own_data(client, owner):
    inv = make_invoice(client, owner, 45000)
    other = signup(client, business="Kola Gadgets", phone="08039999999", name="Kola")
    assert client.get(f"/api/invoices/{inv['id']}", headers=other).status_code == 404
    assert client.get("/api/invoices", headers=other).json() == []
    assert client.get("/api/customers", headers=other).json() == []
    assert client.post(f"/api/invoices/{inv['id']}/cancel", headers=other).status_code == 404
    cust_id = inv["customer"]["id"]
    assert client.get(f"/api/customers/{cust_id}", headers=other).status_code == 404
    # B can't create an invoice for A's customer
    r = client.post("/api/invoices", headers=other, json={"customer_id": cust_id, "amount_naira": 1000})
    assert r.status_code == 404
    # B's payment simulator can't touch A's account
    pay(client, other, inv["account"]["account_number"], 45000)
    assert client.get(f"/api/invoices/{inv['id']}", headers=owner).json()["status"] == "unpaid"
    assert len(client.get("/api/payments?status=unmatched", headers=other).json()) == 1


def test_staff_role_limits(client, owner):
    r = client.post("/api/settings/staff", headers=owner, json={"name": "Bisi", "phone": "08030000002",
                                                               "password": "password123"})
    assert r.status_code == 200
    tok = client.post("/api/auth/login", json={"phone": "08030000002", "password": "password123"}).json()["token"]
    staff = {"Authorization": f"Bearer {tok}"}
    assert client.post("/api/invoices", headers=staff, json={"customer_name": "Ada", "amount_naira": 5000}).status_code == 200
    assert client.get("/api/payments", headers=staff).status_code == 200
    assert client.get("/api/settings", headers=staff).status_code == 403
    assert client.get("/api/reports/export.csv", headers=staff).status_code == 403
    assert client.put("/api/settings/provider", headers=staff, json={"provider": "mock"}).status_code == 403
    # owner removes the login; it stops working; re-adding resets the password
    uid = r.json()["id"]
    assert client.delete(f"/api/settings/staff/{uid}", headers=owner).status_code == 200
    assert client.get("/api/payments", headers=staff).status_code == 401
    assert client.post("/api/settings/staff", headers=owner, json={"name": "Bisi", "phone": "08030000002",
                                                                  "password": "newpassword1"}).status_code == 200
    assert client.post("/api/auth/login", json={"phone": "08030000002", "password": "newpassword1"}).status_code == 200


def test_provider_keys_encrypted_at_rest_and_masked(client, owner, monkeypatch):
    from app.providers.monnify import MonnifyProvider

    monkeypatch.setattr(MonnifyProvider, "test_connection", lambda self: None)
    r = client.put("/api/settings/provider", headers=owner, json={
        "provider": "monnify", "api_key": "MK_TEST_ABCDEF1234", "secret_key": "SUPERSECRETVALUE9876", "contract_code": "1234567"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["secret_key"].endswith("9876") and "SUPERSECRET" not in json.dumps(body)
    assert body["webhook_url"].startswith("http") and "/webhooks/monnify/" in body["webhook_url"]
    with dbmod.session_scope() as s:
        stored = s.scalar(select(models.Merchant.provider_keys))
    assert "SUPERSECRET" not in stored and "MK_TEST" not in stored
    # saving again with blank secrets keeps the old ones
    r = client.put("/api/settings/provider", headers=owner, json={"provider": "monnify", "contract_code": "7654321"})
    assert r.status_code == 200 and r.json()["contract_code"] == "7654321" and r.json()["secret_key"].endswith("9876")


def test_login_errors_and_auth_required(client, owner):
    assert client.post("/api/auth/login", json={"phone": "08030000001", "password": "nope"}).status_code == 401
    assert client.get("/api/invoices").status_code == 401
    assert client.get("/api/invoices", headers={"Authorization": "Bearer junk"}).status_code == 401
    assert client.post("/api/auth/login", json={"phone": "0803 000 0001", "password": "password123"}).status_code == 200
