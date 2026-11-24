"""Invoices, customers, account numbers and seller payout setup."""
from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import Business, normalize_phone
from messaging import notify

from ..models import Customer, Invoice, VirtualAccount
from ..providers import ProviderError, get_provider
from .matching import apply_credit

MIN_INVOICE = 100 * 100  # ₦100


class InvoiceError(ValueError):
    pass


def placeholder_email(business: Business, key: str) -> str:
    # Paystack requires an email; most customers here don't have one.
    return f"{key}.b{business.pk}@customers.landed.ng"


def fee_percent(business: Business) -> float:
    return float(business.fee_percent if business.fee_percent is not None else settings.LANDED_FEE_PERCENT)


# ---------- seller payout setup ----------

@transaction.atomic
def set_payout_account(business: Business, *, bank_code: str, account_number: str) -> Business:
    """Resolve the account name, then create or update the seller's sub-account."""
    account_number = "".join(ch for ch in account_number if ch.isdigit())
    if len(account_number) != 10:
        raise InvoiceError("Account numbers have 10 digits")
    provider = get_provider()
    banks = {b.code: b.name for b in provider.list_banks()}
    if bank_code not in banks:
        raise InvoiceError("Choose your bank from the list")
    try:
        found = None if business.subaccount_code else provider.find_subaccount(bank_code, banks[bank_code], account_number)
        # a reused sub-account was already checked by Paystack when it was made, so don't spend another lookup
        account_name = ((found[1] or f"Account ending {account_number[-4:]}") if found
                        else provider.resolve_account(account_number, bank_code))
        if found:
            business.subaccount_code = found[0]  # same bank account already has a sub-account: reuse it
        elif business.subaccount_code:
            provider.update_subaccount(business.subaccount_code, bank_code=bank_code, account_number=account_number)
        else:
            business.subaccount_code = provider.create_subaccount(
                business_name=business.name, bank_code=bank_code, account_number=account_number,
                fee_percent=fee_percent(business), contact_phone=business.phone, contact_email=business.email)
    except ProviderError as e:
        raise InvoiceError(str(e)) from None
    business.payout_bank_code, business.payout_bank_name = bank_code, banks[bank_code]
    business.payout_account_number, business.payout_account_name = account_number, account_name
    if business.status == Business.Status.SETUP:
        business.status = Business.Status.ACTIVE
    business.save()
    return business


# ---------- customers ----------

def find_or_create_customer(business: Business, name: str, phone: str | None = None) -> Customer:
    phone = normalize_phone(phone)
    qs = Customer.objects.filter(business=business)
    if phone:
        c = qs.filter(phone=phone).first()
        if c:
            return c
    name = " ".join(name.split())
    c = qs.filter(name__iexact=name).first()
    if not c:
        similar = list(qs.filter(name__istartswith=name + " ")[:2])  # "Tunde" finds "Tunde Bello" if he's the only one
        c = similar[0] if len(similar) == 1 else None
    if c:
        if phone and not c.phone:
            c.phone = phone
            c.save(update_fields=["phone"])
        return c
    return Customer.objects.create(business=business, name=name, phone=phone)


@transaction.atomic
def give_dedicated_account(customer: Customer) -> VirtualAccount:
    existing = customer.dedicated_account
    if existing:
        return existing
    b = customer.business
    if not b.can_receive_payments:
        raise InvoiceError("Add your payout bank account first")
    try:
        res = get_provider().create_dedicated_account(
            customer_name=customer.name, phone=customer.phone or b.phone,
            email=customer.email or placeholder_email(b, f"c{customer.pk}"),
            subaccount_code=b.subaccount_code, existing_customer_code=customer.paystack_customer_code)
    except Exception as e:  # ProviderError, or missing DVA permission on the account
        raise InvoiceError(f"Couldn't create the account: {e}") from None
    customer.paystack_customer_code = res.customer_code or customer.paystack_customer_code
    customer.save(update_fields=["paystack_customer_code"])
    va = VirtualAccount.objects.create(business=b, kind=VirtualAccount.Kind.DEDICATED, customer=customer,
                                       account_number=res.account_number, account_name=res.account_name,
                                       bank_name=res.bank_name, provider_reference=res.reference)
    notify.account_assigned(customer, va)
    return va


