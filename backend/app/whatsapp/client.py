"""WhatsApp Business Cloud API client (official Meta API only)."""
from __future__ import annotations

import hashlib
import hmac
import logging

import httpx

from ..config import settings
from .templates import TEMPLATES, render

log = logging.getLogger("landed.whatsapp")


class WhatsAppError(Exception):
    pass


def _url() -> str:
    return f"https://graph.facebook.com/{settings.wa_api_version}/{settings.wa_phone_number_id}/messages"


def is_configured() -> bool:
    return bool(settings.wa_token and settings.wa_phone_number_id) and not settings.wa_dry_run


def _post(payload: dict) -> str:
    if not is_configured():
        text = payload.get("text", {}).get("body") or payload.get("_preview", "")
        log.info("[whatsapp dry-run] to=%s\n%s", payload.get("to"), text)
        return "dry-run"
    payload.pop("_preview", None)
    r = httpx.post(_url(), json=payload, headers={"Authorization": f"Bearer {settings.wa_token}"}, timeout=20)
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
            msg = f"{err.get('code')}: {err.get('message')}"
        except ValueError:
            msg = f"HTTP {r.status_code}"
        raise WhatsAppError(msg)
    return r.json()["messages"][0]["id"]


def send_template(to: str, template: str, args: list[str]) -> str:
    if template not in TEMPLATES:
        raise WhatsAppError(f"Unknown template {template}")
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": settings.wa_template_lang},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": str(a)[:1000]} for a in args]}],
        },
        "_preview": render(template, args),
    }
    return _post(payload)


def send_text(to: str, body: str) -> str:
    return _post({"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body[:4096], "preview_url": True}})


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    """Meta signs webhook calls with X-Hub-Signature-256 = sha256=HMAC(app_secret, body)."""
    if not settings.wa_app_secret:
        return not settings.is_production  # allow unsigned only outside production
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(settings.wa_app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])
