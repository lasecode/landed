"""The matching engine (brief section 7).

Input: a payment that has ALREADY been verified with the provider's verify endpoint.
Output: a Payment row, ledger entries, updated invoice/customer state, queued notifications.

Scenario                         -> behaviour
exact amount                     -> invoice paid
underpayment                     -> invoice part_paid, customer gets balance notice
several payments on one invoice  -> accumulate until paid
overpayment                      -> invoice paid, excess flagged (merchant chooses refund or credit)
payment to static account        -> oldest open invoices of that customer first, rest held as credit
expired / cancelled / unknown    -> unmatched queue
duplicate provider reference     -> ignored
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AccountType,
    Customer,
    ExcessStatus,
    Invoice,
    InvoiceStatus,
    LedgerEntry,
    LedgerType,
    Merchant,
    Payment,
    PaymentStatus,
    VirtualAccount,
    utcnow,
)
from ..providers.base import TransactionResult
from . import notify

log = logging.getLogger("landed.matching")

OPEN_STATUSES = (InvoiceStatus.unpaid, InvoiceStatus.part_paid)


@dataclass
class MatchOutcome:
    result: str  # matched | unmatched | duplicate | not_paid
    payment: Payment | None = None
    detail: str = ""


def _ledger(db: Session, merchant_id: int, type_: LedgerType, amount: int, *, invoice_id=None, payment_id=None,
            customer_id=None, note=None, actor_id=None) -> None:
    db.add(LedgerEntry(merchant_id=merchant_id, type=type_, amount=amount, invoice_id=invoice_id, payment_id=payment_id,
                       customer_id=customer_id, note=note, created_by_id=actor_id))


def _refresh_status(inv: Invoice) -> None:
    if inv.amount_paid >= inv.amount:
        if inv.status != InvoiceStatus.paid:
            inv.paid_at = utcnow()
        inv.status = InvoiceStatus.paid
    elif inv.amount_paid > 0:
        inv.status = InvoiceStatus.part_paid
    # unpaid stays unpaid


def apply_to_invoice(db: Session, merchant: Merchant, inv: Invoice, payment: Payment | None, amount: int, *,
                     actor_id: int | None = None, notify_parties: bool = True, payer: str | None = None) -> int:
    """Apply `amount` kobo to an invoice. Returns the applied amount. Excess is flagged on the invoice."""
    applied = min(amount, inv.balance)
    excess = amount - applied
    pid = payment.id if payment else None
    if applied > 0:
        inv.amount_paid += applied
        _ledger(db, merchant.id, LedgerType.payment, applied, invoice_id=inv.id, payment_id=pid,
                customer_id=inv.customer_id, actor_id=actor_id)
    if excess > 0:
        inv.excess_amount += excess
        inv.excess_status = ExcessStatus.pending
        _ledger(db, merchant.id, LedgerType.overpayment, excess, invoice_id=inv.id, payment_id=pid,
                customer_id=inv.customer_id, note="Awaiting merchant decision: refund or credit", actor_id=actor_id)
    _refresh_status(inv)
    if inv.status == InvoiceStatus.paid and inv.virtual_account and inv.virtual_account.type == AccountType.dynamic:
        inv.virtual_account.active = False
    if payment is not None:
        payment.invoice_id = payment.invoice_id or inv.id
        payment.customer_id = payment.customer_id or inv.customer_id
        payment.status = PaymentStatus.matched
        payment.unmatched_reason = None
    if notify_parties:
        notify.payment_applied(db, merchant, inv, amount, payer or (payment.payer_name if payment else None))
    return applied


def apply_to_customer(db: Session, merchant: Merchant, customer: Customer, payment: Payment | None, amount: int, *,
                      actor_id: int | None = None) -> None:
    """Static account payment: oldest open invoices first, remainder held as customer credit."""
    remaining = amount
    open_invoices = db.scalars(
        select(Invoice)
        .where(Invoice.merchant_id == merchant.id, Invoice.customer_id == customer.id, Invoice.status.in_(OPEN_STATUSES))
        .order_by(Invoice.created_at, Invoice.id)
    ).all()
    for inv in open_invoices:
        if remaining <= 0:
            break
        part = min(remaining, inv.balance)
        apply_to_invoice(db, merchant, inv, payment, part, actor_id=actor_id,
                         payer=payment.payer_name if payment else customer.name)
        remaining -= part
    if remaining > 0:
        customer.credit_balance += remaining
        _ledger(db, merchant.id, LedgerType.credit, remaining, payment_id=payment.id if payment else None,
                customer_id=customer.id, note="Held as credit", actor_id=actor_id)
        notify.credit_received(db, merchant, customer, remaining,
                               f"{'It' if remaining == amount else 'The extra'} is held as credit for your next order.")
    if payment is not None:
        payment.customer_id = customer.id
        payment.status = PaymentStatus.matched
        payment.unmatched_reason = None


def apply_credit_to_invoice(db: Session, merchant: Merchant, inv: Invoice, actor_id: int | None = None) -> int:
    """Use a customer's held credit on a new invoice. Returns kobo used."""
    c = inv.customer
    if not c or c.credit_balance <= 0 or inv.balance <= 0:
        return 0
    use = min(c.credit_balance, inv.balance)
    c.credit_balance -= use
    inv.amount_paid += use
    _ledger(db, merchant.id, LedgerType.credit_applied, use, invoice_id=inv.id, customer_id=c.id,
            note="Customer credit used", actor_id=actor_id)
    _refresh_status(inv)
    return use


