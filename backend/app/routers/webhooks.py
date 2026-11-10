"""Provider webhooks (brief 8.2 steps 1-2): verify signature, store, queue, return 200 fast.

URL per merchant: POST /webhooks/{provider}/{merchant_webhook_token}
(Option A means each merchant has their own provider account and signing secret, so the URL
tells us whose secret to check the signature with.)
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Merchant, WebhookEvent
from ..providers import ProviderError, get_provider
from ..tasks import enqueue

router = APIRouter(tags=["webhooks"])
log = logging.getLogger("landed.webhooks")


@router.post("/webhooks/{provider}/{token}")
async def provider_webhook(provider: str, token: str, request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    m = db.scalar(select(Merchant).where(Merchant.webhook_token == token))
    if not m or m.provider != provider:
        raise HTTPException(404, "Unknown webhook")
    try:
        prov = get_provider(m, db)
    except ProviderError:
        raise HTTPException(503, "Provider not configured") from None

    if not prov.verify_webhook_signature(raw, request.headers):
        db.add(WebhookEvent(provider=provider, merchant_id=m.id, signature_valid=False, status="rejected",
                            detail="Bad signature"))
        db.commit()
        log.warning("rejected webhook with bad signature merchant=%s", m.id)
        raise HTTPException(401, "Invalid signature")

    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "Invalid JSON") from None
    info = prov.parse_webhook(payload)
    ev = WebhookEvent(provider=provider, merchant_id=m.id, reference=info.reference, event_type=info.event_type,
                      signature_valid=True, status="queued" if info.is_payment else "ignored", payload=payload)
    db.add(ev)
    db.commit()
    if info.is_payment:
        enqueue("app.jobs.process_webhook_event", ev.id)
    return {"ok": True}
