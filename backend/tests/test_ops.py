"""Reconciliation, reminders, reports/CSV, WhatsApp bot, Monnify adapter, receipt page."""
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app import db as dbmod
from app import models
from app.models import utcnow

from .conftest import get_invoice, make_invoice, notifications, pay


def test_reconciliation_recovers_missed_webhook(client, owner):
    inv = make_invoice(client, owner, 45000)
    with dbmod.session_scope() as s:  # money landed at the provider but the webhook never arrived
        mid = s.scalar(select(models.Merchant.id))
        s.add(models.MockTransaction(reference="MISSED-1", merchant_id=mid, account_number=inv["account"]["account_number"],
                                     amount=4_500_000, payer_name="TUNDE", paid_at=utcnow()))
    from app.jobs import reconcile_merchant

    assert reconcile_merchant(mid) == {"checked": 1, "recovered": 1}
    d = get_invoice(client, owner, inv["id"])
    assert d["status"] == "paid" and d["payments"][0]["source"] == "reconciliation"
    assert reconcile_merchant(mid) == {"checked": 1, "recovered": 0}  # idempotent


def test_reminders_follow_schedule(client, owner):
    client.patch("/api/settings/business", headers=owner, json={"reminder_days": "5, 2"})
    inv = make_invoice(client, owner, 45000)
    from app.jobs import send_reminders

    now = utcnow()
    assert send_reminders(now) == 0
    assert send_reminders(now + timedelta(days=2, hours=1)) == 1
    assert send_reminders(now + timedelta(days=3)) == 0
    assert send_reminders(now + timedelta(days=5, hours=1)) == 1
    assert send_reminders(now + timedelta(days=9)) == 0  # schedule used up
    assert [t for t, *_ in notifications()].count("payment_reminder") == 2
    pay(client, owner, inv["account"]["account_number"], 45000)


def test_reports_and_csv(client, owner):
    a = make_invoice(client, owner, 45000)
    make_invoice(client, owner, 10000, name="Kemi")
    pay(client, owner, a["account"]["account_number"], 45000)
    r = client.get("/api/reports", headers=owner).json()
    assert r["received"] == 4_500_000 and r["invoiced"] == 5_500_000 and r["paid_invoices"] == 1
    assert r["outstanding_from_period"] == 1_000_000
    csv = client.get("/api/reports/export.csv?kind=payments", headers=owner)
    assert csv.status_code == 200 and "45000.00" in csv.text and a["ref"] in csv.text
    csv = client.get("/api/reports/export.csv?kind=invoices", headers=owner)
    assert csv.text.count("\n") == 3
    ov = client.get("/api/overview", headers=owner).json()
    assert ov["today"] == 4_500_000 and ov["outstanding"] == 1_000_000 and ov["open_invoices"] == 1


# ---------- WhatsApp bot ----------

@pytest.mark.parametrize("text,name,amount,desc,phone", [
    ("invoice Tunde 45000 for 3 bags of rice", "Tunde", 4_500_000, "3 bags of rice", None),
    ("Invoice mama ngozi 45,000 for oil", "Mama Ngozi", 4_500_000, "oil", None),
    ("inv Kemi 12.5k", "Kemi", 1_250_000, "", None),
    ("invoice Chinedu 08031234567 20000 for phone screen", "Chinedu", 2_000_000, "phone screen", "08031234567"),
    ("invoice Grace ₦7,500 for hair", "Grace", 750_000, "hair", None),
])
def test_bot_parses_invoice_commands(text, name, amount, desc, phone):
    from app.whatsapp.bot import parse_invoice

    p = parse_invoice(text)
    assert p and (p.name, p.amount, p.description, p.phone) == (name, amount, desc, phone)


def test_bot_rejects_garbage():
    from app.whatsapp.bot import parse_invoice

    assert parse_invoice("invoice Tunde") is None
    assert parse_invoice("invoice for rice") is None


def _wa(client, phone, text):
    payload = {"entry": [{"changes": [{"value": {"messages": [{"from": phone, "type": "text", "text": {"body": text}}]}}]}]}
    return client.post("/webhooks/whatsapp", content=json.dumps(payload))


def test_whatsapp_bot_end_to_end(client, owner):
    assert _wa(client, "2348030000001", "invoice Tunde 08031112222 45000 for 3 bags of rice").status_code == 200
    sent = notifications()
    assert sent[0][0] == "invoice_new" and sent[0][1] == "2348031112222"  # straight to Tunde
    reply = sent[-1][2]["body"]
    assert reply.startswith("✅ INV-101 created for Tunde: ₦45,000") and "sent it to Tunde" in reply

    _wa(client, "2348030000001", "invoice Bola 10000 for drinks")  # no number: forwardable message
    assert "Forward this to your customer" in notifications()[-1][2]["body"]

    inv = client.get("/api/invoices?q=INV-101", headers=owner).json()[0]
    pay(client, owner, inv["account"]["account_number"], 45000)
    _wa(client, "2348030000001", "today")
    assert "Received: ₦45,000 from 1 payment" in notifications()[-1][2]["body"]
    _wa(client, "2348030000001", "unpaid")
    body = notifications()[-1][2]["body"]
    assert "Bola" in body and "Tunde" not in body

    _wa(client, "2348030000001", "invoice tunde 5000 for oil")  # first name finds Tunde Bello
    assert notifications()[-2][0] == "invoice_new" and notifications()[-2][1] == "2348031112222"

    n = len(notifications())
    _wa(client, "2348099999999", "today")  # unknown number: no reply
    assert len(notifications()) == n


