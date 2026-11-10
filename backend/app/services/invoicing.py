"""Creating invoices and customer accounts through the provider."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    AccountType,
    Customer,
    Invoice,
    InvoiceStatus,
    Merchant,
    VirtualAccount,
    utcnow,
)
from ..providers import AccountRequest, ProviderError, get_provider
from . import notify
from .matching import apply_credit_to_invoice

PLAN_CAPS = {"free": settings.free_plan_invoice_cap, "starter": 500, "growth": 3000, "pro": None}


class InvoiceError(ValueError):
    pass


def _placeholder_email(merchant: Merchant, key: str) -> str:
    # Providers require an email; most customers here don't have one.
    return f"{key}@m{merchant.id}.customers.landed.ng"


def monthly_invoice_count(db: Session, merchant_id: int) -> int:
    now = utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(func.count(Invoice.id)).where(Invoice.merchant_id == merchant_id, Invoice.created_at >= start)) or 0


def find_or_create_customer(db: Session, merchant: Merchant, name: str, phone: str | None = None) -> Customer:
    from ..security import normalize_phone

    phone = normalize_phone(phone)
    q = select(Customer).where(Customer.merchant_id == merchant.id)
    if phone:
        c = db.scalar(q.where(Customer.phone == phone))
        if c:
            return c
    c = db.scalar(q.where(func.lower(Customer.name) == name.strip().lower()))
    if not c:
        # "Tunde" should find "Tunde Bello" when he's the only Tunde
        similar = db.scalars(q.where(func.lower(Customer.name).like(name.strip().lower() + " %")).limit(2)).all()
        if len(similar) == 1:
            c = similar[0]
    if c:
        if phone and not c.phone:
            c.phone = phone
        return c
    c = Customer(merchant_id=merchant.id, name=name.strip(), phone=phone)
    db.add(c)
    db.flush()
    return c


def create_invoice(db: Session, merchant: Merchant, *, amount: int, customer: Customer | None, description: str = "",
                   due_date: datetime | None = None, created_by_id: int | None = None, send: bool = True) -> Invoice:
    if amount < 100 * 100:
        raise InvoiceError("Minimum invoice amount is ₦100")
    cap = PLAN_CAPS.get(merchant.plan)
    if cap is not None and monthly_invoice_count(db, merchant.id) >= cap:
        raise InvoiceError(f"Your {merchant.plan} plan allows {cap} invoices a month. Upgrade to create more.")

    # allocate a per-merchant number under a row lock (Postgres) so two staff can't clash
    locked = db.scalar(select(Merchant).where(Merchant.id == merchant.id).with_for_update())
    number = locked.next_invoice_number
    locked.next_invoice_number = number + 1

    inv = Invoice(merchant_id=merchant.id, number=number, customer_id=customer.id if customer else None, amount=amount,
                  description=description.strip()[:300], due_date=due_date, created_by_id=created_by_id)
    db.add(inv)
    db.flush()

    apply_credit_to_invoice(db, merchant, inv, actor_id=created_by_id)

    if inv.balance > 0:
        if customer and customer.static_account_id:
            inv.virtual_account_id = customer.static_account_id  # regular customer pays into their own account
        else:
            expires = utcnow() + timedelta(days=merchant.invoice_expiry_days or settings.default_invoice_expiry_days)
            if due_date:
                expires = max(expires, due_date if due_date.tzinfo else due_date.replace(tzinfo=timezone.utc))
            req = AccountRequest(
                reference=f"m{merchant.id}-inv{number}-{inv.public_token[:6]}",
                customer_name=customer.name if customer else merchant.business_name,
                customer_email=(customer.email if customer and customer.email else _placeholder_email(merchant, f"inv{number}")),
                description=f"{merchant.business_name[:40]} {inv.ref}",
                amount=inv.balance,
                expires_at=expires,
            )
            try:
                acc = get_provider(merchant, db).create_dynamic_account(req)
            except ProviderError as e:
                raise InvoiceError(f"Couldn't get an account number: {e}") from None
            va = VirtualAccount(merchant_id=merchant.id, account_number=acc.account_number, account_name=acc.account_name,
                                bank_name=acc.bank_name, type=AccountType.dynamic, invoice_id=inv.id,
                                customer_id=customer.id if customer else None, expires_at=acc.expires_at,
                                provider_ref=acc.provider_ref)
            db.add(va)
            db.flush()
            inv.virtual_account_id = va.id
    db.flush()
    db.refresh(inv)
    if send:
        notify.invoice_created(db, merchant, inv)
    return inv


def cancel_invoice(db: Session, merchant: Merchant, inv: Invoice) -> None:
    if inv.status not in (InvoiceStatus.unpaid, InvoiceStatus.expired):
        raise InvoiceError("Only invoices with no payments can be cancelled")
    inv.status = InvoiceStatus.cancelled
    va = inv.virtual_account
    if va and va.type == AccountType.dynamic:
        va.active = False
        if va.provider_ref:
            try:
                get_provider(merchant, db).cancel_dynamic_account(va.provider_ref)
            except ProviderError:
                pass


def assign_static_account(db: Session, merchant: Merchant, customer: Customer, bvn: str | None = None,
                          nin: str | None = None) -> VirtualAccount:
    if customer.static_account_id:
        return customer.static_account
    req = AccountRequest(
        reference=f"m{merchant.id}-cus{customer.id}",
        customer_name=customer.name,
        customer_email=customer.email or _placeholder_email(merchant, f"cus{customer.id}"),
        bvn=bvn, nin=nin,
    )
    try:
        acc = get_provider(merchant, db).create_static_account(req)
    except ProviderError as e:
        raise InvoiceError(f"Couldn't create a dedicated account: {e}") from None
    va = VirtualAccount(merchant_id=merchant.id, account_number=acc.account_number, account_name=acc.account_name,
                        bank_name=acc.bank_name, type=AccountType.static, customer_id=customer.id,
                        provider_ref=acc.provider_ref)
    db.add(va)
    db.flush()
    customer.static_account_id = va.id
    # open invoices on a dynamic account keep theirs; new invoices will use the static one
    if customer.phone:
        notify.queue(db, merchant.id, customer.phone, "static_account_assigned",
                     [customer.name.split()[0], merchant.business_name, acc.account_number, acc.bank_name])
    return va


def expire_due_invoices(db: Session, now: datetime | None = None) -> int:
    """Unpaid invoices whose dynamic account has expired become 'expired'. Part-paid ones stay open."""
    now = now or utcnow()
    rows = db.execute(
        select(Invoice, VirtualAccount)
        .join(VirtualAccount, VirtualAccount.id == Invoice.virtual_account_id)
        .where(Invoice.status == InvoiceStatus.unpaid, VirtualAccount.type == AccountType.dynamic,
               VirtualAccount.expires_at.is_not(None), VirtualAccount.expires_at < now)
    ).all()
    for inv, va in rows:
        inv.status = InvoiceStatus.expired
        va.active = False
    return len(rows)
