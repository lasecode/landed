"""WhatsApp Cloud API webhook: verification handshake, seller commands, delivery statuses."""
from __future__ import annotations

import json
import logging

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.models import User

from . import bot, notify
from .models import Notification, WhatsAppContact
from .whatsapp.client import verify_signature

log = logging.getLogger("landed.whatsapp")


def handle_inbound(phone: str, text: str) -> str | None:
    WhatsAppContact.objects.update_or_create(phone=phone, defaults={"last_inbound_at": timezone.now()})
    user = User.objects.filter(phone=phone, is_active=True, business__isnull=False).select_related("business").first()
    if not user:
        return None  # customers replying, unknown numbers: no auto-reply
    with transaction.atomic():
        reply = bot.handle(user, text)
        notify.queue(user.business, phone, "text", [reply])
    return reply


@csrf_exempt
def whatsapp_webhook(request):
    if request.method == "GET":
        q = request.GET
        if (q.get("hub.mode") == "subscribe" and settings.WHATSAPP_VERIFY_TOKEN
                and q.get("hub.verify_token") == settings.WHATSAPP_VERIFY_TOKEN):
            return HttpResponse(q.get("hub.challenge", ""))
        return HttpResponseForbidden("Verification failed")
    if request.method != "POST":
        return HttpResponse(status=405)
    if not verify_signature(request.body, request.headers.get("X-Hub-Signature-256")):
        return HttpResponseForbidden("Invalid signature")
    data = json.loads(request.body or b"{}")
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for st in value.get("statuses", []):
                if st.get("status") in ("delivered", "read", "failed"):
                    Notification.objects.filter(wa_message_id=st.get("id")).update(
                        status=st["status"], error=json.dumps(st.get("errors", []))[:500] if st["status"] == "failed" else "")
            for msg in value.get("messages", []):
                if msg.get("type") == "text":
                    try:
                        handle_inbound(msg["from"], msg["text"]["body"])
                    except Exception:  # never make Meta retry the whole batch
                        log.exception("bot failed on an inbound message")
    return JsonResponse({"ok": True})
