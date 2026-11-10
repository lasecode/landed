"""Merchant bot commands (brief 5.1): invoice, today, unpaid (+ help)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Invoice, InvoiceStatus, Merchant, Payment, PaymentStatus, StaffUser
from ..money import fmt, naira_to_kobo
from ..services import notify
from ..services.invoicing import InvoiceError, create_invoice, find_or_create_customer

LAGOS = ZoneInfo("Africa/Lagos")
PHONE = r"(?:\+?234|0)[789][01]\d{8}"
INVOICE_RE = re.compile(
    rf"^\s*(?:invoice|inv)\s+(?P<name>.+?)\s+(?:(?P<phone>{PHONE})\s+)?(?P<amount>₦?\d[\d,]*(?:\.\d+)?\s*[km]?)\b\s*(?:for\s+(?P<desc>.+))?\s*$",
    re.IGNORECASE,
)

HELP = (
    "Here's what I can do:\n\n"
    "• *invoice Tunde 45000 for 3 bags of rice*: create an invoice (add his number after the name to send it to him)\n"
    "• *today*: money received today\n"
    "• *unpaid*: invoices still owing\n\n"
    "Payment alerts come to you automatically."
)


@dataclass
class ParsedInvoice:
    name: str
    amount: int
    description: str
    phone: str | None


def parse_invoice(text: str) -> ParsedInvoice | None:
    m = INVOICE_RE.match(text.strip())
    if not m:
        return None
    try:
        amount = naira_to_kobo(m.group("amount").replace(" ", ""))
    except Exception:
        return None
    return ParsedInvoice(name=m.group("name").strip().title(), amount=amount, description=(m.group("desc") or "").strip(),
                         phone=m.group("phone"))


def customer_forward_text(m: Merchant, inv: Invoice) -> str:
    va = inv.virtual_account
    return (
        f"Hello {notify.customer_name(inv)}, here is your invoice from {m.business_name}.\n\n"
        f"{inv.ref}: {fmt(inv.balance)}" + (f" for {inv.description}" if inv.description else "") + "\n\n"
        f"Pay by transfer to:\n{va.account_number}\n{va.bank_name}\n\n"
        f"Your payment is confirmed automatically once it lands. {notify.receipt_url(inv)}"
    ) if va else f"{inv.ref}: {fmt(inv.balance)}"


def handle_message(db: Session, user: StaffUser, text: str) -> str:
    m = user.merchant
    t = text.strip()
    low = t.lower()
    if low in {"help", "hi", "hello", "menu", "start"}:
        return f"Hi {user.name.split()[0]} 👋\n\n{HELP}"
    if low == "today":
        start = datetime.combine(datetime.now(LAGOS).date(), time.min, tzinfo=LAGOS)
        total, n = db.execute(select(func.coalesce(func.sum(Payment.amount), 0), func.count(Payment.id))
                              .where(Payment.merchant_id == m.id, Payment.status != PaymentStatus.refunded,
                                     Payment.paid_at >= start)).one()
        outstanding = db.scalar(select(func.coalesce(func.sum(Invoice.amount - Invoice.amount_paid), 0))
                                .where(Invoice.merchant_id == m.id,
                                       Invoice.status.in_([InvoiceStatus.unpaid, InvoiceStatus.part_paid])))
        unmatched = db.scalar(select(func.count(Payment.id)).where(Payment.merchant_id == m.id,
                                                                  Payment.status == PaymentStatus.unmatched))
        msg = f"*Today so far*\nReceived: {fmt(int(total))} from {n} payment{'s' if n != 1 else ''}\nStill owed to you: {fmt(int(outstanding))}"
        if unmatched:
            msg += f"\n⚠️ {unmatched} payment{'s' if unmatched != 1 else ''} need assigning in the dashboard"
        return msg
    if low == "unpaid":
        invs = db.scalars(select(Invoice).where(Invoice.merchant_id == m.id,
                                                Invoice.status.in_([InvoiceStatus.unpaid, InvoiceStatus.part_paid]))
                          .order_by(Invoice.created_at).limit(15)).all()
        if not invs:
            return "Nothing unpaid. Everyone has paid up 🎉"
        lines = [f"• {i.ref} {i.customer.name if i.customer else ''}: {fmt(i.balance)}"
                 + (" (part-paid)" if i.status == InvoiceStatus.part_paid else "") for i in invs]
        return "*Unpaid invoices*\n" + "\n".join(lines)
    if low.startswith(("invoice", "inv ")):
        p = parse_invoice(t)
        if not p:
            return "I couldn't read that. Try: *invoice Tunde 45000 for 3 bags of rice*"
        try:
            customer = find_or_create_customer(db, m, p.name, p.phone)
            inv = create_invoice(db, m, amount=p.amount, customer=customer, description=p.description,
                                 created_by_id=user.id, send=bool(customer.phone))
        except InvoiceError as e:
            db.rollback()
            return f"Couldn't create the invoice: {e}"
        va = inv.virtual_account
        head = f"✅ {inv.ref} created for {customer.name}: {fmt(inv.amount)}"
        if inv.amount_paid:
            head += f" ({fmt(inv.amount_paid)} covered by their credit)"
        if inv.status == InvoiceStatus.paid:
            return head + "\nFully covered by their credit. Nothing to pay."
        acct = f"\nAccount: {va.account_number} · {va.bank_name}" if va else ""
        if customer.phone:
            return head + acct + f"\n\nI've sent it to {customer.name} on WhatsApp. I'll tell you the moment they pay."
        return head + acct + "\n\nForward this to your customer 👇\n\n" + customer_forward_text(m, inv)
    return "Sorry, I didn't get that.\n\n" + HELP
