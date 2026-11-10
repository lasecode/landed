from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from .. import serialize as S
from ..deps import Ctx, current, owner
from ..models import aware, Customer, Invoice, InvoiceStatus, Payment, PaymentStatus
from ..money import kobo_to_naira
from ..schemas import AssignIn
from ..services import matching

router = APIRouter(prefix="/api", tags=["payments"])
LAGOS = ZoneInfo("Africa/Lagos")
OPEN = (InvoiceStatus.unpaid, InvoiceStatus.part_paid)


def _received(ctx: Ctx, start: datetime, end: datetime | None = None) -> tuple[int, int]:
    stmt = select(func.coalesce(func.sum(Payment.amount), 0), func.count(Payment.id)).where(
        Payment.merchant_id == ctx.mid, Payment.status != PaymentStatus.refunded, Payment.paid_at >= start)
    if end:
        stmt = stmt.where(Payment.paid_at < end)
    total, n = ctx.db.execute(stmt).one()
    return int(total), int(n)


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=LAGOS)


@router.get("/overview")
def overview(ctx: Ctx = Depends(current)):
    today = datetime.now(LAGOS).date()
    t, tn = _received(ctx, _day_start(today))
    w, _ = _received(ctx, _day_start(today - timedelta(days=today.weekday())))
    mo, _ = _received(ctx, _day_start(today.replace(day=1)))
    outstanding, open_n = ctx.db.execute(
        select(func.coalesce(func.sum(Invoice.amount - Invoice.amount_paid), 0), func.count(Invoice.id))
        .where(Invoice.merchant_id == ctx.mid, Invoice.status.in_(OPEN))).one()
    unmatched = ctx.db.scalar(select(func.count(Payment.id)).where(Payment.merchant_id == ctx.mid,
                                                                  Payment.status == PaymentStatus.unmatched))
    overpaid = ctx.db.scalar(select(func.count(Invoice.id)).where(Invoice.merchant_id == ctx.mid,
                                                                  Invoice.excess_status == "pending"))
    recent = ctx.db.scalars(select(Payment).where(Payment.merchant_id == ctx.mid).order_by(Payment.paid_at.desc()).limit(10)).all()
    return {
        "today": t, "today_count": tn, "week": w, "month": mo,
        "outstanding": int(outstanding), "open_invoices": int(open_n),
        "unmatched": int(unmatched or 0), "overpaid": int(overpaid or 0),
        "recent": [S.payment(p) for p in recent],
    }


@router.get("/payments")
def list_payments(status: str | None = None, limit: int = 200, ctx: Ctx = Depends(current)):
    stmt = select(Payment).where(Payment.merchant_id == ctx.mid)
    if status:
        try:
            stmt = stmt.where(Payment.status == PaymentStatus(status))
        except ValueError:
            raise HTTPException(400, "Unknown status") from None
    return [S.payment(p) for p in ctx.db.scalars(stmt.order_by(Payment.paid_at.desc()).limit(min(limit, 500)))]


def _payment(ctx: Ctx, pid: int) -> Payment:
    p = ctx.db.get(Payment, pid)
    if not p or p.merchant_id != ctx.mid:
        raise HTTPException(404, "Payment not found")
    return p


@router.post("/payments/{pid}/assign")
def assign(pid: int, body: AssignIn, ctx: Ctx = Depends(current)):
    p = _payment(ctx, pid)
    inv = ctx.db.get(Invoice, body.invoice_id) if body.invoice_id else None
    cus = ctx.db.get(Customer, body.customer_id) if body.customer_id else None
    if (inv and inv.merchant_id != ctx.mid) or (cus and cus.merchant_id != ctx.mid):
        raise HTTPException(404, "Not found")
    try:
        matching.assign_unmatched(ctx.db, ctx.merchant, p, invoice=inv, customer=cus, actor_id=ctx.user.id)
    except matching.ActionError as e:
        ctx.db.rollback()
        raise HTTPException(400, str(e)) from None
    ctx.db.commit()
    return S.payment(p)


