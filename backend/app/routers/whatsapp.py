"""WhatsApp Cloud API webhook: verification handshake, inbound merchant commands, delivery statuses."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Notification, StaffUser, WhatsAppContact, utcnow
from ..services import notify
from ..whatsapp.bot import handle_message
from ..whatsapp.client import verify_signature

router = APIRouter(tags=["whatsapp"])
log = logging.getLogger("landed.whatsapp")


@router.get("/webhooks/whatsapp")
def verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and settings.wa_verify_token and q.get("hub.verify_token") == settings.wa_verify_token:
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403, "Verification failed")


def handle_inbound(db: Session, phone: str, text: str) -> str | None:
    contact = db.get(WhatsAppContact, phone)
    if contact:
        contact.last_inbound_at = utcnow()
    else:
        db.add(WhatsAppContact(phone=phone, last_inbound_at=utcnow()))
    db.flush()
    user = db.scalar(select(StaffUser).where(StaffUser.phone == phone, StaffUser.active.is_(True)))
    if not user:
        return None  # customers replying, or unknown numbers: no auto-reply for now
    reply = handle_message(db, user, text)
    notify.queue(db, user.merchant_id, phone, "text", body=reply)
    db.commit()
    return reply


@router.post("/webhooks/whatsapp")
async def inbound(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    if not verify_signature(raw, request.headers.get("x-hub-signature-256")):
        raise HTTPException(401, "Invalid signature")
    data = json.loads(raw or b"{}")
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for st in value.get("statuses", []):
                n = db.scalar(select(Notification).where(Notification.wa_message_id == st.get("id")))
                if n and st.get("status") in ("delivered", "read", "failed"):
                    n.status = st["status"]
                    if st["status"] == "failed":
                        n.error = json.dumps(st.get("errors", []))[:500]
            db.commit()
            for msg in value.get("messages", []):
                if msg.get("type") != "text":
                    continue
                try:
                    handle_inbound(db, msg["from"], msg["text"]["body"])
                except Exception:  # never make Meta retry because of one bad message
                    db.rollback()
                    log.exception("bot failed on inbound message")
    return {"ok": True}
