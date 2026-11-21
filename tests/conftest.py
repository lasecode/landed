import os

os.environ.setdefault("PAYMENT_PROVIDER", "mock")
os.environ.pop("REDIS_URL", None)

import pytest  # noqa: E402
from django.test import Client  # noqa: E402

from accounts.models import Business, User  # noqa: E402
from messaging.models import Notification  # noqa: E402
from payments.providers.mock import sign, simulate_transfer, webhook_body  # noqa: E402
from payments.services.invoicing import set_payout_account  # noqa: E402


@pytest.fixture(autouse=True)
def _run_on_commit_now(monkeypatch):
    """Tests run inside a transaction that never commits; run after-commit jobs straight away."""
    from django.db import transaction

    monkeypatch.setattr(transaction, "on_commit", lambda fn, using=None, robust=False: fn())


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.PAYMENT_PROVIDER = "mock"
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.WHATSAPP_DRY_RUN = True
    settings.PUBLIC_URL = "http://testserver"
    settings.WHITENOISE_AUTOREFRESH = True
    settings.STORAGES = {**settings.STORAGES, "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


def make_seller(phone="08030000001", name="Adaeze Okafor", business="Adaeze Foods", payout=True):
    b = Business.objects.create(name=business, phone=phone, alert_phone=phone)
    u = User.objects.create_user(phone, "password123", name=name, business=b, role=User.Role.OWNER)
    if payout:
        set_payout_account(b, bank_code="058", account_number="0123456789")
    return b, u


@pytest.fixture
def seller(db):
    return make_seller()


@pytest.fixture
def owner_client(seller):
    c = Client()
    c.force_login(seller[1])
    return c


def pay(account_number, naira, payer="TUNDE BELLO"):
    """Simulate a transfer at the provider, then deliver Paystack's signed webhook to our endpoint."""
    ref = simulate_transfer(account_number, naira * 100, payer)
    deliver(ref)
    return ref


def deliver(ref, signature=None):
    raw = webhook_body(ref)
    return Client().post("/webhooks/paystack/", data=raw, content_type="application/json",
                         HTTP_X_PAYSTACK_SIGNATURE=signature if signature is not None else sign(raw))


def sent():
    return list(Notification.objects.order_by("id").values_list("template", "to", "params"))
