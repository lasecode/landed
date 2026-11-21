"""Dashboard, public pay page, bot, reports, jobs and the Paystack adapter."""
import json
from datetime import timedelta

import httpx
import pytest
from django.test import Client
from django.utils import timezone

from accounts.models import Business, User
from messaging.bot import parse_invoice
from payments.models import Invoice, MockTransaction, Payment
from payments.services.invoicing import create_invoice, find_or_create_customer, pay_account

from .conftest import pay, sent

pytestmark = pytest.mark.django_db


def test_signup_then_payout_setup():
    c = Client()
    r = c.post("/signup/", {"business_name": "Ada Hair", "name": "Ada Obi", "phone": "0803 111 2222",
                            "password": "longpassword1"})
    assert r.status_code == 302 and r.url == "/app/setup/payout/"
    b = Business.objects.get()
    assert b.status == "setup" and User.objects.get().is_owner
    assert "Add the bank account" in c.get("/app/").content.decode()
    r = c.post("/app/setup/payout/", {"bank_code": "50515", "account_number": "0001112223"})
    assert "Check the account number" in r.content.decode()  # mock refuses 000… numbers
    r = c.post("/app/setup/payout/", {"bank_code": "50515", "account_number": "8012345678"})
    assert r.status_code == 302
    b.refresh_from_db()
    assert b.can_receive_payments and b.payout_bank_name == "Moniepoint MFB"


def test_every_page_renders(seller, owner_client):
    b, _ = seller
    inv = create_invoice(b, amount=4_500_000, customer=find_or_create_customer(b, "Tunde Bello", "08031112222"))
    pay(pay_account(inv).account_number, 45000)
    pay("9900000009", 1000)
    for url in ["/app/", "/app/invoices/", "/app/invoices/?status=paid", "/app/customers/", "/app/unmatched/",
                "/app/reports/", "/app/settings/", "/app/bot/", "/app/setup/payout/", f"/app/invoices/{inv.pk}/",
                f"/app/customers/{inv.customer.pk}/", "/app/invoices/new/", f"/pay/{inv.public_token}/",
                "/login/", "/signup/", "/password-reset/"]:
        assert (owner_client if url.startswith("/app") else Client()).get(url).status_code == 200, url


