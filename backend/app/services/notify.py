"""Queue WhatsApp notifications. Rows are written in the same DB transaction as the money
movement; actual sending happens after commit (so a rolled-back payment never sends a receipt)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import event
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Invoice, Merchant, Notification, VirtualAccount, WhatsAppContact, utcnow
from ..money import fmt
from ..security import normalize_phone

log = logging.getLogger("landed.notify")
_PENDING = "pending_notifications"


def queue(db: Session, merchant_id: int, phone: str | None, template: str, args: list | None = None,
          invoice_id: int | None = None, body: str | None = None) -> Notification | None:
    to = normalize_phone(phone)
    if not to:
        return None
    params = {"body": body} if template == "text" else {"args": [str(a) for a in (args or [])]}
    n = Notification(merchant_id=merchant_id, recipient_phone=to, template=template, params=params, invoice_id=invoice_id)
    db.add(n)
    db.flush()
    db.info.setdefault(_PENDING, []).append(n.id)
    return n


@event.listens_for(Session, "after_commit")
def _dispatch_after_commit(session: Session) -> None:
    ids = session.info.pop(_PENDING, None)
    if not ids:
        return
    from ..tasks import enqueue

    for nid in ids:
        enqueue("app.jobs.send_notification", nid)


@event.listens_for(Session, "after_rollback")
def _drop_after_rollback(session: Session) -> None:
    session.info.pop(_PENDING, None)


def within_service_window(db: Session, phone: str) -> bool:
    c = db.get(WhatsAppContact, phone)
    if not c:
        return False
    last = c.last_inbound_at if c.last_inbound_at.tzinfo else c.last_inbound_at.replace(tzinfo=timezone.utc)
    return utcnow() - last < timedelta(hours=24)


# ---------- message builders ----------

def receipt_url(inv: Invoice) -> str:
    return f"{settings.public_api_url}/r/{inv.public_token}"


def _acct_line(va: VirtualAccount | None) -> tuple[str, str]:
    if not va:
        return ("—", "—")
    return (f"{va.account_number}", f"{va.bank_name}" + (f" · {va.account_name}" if va.account_name else ""))


def customer_name(inv: Invoice) -> str:
    return (inv.customer.name.split()[0] if inv.customer and inv.customer.name else "there")


def invoice_created(db: Session, m: Merchant, inv: Invoice) -> None:
    if not (inv.customer and inv.customer.phone):
        return
    acct, bank = _acct_line(inv.virtual_account)
    queue(db, m.id, inv.customer.phone, "invoice_new",
          [customer_name(inv), m.business_name, inv.ref, fmt(inv.balance), inv.description or "your order",
           acct, bank, receipt_url(inv)], invoice_id=inv.id)


def payment_applied(db: Session, m: Merchant, inv: Invoice, applied: int, payer: str | None) -> None:
    who = payer or (inv.customer.name if inv.customer else "A customer")
    if inv.status.value == "paid":
        state = "Fully paid."
        if inv.excess_amount and inv.excess_status.value == "pending":
            state = f"Fully paid, with {fmt(inv.excess_amount)} extra. Choose refund or credit in the dashboard."
    else:
        state = f"{fmt(inv.balance)} still outstanding."
    for phone in _merchant_alert_phones(m):
        queue(db, m.id, phone, "merchant_payment_alert", [who, fmt(applied), inv.ref, state], invoice_id=inv.id)
    if inv.customer and inv.customer.phone:
        if inv.status.value == "paid":
            queue(db, m.id, inv.customer.phone, "payment_receipt",
                  [customer_name(inv), m.business_name, fmt(applied), inv.ref, "Your invoice is fully paid. Thank you!",
                   receipt_url(inv)], invoice_id=inv.id)
        else:
            acct, bank = _acct_line(inv.virtual_account)
            queue(db, m.id, inv.customer.phone, "balance_notice",
                  [customer_name(inv), m.business_name, fmt(applied), inv.ref, fmt(inv.balance), acct, bank, receipt_url(inv)],
                  invoice_id=inv.id)


def credit_received(db: Session, m: Merchant, customer, amount: int, note: str) -> None:
    for phone in _merchant_alert_phones(m):
        queue(db, m.id, phone, "merchant_payment_alert", [customer.name, fmt(amount), "their account", note])
    if customer.phone:
        queue(db, m.id, customer.phone, "credit_received",
              [customer.name.split()[0], m.business_name, fmt(amount), note])


def unmatched(db: Session, m: Merchant, amount: int, payer: str | None) -> None:
    for phone in _merchant_alert_phones(m):
        queue(db, m.id, phone, "merchant_unmatched_alert", [fmt(amount), payer or "an unknown sender",
                                                              f"{settings.public_web_url}/unmatched"])


def _merchant_alert_phones(m: Merchant) -> list[str]:
    return [p for p in {normalize_phone(m.alert_phone or m.phone)} if p]
