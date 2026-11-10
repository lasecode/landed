"""Platform admin (us): merchants, webhook monitor, failed matches, replays. Header: X-Admin-Token."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import platform_admin
from ..models import Invoice, Merchant, Notification, Payment, PaymentStatus, ReconciliationRun, WebhookEvent
from ..tasks import enqueue

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(platform_admin)])


@router.get("/merchants")
def merchants(db: Session = Depends(get_db)):
    out = []
    for m in db.scalars(select(Merchant).order_by(Merchant.id)):
        out.append({
            "id": m.id, "business_name": m.business_name, "phone": m.phone, "plan": m.plan, "provider": m.provider,
            "connected": bool(m.provider_keys), "created_at": m.created_at.isoformat(),
            "invoices": db.scalar(select(func.count(Invoice.id)).where(Invoice.merchant_id == m.id)),
            "unmatched": db.scalar(select(func.count(Payment.id)).where(Payment.merchant_id == m.id,
                                                                       Payment.status == PaymentStatus.unmatched)),
        })
    return out


@router.patch("/merchants/{mid}/plan")
def set_plan(mid: int, plan: str, db: Session = Depends(get_db)):
    m = db.get(Merchant, mid)
    if not m:
        raise HTTPException(404, "Merchant not found")
    if plan not in ("free", "starter", "growth", "pro"):
        raise HTTPException(400, "Unknown plan")
    m.plan = plan
    db.commit()
    return {"ok": True}


@router.get("/webhooks")
def webhooks(status: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    q = select(WebhookEvent).order_by(WebhookEvent.id.desc()).limit(min(limit, 500))
    if status:
        q = q.where(WebhookEvent.status == status)
    return [{"id": e.id, "merchant_id": e.merchant_id, "provider": e.provider, "reference": e.reference,
             "event_type": e.event_type, "status": e.status, "detail": e.detail, "signature_valid": e.signature_valid,
             "received_at": e.received_at.isoformat()} for e in db.scalars(q)]


@router.post("/webhooks/{event_id}/replay")
def replay(event_id: int, db: Session = Depends(get_db)):
    e = db.get(WebhookEvent, event_id)
    if not e or not e.signature_valid:
        raise HTTPException(400, "Only signed events can be replayed")
    e.status = "queued"
    db.commit()
    enqueue("app.jobs.process_webhook_event", e.id)
    return {"ok": True}


@router.post("/reconcile/{mid}")
def reconcile(mid: int):
    enqueue("app.jobs.reconcile_merchant", mid)
    return {"queued": True}


@router.get("/health")
def health(db: Session = Depends(get_db)):
    return {
        "webhooks_failed": db.scalar(select(func.count(WebhookEvent.id)).where(WebhookEvent.status == "failed")),
        "webhooks_rejected": db.scalar(select(func.count(WebhookEvent.id)).where(WebhookEvent.status == "rejected")),
        "notifications_failed": db.scalar(select(func.count(Notification.id)).where(Notification.status == "failed")),
        "unmatched_payments": db.scalar(select(func.count(Payment.id)).where(Payment.status == PaymentStatus.unmatched)),
        "last_reconciliations": [{"merchant_id": r.merchant_id, "checked": r.checked, "recovered": r.recovered,
                                  "error": r.error, "at": r.created_at.isoformat()}
                                 for r in db.scalars(select(ReconciliationRun).order_by(ReconciliationRun.id.desc()).limit(20))],
    }