# ---------- invoices ----------

@transaction.atomic
def create_invoice(business: Business, *, amount: int, customer: Customer | None, description: str = "",
                   due_date=None, created_by=None, send: bool = True) -> Invoice:
    if amount < MIN_INVOICE:
        raise InvoiceError("The smallest invoice is ₦100")
    if business.status == Business.Status.SUSPENDED:
        raise InvoiceError("This business is suspended. Contact Landed support.")
    locked = Business.objects.select_for_update().get(pk=business.pk)
    number = locked.next_invoice_number
    locked.next_invoice_number = number + 1
    locked.save(update_fields=["next_invoice_number"])
    inv = Invoice.objects.create(
        business=business, number=number, customer=customer, amount=amount, description=description.strip()[:300],
        due_date=due_date or (timezone.localdate() + timedelta(days=business.due_days)), created_by=created_by)
    apply_credit(inv, actor=created_by)
    if send and inv.balance > 0:
        notify.invoice_sent(inv)
    return inv


@transaction.atomic
def cancel_invoice(inv: Invoice) -> None:
    if inv.status != Invoice.Status.UNPAID:
        raise InvoiceError("Only invoices with no payments can be cancelled")
    inv.status = Invoice.Status.CANCELLED
    inv.save(update_fields=["status"])
    inv.accounts.filter(active=True).update(active=False)


def pay_account(inv: Invoice, amount: int | None = None) -> VirtualAccount:
    """Return a live one-time account for this invoice, creating one with the provider if needed.

    Called when the customer taps Pay on the invoice page (like a school fees portal).
    """
    if not inv.is_open:
        raise InvoiceError("This invoice is not open for payment")
    b = inv.business
    if not b.can_receive_payments:
        raise InvoiceError(f"{b.name} can't take payments yet. Please contact them.")
    amount = inv.balance if amount is None else amount
    if amount < MIN_INVOICE or amount > inv.balance:
        raise InvoiceError(f"Enter an amount between ₦100 and the balance")
    current = inv.current_pay_account()
    if current and current.amount == amount:
        return current
    expires = timezone.now() + timedelta(hours=settings.PAY_LINK_HOURS)
    reference = f"LND-{inv.pk}-{secrets.token_hex(5)}"
    customer = inv.customer
    try:
        res = get_provider().create_transfer_account(
            amount=amount, reference=reference, subaccount_code=b.subaccount_code, expires_at=expires,
            email=(customer.email if customer and customer.email else placeholder_email(b, f"i{inv.pk}")),
            metadata={"invoice_id": inv.pk, "business_id": b.pk, "invoice": inv.ref})
    except ProviderError as e:
        raise InvoiceError(f"Couldn't get an account number: {e}") from None
    with transaction.atomic():
        inv.accounts.filter(kind=VirtualAccount.Kind.TRANSFER, active=True).update(active=False)
        return VirtualAccount.objects.create(
            business=b, kind=VirtualAccount.Kind.TRANSFER, invoice=inv, customer=customer, amount=amount,
            account_number=res.account_number, account_name=res.account_name, bank_name=res.bank_name,
            provider_reference=res.reference, expires_at=res.expires_at)


def deactivate_expired_accounts() -> int:
    return VirtualAccount.objects.filter(kind=VirtualAccount.Kind.TRANSFER, active=True,
                                         expires_at__lt=timezone.now()).update(active=False)


def open_invoices(business: Business, q: str = ""):
    qs = Invoice.objects.filter(business=business, status__in=Invoice.OPEN).select_related("customer")
    if q:
        qs = qs.filter(Q(customer__name__icontains=q) | Q(description__icontains=q))
    return qs
