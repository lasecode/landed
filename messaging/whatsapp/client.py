"""WhatsApp Business Cloud API (official Meta API)."""
from __future__ import annotations

import hashlib
import hmac
import logging

import httpx
from django.conf import settings

from .templates import TEMPLATES

log = logging.getLogger("landed.whatsapp")


class WhatsAppError(Exception):
    pass


def is_live() -> bool:
    return bool(settings.WHATSAPP_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID) and not settings.WHATSAPP_DRY_RUN


def _post(payload: dict) -> str:
    url = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    r = httpx.post(url, json=payload, headers={"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}, timeout=20)
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
            raise WhatsAppError(f"{err.get('code')}: {err.get('message')}")
        except ValueError:
            raise WhatsAppError(f"HTTP {r.status_code}") from None
    return r.json()["messages"][0]["id"]


def send_template(to: str, template: str, params: list) -> str:
    if template not in TEMPLATES:
        raise WhatsAppError(f"Unknown template {template}")
    return _post({
        "messaging_product": "whatsapp", "to": to, "type": "template",
        "template": {"name": template, "language": {"code": settings.WHATSAPP_TEMPLATE_LANG},
                     "components": [{"type": "body", "parameters": [{"type": "text", "text": str(p)[:1000]} for p in params]}]},
    })


def send_text(to: str, body: str) -> str:
    return _post({"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body[:4096], "preview_url": True}})


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    """Meta signs webhook calls: X-Hub-Signature-256 = sha256=HMAC(app secret, body)."""
    if not settings.WHATSAPP_APP_SECRET:
        return settings.APP_ENV != "production"
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(settings.WHATSAPP_APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])
