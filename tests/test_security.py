import json

import pytest
from django.test import Client

from payments.models import Invoice, Payment, WebhookEvent
from payments.providers.mock import simulate_transfer
from payments.services.invoicing import create_invoice, find_or_create_customer, pay_account

from .conftest import deliver, make_seller

pytestmark = pytest.mark.django_db


def test_bad_signature_rejected(seller):
    b, _ = seller
    inv = create_invoice(b, amount=4_500_000, customer=None)
    ref = simulate_transfer(pay_account(inv).account_number, 4_500_000)
    assert deliver(ref, signature="deadbeef").status_code == 401
    assert deliver(ref, signature="").status_code == 401
    assert Payment.objects.count() == 0 and WebhookEvent.objects.filter(status="rejected").count() == 2


def test_signed_but_unverifiable_reference_creates_nothing(seller):
    assert deliver("NOT-A-REAL-REF").status_code == 200
    assert Payment.objects.count() == 0
    assert WebhookEvent.objects.get().status == "ignored"


def test_amount_comes_from_verify_not_body(seller):
    b, _ = seller
    inv = create_invoice(b, amount=4_500_000, customer=None)
    ref = simulate_transfer(pay_account(inv).account_number, 100_000)  # really paid ₦1,000
    raw = json.dumps({"event": "charge.success", "data": {"reference": ref, "amount": 4_500_000}}).encode()
    from payments.providers.mock import sign

    Client().post("/webhooks/paystack/", data=raw, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sign(raw))
    inv.refresh_from_db()
    assert inv.amount_paid == 100_000 and inv.status == "part_paid"


def test_duplicate_webhooks_counted_once(seller):
    b, _ = seller
    inv = create_invoice(b, amount=4_500_000, customer=None)
    ref = simulate_transfer(pay_account(inv).account_number, 2_000_000)
    for _ in range(3):
        deliver(ref)
    inv.refresh_from_db()
    assert inv.amount_paid == 2_000_000 and Payment.objects.count() == 1
    assert WebhookEvent.objects.filter(status="duplicate").count() == 2


def test_sellers_cannot_see_each_other(seller, owner_client):
    b, _ = seller
    inv = create_invoice(b, amount=500_000, customer=find_or_create_customer(b, "Tunde", "08031112222"))
    _, other = make_seller(phone="08039999999", business="Kola Gadgets", name="Kola")
    c = Client()
    c.force_login(other)
    assert c.get(f"/app/invoices/{inv.pk}/").status_code == 404
    assert c.post(f"/app/invoices/{inv.pk}/cancel/").status_code == 404
    assert c.get(f"/app/customers/{inv.customer.pk}/").status_code == 404
    assert "Tunde" not in c.get("/app/customers/").content.decode()
    assert inv.ref not in c.get("/app/invoices/").content.decode()
    assert owner_client.get(f"/app/invoices/{inv.pk}/").status_code == 200


def test_staff_limits(seller, owner_client):
    r = owner_client.post("/app/settings/", {"section": "staff", "staff-name": "Bisi", "staff-phone": "08030000002",
                                             "staff-password": "password123", "staff-role": "staff"})
    assert r.status_code == 302
    staff = Client()
    assert staff.post("/login/", {"username": "0803 000 0002", "password": "password123"}).status_code == 302
    assert staff.get("/app/invoices/").status_code == 200
    assert staff.get("/app/settings/").status_code == 403
    assert staff.get("/app/reports/export.csv").status_code == 403
    assert staff.get("/app/setup/payout/").status_code == 403


def test_auth_required_and_bad_login(db):
    assert Client().get("/app/").status_code == 302
    make_seller(payout=False)
    r = Client().post("/login/", {"username": "08030000001", "password": "wrong"})
    assert r.status_code == 200 and "don&#x27;t match" in r.content.decode()


def test_public_page_shows_nothing_private(seller):
    b, _ = seller
    inv = create_invoice(b, amount=500_000, customer=find_or_create_customer(b, "Tunde", "08031112222"))
    html = Client().get(f"/pay/{inv.public_token}/").content.decode()
    assert "Adaeze Foods" in html and "08031112222" not in html and "2348031112222" not in html
    assert Client().get("/pay/not-a-token/").status_code == 404
