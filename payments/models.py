"""Customers, invoices, account numbers, payments and the ledger.

All money is integer kobo (₦1 = 100 kobo). The ledger is the source of truth; invoice.amount_paid
and customer.credit_balance are kept in step with it in the same transaction.
"""
from __future__ import annotations

import secrets

from django.db import models
from django.utils import timezone

from accounts.models import Business, User


def token() -> str:
    return secrets.token_urlsafe(12)


class Customer(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="customers")
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    credit_balance = models.BigIntegerField(default=0)
    paystack_customer_code = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["business", "phone"])]

    def __str__(self):
        return self.name

    @property
    def first_name(self) -> str:
        return (self.name.split() or ["there"])[0]

    @property
    def dedicated_account(self):
        return self.accounts.filter(kind=VirtualAccount.Kind.DEDICATED, active=True).first()


class Invoice(models.Model):
    class Status(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PART_PAID = "part_paid", "Part-paid"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    class Excess(models.TextChoices):
        NONE = "none", "None"
        PENDING = "pending", "Awaiting decision"
        REFUNDED = "refunded", "Refunded"
        CREDITED = "credited", "Kept as credit"

    OPEN = (Status.UNPAID, Status.PART_PAID)

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="invoices")
    number = models.PositiveIntegerField()
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices")
    amount = models.BigIntegerField()
    amount_paid = models.BigIntegerField(default=0)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UNPAID, db_index=True)
    description = models.CharField(max_length=300, blank=True)
    due_date = models.DateField(null=True, blank=True)
    excess_amount = models.BigIntegerField(default=0)
    excess_status = models.CharField(max_length=10, choices=Excess.choices, default=Excess.NONE)
    public_token = models.CharField(max_length=32, unique=True, default=token)
    reminders_sent = models.PositiveSmallIntegerField(default=0)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-number"]
        constraints = [models.UniqueConstraint(fields=["business", "number"], name="uniq_invoice_number")]

    def __str__(self):
        return self.ref

    @property
    def ref(self) -> str:
        return f"INV-{self.number}"

    @property
    def balance(self) -> int:
        return max(self.amount - self.amount_paid, 0)

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN

    @property
    def percent_paid(self) -> int:
        return min(100, round(self.amount_paid * 100 / self.amount)) if self.amount else 0

    def current_pay_account(self):
        """The unexpired one-time account for this invoice, if any."""
        return (self.accounts.filter(kind=VirtualAccount.Kind.TRANSFER, active=True, expires_at__gt=timezone.now())
                .order_by("-created_at").first())


class VirtualAccount(models.Model):
    """An account number a customer pays into.

    DEDICATED: permanent, per customer (Paystack dedicated virtual account, split to the seller).
    TRANSFER: one-time, per payment on an invoice (Paystack Pay with Transfer), expires within hours.
    """

    class Kind(models.TextChoices):
        DEDICATED = "dedicated", "Permanent (per customer)"
        TRANSFER = "transfer", "One-time (per payment)"

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="accounts")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    account_number = models.CharField(max_length=20, db_index=True)
    account_name = models.CharField(max_length=160, blank=True)
    bank_name = models.CharField(max_length=80)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.CASCADE, related_name="accounts")
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.CASCADE, related_name="accounts")
    amount = models.BigIntegerField(null=True, blank=True, help_text="Expected amount (one-time accounts)")
    provider_reference = models.CharField(max_length=120, blank=True, db_index=True,
                                          help_text="Charge reference (one-time) or DVA id (permanent)")
    expires_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.account_number} ({self.bank_name})"

    @property
    def expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())


class Payment(models.Model):
    class Status(models.TextChoices):
        MATCHED = "matched", "Matched"
        UNMATCHED = "unmatched", "Needs assigning"
        REFUNDED = "refunded", "Refunded"

    business = models.ForeignKey(Business, null=True, blank=True, on_delete=models.CASCADE, related_name="payments",
                                 help_text="Empty when we couldn't tell which seller it was for")
    provider = models.CharField(max_length=20)
    reference = models.CharField(max_length=120, unique=True)  # provider reference: idempotency key
    amount = models.BigIntegerField(help_text="Amount the customer paid (gross)")
    fees = models.BigIntegerField(default=0, help_text="Provider fees reported for this payment")
    channel = models.CharField(max_length=30, blank=True)
    payer_name = models.CharField(max_length=160, blank=True)
    payer_bank = models.CharField(max_length=80, blank=True)
    account = models.ForeignKey(VirtualAccount, null=True, blank=True, on_delete=models.SET_NULL)
    account_number = models.CharField(max_length=20, blank=True)
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.SET_NULL, related_name="payments")
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="payments")
    status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
    unmatched_reason = models.CharField(max_length=200, blank=True)
    source = models.CharField(max_length=20, default="webhook")  # webhook | reconciliation | page_check
    paid_at = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-paid_at"]

    def __str__(self):
        return self.reference


class LedgerEntry(models.Model):
    class Type(models.TextChoices):
        PAYMENT = "payment", "Payment applied"
        OVERPAYMENT = "overpayment", "Paid more than invoice"
        REFUND = "refund", "Refunded"
        CREDIT = "credit", "Held as credit"
        CREDIT_APPLIED = "credit_applied", "Credit used"
        UNMATCHED = "unmatched", "Unmatched"
        ASSIGNMENT = "assignment", "Assigned manually"

    business = models.ForeignKey(Business, null=True, on_delete=models.CASCADE, related_name="ledger")
    type = models.CharField(max_length=16, choices=Type.choices)
    amount = models.BigIntegerField()  # always positive; meaning comes from type
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.CASCADE, related_name="ledger")
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.CASCADE, related_name="ledger")
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.CASCADE)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name_plural = "ledger entries"


class WebhookEvent(models.Model):
    provider = models.CharField(max_length=20)
    event_type = models.CharField(max_length=60, blank=True)
    reference = models.CharField(max_length=120, blank=True, db_index=True)
    signature_valid = models.BooleanField(default=False)
    status = models.CharField(max_length=12, default="queued")  # queued|processed|duplicate|ignored|failed|rejected
    detail = models.TextField(blank=True)
    payload = models.JSONField(null=True, blank=True)
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]


class ReconciliationRun(models.Model):
    started_at = models.DateTimeField(default=timezone.now)
    window_from = models.DateTimeField()
    window_to = models.DateTimeField()
    checked = models.PositiveIntegerField(default=0)
    recovered = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]


class MockTransaction(models.Model):
    """Backs the mock provider (test mode): a transfer that 'landed' at the provider."""
    reference = models.CharField(max_length=120, primary_key=True)
    account_number = models.CharField(max_length=20)
    amount = models.BigIntegerField()
    payer_name = models.CharField(max_length=160, blank=True)
    subaccount_code = models.CharField(max_length=40, blank=True)
    paid_at = models.DateTimeField(default=timezone.now)
