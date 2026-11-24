"""Run the real seller payout setup (resolve name -> create sub-account) against Paystack TEST mode.

Accounts come from env PAYSTACK_TEST_ACCOUNTS, e.g. "<number>:opay,palmpay;<number>:opay".
Nothing is kept in Landed's database (rolled back); the Paystack test sub-accounts stay.
Prints lines starting SUBACCOUNT_TEST.
"""
import os
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.test import override_settings

from accounts.models import Business
from payments.providers.base import ProviderError
from payments.providers.paystack import PaystackProvider
from payments.services.invoicing import InvoiceError, set_payout_account

ALIASES = {"opay": ["opay", "paycom"], "palmpay": ["palmpay"], "moniepoint": ["moniepoint"]}


class Rollback(Exception):
    pass


class Command(BaseCommand):
    def out(self, msg):
        self.stdout.write(f"SUBACCOUNT_TEST {msg}")

    def handle(self, **kw):
        if not settings.PAYSTACK_SECRET_KEY.startswith("sk_test_"):
            return self.out("SKIP: needs a sk_test_ key")
        p = PaystackProvider()
        banks = p.list_banks()
        for part in filter(None, os.getenv("PAYSTACK_TEST_ACCOUNTS", "").split(";")):
            number, _, names = part.partition(":")
            number = number.strip()
            for alias in filter(None, (n.strip().lower() for n in names.split(","))):
                hits = [b for b in banks if any(k in b.name.lower() for k in ALIASES.get(alias, [alias]))]
                if not hits:
                    self.out(f"{alias} ..{number[-4:]}: FAIL no bank matching '{alias}'")
                    continue
                bank = hits[0]
                extra = f" (also: {', '.join(f'{b.name}={b.code}' for b in hits[1:4])})" if len(hits) > 1 else ""
                try:
                    with transaction.atomic(), override_settings(PAYMENT_PROVIDER="paystack"):
                        b = Business.objects.create(name=f"Landed Test {alias.title()} {number[-4:]}", phone="2347000000099")
                        set_payout_account(b, bank_code=bank.code, account_number=number)
                        msg = (f"{bank.name} [{bank.code}] ..{number[-4:]}: PASS name='{b.payout_account_name}' "
                               f"subaccount={b.subaccount_code}{extra}")
                        try:
                            r = p.create_transfer_account(
                                amount=250000, email=f"split{number[-4:]}{bank.code}@example.com",
                                reference=f"landedsplit{number[-4:]}{bank.code}{int(datetime.now().timestamp())}",
                                subaccount_code=b.subaccount_code,
                                expires_at=datetime.now(timezone.utc) + timedelta(hours=1), metadata={"test": True})
                            msg += f" | split one-time account OK: {r.bank_name} {r.account_number} '{r.account_name}'"
                        except ProviderError as e:
                            msg += f" | split one-time account FAIL: {e}"
                        self.out(msg)
                        raise Rollback
                except Rollback:
                    pass
                except InvoiceError as e:
                    self.out(f"{bank.name} [{bank.code}] ..{number[-4:]}: FAIL {e}{extra}")
                except Exception as e:  # noqa: BLE001
                    self.out(f"{bank.name} [{bank.code}] ..{number[-4:]}: ERROR {type(e).__name__}: {str(e)[:150]}")