def _mark_unmatched(db: Session, merchant: Merchant, payment: Payment, reason: str) -> None:
    payment.status = PaymentStatus.unmatched
    payment.unmatched_reason = reason
    _ledger(db, merchant.id, LedgerType.unmatched, payment.amount, payment_id=payment.id, note=reason)
    notify.unmatched(db, merchant, payment.amount, payment.payer_name)


def _find_account(db: Session, merchant_id: int, tx: TransactionResult) -> VirtualAccount | None:
    if tx.account_number:
        va = db.scalar(select(VirtualAccount).where(VirtualAccount.merchant_id == merchant_id,
                                                    VirtualAccount.account_number == tx.account_number)
                       .order_by(VirtualAccount.id.desc()))
        if va:
            return va
    if tx.product_reference:
        return db.scalar(select(VirtualAccount).where(VirtualAccount.merchant_id == merchant_id,
                                                      VirtualAccount.provider_ref == tx.product_reference))
    return None


def process_verified_payment(db: Session, merchant: Merchant, tx: TransactionResult, provider_name: str,
                             source: str = "webhook") -> MatchOutcome:
    """Caller commits. `tx` must come from provider.verify_transaction(), never from a webhook body."""
    if not tx.is_paid or tx.amount <= 0:
        return MatchOutcome("not_paid", detail=f"Provider status: {tx.status}")

    if db.scalar(select(Payment.id).where(Payment.provider_reference == tx.reference)):
        return MatchOutcome("duplicate", detail="Already processed")

    va = _find_account(db, merchant.id, tx)
    payment = Payment(
        merchant_id=merchant.id, provider=provider_name, provider_reference=tx.reference, amount=tx.amount,
        payer_name=tx.payer_name, payer_bank=tx.payer_bank, virtual_account_id=va.id if va else None,
        account_number=tx.account_number, status=PaymentStatus.unmatched, source=source,
        paid_at=tx.paid_at or utcnow(),
    )
    try:
        with db.begin_nested():
            db.add(payment)
            db.flush()
    except IntegrityError:  # raced with another worker on the same reference
        return MatchOutcome("duplicate", detail="Already processed (race)")

    if va is None:
        _mark_unmatched(db, merchant, payment, "Account not recognised")
    elif va.type == AccountType.dynamic:
        inv = db.get(Invoice, va.invoice_id) if va.invoice_id else None
        if inv is None or inv.merchant_id != merchant.id:
            _mark_unmatched(db, merchant, payment, "Account has no invoice")
        elif inv.status == InvoiceStatus.cancelled:
            _mark_unmatched(db, merchant, payment, f"{inv.ref} was cancelled")
        elif inv.status == InvoiceStatus.expired:
            _mark_unmatched(db, merchant, payment, f"{inv.ref} had expired")
        else:
            apply_to_invoice(db, merchant, inv, payment, tx.amount)
    else:
        cust = db.get(Customer, va.customer_id) if va.customer_id else None
        if cust is None or cust.merchant_id != merchant.id:
            _mark_unmatched(db, merchant, payment, "Static account has no customer")
        else:
            apply_to_customer(db, merchant, cust, payment, tx.amount)

    return MatchOutcome(payment.status.value, payment)


