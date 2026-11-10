"""Monnify implementation.

Docs: https://developers.monnify.com
- Auth: POST /api/v1/auth/login with Basic base64(apiKey:secretKey) -> bearer token
- Dynamic (per invoice): POST /api/v1/invoice/create -> a virtual account tied to the invoice that
  stops accepting money once paid or at expiryDate
- Static (per customer): POST /api/v2/bank-transfer/reserved-accounts (needs customer BVN or NIN)
- Verify: GET /api/v2/transactions/{urlencoded transactionReference}
- List: GET /api/v1/transactions/search?from=&to=&page=&size=
- Webhook: header 'monnify-signature' = HMAC-SHA512(secretKey, raw body), hex

Merchant keys (Option A, merchant's own Monnify account):
    {"api_key": "...", "secret_key": "...", "contract_code": "...", "live": false}
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Mapping
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from ..config import settings
from .base import (
    AccountRequest,
    AccountResult,
    PaymentProvider,
    ProviderError,
    TransactionResult,
    WebhookInfo,
)

LAGOS = ZoneInfo("Africa/Lagos")
LIVE_URL = "https://api.monnify.com"


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):  # epoch ms
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    s = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %I:%M:%S %p", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=LAGOS).astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=LAGOS).astimezone(timezone.utc)
    except ValueError:
        return None


def _to_kobo(value) -> int:
    from decimal import Decimal

    return int((Decimal(str(value or 0)) * 100).quantize(Decimal(1)))


class MonnifyProvider(PaymentProvider):
    name = "monnify"

    def __init__(self, keys: Mapping[str, str], client: httpx.Client | None = None):
        super().__init__(keys)
        for k in ("api_key", "secret_key", "contract_code"):
            if not self.keys.get(k):
                raise ProviderError(f"Monnify {k} is missing in settings")
        live = str(self.keys.get("live", "")).lower() in {"1", "true", "yes"}
        self.base_url = LIVE_URL if live else settings.monnify_base_url
        self._client = client or httpx.Client(timeout=20)
        self._token: str | None = None
        self._token_exp = 0.0

    # ---- HTTP helpers ----
    def _auth(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        basic = base64.b64encode(f"{self.keys['api_key']}:{self.keys['secret_key']}".encode()).decode()
        try:
            r = self._client.post(f"{self.base_url}/api/v1/auth/login", headers={"Authorization": f"Basic {basic}"})
        except httpx.HTTPError as e:
            raise ProviderError(f"Monnify unreachable: {type(e).__name__}") from None
        body = self._json(r)
        rb = body.get("responseBody") or {}
        self._token = rb.get("accessToken")
        self._token_exp = time.time() + int(rb.get("expiresIn") or 300)
        if not self._token:
            raise ProviderError("Monnify login failed (check API key / secret)")
        return self._token

    @staticmethod
    def _json(r: httpx.Response) -> dict:
        try:
            body = r.json()
        except ValueError:
            raise ProviderError(f"Monnify returned HTTP {r.status_code}") from None
        if r.status_code >= 400 or not body.get("requestSuccessful", False):
            msg = body.get("responseMessage") or f"HTTP {r.status_code}"
            raise ProviderError(f"Monnify: {msg}")
        return body

    def _request(self, method: str, path: str, **kw) -> dict:
        headers = {"Authorization": f"Bearer {self._auth()}"}
        try:
            r = self._client.request(method, f"{self.base_url}{path}", headers=headers, **kw)
        except httpx.HTTPError as e:
            raise ProviderError(f"Monnify unreachable: {type(e).__name__}") from None
        return self._json(r)

    # ---- interface ----
    def test_connection(self) -> None:
        self._auth()

    def create_dynamic_account(self, req: AccountRequest) -> AccountResult:
        if req.amount is None or req.expires_at is None:
            raise ProviderError("Dynamic account needs amount and expiry")
        payload = {
            "amount": f"{req.amount / 100:.2f}",
            "invoiceReference": req.reference,
            "description": (req.description or "Invoice")[:100],
            "currencyCode": "NGN",
            "contractCode": self.keys["contract_code"],
            "customerEmail": req.customer_email,
            "customerName": req.customer_name,
            "expiryDate": req.expires_at.astimezone(LAGOS).strftime("%Y-%m-%d %H:%M:%S"),
            "paymentMethods": ["ACCOUNT_TRANSFER"],
        }
        rb = self._request("POST", "/api/v1/invoice/create", json=payload)["responseBody"]
        if not rb.get("accountNumber"):
            raise ProviderError("Monnify did not return an account number")
        return AccountResult(
            account_number=rb["accountNumber"],
            bank_name=rb.get("bankName") or "",
            account_name=rb.get("accountName"),
            provider_ref=rb.get("invoiceReference") or req.reference,
            expires_at=_parse_dt(rb.get("expiryDate")) or req.expires_at,
        )

    def create_static_account(self, req: AccountRequest) -> AccountResult:
        payload = {
            "accountReference": req.reference,
            "accountName": req.customer_name[:60],
            "currencyCode": "NGN",
            "contractCode": self.keys["contract_code"],
            "customerEmail": req.customer_email,
            "customerName": req.customer_name,
            "getAllAvailableBanks": True,
        }
        if req.bvn:
            payload["bvn"] = req.bvn
        if req.nin:
            payload["nin"] = req.nin
        rb = self._request("POST", "/api/v2/bank-transfer/reserved-accounts", json=payload)["responseBody"]
        accounts = rb.get("accounts") or []
        if not accounts:
            raise ProviderError("Monnify did not return any reserved account")
        a = accounts[0]
        return AccountResult(
            account_number=a["accountNumber"],
            bank_name=a.get("bankName") or "",
            account_name=a.get("accountName"),
            provider_ref=rb.get("accountReference") or req.reference,
        )

    def cancel_dynamic_account(self, provider_ref: str) -> None:
        try:
            self._request("DELETE", f"/api/v1/invoice/{quote(provider_ref, safe='')}/cancel")
        except ProviderError:
            pass  # cancelling is best effort; account still expires

    def _tx_from(self, d: dict) -> TransactionResult:
        status_raw = (d.get("paymentStatus") or "").upper()
        status = "paid" if status_raw in {"PAID", "OVERPAID", "PARTIALLY_PAID"} else ("pending" if status_raw == "PENDING" else "failed")
        dest = d.get("destinationAccountInformation") or {}
        acct = d.get("accountDetails") or {}
        src = d.get("paymentSourceInformation") or []
        src0 = src[0] if isinstance(src, list) and src else {}
        product = d.get("product") or {}
        amount = d.get("amountPaid") if d.get("amountPaid") is not None else d.get("amount")
        return TransactionResult(
            reference=d.get("transactionReference"),
            amount=_to_kobo(amount),
            status=status,
            paid_at=_parse_dt(d.get("paidOn") or d.get("completedOn")),
            account_number=dest.get("accountNumber") or acct.get("accountNumber") or None,
            product_reference=product.get("reference") or d.get("paymentReference"),
            payer_name=src0.get("accountName") or acct.get("accountName") or (d.get("customer") or {}).get("name"),
            payer_bank=src0.get("bankCode") or acct.get("bankCode"),
            raw={k: d.get(k) for k in ("transactionReference", "paymentReference", "paymentStatus", "amountPaid", "paidOn")},
        )

    def verify_transaction(self, reference: str) -> TransactionResult:
        body = self._request("GET", f"/api/v2/transactions/{quote(reference, safe='')}")
        return self._tx_from(body.get("responseBody") or {})

    def list_transactions(self, start: datetime, end: datetime) -> list[TransactionResult]:
        out: list[TransactionResult] = []
        page = 0
        while True:
            params = {
                "page": page,
                "size": 100,
                "from": int(start.timestamp() * 1000),
                "to": int(end.timestamp() * 1000),
                "paymentStatus": "PAID",
            }
            rb = self._request("GET", "/api/v1/transactions/search", params=params).get("responseBody") or {}
            for d in rb.get("content") or []:
                out.append(self._tx_from(d))
            if rb.get("last", True) or page > 200:
                break
            page += 1
        return out

    def verify_webhook_signature(self, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        sig = headers.get("monnify-signature") or headers.get("Monnify-Signature")
        if not sig:
            return False
        expected = hmac.new(self.keys["secret_key"].encode(), raw_body, hashlib.sha512).hexdigest()
        return hmac.compare_digest(expected, sig.strip().lower())

    def parse_webhook(self, payload: dict) -> WebhookInfo:
        et = payload.get("eventType") or ""
        data = payload.get("eventData") or {}
        return WebhookInfo(event_type=et, reference=data.get("transactionReference"), is_payment=et == "SUCCESSFUL_TRANSACTION")
