from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select

from .. import serialize as S
from ..deps import Ctx, current
from ..models import Customer, Invoice, InvoiceStatus, LedgerEntry, Payment
from ..schemas import ExcessIn, InvoiceIn
from ..services import matching, notify
from ..services.invoicing import InvoiceError, cancel_invoice, create_invoice, find_or_create_customer

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


def _get(ctx: Ctx, invoice_id: int) -> Invoice:
    inv = ctx.db.get(Invoice, invoice_id)
    if not inv or inv.merchant_id != ctx.mid:  # scope every lookup by merchant
        raise HTTPException(404, "Invoice not found")
    return inv


@router.get("")
def list_invoices(status: str | None = None, q: str | None = None, limit: int = 200, ctx: Ctx = Depends(current)):
    stmt = select(Invoice).where(Invoice.merchant_id == ctx.mid)
    if status:
        try:
            stmt = stmt.where(Invoice.status == InvoiceStatus(status))
        except ValueError:
            raise HTTPException(400, "Unknown status") from None
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.outerjoin(Customer, Customer.id == Invoice.customer_id).where(
            or_(Customer.name.ilike(like), Invoice.description.ilike(like),
                Invoice.number == (int(q.strip().upper().replace("INV-", "")) if q.strip().upper().replace("INV-", "").isdigit() else -1)))
    rows = ctx.db.scalars(stmt.order_by(Invoice.id.desc()).limit(min(limit, 500))).all()
    return [S.invoice(i) for i in rows]


@router.post("")
def create(body: InvoiceIn, ctx: Ctx = Depends(current)):
    db = ctx.db
    customer = None
    if body.customer_id:
        customer = db.get(Customer, body.customer_id)
        if not customer or customer.merchant_id != ctx.mid:
            raise HTTPException(404, "Customer not found")
    elif body.customer_name:
        customer = find_or_create_customer(db, ctx.merchant, body.customer_name, body.customer_phone)
    try:
        amount = body.amount_kobo
    except Exception:
        raise HTTPException(400, "Enter a valid amount") from None
    try:
        inv = create_invoice(db, ctx.merchant, amount=amount, customer=customer, description=body.description,
                             due_date=body.due_date, created_by_id=ctx.user.id, send=body.send_whatsapp)
    except InvoiceError as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    db.commit()
    return S.invoice(inv)


@router.get("/{invoice_id}")
def detail(invoice_id: int, ctx: Ctx = Depends(current)):
    inv = _get(ctx, invoice_id)
    pays = ctx.db.scalars(select(Payment).where(Payment.merchant_id == ctx.mid, Payment.invoice_id == inv.id)
                          .order_by(Payment.paid_at)).all()
    entries = ctx.db.scalars(select(LedgerEntry).where(LedgerEntry.merchant_id == ctx.mid, LedgerEntry.invoice_id == inv.id)
                             .order_by(LedgerEntry.id)).all()
    return {**S.invoice(inv), "payments": [S.payment(p) for p in pays], "ledger": [S.ledger(e) for e in entries]}


@router.post("/{invoice_id}/cancel")
def cancel(invoice_id: int, ctx: Ctx = Depends(current)):
    inv = _get(ctx, invoice_id)
    try:
        cancel_invoice(ctx.db, ctx.merchant, inv)
    except InvoiceError as e:
        raise HTTPException(400, str(e)) from None
    ctx.db.commit()
    return S.invoice(inv)


@router.post("/{invoice_id}/resend")
def resend(invoice_id: int, ctx: Ctx = Depends(current)):
    inv = _get(ctx, invoice_id)
    if not (inv.customer and inv.customer.phone):
        raise HTTPException(400, "This customer has no WhatsApp number saved")
    if inv.status not in (InvoiceStatus.unpaid, InvoiceStatus.part_paid):
        raise HTTPException(400, "Only open invoices can be resent")
    notify.invoice_created(ctx.db, ctx.merchant, inv)
    ctx.db.commit()
    return {"ok": True}


@router.post("/{invoice_id}/excess")
def excess(invoice_id: int, body: ExcessIn, ctx: Ctx = Depends(current)):
    inv = _get(ctx, invoice_id)
    try:
        matching.resolve_excess(ctx.db, ctx.merchant, inv, body.decision, actor_id=ctx.user.id)
    except matching.ActionError as e:
        raise HTTPException(400, str(e)) from None
    ctx.db.commit()
    return S.invoice(inv)
