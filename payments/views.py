"""Paystack webhook: check the signature, store the event, queue verification, answer 200 fast."""
from __future__ import annotations

import json
import logging

from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import WebhookEvent
from .providers import get_provider
from .tasks import process_webhook

log = logging.getLogger("landed.webhooks")


@csrf_exempt
@require_POST
def paystack_webhook(request):
    provider = get_provider()
    raw = request.body
    if not provider.verify_signature(raw, request.headers):
        WebhookEvent.objects.create(provider=provider.name, status="rejected", detail="Bad signature")
        log.warning("rejected webhook with a bad signature from %s", request.META.get("REMOTE_ADDR"))
        return HttpResponse("Invalid signature", status=401)
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        return HttpResponse("Invalid JSON", status=400)
    info = provider.parse_webhook(payload)
    ev = WebhookEvent.objects.create(provider=provider.name, event_type=info.event, reference=info.reference,
                                     signature_valid=True, payload=payload,
                                     status="queued" if info.is_payment and info.reference else "ignored")
    if ev.status == "queued":
        transaction.on_commit(lambda: process_webhook.delay(ev.id))
    return JsonResponse({"ok": True})
