"""The matching engine.

Input: a transaction ALREADY verified with the provider (never a webhook body).
Output: a Payment, ledger entries, updated invoice/customer, queued WhatsApp messages.

exact amount                 -> invoice paid
less than the balance        -> part-paid, customer gets a balance notice
several payments             -> accumulate until paid
more than the balance        -> paid, excess flagged (seller chooses refund or credit)
permanent (customer) account -> oldest open invoices first, the rest held as credit
cancelled invoice / unknown  -> unmatched queue
same provider reference      -> ignored (duplicate)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Business
from messaging import notify

from ..models import Customer, Invoice, LedgerEntry, Payment, VirtualAccount
from ..providers import Transaction

log = logging.getLogger("landed.matching")


class ActionError(ValueError):
    pass


@dataclass
class Outcome:
    result: str  # matched | unmatched | duplicate | not_paid
    payment: Payment | None = None
    detail: str = ""


def _ledger(business, type_, amount, **kw):
    LedgerEntry.objects.create(business=business, type=type_, amount=amount, **kw)


def _refresh(inv: Invoice) -> None:
    if inv.status == Invoice.Status.CANCELLED:
        return
    if inv.amount_paid >= inv.amount:
        if inv.status != Invoice.Status.PAID:
            inv.paid_at = timezone.now()
        inv.status = Invoice.Status.PAID
    elif inv.amount_paid > 0:
        inv.status = Invoice.Status.PART_PAID
    else:
        inv.status = Invoice.Status.UNPAID


def apply_to_invoice(inv: Invoice, payment: Payment | None, amount: int, *, actor=None, notify_parties=True,
                     payer: str = "") -> int:
    """Apply `amount` kobo to `inv`. Returns the applied part; any excess is flagged on the invoice."""
    inv = Invoice.objects.select_for_update().get(pk=inv.pk)
    applied = min(amount, inv.balance)
    excess = amount - applied
    if applied:
        inv.amount_paid += applied
        _ledger(inv.business, LedgerEntry.Type.PAYMENT, applied, invoice=inv, payment=payment,
                customer=inv.customer, created_by=actor)
    if excess:
        inv.excess_amount += excess
        inv.excess_status = Invoice.Excess.PENDING
        _ledger(inv.business, LedgerEntry.Type.OVERPAYMENT, excess, invoice=inv, payment=payment,
                customer=inv.customer, note="Awaiting seller decision: refund or credit", created_by=actor)
    _refresh(inv)
    inv.save()
    if inv.status == Invoice.Status.PAID:
        inv.accounts.filter(kind=VirtualAccount.Kind.TRANSFER, active=True).update(active=False)
    if payment is not None:
        payment.invoice = payment.invoice or inv
        payment.customer = payment.customer or inv.customer
        payment.status, payment.unmatched_reason = Payment.Status.MATCHED, ""
        payment.save()
    if notify_parties:
        notify.payment_applied(inv, amount, payer or (payment.payer_name if payment else ""))
    return applied


def apply_to_customer(customer: Customer, payment: Payment | None, amount: int, *, actor=None) -> None:
    remaining = amount
    for inv in customer.invoices.filter(status__in=Invoice.OPEN).order_by("created_at", "id"):
        if remaining <= 0:
            break
        part = min(remaining, inv.balance)
        apply_to_invoice(inv, payment, part, actor=actor, payer=payment.payer_name if payment else customer.name)
        remaining -= part
    if remaining > 0:
        Customer.objects.filter(pk=customer.pk).update(credit_balance=customer.credit_balance + remaining)
        customer.refresh_from_db()
        _ledger(customer.business, LedgerEntry.Type.CREDIT, remaining, payment=payment, customer=customer,
                note="Held as credit", created_by=actor)
        notify.credit_received(customer.business, customer, remaining,
                               "It is held as credit for your next order." if remaining == amount
                               else "The extra is held as credit for your next order.")
    if payment is not None:
        payment.customer = customer
        payment.status, payment.unmatched_reason = Payment.Status.MATCHED, ""
        payment.save()


def apply_credit(inv: Invoice, actor=None) -> int:
    c = inv.customer
    if not c or c.credit_balance <= 0 or inv.balance <= 0:
        return 0
    use = min(c.credit_balance, inv.balance)
    c.credit_balance -= use
    c.save(update_fields=["credit_balance"])
    inv.amount_paid += use
    _refresh(inv)
    inv.save()
    _ledger(inv.business, LedgerEntry.Type.CREDIT_APPLIED, use, invoice=inv, customer=c, note="Customer credit used",
            created_by=actor)
    return use


def _unmatched(payment: Payment, reason: str) -> None:
    payment.status, payment.unmatched_reason = Payment.Status.UNMATCHED, reason
    payment.save()
    _ledger(payment.business, LedgerEntry.Type.UNMATCHED, payment.amount, payment=payment, note=reason)
    notify.unmatched(payment.business, payment.amount, payment.payer_name)


def _find(tx: Transaction) -> tuple[VirtualAccount | None, Business | None]:
    va = None
    if tx.reference:
        va = VirtualAccount.objects.filter(kind=VirtualAccount.Kind.TRANSFER, provider_reference=tx.reference).first()
    if not va and tx.account_number:
        va = VirtualAccount.objects.filter(account_number=tx.account_number).order_by("-created_at").first()
    business = va.business if va else None
    if not business and tx.subaccount_code:
        business = Business.objects.filter(subaccount_code=tx.subaccount_code).first()
    return va, business


@transaction.atomic
def process(tx: Transaction, provider: str, source: str = "webhook") -> Outcome:
    if not tx.is_success or tx.amount <= 0:
        return Outcome("not_paid", detail=f"Provider status: {tx.status or 'unknown'}")
    if Payment.objects.filter(reference=tx.reference).exists():
        return Outcome("duplicate", detail="Already processed")

    va, business = _find(tx)
    try:
        with transaction.atomic():
            payment = Payment.objects.create(
                business=business, provider=provider, reference=tx.reference, amount=tx.amount, fees=tx.fees,
                channel=tx.channel, payer_name=tx.payer_name[:160], payer_bank=tx.payer_bank[:80], account=va,
                account_number=tx.account_number, status=Payment.Status.UNMATCHED, source=source,
                paid_at=tx.paid_at or timezone.now(),
            )
    except IntegrityError:
        return Outcome("duplicate", detail="Already processed (race)")

    if va is not None and va.kind == VirtualAccount.Kind.TRANSFER and va.active:
        VirtualAccount.objects.filter(pk=va.pk).update(active=False)  # one-time accounts take one payment

    if va is None:
        _unmatched(payment, "Account not recognised")
    elif va.kind == VirtualAccount.Kind.TRANSFER:
        inv = va.invoice
        if inv is None:
            _unmatched(payment, "One-time account has no invoice")
        elif inv.status == Invoice.Status.CANCELLED:
            _unmatched(payment, f"{inv.ref} was cancelled")
        else:
            apply_to_invoice(inv, payment, tx.amount)
    else:
        if va.customer is None:
            _unmatched(payment, "Account has no customer")
        else:
            apply_to_customer(va.customer, payment, tx.amount)
    return Outcome(payment.status, payment)


# ---------- seller actions ----------

@transaction.atomic
def assign(payment: Payment, business: Business, *, invoice: Invoice | None = None, customer: Customer | None = None,
           actor=None) -> None:
    if payment.business_id != business.id or payment.status != Payment.Status.UNMATCHED:
        raise ActionError("This payment is not waiting to be assigned")
    if (invoice is None) == (customer is None):
        raise ActionError("Choose an invoice or a customer")
    _ledger(business, LedgerEntry.Type.ASSIGNMENT, payment.amount, payment=payment, invoice=invoice,
            customer=customer, note="Assigned manually", created_by=actor)
    if invoice is not None:
        if invoice.business_id != business.id or invoice.status == Invoice.Status.CANCELLED:
            raise ActionError("That invoice can't take payments")
        apply_to_invoice(invoice, payment, payment.amount, actor=actor)
    else:
        if customer.business_id != business.id:
            raise ActionError("Customer not found")
        apply_to_customer(customer, payment, payment.amount, actor=actor)


@transaction.atomic
def resolve_excess(inv: Invoice, decision: str, actor=None) -> None:
    if inv.excess_status != Invoice.Excess.PENDING or inv.excess_amount <= 0:
        raise ActionError("No overpayment waiting on this invoice")
    if decision == "refund":
        _ledger(inv.business, LedgerEntry.Type.REFUND, inv.excess_amount, invoice=inv, customer=inv.customer,
                note="Extra refunded by seller", created_by=actor)
        inv.excess_status = Invoice.Excess.REFUNDED
    elif decision == "credit":
        if not inv.customer:
            raise ActionError("This invoice has no customer to hold credit for")
        inv.customer.credit_balance += inv.excess_amount
        inv.customer.save(update_fields=["credit_balance"])
        _ledger(inv.business, LedgerEntry.Type.CREDIT, inv.excess_amount, invoice=inv, customer=inv.customer,
                note="Extra kept as customer credit", created_by=actor)
        inv.excess_status = Invoice.Excess.CREDITED
    else:
        raise ActionError("Choose refund or credit")
    inv.save(update_fields=["excess_status"])


@transaction.atomic
def refund_unmatched(payment: Payment, business: Business, actor=None) -> None:
    if payment.business_id != business.id or payment.status != Payment.Status.UNMATCHED:
        raise ActionError("This payment is not waiting to be assigned")
    _ledger(business, LedgerEntry.Type.REFUND, payment.amount, payment=payment, note="Sent back to the payer",
            created_by=actor)
    payment.status = Payment.Status.REFUNDED
    payment.save(update_fields=["status"])
