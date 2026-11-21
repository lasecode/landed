"""Create a demo seller with realistic data (test mode only):  python manage.py seed_demo --password ..."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Business, User
from payments.providers.mock import simulate_transfer
from payments.services.invoicing import (create_invoice, find_or_create_customer, give_dedicated_account,
                                         pay_account, set_payout_account)
from payments.tasks import process_webhook


class Command(BaseCommand):
    help = "Create the Adaeze Foods demo seller with invoices and payments (test mode only)"

    def add_arguments(self, parser):
        parser.add_argument("--phone", default="07000000001")
        parser.add_argument("--password", required=True)

    def handle(self, phone, password, **kw):
        if settings.PAYMENT_PROVIDER != "mock":
            raise CommandError("Demo data can only be created in test mode (PAYMENT_PROVIDER=mock)")
        if User.objects.filter(phone__endswith=phone[-10:]).exists():
            raise CommandError("That demo login already exists")
        with transaction.atomic():
            b = Business.objects.create(name="Adaeze Foods (Demo)", phone="2347000000001", alert_phone="2347000000001")
            User.objects.create_user(phone, password, name="Adaeze Okafor", business=b, role=User.Role.OWNER)
            set_payout_account(b, bank_code="50515", account_number="8012345678")

            def pay(acct, naira, who):
                from payments.models import WebhookEvent

                ref = simulate_transfer(acct, naira * 100, who)
                ev = WebhookEvent.objects.create(provider="mock", event_type="charge.success", reference=ref,
                                                 signature_valid=True, status="queued")
                process_webhook(ev.id)

            tunde = find_or_create_customer(b, "Tunde Bello")
            inv = create_invoice(b, amount=4_500_000, customer=tunde, description="3 bags of rice", send=False)
            pay(pay_account(inv).account_number, 45000, "TUNDE BELLO")
            kemi = find_or_create_customer(b, "Kemi Adebayo")
            inv = create_invoice(b, amount=3_000_000, customer=kemi, description="Hair, 2 bundles", send=False)
            pay(pay_account(inv, 2_000_000).account_number, 20000, "KEMI ADEBAYO")
            ngozi = find_or_create_customer(b, "Mama Ngozi Stores")
            va = give_dedicated_account(ngozi)
            create_invoice(b, amount=5_000_000, customer=ngozi, description="5 cartons noodles", send=False)
            create_invoice(b, amount=4_000_000, customer=ngozi, description="2 kegs groundnut oil", send=False)
            pay(va.account_number, 70000, "NGOZI OKAFOR")
            chinedu = find_or_create_customer(b, "Chinedu Eze")
            inv = create_invoice(b, amount=2_000_000, customer=chinedu, description="Phone screen", send=False)
            pay(pay_account(inv).account_number, 25000, "CHINEDU EZE")
            create_invoice(b, amount=1_500_000, customer=find_or_create_customer(b, "Grace Okon"), description="Food trays x3", send=False)
            pay("9900000777", 12500, "IBRAHIM MUSA")
            from payments.models import LedgerEntry, Payment

            # with real Paystack the split tells us whose payment it is; mirror that for the demo
            p = Payment.objects.get(account_number="9900000777")
            Payment.objects.filter(pk=p.pk).update(business=b)
            LedgerEntry.objects.filter(payment=p).update(business=b)
        self.stdout.write(self.style.SUCCESS(f"Demo seller ready. Sign in with {phone}."))
