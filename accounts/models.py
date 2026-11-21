from __future__ import annotations

import re

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


def normalize_phone(phone: str | None) -> str:
    """Nigerian numbers in international digits form: 08031234567 -> 2348031234567."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("0") and len(digits) == 11:
        digits = "234" + digits[1:]
    elif len(digits) == 10 and digits[0] in "789":
        digits = "234" + digits
    return digits


class Business(models.Model):
    class Status(models.TextChoices):
        SETUP = "setup", "Setting up"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SETUP)
    alert_phone = models.CharField(max_length=20, blank=True, help_text="WhatsApp number for payment alerts")
    reminder_days = models.CharField(max_length=60, default="2,5", blank=True)
    due_days = models.PositiveSmallIntegerField(default=7)
    next_invoice_number = models.PositiveIntegerField(default=101)

    # Payout (settlement) details: where Paystack sends this seller's share
    payout_bank_code = models.CharField(max_length=12, blank=True)
    payout_bank_name = models.CharField(max_length=80, blank=True)
    payout_account_number = models.CharField(max_length=10, blank=True)
    payout_account_name = models.CharField(max_length=120, blank=True)
    subaccount_code = models.CharField(max_length=40, blank=True, db_index=True)
    fee_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                      help_text="Landed's cut for this seller. Empty = platform default.")
    notes = models.TextField(blank=True, help_text="Internal notes (KYC checks, calls)")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name_plural = "businesses"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name

    @property
    def can_receive_payments(self) -> bool:
        return self.status == self.Status.ACTIVE and bool(self.subaccount_code)

    @property
    def alert_number(self) -> str:
        return normalize_phone(self.alert_phone or self.phone)


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, phone, password=None, **extra):
        phone = normalize_phone(phone)
        if not phone:
            raise ValueError("A phone number is required")
        user = self.model(phone=phone, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("name", "Landed admin")
        return self.create_user(phone, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        STAFF = "staff", "Staff"

    phone = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    business = models.ForeignKey(Business, null=True, blank=True, on_delete=models.CASCADE, related_name="members")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STAFF)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="Landed team: can open the admin")
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()
    USERNAME_FIELD = "phone"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.phone})"

    @property
    def is_owner(self) -> bool:
        return self.role == self.Role.OWNER
