"""Run on every start (idempotent):
- ADMIN_PHONE + ADMIN_PASSWORD -> makes sure that Landed admin login exists (password set only on creation)
- DEMO_PASSWORD (test mode only) -> makes sure the demo seller exists
"""
import os

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from accounts.models import User, normalize_phone


class Command(BaseCommand):
    help = "Create the admin and demo logins from environment variables if they don't exist"

    def handle(self, **kw):
        phone, pw = os.getenv("ADMIN_PHONE", ""), os.getenv("ADMIN_PASSWORD", "")
        if phone and pw and not User.objects.filter(phone=normalize_phone(phone)).exists():
            User.objects.create_superuser(phone, pw, name="Landed admin")
            self.stdout.write("Admin login created")
        demo_pw = os.getenv("DEMO_PASSWORD", "")
        if demo_pw and settings.PAYMENT_PROVIDER == "mock" and not User.objects.filter(phone="2347000000001").exists():
            call_command("seed_demo", phone="07000000001", password=demo_pw)
        if os.getenv("PAYSTACK_SELFTEST") == "1" and settings.PAYSTACK_SECRET_KEY:
            try:
                call_command("paystack_check")
            except Exception as e:  # never block startup
                self.stdout.write(f"PAYSTACK_CHECK FAIL crashed: {type(e).__name__}")
        if os.getenv("PAYSTACK_TEST_ACCOUNTS") and settings.PAYSTACK_SECRET_KEY:
            try:
                call_command("paystack_subaccount_test")
            except Exception as e:  # never block startup
                self.stdout.write(f"SUBACCOUNT_TEST crashed: {type(e).__name__}")
        if os.getenv("PURGE_BEFORE"):
            try:
                call_command("purge_test_data")
            except Exception as e:  # never block startup
                self.stdout.write(f"PURGE crashed: {type(e).__name__}: {e}")
        if os.getenv("FLOW_TEST"):
            try:
                call_command("flow_test")
            except Exception as e:  # never block startup
                self.stdout.write(f"FLOW crashed: {type(e).__name__}: {e}")
        if os.getenv("RESET_FOR_LIVE") == "1":
            try:
                call_command("reset_for_live")
            except Exception as e:  # never block startup
                self.stdout.write(f"RESET_FOR_LIVE crashed: {type(e).__name__}: {e}")
