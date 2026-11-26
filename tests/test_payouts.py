"""Payout tracking: the seller can always see where their share is."""
from datetime import date, datetime, timezone as tz

import pytest

from messaging.bot import handle
from messaging.models import Notification
from payments.models import Payment
from payments.services import payouts
from payments.services.invoicing import create_invoice, find_or_create_customer, pay_account

from .conftest import pay

pytestmark = pytest.mark.django_db


def test_next_business_day_skips_weekend():
    assert payouts.next_business_day(date(2026, 11, 26)) == date(2026, 11, 27)  # Thu -> Fri
    assert payouts.next_business_day(date(2026, 11, 27)) == date(2026, 11, 30)  # Fri -> Mon
    assert payouts.next_business_day(date(2026, 11, 28)) == date(2026, 11, 30)  # Sat -> Mon


def _paid(seller, naira=50000):
    b, u = seller
    inv = create_invoice(b, amount=naira * 100, customer=find_or_create_customer(b, "Kemi"), send=False)
    ref = pay(pay_account(inv).account_number, naira)
    return b, u, Payment.objects.get(reference=ref)


def test_payment_gets_share_and_expected_date(seller):
    b, u, p = _paid(seller)
    assert p.payout_status == "waiting" and p.payout_expected is not None
    assert p.payout_amount == p.amount - p.fees - round(p.amount * 0.01)
    assert p.landed_fee == round(p.amount * 0.01)
    alert = Notification.objects.filter(template="landed_seller_alert").last()
    assert "Your share goes to your" in alert.body and " by " in alert.body


def test_sync_marks_sent_and_tells_seller(seller, monkeypatch):
    b, u, p = _paid(seller)

    class Fake:
        def list_settlements(self, code, since):
            assert code == b.subaccount_code
            return [{"id": "77", "status": "success", "amount": p.payout_amount,
                     "date": datetime(2026, 11, 27, 9, tzinfo=tz.utc), "references": [p.reference, "other"]}]

    assert payouts.sync_business(b, Fake()) == 1
    p.refresh_from_db()
    assert p.payout_status == "sent" and p.payout_reference == "77" and p.payout_sent_at
    assert Notification.objects.filter(template="landed_payout_sent").count() == 1
    assert payouts.sync_business(b, Fake()) == 0  # already done: no repeat message
    assert Notification.objects.filter(template="landed_payout_sent").count() == 1


def test_late_payout_is_flagged(seller):
    b, u, p = _paid(seller)
    Payment.objects.filter(pk=p.pk).update(payout_expected=date(2020, 1, 1))
    s = payouts.summary(b)
    assert s["late"] and s["on_way"] == p.payout_amount


def test_payouts_page_and_overview(owner_client, seller):
    _paid(seller)
    r = owner_client.get("/app/payouts/")
    assert r.status_code == 200 and "on its way" in r.content.decode()
    assert owner_client.post("/app/payouts/").status_code == 302
    assert "On its way to your bank" in owner_client.get("/app/").content.decode()


def test_bot_option_7(seller):
    b, u, p = _paid(seller)
    handle(u, "menu")
    r = handle(u, "7")
    assert "on its way to" in r and "expected by" in r