def test_whatsapp_verify_handshake(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "wa_verify_token", "abc")
    ok = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "abc", "hub.challenge": "42"})
    assert ok.text == "42"
    assert client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "x"}).status_code == 403


def test_receipt_page(client, owner):
    inv = make_invoice(client, owner, 45000)
    r = client.get(inv["receipt_url"].split("8000", 1)[1])
    assert r.status_code == 200 and "Awaiting payment" in r.text and inv["account"]["account_number"] in r.text
    pay(client, owner, inv["account"]["account_number"], 45000)
    r = client.get(inv["receipt_url"].split("8000", 1)[1])
    assert "Receipt" in r.text and "Paid" in r.text and inv["account"]["account_number"] not in r.text.split("Payments")[0].split("remaining")[1]


def test_free_plan_cap(client, owner, monkeypatch):
    from app.services import invoicing

    monkeypatch.setitem(invoicing.PLAN_CAPS, "free", 2)
    make_invoice(client, owner, 1000)
    make_invoice(client, owner, 1000)
    r = client.post("/api/invoices", headers=owner, json={"customer_name": "X", "amount_naira": 1000})
    assert r.status_code == 400 and "plan" in r.json()["detail"]


# ---------- Monnify adapter against a fake HTTP server ----------

def _monnify(handler):
    from app.providers.monnify import MonnifyProvider

    return MonnifyProvider({"api_key": "MK_TEST", "secret_key": "SECRET", "contract_code": "C1"},
                           client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_monnify_dynamic_account_and_verify():
    from datetime import datetime, timezone

    from app.providers.base import AccountRequest

    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path))
        if req.url.path == "/api/v1/auth/login":
            assert req.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"requestSuccessful": True, "responseBody": {"accessToken": "T", "expiresIn": 3600}})
        assert req.headers["authorization"] == "Bearer T"
        if req.url.path == "/api/v1/invoice/create":
            body = json.loads(req.content)
            assert body["amount"] == "45000.00" and body["paymentMethods"] == ["ACCOUNT_TRANSFER"]
            return httpx.Response(200, json={"requestSuccessful": True, "responseBody": {
                "invoiceReference": body["invoiceReference"], "accountNumber": "5000123456", "accountName": "Adaeze Foods INV-101",
                "bankName": "Wema bank", "expiryDate": "2026-11-17 10:00:00"}})
        if req.url.path.startswith("/api/v2/transactions/"):
            assert "MNFY%7C" in req.url.raw_path.decode()
            return httpx.Response(200, json={"requestSuccessful": True, "responseBody": {
                "transactionReference": "MNFY|01|X", "paymentStatus": "PAID", "amountPaid": "45000.00",
                "paidOn": "2026-11-10 14:05:00.0", "product": {"reference": "m1-inv101-abc", "type": "INVOICE"},
                "destinationAccountInformation": {"accountNumber": "5000123456", "bankName": "Wema"},
                "paymentSourceInformation": [{"accountName": "TUNDE BELLO", "bankCode": "058"}]}})
        return httpx.Response(404, json={"requestSuccessful": False, "responseMessage": "nope"})

    p = _monnify(handler)
    acc = p.create_dynamic_account(AccountRequest(reference="m1-inv101-abc", customer_name="Tunde", customer_email="t@x.ng",
                                                  amount=4_500_000, expires_at=datetime(2026, 11, 17, 9, tzinfo=timezone.utc)))
    assert acc.account_number == "5000123456" and acc.expires_at.hour == 9  # 10:00 Lagos = 09:00 UTC
    tx = p.verify_transaction("MNFY|01|X")
    assert tx.is_paid and tx.amount == 4_500_000 and tx.account_number == "5000123456" and tx.payer_name == "TUNDE BELLO"
    assert tx.paid_at.hour == 13
    assert calls.count(("POST", "/api/v1/auth/login")) == 1  # token cached


def test_monnify_signature_and_errors():
    import hashlib
    import hmac

    from app.providers.base import ProviderError

    p = _monnify(lambda r: httpx.Response(401, json={"requestSuccessful": False, "responseMessage": "Invalid credentials"}))
    body = b'{"eventType":"SUCCESSFUL_TRANSACTION","eventData":{"transactionReference":"MNFY|1"}}'
    good = hmac.new(b"SECRET", body, hashlib.sha512).hexdigest()
    assert p.verify_webhook_signature(body, {"monnify-signature": good})
    assert not p.verify_webhook_signature(body + b" ", {"monnify-signature": good})
    assert not p.verify_webhook_signature(body, {})
    info = p.parse_webhook(json.loads(body))
    assert info.is_payment and info.reference == "MNFY|1"
    with pytest.raises(ProviderError) as e:
        p.test_connection()
    assert "SECRET" not in str(e.value) and "MK_TEST" not in str(e.value)
