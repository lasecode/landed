"""Seller commands on WhatsApp: invoice, today, unpaid, help."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time

from django.db.models import Count, F, Sum
from django.utils import timezone

from payments.models import Invoice, Payment
from payments.money import naira, naira_to_kobo
from payments.services.invoicing import InvoiceError, create_invoice, find_or_create_customer

PHONE = r"(?:\+?234|0)[789][01]\d{8}"
INVOICE_RE = re.compile(
    rf"^\s*(?:invoice|inv)\s+(?P<name>.+?)\s+(?:(?P<phone>{PHONE})\s+)?(?P<amount>₦?\d[\d,]*(?:\.\d+)?\s*[km]?)\b"
    rf"\s*(?:for\s+(?P<desc>.+))?\s*$", re.IGNORECASE)

HELP = ("Here's what I can do:\n\n"
        "• *invoice Tunde 45000 for 3 bags of rice*: create an invoice (add his number after the name to send it to him)\n"
        "• *today*: money received today\n"
        "• *unpaid*: invoices still owing\n\n"
        "Payment alerts come to you automatically.")


@dataclass
class Parsed:
    name: str
    amount: int
    description: str
    phone: str | None


def parse_invoice(text: str) -> Parsed | None:
    m = INVOICE_RE.match(text.strip())
    if not m:
        return None
    try:
        amount = naira_to_kobo(m.group("amount"))
    except ValueError:
        return None
    return Parsed(m.group("name").strip().title(), amount, (m.group("desc") or "").strip(), m.group("phone"))


def handle(user, text: str) -> str:
    b = user.business
    low = text.strip().lower()
    if low in {"help", "hi", "hello", "menu", "start"}:
        return f"Hi {user.name.split()[0]} 👋\n\n{HELP}"
    if low == "today":
        start = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
        agg = Payment.objects.filter(business=b, paid_at__gte=start).exclude(status=Payment.Status.REFUNDED) \
            .aggregate(total=Sum("amount"), n=Count("id"))
        owed = Invoice.objects.filter(business=b, status__in=Invoice.OPEN) \
            .aggregate(t=Sum(F("amount") - F("amount_paid")))["t"] or 0
        unmatched = Payment.objects.filter(business=b, status=Payment.Status.UNMATCHED).count()
        n = agg["n"] or 0
        msg = (f"*Today so far*\nReceived: {naira(agg['total'] or 0)} from {n} payment{'s' if n != 1 else ''}\n"
               f"Still owed to you: {naira(owed)}")
        if unmatched:
            msg += f"\n⚠️ {unmatched} payment{'s' if unmatched != 1 else ''} need assigning in Landed"
        return msg
    if low == "unpaid":
        invs = Invoice.objects.filter(business=b, status__in=Invoice.OPEN).select_related("customer").order_by("created_at")[:15]
        if not invs:
            return "Nothing unpaid. Everyone has paid up 🎉"
        return "*Unpaid invoices*\n" + "\n".join(
            f"• {i.ref} {i.customer.name if i.customer else ''}: {naira(i.balance)}"
            + (" (part-paid)" if i.status == Invoice.Status.PART_PAID else "") for i in invs)
    if low.startswith(("invoice", "inv ")):
        p = parse_invoice(text)
        if not p:
            return "I couldn't read that. Try: *invoice Tunde 45000 for 3 bags of rice*"
        try:
            customer = find_or_create_customer(b, p.name, p.phone)
            inv = create_invoice(b, amount=p.amount, customer=customer, description=p.description,
                                 created_by=user, send=bool(customer.phone))
        except InvoiceError as e:
            return f"Couldn't create the invoice: {e}"
        from messaging.notify import pay_url

        head = f"✅ {inv.ref} created for {customer.name}: {naira(inv.amount)}"
        if inv.amount_paid:
            head += f" ({naira(inv.amount_paid)} covered by their credit)"
        if inv.status == Invoice.Status.PAID:
            return head + "\nFully covered by their credit. Nothing to pay."
        if customer.phone:
            return head + f"\n\nI've sent it to {customer.name} on WhatsApp. I'll tell you the moment they pay."
        return (head + "\n\nForward this to your customer 👇\n\n"
                f"Hello {customer.first_name}, here is your invoice from {b.name}: {inv.ref}, {naira(inv.balance)}"
                + (f" for {inv.description}" if inv.description else "")
                + f".\n\nPay by bank transfer here: {pay_url(inv)}\nYour payment is confirmed automatically.")
    return "Sorry, I didn't get that.\n\n" + HELP
