"""Provider registry: get the right PaymentProvider for a merchant."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import settings
from ..security import decrypt_keys
from .base import AccountRequest, AccountResult, PaymentProvider, ProviderError, TransactionResult, WebhookInfo

SUPPORTED = ("monnify", "mock")
PLANNED = ("paystack", "flutterwave")  # same interface; not implemented yet


def build_provider(name: str, keys: dict, db: Session | None = None, merchant_id: int | None = None) -> PaymentProvider:
    if name == "monnify":
        from .monnify import MonnifyProvider

        return MonnifyProvider(keys)
    if name == "mock":
        if not settings.allow_mock_provider:
            raise ProviderError("Mock provider is disabled")
        from .mock import MockProvider

        return MockProvider(keys, db=db, merchant_id=merchant_id)
    if name in PLANNED:
        raise ProviderError(f"{name.title()} support is planned but not built yet")
    raise ProviderError(f"Unknown provider '{name}'")


def get_provider(merchant, db: Session | None = None) -> PaymentProvider:
    return build_provider(merchant.provider, decrypt_keys(merchant.provider_keys), db=db, merchant_id=merchant.id)


__all__ = [
    "AccountRequest", "AccountResult", "PaymentProvider", "ProviderError", "TransactionResult", "WebhookInfo",
    "build_provider", "get_provider", "SUPPORTED",
]
