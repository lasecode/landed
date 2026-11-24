"""Delete fake (mock-mode) data created BEFORE a cutoff time. Admin logins are kept.

Safe to leave in place: anything created after PURGE_BEFORE is never touched.
Env: PURGE_BEFORE=2026-11-24T18:40:00Z
"""
import os
from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Business, User
from messaging.models import BotSession, Notification
from payments.models import LedgerEntry, MockTransaction, Payment, ReconciliationRun, WebhookEvent


class Command(BaseCommand):
    def handle(self, **kw):
        raw = os.getenv("PURGE_BEFORE", "")
        try:
            cutoff = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return self.stdout.write(f"PURGE skipped: bad PURGE_BEFORE '{raw}'")
        if cutoff.tzinfo is None:
            return self.stdout.write("PURGE skipped: PURGE_BEFORE needs a timezone (end with Z)")
        with transaction.atomic():
            counts = {
                "businesses": Business.objects.filter(created_at__lt=cutoff).delete()[1].get("accounts.Business", 0),
                "loose payments": Payment.objects.filter(business__isnull=True, created_at__lt=cutoff).delete()[0],
                "loose ledger": LedgerEntry.objects.filter(business__isnull=True, created_at__lt=cutoff).delete()[0],
                "loose notifications": Notification.objects.filter(business__isnull=True, created_at__lt=cutoff).delete()[0],
                "logins without a business": User.objects.filter(is_superuser=False, business__isnull=True,
                                                                 date_joined__lt=cutoff).delete()[0],
                "mock transfers": MockTransaction.objects.all().delete()[0],
                "webhook events": WebhookEvent.objects.filter(received_at__lt=cutoff).delete()[0],
                "reconcile runs": ReconciliationRun.objects.filter(started_at__lt=cutoff).delete()[0],
                "bot sessions": BotSession.objects.filter(updated_at__lt=cutoff).delete()[0],
            }
        self.stdout.write("PURGE done: " + ", ".join(f"{k}={v}" for k, v in counts.items())
                          + f" | left: businesses={Business.objects.count()} logins={User.objects.count()}")
