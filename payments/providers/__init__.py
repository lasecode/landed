from django.conf import settings

from .base import AccountResult, Bank, Provider, ProviderError, Transaction, WebhookInfo


def get_provider(name: str | None = None) -> Provider:
    name = name or settings.PAYMENT_PROVIDER
    if name == "paystack":
        from .paystack import PaystackProvider

        return PaystackProvider()
    if name == "mock":
        if settings.APP_ENV == "production":
            raise ProviderError("The test provider is disabled in production")
        from .mock import MockProvider

        return MockProvider()
    raise ProviderError(f"Unknown payment provider '{name}'")


def is_test_mode() -> bool:
    return settings.PAYMENT_PROVIDER == "mock"


__all__ = ["AccountResult", "Bank", "Provider", "ProviderError", "Transaction", "WebhookInfo", "get_provider",
           "is_test_mode"]