# ---------- merchant actions ----------

class ActionError(ValueError):
    pass


def assign_unmatched(db: Session, merchant: Merchant, payment: Payment, *, invoice: Invoice | None = None,
                     customer: Customer | None = None, actor_id: int | None = None) -> None:
    if payment.merchant_id != merchant.id or payment.status != PaymentStatus.unmatched:
        raise ActionError("Payment is not in the unmatched queue")
    if (invoice is None) == (customer is None):
        raise ActionError("Choose an invoice or a customer")
    _ledger(db, merchant.id, LedgerType.assignment, payment.amount, payment_id=payment.id,
            invoice_id=invoice.id if invoice else None, customer_id=customer.id if customer else None,
            note="Assigned manually", actor_id=actor_id)
    if invoice is not None:
        if invoice.merchant_id != merchant.id or invoice.status == InvoiceStatus.cancelled:
            raise ActionError("Invoice can't take payments")
        if invoice.status == InvoiceStatus.expired:
            invoice.status = InvoiceStatus.unpaid  # money arrived; reopen before applying
            _refresh_status(invoice)
        apply_to_invoice(db, merchant, invoice, payment, payment.amount, actor_id=actor_id)
    else:
        if customer.merchant_id != merchant.id:
            raise ActionError("Customer not found")
        apply_to_customer(db, merchant, customer, payment, payment.amount, actor_id=actor_id)


def resolve_excess(db: Session, merchant: Merchant, inv: Invoice, decision: str, actor_id: int | None = None) -> None:
    if inv.excess_status != ExcessStatus.pending or inv.excess_amount <= 0:
        raise ActionError("No overpayment waiting on this invoice")
    if decision == "refund":
        _ledger(db, merchant.id, LedgerType.refund, inv.excess_amount, invoice_id=inv.id, customer_id=inv.customer_id,
                note="Excess refunded by merchant", actor_id=actor_id)
        inv.excess_status = ExcessStatus.refunded
    elif decision == "credit":
        if not inv.customer:
            raise ActionError("Invoice has no customer to hold credit for")
        inv.customer.credit_balance += inv.excess_amount
        _ledger(db, merchant.id, LedgerType.credit, inv.excess_amount, invoice_id=inv.id, customer_id=inv.customer_id,
                note="Excess held as customer credit", actor_id=actor_id)
        inv.excess_status = ExcessStatus.credited
    else:
        raise ActionError("Decision must be refund or credit")


def refund_unmatched(db: Session, merchant: Merchant, payment: Payment, actor_id: int | None = None) -> None:
    """Merchant sent the money back to the payer outside the system; record it."""
    if payment.merchant_id != merchant.id or payment.status != PaymentStatus.unmatched:
        raise ActionError("Payment is not in the unmatched queue")
    _ledger(db, merchant.id, LedgerType.refund, payment.amount, payment_id=payment.id, note="Refunded to sender",
            actor_id=actor_id)
    payment.status = PaymentStatus.refunded
