"""The USSD-style menu: numbers or letters, step by step."""
import pytest

from messaging.bot import MENU, choice, handle
from messaging.models import BotSession
from payments.models import Customer, Invoice
from payments.services.invoicing import create_invoice, find_or_create_customer

pytestmark = pytest.mark.django_db


def test_choice_accepts_numbers_and_letters():
    assert [choice(x) for x in ["1", " 2. ", "b", "B", "c)", "hello", ""]] == [1, 2, 2, 2, 3, None, None]


def test_menu_and_new_invoice_for_new_customer(seller):
    b, u = seller
    assert handle(u, "hi") == MENU
    r = handle(u, "1")
    assert "Who is it for?" in r
    r = handle(u, "grace okon")
    assert "How much is Grace Okon's invoice?" in r
    assert "Type the amount" in handle(u, "lots")
    assert "What is it for?" in handle(u, "15k")
    r = handle(u, "food trays x3")
    assert "WhatsApp number" in r
    r = handle(u, "0803 777 8888")
    assert "Invoice for Grace Okon: ₦15,000 for food trays x3" in r and "1. Create and send to Grace" in r
    r = handle(u, "a")  # letters work too
    assert "INV-101 created for Grace Okon" in r and "Sent to Grace Okon on WhatsApp" in r
    inv = Invoice.objects.get()
    assert inv.amount == 1_500_000 and inv.customer.phone == "2348037778888"
    assert BotSession.objects.get(phone=u.phone).state == "menu"


def test_new_invoice_pick_existing_customer_create_only(seller):
    b, u = seller
    tunde = find_or_create_customer(b, "Tunde Bello", "08031112222")
    create_invoice(b, amount=100_000, customer=tunde, send=False)
    handle(u, "menu")
    r = handle(u, "1")
    assert "1. Tunde Bello" in r
    assert "How much is Tunde Bello's" in handle(u, "1")
    handle(u, "45000")
    assert "Check this" in handle(u, "1")  # 1 = skip description
    r = handle(u, "2")  # create only
    assert "Forward this to your customer" in r and "/pay/" in r


def test_unpaid_then_remind(seller):
    from messaging.models import Notification

    b, u = seller
    create_invoice(b, amount=500_000, customer=find_or_create_customer(b, "Kemi", "08035550000"), send=False)
    handle(u, "menu")
    r = handle(u, "3")
    assert "1. Kemi, INV-101: ₦5,000" in r
    r = handle(u, "1")
    assert "1. Send a reminder" in r
    assert "Reminder sent to Kemi" in handle(u, "1")
    assert Notification.objects.filter(template="landed_reminder").count() == 1


def test_customer_lookup_and_reset(seller):
    b, u = seller
    create_invoice(b, amount=700_000, customer=find_or_create_customer(b, "Chinedu Eze"), send=False)
    handle(u, "menu")
    assert "1. Chinedu Eze" in handle(u, "4")
    r = handle(u, "chin")
    assert "*Chinedu Eze*" in r and "Owes you: ₦7,000" in r
    assert "Who is it for" in handle(u, "1")
    assert handle(u, "0") == MENU  # 0 always goes home


def test_session_expires(seller):
    from datetime import timedelta

    from django.utils import timezone

    b, u = seller
    handle(u, "1")
    BotSession.objects.filter(phone=u.phone).update(updated_at=timezone.now() - timedelta(minutes=30))
    assert "Today so far" in handle(u, "2")  # back at the main menu, 2 = today