def test_create_invoice_from_dashboard_and_simulate(owner_client, seller):
    r = owner_client.post("/app/invoices/new/", {"customer_name": "Grace Okon", "customer_phone": "08037778888",
                                                 "amount": "15,000", "description": "Food trays", "send": "on"},
                          HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and "INV-101" in r.content.decode()
    assert json.loads(r["HX-Trigger"])["toast"].startswith("INV-101 created")
    inv = Invoice.objects.get()
    r = owner_client.post(f"/app/invoices/{inv.pk}/simulate/", {"amount": ""}, HTTP_HX_REQUEST="true")
    inv.refresh_from_db()
    assert inv.status == "paid", r.content.decode()[:500]


def test_public_pay_flow(seller):
    b, _ = seller
    inv = create_invoice(b, amount=4_500_000, customer=None, description="Rice")
    c = Client()
    page = c.get(f"/pay/{inv.public_token}/").content.decode()
    assert "Pay ₦45,000 by bank transfer" in page
    box = c.post(f"/pay/{inv.public_token}/account/").content.decode()
    acct = inv.accounts.get()
    assert acct.account_number in box and "Transfer exactly" in box
    part = c.post(f"/pay/{inv.public_token}/account/", {"amount": "10000"}).content.decode()
    assert "₦10,000" in part
    # customer pays but the webhook is lost: the "I've sent it" check asks the provider directly
    from payments.providers.mock import simulate_transfer

    live = inv.current_pay_account()
    simulate_transfer(live.account_number, 1_000_000, "ADA")
    r = c.get(f"/pay/{inv.public_token}/status/?paid_before=0&check=1")
    assert r["HX-Refresh"] == "true"
    inv.refresh_from_db()
    assert inv.amount_paid == 1_000_000 and Payment.objects.get().source == "page_check"


def test_assign_and_refund_from_dashboard(seller, owner_client):
    b, _ = seller
    inv = create_invoice(b, amount=3_000_000, customer=None)
    a = pay_account(inv)
    inv.status = "cancelled"
    inv.save()
    pay(a.account_number, 30000)
    p = Payment.objects.get()
    inv2 = create_invoice(b, amount=3_000_000, customer=None)
    assert owner_client.get(f"/app/payments/{p.pk}/assign/").status_code == 200
    r = owner_client.post(f"/app/payments/{p.pk}/assign/", {"mode": "invoice", "target": inv2.pk}, HTTP_HX_REQUEST="true")
    assert r.status_code == 204
    inv2.refresh_from_db()
    assert inv2.status == "paid"


def test_reports_and_csv(seller, owner_client):
    b, _ = seller
    inv = create_invoice(b, amount=4_500_000, customer=find_or_create_customer(b, "Tunde Bello"))
    create_invoice(b, amount=1_000_000, customer=None)
    pay(pay_account(inv).account_number, 45000)
    html = owner_client.get("/app/reports/").content.decode()
    assert "₦45,000" in html and "₦55,000" in html
    csv = owner_client.get("/app/reports/export.csv?kind=payments").content.decode()
    assert "45000.00" in csv and "INV-101" in csv
    assert owner_client.get("/app/reports/export.csv?kind=invoices").content.decode().count("\n") == 3


def test_reconciliation_recovers_missed_webhook(seller):
    from payments.providers.mock import simulate_transfer
    from payments.tasks import reconcile

    b, _ = seller
    inv = create_invoice(b, amount=4_500_000, customer=None)
    simulate_transfer(pay_account(inv).account_number, 4_500_000)  # no webhook delivered
    assert reconcile() == {"checked": 1, "recovered": 1}
    inv.refresh_from_db()
    assert inv.status == "paid"
    assert reconcile() == {"checked": 1, "recovered": 0}


def test_reminders_follow_schedule(seller):
    from payments.tasks import send_reminders

    b, _ = seller
    b.reminder_days = "2,5"
    b.save()
    create_invoice(b, amount=500_000, customer=find_or_create_customer(b, "Tunde", "08031112222"))
    now = timezone.now()
    assert send_reminders(now) == 0
    assert send_reminders(now + timedelta(days=2, hours=1)) == 1
    assert send_reminders(now + timedelta(days=3)) == 0
    assert send_reminders(now + timedelta(days=5, hours=1)) == 1
    assert send_reminders(now + timedelta(days=9)) == 0


@pytest.mark.parametrize("text,name,amount,desc,phone", [
    ("invoice Tunde 45000 for 3 bags of rice", "Tunde", 4_500_000, "3 bags of rice", None),
    ("Invoice mama ngozi 45,000 for oil", "Mama Ngozi", 4_500_000, "oil", None),
    ("inv Kemi 12.5k", "Kemi", 1_250_000, "", None),
    ("invoice Chinedu 08031234567 20000 for screen", "Chinedu", 2_000_000, "screen", "08031234567"),
])
def test_bot_parser(text, name, amount, desc, phone):
    p = parse_invoice(text)
    assert (p.name, p.amount, p.description, p.phone) == (name, amount, desc, phone)


def test_whatsapp_bot_end_to_end(seller):
    def wa(phone, text):
        body = {"entry": [{"changes": [{"value": {"messages": [{"from": phone, "type": "text", "text": {"body": text}}]}}]}]}
        return Client().post("/webhooks/whatsapp/", data=json.dumps(body), content_type="application/json")

    assert wa("2348030000001", "invoice Tunde 08031112222 45000 for rice").status_code == 200
    assert sent()[-2][0] == "landed_invoice" and "INV-101 created for Tunde" in sent()[-1][2][0]
    wa("2348030000001", "invoice tunde 5000 for oil")
    assert sent()[-2][1] == "2348031112222"  # "tunde" found Tunde's saved number
    wa("2348030000001", "today")
    assert "Today so far" in sent()[-1][2][0]
    n = len(sent())
    wa("2348099999999", "today")
    assert len(sent()) == n


# ---------- Paystack adapter against a fake Paystack ----------

def test_paystack_adapter(settings):
    from payments.providers.paystack import PaystackProvider

    settings.PAYSTACK_DVA_BANK = "test-bank"
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path))
        assert req.headers["authorization"] == "Bearer sk_test_x"
        p = req.url.path
        if p == "/bank":
            return httpx.Response(200, json={"status": True, "data": [{"code": "058", "name": "GTBank", "active": True}]})
        if p == "/bank/resolve":
            return httpx.Response(200, json={"status": True, "data": {"account_name": "ADA OBI"}})
        if p == "/subaccount":
            body = json.loads(req.content)
            assert body["percentage_charge"] == 1.0 and body["settlement_bank"] == "058"
            return httpx.Response(200, json={"status": True, "data": {"subaccount_code": "ACCT_abc"}})
        if p == "/charge":
            body = json.loads(req.content)
            assert body["subaccount"] == "ACCT_abc" and body["amount"] == "4500000" and "account_expires_at" in body["bank_transfer"]
            return httpx.Response(200, json={"status": True, "data": {
                "status": "pending_bank_transfer", "reference": body["reference"], "account_number": "1231084927",
                "account_name": "PAYSTACK CHECKOUT", "bank": {"name": "Test Bank"},
                "account_expires_at": "2026-11-21T18:00:00.000Z"}})
        if p == "/customer":
            return httpx.Response(200, json={"status": True, "data": {"customer_code": "CUS_1"}})
        if p == "/dedicated_account":
            body = json.loads(req.content)
            assert body["subaccount"] == "ACCT_abc" and body["preferred_bank"] == "test-bank"
            return httpx.Response(200, json={"status": True, "data": {"id": 7, "account_number": "9930000737",
                                                                      "account_name": "LANDED/ADA", "bank": {"name": "Wema Bank"}}})
        if p.startswith("/transaction/verify/"):
            return httpx.Response(200, json={"status": True, "data": {
                "status": "success", "reference": "LND-1-x", "amount": 4500000, "fees": 77500, "paid_at": "2026-11-21T10:05:00.000Z",
                "channel": "bank_transfer", "subaccount": {"subaccount_code": "ACCT_abc"},
                "customer": {"customer_code": "CUS_1"},
                "authorization": {"sender_name": "TUNDE BELLO", "sender_bank": "GTBank", "receiver_bank_account_number": "1231084927"}}})
        return httpx.Response(404, json={"status": False, "message": "Not found"})

    p = PaystackProvider("sk_test_x", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert p.list_banks()[0].code == "058"
    assert p.resolve_account("0123456789", "058") == "ADA OBI"
    assert p.create_subaccount(business_name="Ada", bank_code="058", account_number="0123456789", fee_percent=1.0,
                               contact_phone="234", contact_email="") == "ACCT_abc"
    acct = p.create_transfer_account(amount=4_500_000, email="x@y.ng", reference="LND-1-x", subaccount_code="ACCT_abc",
                                     expires_at=timezone.now() + timedelta(hours=8), metadata={})
    assert acct.account_number == "1231084927" and acct.expires_at.hour == 18
    dva = p.create_dedicated_account(customer_name="Ada Obi", phone="234", email="a@b.ng", subaccount_code="ACCT_abc")
    assert dva.account_number == "9930000737" and dva.customer_code == "CUS_1"
    tx = p.verify("LND-1-x")
    assert tx.is_success and tx.amount == 4_500_000 and tx.fees == 77500 and tx.payer_name == "TUNDE BELLO"
    assert tx.account_number == "1231084927" and tx.subaccount_code == "ACCT_abc"

    import hashlib
    import hmac

    raw = b'{"event":"charge.success","data":{"reference":"LND-1-x"}}'
    good = hmac.new(b"sk_test_x", raw, hashlib.sha512).hexdigest()
    assert p.verify_signature(raw, {"x-paystack-signature": good})
    assert not p.verify_signature(raw + b" ", {"x-paystack-signature": good})
    assert p.parse_webhook(json.loads(raw)).is_payment


def test_paystack_errors_never_leak_key():
    from payments.providers.base import ProviderError
    from payments.providers.paystack import PaystackProvider

    p = PaystackProvider("sk_live_SECRET", client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"status": False, "message": "Invalid key"}))))
    with pytest.raises(ProviderError) as e:
        p.list_banks()
    assert "SECRET" not in str(e.value) and "Invalid key" in str(e.value)
