"""Paystack, with Landed as the main account and each seller as a sub-account.

Docs: https://paystack.com/docs/api/
- Banks:            GET  /bank?country=nigeria
- Resolve account:  GET  /bank/resolve?account_number=&bank_code=
- Sub-account:      POST /subaccount, PUT /subaccount/:code   (percentage_charge = Landed's cut)
- Customer:         POST /customer
- Dedicated acct:   POST /dedicated_account  {customer, preferred_bank, subaccount}
- Pay with Transfer POST /charge {email, amount, bank_transfer:{account_expires_at}, subaccount, reference}
- Verify:           GET  /transaction/verify/:reference
- List:             GET  /transaction?status=success&from=&to=&perPage=&page=
- Webhook:          header x-paystack-signature = HMAC-SHA512(secret key, raw body), hex
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Mapping
from urllib.parse import quote

import httpx
from django.conf import settings

from .base import AccountResult, Bank, Provider, ProviderError, Transaction, WebhookInfo

log = logging.getLogger("landed.paystack")


def _dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


class PaystackProvider(Provider):
    name = "paystack"

    def __init__(self, secret_key: str | None = None, client: httpx.Client | None = None):
        self.secret = secret_key or settings.PAYSTACK_SECRET_KEY
        if not self.secret:
            raise ProviderError("Paystack is not configured (PAYSTACK_SECRET_KEY is empty)")
        self.base = settings.PAYSTACK_BASE_URL.rstrip("/")
        self.client = client or httpx.Client(timeout=25)

    def __repr__(self):
        return "<PaystackProvider>"

    def _call(self, method: str, path: str, **kw) -> dict:
        headers = {"Authorization": f"Bearer {self.secret}", "Content-Type": "application/json"}
        try:
            r = self.client.request(method, f"{self.base}{path}", headers=headers, **kw)
        except httpx.HTTPError as e:
            raise ProviderError(f"Paystack unreachable ({type(e).__name__})") from None
        try:
            body = r.json()
        except ValueError:
            raise ProviderError(f"Paystack returned HTTP {r.status_code}") from None
        if r.status_code >= 400 or not body.get("status"):
            raise ProviderError(f"Paystack: {body.get('message') or f'HTTP {r.status_code}'}")
        return body

    # ---- sellers ----
    def list_banks(self) -> list[Bank]:
        data = self._call("GET", "/bank", params={"country": "nigeria", "perPage": 200})["data"]
        seen, out = set(), []
        for b in data:
            if b.get("active", True) and b.get("code") and b["code"] not in seen:
                seen.add(b["code"])
                out.append(Bank(code=b["code"], name=b["name"]))
        return sorted(out, key=lambda b: b.name.lower())

    def resolve_account(self, account_number: str, bank_code: str) -> str:
        data = self._call("GET", "/bank/resolve", params={"account_number": account_number, "bank_code": bank_code})["data"]
        return data.get("account_name") or ""

    def create_subaccount(self, *, business_name, bank_code, account_number, fee_percent, contact_phone, contact_email) -> str:
        payload = {
            "business_name": business_name[:100],
            "settlement_bank": bank_code,
            "account_number": account_number,
            "percentage_charge": float(fee_percent),
            "primary_contact_phone": contact_phone,
        }
        if contact_email:
            payload["primary_contact_email"] = contact_email
        data = self._call("POST", "/subaccount", json=payload)["data"]
        return data["subaccount_code"]

    def find_subaccount(self, bank_code, bank_name, account_number):
        for page in range(1, 6):
            body = self._call("GET", "/subaccount", params={"perPage": 100, "page": page})
            for x in body.get("data") or []:
                bank = str(x.get("settlement_bank") or "")
                if str(x.get("account_number")) == account_number and bank in (bank_code, bank_name) and x.get("active", True):
                    return x["subaccount_code"], x.get("account_name") or ""
            if page >= int((body.get("meta") or {}).get("pageCount") or 1):
                break
        return None

    def update_subaccount(self, code, *, bank_code=None, account_number=None, fee_percent=None, business_name=None) -> None:
        payload = {}
        if bank_code:
            payload["settlement_bank"] = bank_code
        if account_number:
            payload["account_number"] = account_number
        if fee_percent is not None:
            payload["percentage_charge"] = float(fee_percent)
        if business_name:
            payload["business_name"] = business_name[:100]
        if payload:
            self._call("PUT", f"/subaccount/{quote(code, safe='')}", json=payload)

    # ---- accounts ----
    def _customer(self, name: str, phone: str, email: str) -> str:
        first, _, last = name.strip().partition(" ")
        data = self._call("POST", "/customer", json={"email": email, "first_name": first or name,
                                                     "last_name": last or first or name, "phone": phone})["data"]
        return data["customer_code"]

    def create_dedicated_account(self, *, customer_name, phone, email, subaccount_code, existing_customer_code="") -> AccountResult:
        code = existing_customer_code or self._customer(customer_name, phone, email)
        data = self._call("POST", "/dedicated_account", json={
            "customer": code, "preferred_bank": settings.PAYSTACK_DVA_BANK, "subaccount": subaccount_code,
        })["data"]
        return AccountResult(account_number=data["account_number"], bank_name=(data.get("bank") or {}).get("name", ""),
                             account_name=data.get("account_name", ""), reference=str(data.get("id", "")),
                             customer_code=code)

    def create_transfer_account(self, *, amount, email, reference, subaccount_code, expires_at, metadata) -> AccountResult:
        payload = {
            "email": email,
            "amount": str(amount),
            "reference": reference,
            "subaccount": subaccount_code,
            "bearer": settings.PAYSTACK_FEE_BEARER,
            "metadata": metadata,
            "bank_transfer": {"account_expires_at": expires_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        }
        data = self._call("POST", "/charge", json=payload)["data"]
        if not data.get("account_number"):
            raise ProviderError(data.get("display_text") or "Paystack did not return an account number")
        return AccountResult(account_number=data["account_number"], bank_name=(data.get("bank") or {}).get("name", ""),
                             account_name=data.get("account_name", ""), reference=data.get("reference") or reference,
                             expires_at=_dt(data.get("account_expires_at")) or expires_at)

    # ---- confirmation ----
    @staticmethod
    def _tx(d: dict) -> Transaction:
        auth = d.get("authorization") or {}
        sub = d.get("subaccount") or {}
        meta = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
        return Transaction(
            reference=d.get("reference", ""),
            status=d.get("status", ""),
            amount=int(d.get("amount") or 0),
            fees=int(d.get("fees") or 0),
            paid_at=_dt(d.get("paid_at") or d.get("paidAt")),
            channel=d.get("channel", ""),
            account_number=auth.get("receiver_bank_account_number") or "",
            subaccount_code=sub.get("subaccount_code", "") if isinstance(sub, dict) else "",
            customer_code=(d.get("customer") or {}).get("customer_code", ""),
            payer_name=auth.get("sender_name") or auth.get("account_name") or "",
            payer_bank=auth.get("sender_bank") or auth.get("bank") or "",
            metadata=meta,
        )

    def verify(self, reference: str) -> Transaction:
        return self._tx(self._call("GET", f"/transaction/verify/{quote(reference, safe='')}")["data"])

    def list_transactions(self, start: datetime, end: datetime) -> list[Transaction]:
        out, page = [], 1
        while page <= 50:
            body = self._call("GET", "/transaction", params={
                "status": "success", "perPage": 100, "page": page,
                "from": start.astimezone(timezone.utc).isoformat(), "to": end.astimezone(timezone.utc).isoformat(),
            })
            out += [self._tx(d) for d in body.get("data") or []]
            meta = body.get("meta") or {}
            if page >= int(meta.get("pageCount") or 1):
                break
            page += 1
        return out

    def verify_signature(self, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        sig = headers.get("x-paystack-signature") or headers.get("X-Paystack-Signature") or ""
        expected = hmac.new(self.secret.encode(), raw_body, hashlib.sha512).hexdigest()
        return bool(sig) and hmac.compare_digest(expected, sig.strip())

    def parse_webhook(self, payload: dict) -> WebhookInfo:
        event = payload.get("event", "")
        data = payload.get("data") or {}
        return WebhookInfo(event=event, reference=data.get("reference", ""), is_payment=event == "charge.success")
