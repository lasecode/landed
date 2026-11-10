"""Mock provider: behaves like a real provider, backed by the mock_transactions table.

Lets the whole system (accounts, signed webhooks, verification, reconciliation) run before a merchant
has real Monnify keys. Disabled when ALLOW_MOCK_PROVIDER=false (always in production).
"""
from __future__ import annotations

import hashlib
import hmac
import random
from datetime import datetime, timezone
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from .base import AccountRequest, AccountResult, PaymentProvider, ProviderError, TransactionResult, WebhookInfo

BANKS = ["Wema Bank", "Moniepoint MFB", "Sterling Bank"]
DEFAULT_SECRET = "mock-secret"


def sign(secret: str, raw_body: bytes) -> str:
    return hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest()


class MockProvider(PaymentProvider):
    name = "mock"

    def __init__(self, keys: Mapping[str, str], db: Session | None = None, merchant_id: int | None = None):
        super().__init__(keys)
        self.db = db
        self.merchant_id = merchant_id
        self.secret = self.keys.get("secret_key") or DEFAULT_SECRET

    def _acct(self) -> str:
        return "9" + "".join(random.choice("0123456789") for _ in range(9))

    def create_dynamic_account(self, req: AccountRequest) -> AccountResult:
        return AccountResult(self._acct(), random.choice(BANKS), req.description[:40] or req.customer_name, req.reference, req.expires_at)

    def create_static_account(self, req: AccountRequest) -> AccountResult:
        return AccountResult(self._acct(), random.choice(BANKS), req.customer_name, req.reference, None)

    def verify_transaction(self, reference: str) -> TransactionResult:
        from ..models import MockTransaction

        if self.db is None:
            raise ProviderError("Mock provider needs a database session")
        t = self.db.get(MockTransaction, reference)
        if t is None or (self.merchant_id and t.merchant_id != self.merchant_id):
            return TransactionResult(reference=reference, amount=0, status="failed", paid_at=None)
        return self._tx(t)

    @staticmethod
    def _tx(t) -> TransactionResult:
        paid_at = t.paid_at if t.paid_at.tzinfo else t.paid_at.replace(tzinfo=timezone.utc)
        return TransactionResult(
            reference=t.reference, amount=t.amount, status="paid", paid_at=paid_at,
            account_number=t.account_number, payer_name=t.payer_name, payer_bank=t.payer_bank,
        )

    def list_transactions(self, start: datetime, end: datetime) -> list[TransactionResult]:
        from ..models import MockTransaction

        if self.db is None:
            return []
        q = select(MockTransaction).where(MockTransaction.merchant_id == self.merchant_id)
        out = []
        for t in self.db.scalars(q):
            pa = t.paid_at if t.paid_at.tzinfo else t.paid_at.replace(tzinfo=timezone.utc)
            if start <= pa <= end:
                out.append(self._tx(t))
        return out

    def verify_webhook_signature(self, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        sig = headers.get("x-mock-signature") or headers.get("X-Mock-Signature")
        return bool(sig) and hmac.compare_digest(sign(self.secret, raw_body), sig)

    def parse_webhook(self, payload: dict) -> WebhookInfo:
        return WebhookInfo(event_type=payload.get("event", ""), reference=payload.get("reference"), is_payment=payload.get("event") == "payment.received")
