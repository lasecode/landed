"""Check that the Paystack key works and which features this account has.

Prints one PASS/FAIL/INFO line per check. Never prints the key. Test mode only creates test data.
Run: python manage.py paystack_check
"""
import time
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.management.base import BaseCommand

from payments.providers.base import ProviderError
from payments.providers.paystack import PaystackProvider


class Command(BaseCommand):
    help = "Check the Paystack key and features"

    def line(self, status, name, detail=""):
        self.stdout.write(f"PAYSTACK_CHECK {status:4} {name}{': ' + detail if detail else ''}")

    def handle(self, **kw):
        key = settings.PAYSTACK_SECRET_KEY
        if not key:
            return self.line("FAIL", "key", "PAYSTACK_SECRET_KEY is empty")
        mode = "test" if key.startswith("sk_test_") else "live" if key.startswith("sk_live_") else "unknown"
        self.line("PASS" if mode == "test" else "WARN", "key type", f"{mode} secret key")
        if mode == "live":
            return self.line("INFO", "stopped", "live key: not creating anything")
        p = PaystackProvider()

        def run(name, fn):
            try:
                out = fn()
                self.line("PASS", name, str(out)[:200])
                return out
            except ProviderError as e:
                self.line("FAIL", name, str(e)[:200])
            except Exception as e:  # noqa: BLE001
                self.line("FAIL", name, f"{type(e).__name__}: {str(e)[:150]}")

        run("balance (key accepted)", lambda: [f"{b['currency']} {b['balance']/100:,.2f}" for b in p._call("GET", "/balance")["data"]])
        banks = run("bank list", lambda: f"{len(p.list_banks())} banks")
        run("DVA providers", lambda: [x.get("provider_slug") for x in p._call("GET", "/dedicated_account/available_providers")["data"]])
        run("resolve account (058 / 0000000000)", lambda: p.resolve_account("0000000000", "058") or "no name")

        sub = run("create subaccount", lambda: p.create_subaccount(
            business_name="Landed Check Seller", bank_code="058", account_number="0000000000",
            fee_percent=settings.LANDED_FEE_PERCENT, contact_phone="2347000000099", contact_email=""))
        stamp = int(time.time())
        email = f"check{stamp}@example.com"

        def dva():
            payload = {"customer": p._customer("Check Customer", "+2347000000098", email), "preferred_bank": settings.PAYSTACK_DVA_BANK}
            if sub:
                payload["subaccount"] = sub
            d = p._call("POST", "/dedicated_account", json=payload)["data"]
            return f"{(d.get('bank') or {}).get('name')} {d.get('account_number')} '{d.get('account_name')}'"
        run(f"dedicated account ({settings.PAYSTACK_DVA_BANK})", dva)

        def pwt():
            payload = {"email": email, "amount": "500000", "reference": f"landedcheck{stamp}",
                       "bank_transfer": {"account_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}}
            if sub:
                payload["subaccount"] = sub
                payload["bearer"] = settings.PAYSTACK_FEE_BEARER
            d = p._call("POST", "/charge", json=payload)["data"]
            return f"status={d.get('status')} {(d.get('bank') or {}).get('name')} {d.get('account_number')} '{d.get('account_name')}' {d.get('display_text') or ''}"
        run("pay with transfer (one-time account)", pwt)
        run("transaction list", lambda: f"{len(p.list_transactions(datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc)))} in last day")
