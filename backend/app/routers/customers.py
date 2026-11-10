from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from .. import serialize as S
from ..deps import Ctx, current
from ..models import Customer, Invoice, InvoiceStatus, Payment
from ..schemas import CustomerIn, StaticAccountIn
from ..security import normalize_phone
from ..services.invoicing import InvoiceError, assign_static_account

router = APIRouter(prefix="/api/customers", tags=["customers"])
OPEN = (InvoiceStatus.unpaid, InvoiceStatus.part_paid)


def _get(ctx: Ctx, cid: int) -> Customer:
    c = ctx.db.get(Customer, cid)
    if not c or c.merchant_id != ctx.mid:
        raise HTTPException(404, "Customer not found")
    return c


def _owed_map(ctx: Ctx) -> dict[int, tuple[int, int]]:
    rows = ctx.db.execute(
        select(Invoice.customer_id, func.sum(Invoice.amount - Invoice.amount_paid), func.count(Invoice.id))
        .where(Invoice.merchant_id == ctx.mid, Invoice.status.in_(OPEN), Invoice.customer_id.is_not(None))
        .group_by(Invoice.customer_id)
    ).all()
    return {cid: (int(owed or 0), int(n)) for cid, owed, n in rows}


@router.get("")
def list_customers(q: str | None = None, ctx: Ctx = Depends(current)):
    stmt = select(Customer).where(Customer.merchant_id == ctx.mid)
    if q:
        stmt = stmt.where(Customer.name.ilike(f"%{q.strip()}%"))
    owed = _owed_map(ctx)
    rows = ctx.db.scalars(stmt.order_by(Customer.name)).all()
    return [S.customer(c, *owed.get(c.id, (0, 0))) for c in rows]


@router.post("")
def create(body: CustomerIn, ctx: Ctx = Depends(current)):
    phone = normalize_phone(body.phone)
    if phone and ctx.db.scalar(select(Customer).where(Customer.merchant_id == ctx.mid, Customer.phone == phone)):
        raise HTTPException(409, "A customer with that number already exists")
    c = Customer(merchant_id=ctx.mid, name=body.name.strip(), phone=phone, email=body.email)
    ctx.db.add(c)
    ctx.db.commit()
    return S.customer(c)


@router.patch("/{cid}")
def update(cid: int, body: CustomerIn, ctx: Ctx = Depends(current)):
    c = _get(ctx, cid)
    c.name, c.phone, c.email = body.name.strip(), normalize_phone(body.phone), body.email
    ctx.db.commit()
    return S.customer(c)


@router.get("/{cid}")
def detail(cid: int, ctx: Ctx = Depends(current)):
    c = _get(ctx, cid)
    invs = ctx.db.scalars(select(Invoice).where(Invoice.merchant_id == ctx.mid, Invoice.customer_id == c.id)
                          .order_by(Invoice.id.desc())).all()
    pays = ctx.db.scalars(select(Payment).where(Payment.merchant_id == ctx.mid, Payment.customer_id == c.id)
                          .order_by(Payment.paid_at.desc())).all()
    owed = sum(i.balance for i in invs if i.status in OPEN)
    return {**S.customer(c, owed, sum(1 for i in invs if i.status in OPEN)),
            "invoices": [S.invoice(i) for i in invs], "payments": [S.payment(p) for p in pays]}


@router.post("/{cid}/static-account")
def static_account(cid: int, body: StaticAccountIn, ctx: Ctx = Depends(current)):
    c = _get(ctx, cid)
    try:
        assign_static_account(ctx.db, ctx.merchant, c, bvn=body.bvn, nin=body.nin)  # BVN/NIN passed through, never stored
    except InvoiceError as e:
        ctx.db.rollback()
        raise HTTPException(400, str(e)) from None
    ctx.db.commit()
    return S.customer(c)