@router.post("/payments/{pid}/refund")
def refund(pid: int, ctx: Ctx = Depends(owner)):
    p = _payment(ctx, pid)
    try:
        matching.refund_unmatched(ctx.db, ctx.merchant, p, actor_id=ctx.user.id)
    except matching.ActionError as e:
        raise HTTPException(400, str(e)) from None
    ctx.db.commit()
    return S.payment(p)


# ---------- reports ----------

def _range(start: date | None, end: date | None) -> tuple[datetime, datetime, date, date]:
    today = datetime.now(LAGOS).date()
    end = end or today
    start = start or end.replace(day=1)
    if start > end:
        raise HTTPException(400, "Start date is after end date")
    return _day_start(start), _day_start(end + timedelta(days=1)), start, end


@router.get("/reports")
def report(start: date | None = None, end: date | None = None, ctx: Ctx = Depends(current)):
    s, e, sd, ed = _range(start, end)
    pays = ctx.db.scalars(select(Payment).where(Payment.merchant_id == ctx.mid, Payment.paid_at >= s, Payment.paid_at < e)).all()
    invs = ctx.db.scalars(select(Invoice).where(Invoice.merchant_id == ctx.mid, Invoice.created_at >= s, Invoice.created_at < e)).all()
    days: dict[str, int] = {}
    d = sd
    while d <= ed:
        days[d.isoformat()] = 0
        d += timedelta(days=1)
    for p in pays:
        if p.status != PaymentStatus.refunded:
            k = aware(p.paid_at).astimezone(LAGOS).date().isoformat()
            days[k] = days.get(k, 0) + p.amount
    live = [i for i in invs if i.status != InvoiceStatus.cancelled]
    return {
        "start": sd.isoformat(), "end": ed.isoformat(),
        "received": sum(p.amount for p in pays if p.status != PaymentStatus.refunded),
        "payments": len(pays),
        "unmatched": sum(1 for p in pays if p.status == PaymentStatus.unmatched),
        "invoiced": sum(i.amount for i in live), "invoices": len(live),
        "paid_invoices": sum(1 for i in live if i.status == InvoiceStatus.paid),
        "outstanding_from_period": sum(i.balance for i in live if i.status in OPEN),
        "daily": [{"date": k, "amount": v} for k, v in sorted(days.items())],
    }


@router.get("/reports/export.csv")
def export_csv(start: date | None = None, end: date | None = None, kind: str = "payments", ctx: Ctx = Depends(owner)):
    s, e, sd, ed = _range(start, end)
    buf = io.StringIO()
    w = csv.writer(buf)
    if kind == "invoices":
        w.writerow(["Invoice", "Created", "Customer", "Phone", "Description", "Amount (NGN)", "Paid (NGN)", "Balance (NGN)",
                    "Status", "Account number", "Bank"])
        rows = ctx.db.scalars(select(Invoice).where(Invoice.merchant_id == ctx.mid, Invoice.created_at >= s,
                                                    Invoice.created_at < e).order_by(Invoice.created_at)).all()
        for i in rows:
            va = i.virtual_account
            w.writerow([i.ref, aware(i.created_at).astimezone(LAGOS).strftime("%Y-%m-%d %H:%M"), i.customer.name if i.customer else "",
                        i.customer.phone if i.customer else "", i.description, kobo_to_naira(i.amount),
                        kobo_to_naira(i.amount_paid), kobo_to_naira(i.balance), i.status.value,
                        va.account_number if va else "", va.bank_name if va else ""])
    else:
        w.writerow(["Paid at", "Reference", "Payer", "Amount (NGN)", "Invoice", "Customer", "Status", "Note", "Source"])
        rows = ctx.db.scalars(select(Payment).where(Payment.merchant_id == ctx.mid, Payment.paid_at >= s,
                                                    Payment.paid_at < e).order_by(Payment.paid_at)).all()
        for p in rows:
            w.writerow([aware(p.paid_at).astimezone(LAGOS).strftime("%Y-%m-%d %H:%M"), p.provider_reference, p.payer_name or "",
                        kobo_to_naira(p.amount), p.invoice.ref if p.invoice else "", p.customer.name if p.customer else "",
                        p.status.value, p.unmatched_reason or "", p.source])
    name = f"{kind}-{sd.isoformat()}-to-{ed.isoformat()}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})
