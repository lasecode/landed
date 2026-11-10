"""Data model (brief section 8.1), plus the few extra columns a working system needs.

All money is stored as integer kobo (₦1 = 100 kobo) to avoid float rounding.
"""
from __future__ import annotations

import enum
import secrets
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; treat naive values from the DB as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def new_token(n: int = 18) -> str:
    return secrets.token_urlsafe(n)


class Role(str, enum.Enum):
    owner = "owner"
    staff = "staff"


class InvoiceStatus(str, enum.Enum):
    unpaid = "unpaid"
    part_paid = "part_paid"
    paid = "paid"
    expired = "expired"
    cancelled = "cancelled"


class ExcessStatus(str, enum.Enum):
    none = "none"
    pending = "pending"      # overpaid; merchant must choose
    refunded = "refunded"
    credited = "credited"


class AccountType(str, enum.Enum):
    static = "static"
    dynamic = "dynamic"


class PaymentStatus(str, enum.Enum):
    matched = "matched"
    unmatched = "unmatched"
    refunded = "refunded"  # unmatched money the merchant sent back


class LedgerType(str, enum.Enum):
    payment = "payment"                # money applied to an invoice
    overpayment = "overpayment"        # excess received above invoice amount (awaiting decision)
    refund = "refund"                  # merchant refunded excess / payment
    credit = "credit"                  # money held as customer credit
    credit_applied = "credit_applied"  # customer credit used on an invoice
    unmatched = "unmatched"            # money received but not yet assigned
    assignment = "assignment"          # unmatched money assigned by merchant


TZ = DateTime(timezone=True)


class Merchant(Base):
    __tablename__ = "merchants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(160))
    provider: Mapped[str] = mapped_column(String(20), default="mock")
    provider_keys: Mapped[str | None] = mapped_column(Text)  # Fernet-encrypted JSON, never logged
    webhook_token: Mapped[str] = mapped_column(String(40), unique=True, default=new_token)
    plan: Mapped[str] = mapped_column(String(20), default="free")
    alert_phone: Mapped[str | None] = mapped_column(String(20))
    # comma separated days after invoice creation to send reminders, e.g. "1,3,7"
    reminder_days: Mapped[str] = mapped_column(String(60), default="2,5")
    invoice_expiry_days: Mapped[int] = mapped_column(Integer, default=7)
    next_invoice_number: Mapped[int] = mapped_column(Integer, default=101)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    staff: Mapped[list["StaffUser"]] = relationship(back_populates="merchant")


class StaffUser(Base):
    __tablename__ = "staff_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20), unique=True)  # identifies the merchant on WhatsApp
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False), default=Role.staff)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    merchant: Mapped[Merchant] = relationship(back_populates="staff")


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(160))
    static_account_id: Mapped[int | None] = mapped_column(ForeignKey("virtual_accounts.id", use_alter=True))
    credit_balance: Mapped[int] = mapped_column(BigInteger, default=0)  # kobo; mirrors ledger credit entries
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    static_account: Mapped["VirtualAccount | None"] = relationship(foreign_keys=[static_account_id], post_update=True)


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("merchant_id", "number"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    amount_paid: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[InvoiceStatus] = mapped_column(Enum(InvoiceStatus, native_enum=False), default=InvoiceStatus.unpaid, index=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    due_date: Mapped[datetime | None] = mapped_column(TZ)
    virtual_account_id: Mapped[int | None] = mapped_column(ForeignKey("virtual_accounts.id", use_alter=True))
    excess_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    excess_status: Mapped[ExcessStatus] = mapped_column(Enum(ExcessStatus, native_enum=False), default=ExcessStatus.none)
    public_token: Mapped[str] = mapped_column(String(40), unique=True, default=new_token)
    reminders_sent: Mapped[int] = mapped_column(Integer, default=0)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("staff_users.id"))
    paid_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, index=True)

    customer: Mapped[Customer | None] = relationship(foreign_keys=[customer_id])
    virtual_account: Mapped["VirtualAccount | None"] = relationship(foreign_keys=[virtual_account_id], post_update=True)

    @property
    def ref(self) -> str:
        return f"INV-{self.number}"

    @property
    def balance(self) -> int:
        return max(self.amount - self.amount_paid, 0)


class VirtualAccount(Base):
    __tablename__ = "virtual_accounts"
    __table_args__ = (Index("ix_va_merchant_account", "merchant_id", "account_number"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"), index=True)
    account_number: Mapped[str] = mapped_column(String(20))
    account_name: Mapped[str | None] = mapped_column(String(120))
    bank_name: Mapped[str] = mapped_column(String(80))
    type: Mapped[AccountType] = mapped_column(Enum(AccountType, native_enum=False))
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    expires_at: Mapped[datetime | None] = mapped_column(TZ)
    provider_ref: Mapped[str | None] = mapped_column(String(120), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    provider_reference: Mapped[str] = mapped_column(String(120), unique=True)  # idempotency key
    amount: Mapped[int] = mapped_column(BigInteger)
    payer_name: Mapped[str | None] = mapped_column(String(160))
    payer_bank: Mapped[str | None] = mapped_column(String(80))
    virtual_account_id: Mapped[int | None] = mapped_column(ForeignKey("virtual_accounts.id"))
    account_number: Mapped[str | None] = mapped_column(String(20))
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus, native_enum=False), index=True)
    unmatched_reason: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(20), default="webhook")  # webhook | reconciliation | manual
    paid_at: Mapped[datetime] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    invoice: Mapped[Invoice | None] = relationship(foreign_keys=[invoice_id])
    customer: Mapped[Customer | None] = relationship(foreign_keys=[customer_id])


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"), index=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), index=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    type: Mapped[LedgerType] = mapped_column(Enum(LedgerType, native_enum=False))
    amount: Mapped[int] = mapped_column(BigInteger)  # always positive; meaning comes from type
    note: Mapped[str | None] = mapped_column(String(200))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("staff_users.id"))
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, index=True)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"), index=True)
    recipient_phone: Mapped[str] = mapped_column(String(20))
    template: Mapped[str] = mapped_column(String(60))  # template name, or "text" for free-form replies
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | sent | failed | skipped
    error: Mapped[str | None] = mapped_column(Text)
    wa_message_id: Mapped[str | None] = mapped_column(String(120))
    sent_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class WebhookEvent(Base):
    """Every inbound provider webhook, for the admin monitor and for replays."""
    __tablename__ = "webhook_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    merchant_id: Mapped[int | None] = mapped_column(ForeignKey("merchants.id"), index=True)
    reference: Mapped[str | None] = mapped_column(String(120), index=True)
    event_type: Mapped[str | None] = mapped_column(String(60))
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|processed|duplicate|ignored|failed|rejected
    detail: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(TZ)


class WhatsAppContact(Base):
    """Tracks the last inbound message per phone, for the 24-hour free-form window."""
    __tablename__ = "whatsapp_contacts"
    phone: Mapped[str] = mapped_column(String(20), primary_key=True)
    last_inbound_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"), index=True)
    window_from: Mapped[datetime] = mapped_column(TZ)
    window_to: Mapped[datetime] = mapped_column(TZ)
    checked: Mapped[int] = mapped_column(Integer, default=0)
    recovered: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class MockTransaction(Base):
    """Backs the mock provider so the full flow works without real provider keys (dev/demo only)."""
    __tablename__ = "mock_transactions"
    reference: Mapped[str] = mapped_column(String(120), primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"), index=True)
    account_number: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(BigInteger)
    payer_name: Mapped[str | None] = mapped_column(String(160))
    payer_bank: Mapped[str | None] = mapped_column(String(80))
    paid_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
