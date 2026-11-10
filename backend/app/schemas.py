"""API shapes. Money goes over the wire as integer kobo; the dashboard formats it."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from .money import naira_to_kobo


class SignupIn(BaseModel):
    business_name: str = Field(min_length=2, max_length=120)
    owner_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=7, max_length=20)
    email: str | None = None
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    phone: str
    password: str


class TokenOut(BaseModel):
    token: str
    user: dict
    merchant: dict


class InvoiceIn(BaseModel):
    customer_id: int | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    amount_naira: float | str
    description: str = ""
    due_date: datetime | None = None
    send_whatsapp: bool = True

    @property
    def amount_kobo(self) -> int:
        return naira_to_kobo(self.amount_naira)


class CustomerIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = None
    email: str | None = None


class StaticAccountIn(BaseModel):
    bvn: str | None = Field(default=None, pattern=r"^\d{11}$")
    nin: str | None = Field(default=None, pattern=r"^\d{11}$")


class AssignIn(BaseModel):
    invoice_id: int | None = None
    customer_id: int | None = None


class ExcessIn(BaseModel):
    decision: str  # refund | credit


class BusinessIn(BaseModel):
    business_name: str | None = None
    phone: str | None = None
    email: str | None = None
    alert_phone: str | None = None
    reminder_days: str | None = None
    invoice_expiry_days: int | None = Field(default=None, ge=1, le=90)

    @field_validator("reminder_days")
    @classmethod
    def _days(cls, v):
        if v is None:
            return v
        parts = [p.strip() for p in v.split(",") if p.strip()]
        if not all(p.isdigit() and 0 < int(p) <= 90 for p in parts):
            raise ValueError("Use whole days separated by commas, e.g. 2,5,10")
        return ",".join(str(int(p)) for p in sorted(set(parts), key=int))


class ProviderIn(BaseModel):
    provider: str
    api_key: str | None = None
    secret_key: str | None = None
    contract_code: str | None = None
    live: bool = False


class StaffIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str
    password: str = Field(min_length=8, max_length=128)
    role: str = "staff"


class SimulateIn(BaseModel):
    account_number: str
    amount_naira: float | str
    payer_name: str = "Test Payer"
