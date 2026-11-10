"""Testing tools available only while the mock provider is allowed (never in production).

- simulate a customer bank transfer into any mock virtual account (goes through the real signed
  webhook → queue → verify → matching path)
- talk to the WhatsApp bot from the dashboard without a WhatsApp number
"""
from __future__ import annotations

import json
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..config import settings
from ..deps import Ctx, current
from ..models import MockTransaction, utcnow
from ..money import naira_to_kobo
from ..providers.mock import DEFAULT_SECRET, sign
from ..schemas import SimulateIn
from ..security import decrypt_keys
from ..whatsapp.bot import handle_message

router = APIRouter(prefix="/api/dev", tags=["dev"])


def _guard(ctx: Ctx) -> None:
    if not settings.allow_mock_provider or settings.is_production:
        raise HTTPException(404, "Not found")
    if ctx.merchant.provider != "mock":
        raise HTTPException(400, "Simulated payments only work with the test provider. Real money flows through your provider.")


@router.post("/simulate-payment")
async def simulate(body: SimulateIn, request: Request, ctx: Ctx = Depends(current)):
    _guard(ctx)
    ref = f"MOCK|{utcnow():%Y%m%d%H%M%S}|{secrets.token_hex(3).upper()}"
    ctx.db.add(MockTransaction(reference=ref, merchant_id=ctx.mid, account_number=body.account_number.strip(),
                               amount=naira_to_kobo(body.amount_naira), payer_name=body.payer_name, payer_bank="GTBank",
                               paid_at=utcnow()))
    ctx.db.commit()
    raw = json.dumps({"event": "payment.received", "reference": ref}).encode()
    secret = decrypt_keys(ctx.merchant.provider_keys).get("secret_key") or DEFAULT_SECRET
    # call our own webhook endpoint exactly like the provider would
    transport = httpx.ASGITransport(app=request.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://internal") as client:
        r = await client.post(f"/webhooks/mock/{ctx.merchant.webhook_token}", content=raw,
                              headers={"x-mock-signature": sign(secret, raw), "content-type": "application/json"})
    return {"reference": ref, "webhook_status": r.status_code}


class BotIn(BaseModel):
    text: str


@router.post("/bot")
def bot(body: BotIn, ctx: Ctx = Depends(current)):
    if not settings.allow_mock_provider or settings.is_production:
        raise HTTPException(404, "Not found")
    reply = handle_message(ctx.db, ctx.user, body.text)
    ctx.db.commit()
    return {"reply": reply}
