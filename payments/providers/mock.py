"""Test-mode provider: behaves like Paystack, backed by the MockTransaction table.

Lets the whole flow (seller payout setup, account numbers, signed webhooks, verification,
reconciliation) run before Landed's Paystack account is approved. Refused in production.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import random
import secrets
from datetime import datetime
from typing import Mapping

from django.utils import timezone

from .base import AccountResult, Bank, Provider, ProviderError, Transaction, WebhookInfo

SECRET = "mock-webhook-secret"
BANKS = [Bank("058", "Guaranty Trust Bank"), Bank("044", "Access Bank"), Bank("057", "Zenith Bank"),
         Bank("011", "First Bank of Nigeria"), Bank("50515", "Moniepoint MFB"), Bank("999992", "OPay"),
         Bank("50211", "Kuda Bank"), Bank("035", "Wema Bank"), Bank("033", "United Bank For Africa")]


def sign(raw: bytes) -> str:
    return hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()


def webhook_body(reference: str) -> bytes:
    return json.dumps({"event": "charge.success", "data": {"reference": reference}}).encode()


class MockProvider(Provider):
    name = "mock"

    def _num(self) -> str:
        return "99" + "".join(random.choice("0123456789") for _ in range(8))

    def list_banks(self):
        return BANKS

    def resolve_account(self, account_number, bank_code):
        if len(account_number) != 10 or not account_number.isdigit():
            raise ProviderError("Could not resolve account name. Check the account number.")
        if account_number.startswith("000"):
            raise ProviderError("Could not resolve account name. Check the account number.")
        return "TEST ACCOUNT " + account_number[-4:]

    def create_subaccount(self, **kw):
        return "ACCT_mock" + secrets.token_hex(5)

    def update_subaccount(self, code, **kw):
        return None

    def create_dedicated_account(self, *, customer_name, phone, email, subaccount_code, existing_customer_code=""):
        return AccountResult(self._num(), "Test Bank", f"LANDED/{customer_name.upper()[:30]}",
                             reference="dva_" + secrets.token_hex(4),
                             customer_code=existing_customer_code or "CUS_mock" + secrets.token_hex(4))

    def create_transfer_account(self, *, amount, email, reference, subaccount_code, expires_at, metadata):
        return AccountResult(self._num(), "Test Bank", "LANDED CHECKOUT", reference=reference, expires_at=expires_at)

    def _tx(self, t) -> Transaction:
        from ..models import VirtualAccount

        va = VirtualAccount.objects.filter(account_number=t.account_number).order_by("-created_at").first()
        return Transaction(
            reference=t.reference, status="success", amount=t.amount, fees=min(t.amount * 15 // 1000, 200000),
            paid_at=t.paid_at, channel="dedicated_nuban" if (va and va.kind == "dedicated") else "bank_transfer",
            account_number=t.account_number, subaccount_code=t.subaccount_code,
            payer_name=t.payer_name, payer_bank="Test Bank",
        )

    def verify(self, reference):
        from ..models import MockTransaction

        t = MockTransaction.objects.filter(pk=reference).first()
        if not t:
            return Transaction(reference=reference, status="failed", amount=0)
        return self._tx(t)

    def list_transactions(self, start: datetime, end: datetime):
        from ..models import MockTransaction

        return [self._tx(t) for t in MockTransaction.objects.filter(paid_at__gte=start, paid_at__lte=end)]

    def verify_signature(self, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        sig = headers.get("x-paystack-signature") or headers.get("X-Paystack-Signature") or ""
        return bool(sig) and hmac.compare_digest(sign(raw_body), sig)

    def parse_webhook(self, payload):
        data = payload.get("data") or {}
        return WebhookInfo(payload.get("event", ""), data.get("reference", ""), payload.get("event") == "charge.success")


def simulate_transfer(account_number: str, amount: int, payer_name: str = "TEST PAYER") -> str:
    """Record a pretend transfer at the 'provider' and return its reference."""
    from ..models import MockTransaction, VirtualAccount

    va = VirtualAccount.objects.filter(account_number=account_number).select_related("business").first()
    if va and va.kind == VirtualAccount.Kind.TRANSFER and va.provider_reference:
        ref = va.provider_reference  # Pay with Transfer: the charge reference is the transaction reference
        if MockTransaction.objects.filter(pk=ref).exists():
            raise ProviderError("That one-time account has already been paid")
    else:
        ref = "MOCK_" + timezone.now().strftime("%Y%m%d%H%M%S") + "_" + secrets.token_hex(3)
    MockTransaction.objects.create(
        reference=ref, account_number=account_number, amount=amount, payer_name=payer_name,
        subaccount_code=va.business.subaccount_code if va else "",
    )
    return ref
