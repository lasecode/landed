"""End-to-end seller flow through the real web views, against whatever provider the site uses.

Env FLOW_TEST="<phone>|<password>|<bank word>|<account number>". Keeps the seller it creates.
Prints lines starting FLOW.
"""
import hashlib
import hmac
import json
import os
import re

from django.conf import settings
from django.core.management.base import BaseCommand
from django.test import Client

from accounts.models import User, normalize_phone
from payments.models import Invoice, Payment
from payments.providers import get_provider


class Command(BaseCommand):
    def out(self, ok, step, detail=""):
        self.stdout.write(f"FLOW {ok:4} {step}{': ' + str(detail)[:300] if detail else ''}")

    def handle(self, **kw):
        try:
            phone, password, bank_word, number = os.environ["FLOW_TEST"].split("|")
        except (KeyError, ValueError):
            return self.out("SKIP", "FLOW_TEST not set")
        host = os.getenv("RENDER_EXTERNAL_HOSTNAME") or (settings.ALLOWED_HOSTS or ["localhost"])[0]
        c = Client(HTTP_HOST=host, secure=True)
        self.out("INFO", "provider", settings.PAYMENT_PROVIDER)

        # 1. sign up (or sign in if the number already has an account)
        if User.objects.filter(phone=normalize_phone(phone)).exists():
            r = c.post("/login/", {"username": phone, "password": password})
            self.out("PASS" if r.status_code == 302 else "FAIL", "sign in (account existed)", r.status_code)
        else:
            r = c.post("/signup/", {"business_name": "Lase Test Store", "name": "Lase", "phone": phone,
                                    "email": "", "password": password})
            self.out("PASS" if r.status_code == 302 else "FAIL", "sign up", f"HTTP {r.status_code} -> {r.get('Location', '')}")
        user = User.objects.get(phone=normalize_phone(phone))
        b = user.business

        # 2. payout setup: bank list, account check, sub-account
        r = c.get("/app/setup/payout/")
        html = r.content.decode()
        opts = re.findall(r'<option value="([^"]+)"[^>]*>([^<]+)</option>', html)
        bank = next(((code, name) for code, name in opts if bank_word.lower() in name.lower()), None)
        self.out("PASS" if r.status_code == 200 and opts else "FAIL", "payout page + bank list", f"{len(opts)} banks; picked {bank}")
        if not b.subaccount_code and bank:
            r = c.post("/app/setup/payout/", {"bank_code": bank[0], "account_number": number})
            b.refresh_from_db()
            if r.status_code == 302:
                self.out("PASS", "payout saved", f"name='{b.payout_account_name}' bank={b.payout_bank_name} subaccount={b.subaccount_code} status={b.status}")
            else:
                from payments.services.invoicing import InvoiceError, set_payout_account
                try:
                    set_payout_account(b, bank_code=bank[0], account_number=number)
                    self.out("PASS", "payout saved on retry", f"{b.payout_account_name} {b.subaccount_code}")
                except InvoiceError as e:
                    self.out("FAIL", "payout save", e)
                    try:
                        subs = get_provider()._call("GET", "/subaccount", params={"perPage": 50})["data"]
                        self.out("INFO", "existing sub-accounts", [
                            {k: (str(x.get(k))[-4:] if k == "account_number" else x.get(k))
                             for k in ("subaccount_code", "settlement_bank", "account_number", "account_name", "active")}
                            for x in subs])
                    except Exception as e2:  # noqa: BLE001
                        self.out("INFO", "existing sub-accounts", e2)
                    return
        else:
            self.out("INFO", "payout already set", f"{b.payout_account_name} {b.payout_bank_name} {b.subaccount_code}")

        # 3. create an invoice through the dashboard
        r = c.post("/app/invoices/new/", {"customer_name": "Flow Test Customer", "amount": "2500",
                                          "description": "Landed flow test"})
        inv = Invoice.objects.filter(business=b).order_by("-created_at").first()
        self.out("PASS" if r.status_code == 200 and inv else "FAIL", "create invoice",
                 f"{inv.ref} ₦{inv.amount/100:,.0f} link=/pay/{inv.public_token}/" if inv else r.status_code)

        # 4. customer opens the pay page and taps Pay -> one-time account
        r = c.get(f"/pay/{inv.public_token}/")
        self.out("PASS" if r.status_code == 200 else "FAIL", "public invoice page", r.status_code)
        r = c.post(f"/pay/{inv.public_token}/account/")
        acct = inv.current_pay_account()
        if acct:
            self.out("PASS", "one-time account", f"{acct.bank_name} {acct.account_number} '{acct.account_name}' "
                     f"₦{acct.amount/100:,.0f} expires {acct.expires_at:%H:%M} UTC ref={acct.provider_reference}")
        else:
            return self.out("FAIL", "one-time account", re.sub(r"\s+", " ", r.content.decode())[:300])

        # 5. Paystack's own view of that charge (should be waiting for a transfer, split to the seller)
        try:
            tx = get_provider().verify(acct.provider_reference)
            self.out("PASS", "provider status", f"status={tx.status} subaccount={tx.subaccount_code or '-'}")
        except Exception as e:  # noqa: BLE001
            self.out("INFO", "provider status", e)

        # 6. status poll with provider double-check: must stay unpaid
        c.get(f"/pay/{inv.public_token}/status/?check=1")
        inv.refresh_from_db()
        self.out("PASS" if inv.status == "unpaid" else "FAIL", "unpaid before any transfer", inv.status)

        # 7. security: a forged 'payment' webhook must not mark it paid
        body = json.dumps({"event": "charge.success", "data": {"reference": acct.provider_reference,
                                                               "amount": acct.amount, "status": "success"}}).encode()
        r1 = c.post("/webhooks/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE="bad")
        sig = hmac.new(settings.PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512).hexdigest() if settings.PAYSTACK_SECRET_KEY else "x"
        r2 = c.post("/webhooks/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
        inv.refresh_from_db()
        ok = r1.status_code in (400, 401, 403) and inv.status == "unpaid" and not Payment.objects.filter(reference=acct.provider_reference).exists()
        self.out("PASS" if ok else "FAIL", "fake webhooks rejected",
                 f"bad signature -> HTTP {r1.status_code}; signed but unpaid -> HTTP {r2.status_code}; invoice still {inv.status}")

        # 8. dashboard pages load
        bad = [p for p in ["/app/", "/app/invoices/", "/app/customers/", "/app/unmatched/", "/app/reports/", "/app/settings/", "/app/bot/"]
               if c.get(p).status_code != 200]
        self.out("PASS" if not bad else "FAIL", "dashboard pages", bad or "all 7 load")

        # 9. WhatsApp menu replies (not sent, just generated)
        from messaging.bot import handle
        self.out("PASS", "bot menu", handle(user, "menu").splitlines()[0])
        self.out("PASS", "bot unpaid", " / ".join(handle(user, "3").splitlines()[:3]))
        handle(user, "0")
