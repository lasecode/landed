"""PaymentProvider interface (brief section 4.3).

The matching engine only ever talks to this interface, never to a specific provider.
All amounts are integer kobo. All datetimes are timezone-aware UTC.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


class ProviderError(Exception):
    """A provider call failed. Message must never contain secrets."""


@dataclass
class AccountResult:
    account_number: str
    bank_name: str
    account_name: str | None = None
    provider_ref: str | None = None
    expires_at: datetime | None = None


@dataclass
class TransactionResult:
    reference: str
    amount: int                      # kobo actually paid
    status: str                      # "paid" | "pending" | "failed"
    paid_at: datetime | None
    account_number: str | None = None  # destination virtual account
    product_reference: str | None = None  # our invoice / customer reference echoed back
    payer_name: str | None = None
    payer_bank: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def is_paid(self) -> bool:
        return self.status == "paid"


@dataclass
class WebhookInfo:
    event_type: str
    reference: str | None
    is_payment: bool


@dataclass
class AccountRequest:
    """Provider-neutral description of what we want an account for."""
    reference: str                    # our unique reference (e.g. "m12-inv-108-ab12")
    customer_name: str
    customer_email: str
    description: str = ""
    amount: int | None = None         # kobo; dynamic accounts only
    expires_at: datetime | None = None
    bvn: str | None = None            # static accounts: CBN requires BVN or NIN
    nin: str | None = None


class PaymentProvider(abc.ABC):
    name: str = "base"

    def __init__(self, keys: Mapping[str, str]):
        self.keys = dict(keys)

    def __repr__(self) -> str:  # never print keys
        return f"<{type(self).__name__}>"

    @abc.abstractmethod
    def create_static_account(self, req: AccountRequest) -> AccountResult: ...

    @abc.abstractmethod
    def create_dynamic_account(self, req: AccountRequest) -> AccountResult: ...

    @abc.abstractmethod
    def verify_transaction(self, reference: str) -> TransactionResult: ...

    @abc.abstractmethod
    def list_transactions(self, start: datetime, end: datetime) -> list[TransactionResult]: ...

    @abc.abstractmethod
    def verify_webhook_signature(self, raw_body: bytes, headers: Mapping[str, str]) -> bool: ...

    @abc.abstractmethod
    def parse_webhook(self, payload: dict) -> WebhookInfo: ...

    def cancel_dynamic_account(self, provider_ref: str) -> None:
        """Best effort; providers that can't cancel just let the account expire."""
        return None

    def test_connection(self) -> None:
        """Raise ProviderError if keys don't work."""
        return None
