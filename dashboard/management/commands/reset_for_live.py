"""Run once when switching from a Paystack TEST key to a LIVE key.

Test-mode sub-accounts and one-time account numbers don't exist in live mode, so:
- clears every seller's payout sub-account (they redo Payout setup once, in live mode)
- switches off every active one-time account (customers just tap Pay again)
Invoices, customers and logins are kept. Only runs with a live key.
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Business
from payments.models import VirtualAccount


class Command(BaseCommand):
    def handle(self, **kw):
        if not settings.PAYSTACK_SECRET_KEY.startswith("sk_live_"):
            return self.stdout.write("RESET_FOR_LIVE skipped: the key is not a live key")
        with transaction.atomic():
            sellers = Business.objects.exclude(subaccount_code="").update(
                subaccount_code="", payout_bank_code="", payout_bank_name="", payout_account_number="",
                payout_account_name="", status=Business.Status.SETUP)
            accts = VirtualAccount.objects.filter(active=True).update(active=False)
        self.stdout.write(f"RESET_FOR_LIVE done: {sellers} sellers need Payout setup again, {accts} test accounts switched off")
