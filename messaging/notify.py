"""Queue WhatsApp messages. Rows are written in the same DB transaction as the money movement and
sent only after commit, so a rolled-back payment never sends a receipt."""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts.models import normalize_phone
from payments.money import naira

from .models import Notification, WhatsAppContact
from .whatsapp.templates import render


def queue(business, phone: str | None, template: str, params: list, invoice=None) -> Notification | None:
    to = normalize_phone(phone)
    if not to:
        return None
    params = [str(p) for p in params]
    n = Notification.objects.create(business=business, to=to, template=template, params=params,
                                    body=render(template, params), invoice=invoice)
    from .tasks import send_notification

    transaction.on_commit(lambda: send_notification.delay(n.id))
    return n


def in_service_window(phone: str) -> bool:
    c = WhatsAppContact.objects.filter(phone=phone).first()
    return bool(c and timezone.now() - c.last_inbound_at < timedelta(hours=24))


# ---------- builders ----------

def pay_url(invoice) -> str:
    return settings.PUBLIC_URL + reverse("public_invoice", args=[invoice.public_token])


def dashboard_url(name: str) -> str:
    return settings.PUBLIC_URL + reverse(name)


def _first(invoice) -> str:
    return invoice.customer.first_name if invoice.customer else "there"


def invoice_sent(invoice) -> None:
    c = invoice.customer
    if not (c and c.phone):
        return
    b = invoice.business
    dva = c.dedicated_account
    if dva:
        queue(b, c.phone, "landed_invoice_account",
              [_first(invoice), b.name, invoice.ref, naira(invoice.balance), invoice.description or "your order",
               dva.account_number, dva.bank_name, pay_url(invoice)], invoice)
    else:
        queue(b, c.phone, "landed_invoice",
              [_first(invoice), b.name, invoice.ref, naira(invoice.balance), invoice.description or "your order",
               pay_url(invoice)], invoice)


def payment_applied(invoice, amount: int, payer: str) -> None:
    b = invoice.business
    if invoice.status == invoice.Status.PAID:
        state = "Fully paid."
        if invoice.excess_status == invoice.Excess.PENDING:
            state = f"Fully paid, with {naira(invoice.excess_amount)} extra. Choose refund or credit in Landed."
    else:
        state = f"{naira(invoice.balance)} still outstanding."
    queue(b, b.alert_number, "landed_seller_alert", [payer or "A customer", naira(amount), invoice.ref, state], invoice)
    c = invoice.customer
    if c and c.phone:
        if invoice.status == invoice.Status.PAID:
            queue(b, c.phone, "landed_receipt", [_first(invoice), b.name, naira(amount), invoice.ref,
                                                  "Your invoice is fully paid. Thank you!", pay_url(invoice)], invoice)
        else:
            queue(b, c.phone, "landed_balance", [_first(invoice), b.name, naira(amount), invoice.ref,
                                                  naira(invoice.balance), pay_url(invoice)], invoice)


def credit_received(business, customer, amount: int, note: str) -> None:
    queue(business, business.alert_number, "landed_seller_alert", [customer.name, naira(amount), "their account", note])
    if customer.phone:
        queue(business, customer.phone, "landed_credit", [customer.first_name, business.name, naira(amount), note])


def unmatched(business, amount: int, payer: str) -> None:
    if business:
        queue(business, business.alert_number, "landed_seller_unmatched",
              [naira(amount), payer or "an unknown sender", dashboard_url("unmatched")])


def account_assigned(customer, account) -> None:
    if customer.phone:
        queue(customer.business, customer.phone, "landed_account_assigned",
              [customer.first_name, customer.business.name, account.account_number, account.bank_name])
