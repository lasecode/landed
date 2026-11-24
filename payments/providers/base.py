"""Provider interface. Landed is the platform (main) account; each seller is a sub-account.

Everything the rest of the app needs from a payment provider goes through here, so a second
provider (Monnify, Flutterwave) is one new class, not a rewrite. Amounts are integer kobo.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


class ProviderError(Exception):
    """A provider call failed. The message is safe to show and never contains secrets."""


@dataclass
class Bank:
    code: str
    name: str


@dataclass
class AccountResult:
    account_number: str
    bank_name: str
    account_name: str = ""
    reference: str = ""            # charge reference (one-time) or provider id (permanent)
    expires_at: datetime | None = None
    customer_code: str = ""


@dataclass
class Transaction:
    reference: str
    status: str                    # "success" | "pending" | "failed" | "abandoned"
    amount: int                    # kobo the customer paid
    fees: int = 0
    paid_at: datetime | None = None
    channel: str = ""
    account_number: str = ""       # the account number the customer paid into
    subaccount_code: str = ""
    customer_code: str = ""
    payer_name: str = ""
    payer_bank: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status == "success"


@dataclass
class WebhookInfo:
    event: str
    reference: str
    is_payment: bool


class Provider(abc.ABC):
    name = "base"

    # sellers
    @abc.abstractmethod
    def list_banks(self) -> list[Bank]: ...

    @abc.abstractmethod
    def resolve_account(self, account_number: str, bank_code: str) -> str:
        """Return the account holder's name, or raise ProviderError."""

    @abc.abstractmethod
    def create_subaccount(self, *, business_name: str, bank_code: str, account_number: str,
                          fee_percent: float, contact_phone: str, contact_email: str) -> str:
        """Create the seller's sub-account and return its code."""

    def find_subaccount(self, bank_code: str, bank_name: str, account_number: str) -> tuple[str, str] | None:
        """An existing sub-account for this bank account, as (code, account name). Avoids duplicates."""
        return None

    @abc.abstractmethod
    def update_subaccount(self, code: str, *, bank_code: str | None = None, account_number: str | None = None,
                          fee_percent: float | None = None, business_name: str | None = None) -> None: ...

    # accounts customers pay into
    @abc.abstractmethod
    def create_dedicated_account(self, *, customer_name: str, phone: str, email: str, subaccount_code: str,
                                 existing_customer_code: str = "") -> AccountResult:
        """Permanent account for a regular customer; every payment splits to the seller."""

    @abc.abstractmethod
    def create_transfer_account(self, *, amount: int, email: str, reference: str, subaccount_code: str,
                                expires_at: datetime, metadata: dict) -> AccountResult:
        """One-time account for a single payment (Pay with Transfer), split to the seller."""

    # confirmation
    @abc.abstractmethod
    def verify(self, reference: str) -> Transaction: ...

    @abc.abstractmethod
    def list_transactions(self, start: datetime, end: datetime) -> list[Transaction]: ...

    @abc.abstractmethod
    def verify_signature(self, raw_body: bytes, headers: Mapping[str, str]) -> bool: ...

    @abc.abstractmethod
    def parse_webhook(self, payload: dict) -> WebhookInfo: ...
