"""Background jobs: webhook processing, reconciliation, expiry and reminders."""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from messaging import notify

from .models import Invoice, ReconciliationRun, WebhookEvent
from .money import naira
from .providers import ProviderError, get_provider
from .services import matching
from .services.invoicing import deactivate_expired_accounts

log = logging.getLogger("landed.tasks")


@shared_task(bind=True, max_retries=5, default_retry_delay=30)
def process_webhook(self, event_id: int) -> str:
    ev = WebhookEvent.objects.filter(pk=event_id).first()
    if not ev or ev.status not in ("queued", "failed"):
        return "skip"
    provider = get_provider()
    try:
        tx = provider.verify(ev.reference)  # never trust the webhook body
    except ProviderError as e:
        ev.status, ev.detail = "failed", f"Verify failed: {e}"
        ev.save(update_fields=["status", "detail"])
        if self.request.is_eager:
            return "failed"
        raise self.retry(exc=e)
    out = matching.process(tx, provider.name, source="webhook")
    ev.status = {"matched": "processed", "unmatched": "processed", "duplicate": "duplicate"}.get(out.result, "ignored")
    ev.detail, ev.processed_at = out.detail or out.result, timezone.now()
    ev.save(update_fields=["status", "detail", "processed_at"])
    return out.result


def check_reference(reference: str, source: str = "page_check") -> str:
    """Ask the provider directly about one reference (used by the pay page when a customer says 'I've paid')."""
    provider = get_provider()
    try:
        tx = provider.verify(reference)
    except ProviderError:
        return "error"
    return matching.process(tx, provider.name, source=source).result


@shared_task
def reconcile(hours: int = 8) -> dict:
    end = timezone.now()
    run = ReconciliationRun.objects.create(window_from=end - timedelta(hours=hours), window_to=end)
    provider = get_provider()
    try:
        txs = provider.list_transactions(run.window_from, end)
    except ProviderError as e:
        run.error = str(e)
        run.save(update_fields=["error"])
        return {"error": str(e)}
    for t in txs:
        try:
            verified = provider.verify(t.reference)
        except ProviderError as e:
            log.warning("reconcile verify failed %s: %s", t.reference, e)
            continue
        if matching.process(verified, provider.name, source="reconciliation").result in ("matched", "unmatched"):
            run.recovered += 1
    run.checked = len(txs)
    run.save(update_fields=["checked", "recovered"])
    return {"checked": run.checked, "recovered": run.recovered}


@shared_task
def expire_invoices() -> int:
    return deactivate_expired_accounts()


@shared_task
def send_reminders(now=None) -> int:
    """Remind customers on each seller's schedule (days after the invoice was created)."""
    now = now or timezone.now()
    sent = 0
    qs = (Invoice.objects.filter(status__in=Invoice.OPEN, customer__phone__gt="")
          .select_related("business", "customer"))
    for inv in qs:
        days = sorted({int(d) for d in inv.business.reminder_days.split(",") if d.strip().isdigit()})
        if inv.reminders_sent >= len(days) or (now - inv.created_at).days < days[inv.reminders_sent]:
            continue
        notify.queue(inv.business, inv.customer.phone, "landed_reminder",
                     [inv.customer.first_name, inv.business.name, naira(inv.balance), inv.ref, notify.pay_url(inv)], inv)
        Invoice.objects.filter(pk=inv.pk).update(reminders_sent=inv.reminders_sent + 1)
        sent += 1
    return sent


@shared_task
def sync_payouts() -> int:
    from .services.payouts import sync_all

    return sync_all()
