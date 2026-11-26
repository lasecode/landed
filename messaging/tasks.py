from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from .models import Notification
from .notify import in_service_window
from .whatsapp import client

log = logging.getLogger("landed.whatsapp")


@shared_task(bind=True, max_retries=4, default_retry_delay=60)
def send_notification(self, notification_id: int) -> str:
    n = Notification.objects.filter(pk=notification_id).first()
    if not n or n.status in ("sent", "delivered", "read", "logged"):
        return "skip"
    if not client.is_live():
        log.info("[whatsapp dry-run] to=%s\n%s", n.to, n.body)
        n.status, n.sent_at = "logged", timezone.now()
        n.save(update_fields=["status", "sent_at"])
        return n.status
    try:
        if n.template == "text":
            if not in_service_window(n.to):
                n.status, n.error = "skipped", "Outside the 24-hour window; free-form text not allowed"
                n.save(update_fields=["status", "error"])
                return n.status
            n.wa_message_id = client.send_text(n.to, n.body)
        elif in_service_window(n.to):
            # they messaged us in the last 24h: a plain message works and needs no approved template
            n.wa_message_id = client.send_text(n.to, n.body)
        else:
            n.wa_message_id = client.send_template(n.to, n.template, n.params)
        n.status, n.sent_at, n.error = "sent", timezone.now(), ""
    except Exception as e:  # noqa: BLE001
        n.status, n.error = "failed", str(e)[:500]
        n.save(update_fields=["status", "error"])
        if self.request.is_eager:
            return n.status
        raise self.retry(exc=e)
    n.save(update_fields=["status", "sent_at", "error", "wa_message_id"])
    return n.status
