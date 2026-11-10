"""Background jobs run by the worker (RQ) or inline in dev."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .config import settings
from .db import session_scope
from .models import (
    Invoice,
    InvoiceStatus,
    Merchant,
    Notification,
    ReconciliationRun,
    WebhookEvent,
    utcnow,
)
from .money import fmt
from .providers import ProviderError, get_provider
from .services import matching, notify
from .services.invoicing import expire_due_invoices

log = logging.getLogger("landed.jobs")


from .models import aware as _aware  # noqa: E402


# ---------- webhook processing (brief 8.2, worker steps 3-7) ----------

def process_webhook_event(event_id: int) -> str:
    with session_scope() as db:
        ev = db.get(WebhookEvent, event_id)
        if ev is None or ev.status not in ("queued", "failed"):
            return "skip"
        merchant = db.get(Merchant, ev.merchant_id)
        if not ev.reference:
            ev.status, ev.detail, ev.processed_at = "ignored", "No transaction reference", utcnow()
            return ev.status
        try:
            provider = get_provider(merchant, db)
            tx = provider.verify_transaction(ev.reference)  # never trust the webhook body
        except ProviderError as e:
            ev.status, ev.detail = "failed", f"Verify failed: {e}"
            db.commit()
            raise  # let RQ retry; reconciliation also catches it later
        outcome = matching.process_verified_payment(db, merchant, tx, provider.name, source="webhook")
        ev.status = {"matched": "processed", "unmatched": "processed", "duplicate": "duplicate"}.get(outcome.result, "ignored")
        ev.detail = outcome.detail or outcome.result
        ev.processed_at = utcnow()
        return outcome.result


# ---------- notifications ----------

def send_notification(notification_id: int) -> str:
    from .whatsapp import client
    from .whatsapp.templates import render

    with session_scope() as db:
        n = db.get(Notification, notification_id)
        if n is None or n.status == "sent":
            return "skip"
        try:
            if n.template == "text":
                if client.is_configured() and not notify.within_service_window(db, n.recipient_phone):
                    n.status, n.error = "skipped", "Outside 24h window; free-form text not allowed"
                    return n.status
                n.wa_message_id = client.send_text(n.recipient_phone, n.params.get("body", ""))
            else:
                n.wa_message_id = client.send_template(n.recipient_phone, n.template, n.params.get("args", []))
            n.status, n.sent_at, n.error = "sent", utcnow(), None
            if n.wa_message_id == "dry-run":
                n.error = render(n.template, n.params if n.template == "text" else n.params.get("args", []))[:500]
                n.status = "logged"
        except Exception as e:  # noqa: BLE001
            n.status, n.error = "failed", str(e)[:500]
        return n.status


# ---------- reconciliation (catches missed webhooks) ----------

def reconcile_merchant(merchant_id: int, hours: int | None = None) -> dict:
    hours = hours or max(settings.reconcile_every_hours * 2, 6)  # overlapping windows
    end = utcnow()
    start = end - timedelta(hours=hours)
    with session_scope() as db:
        m = db.get(Merchant, merchant_id)
        run = ReconciliationRun(merchant_id=merchant_id, window_from=start, window_to=end, checked=0, recovered=0)
        db.add(run)
        try:
            provider = get_provider(m, db)
            txs = provider.list_transactions(start, end)
        except ProviderError as e:
            run.error = str(e)
            return {"error": str(e)}
        run.checked = len(txs)
        for t in txs:
            if not t.is_paid:
                continue
            try:
                verified = provider.verify_transaction(t.reference)
            except ProviderError as e:
                log.warning("reconcile verify failed merchant=%s ref=%s: %s", merchant_id, t.reference, e)
                continue
            out = matching.process_verified_payment(db, m, verified, provider.name, source="reconciliation")
            if out.result in ("matched", "unmatched"):
                run.recovered += 1
        return {"checked": run.checked, "recovered": run.recovered}


def _real_merchants(db):
    from sqlalchemy import or_

    cond = Merchant.provider_keys.is_not(None)
    if settings.allow_mock_provider:
        cond = or_(cond, Merchant.provider == "mock")
    return db.scalars(select(Merchant.id).where(cond)).all()


def reconcile_all() -> None:
    from .tasks import enqueue

    with session_scope() as db:
        ids = _real_merchants(db)
    for mid in ids:
        enqueue("app.jobs.reconcile_merchant", mid)


# ---------- expiry and reminders ----------

def expire_invoices() -> int:
    with session_scope() as db:
        return expire_due_invoices(db)


def send_reminders(now: datetime | None = None) -> int:
    """Remind customers on the merchant's schedule (days after invoice creation)."""
    now = now or utcnow()
    sent = 0
    with session_scope() as db:
        for m in db.scalars(select(Merchant)):
            days = sorted({int(d) for d in (m.reminder_days or "").split(",") if d.strip().isdigit()})
            if not days:
                continue
            invoices = db.scalars(select(Invoice).where(Invoice.merchant_id == m.id,
                                                        Invoice.status.in_([InvoiceStatus.unpaid, InvoiceStatus.part_paid]))).all()
            for inv in invoices:
                if not (inv.customer and inv.customer.phone) or inv.reminders_sent >= len(days):
                    continue
                age_days = (now - _aware(inv.created_at)).days
                if age_days < days[inv.reminders_sent]:
                    continue
                va = inv.virtual_account
                if va and not va.active and va.type.value == "dynamic":
                    continue  # account closed; the merchant should reissue
                notify.queue(db, m.id, inv.customer.phone, "payment_reminder",
                             [notify.customer_name(inv), m.business_name, fmt(inv.balance), inv.ref,
                              va.account_number if va else "—", va.bank_name if va else "—", notify.receipt_url(inv)],
                             invoice_id=inv.id)
                inv.reminders_sent += 1
                sent += 1
    return sent
