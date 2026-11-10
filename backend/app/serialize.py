from __future__ import annotations

from .config import settings
from .models import aware, Customer, Invoice, LedgerEntry, Merchant, Payment, StaffUser, VirtualAccount


def _dt(v):
    return aware(v).isoformat() if v else None


def account(va: VirtualAccount | None) -> dict | None:
    if not va:
        return None
    return {"id": va.id, "account_number": va.account_number, "bank_name": va.bank_name, "account_name": va.account_name,
            "type": va.type.value, "expires_at": _dt(va.expires_at), "active": va.active}


def invoice(inv: Invoice) -> dict:
    return {
        "id": inv.id, "ref": inv.ref, "number": inv.number, "amount": inv.amount, "amount_paid": inv.amount_paid,
        "balance": inv.balance, "status": inv.status.value, "description": inv.description,
        "due_date": _dt(inv.due_date), "created_at": _dt(inv.created_at), "paid_at": _dt(inv.paid_at),
        "excess_amount": inv.excess_amount, "excess_status": inv.excess_status.value,
        "customer": {"id": inv.customer.id, "name": inv.customer.name, "phone": inv.customer.phone} if inv.customer else None,
        "account": account(inv.virtual_account),
        "receipt_url": f"{settings.public_api_url}/r/{inv.public_token}",
        "reminders_sent": inv.reminders_sent,
    }


def payment(p: Payment) -> dict:
    return {
        "id": p.id, "reference": p.provider_reference, "amount": p.amount, "payer_name": p.payer_name,
        "payer_bank": p.payer_bank, "account_number": p.account_number, "status": p.status.value,
        "unmatched_reason": p.unmatched_reason, "source": p.source, "paid_at": _dt(p.paid_at),
        "invoice": {"id": p.invoice.id, "ref": p.invoice.ref} if p.invoice else None,
        "customer": {"id": p.customer.id, "name": p.customer.name} if p.customer else None,
    }


def customer(c: Customer, owed: int = 0, open_count: int = 0) -> dict:
    return {"id": c.id, "name": c.name, "phone": c.phone, "email": c.email, "credit_balance": c.credit_balance,
            "owed": owed, "open_invoices": open_count, "static_account": account(c.static_account),
            "created_at": _dt(c.created_at)}


def ledger(e: LedgerEntry) -> dict:
    return {"id": e.id, "type": e.type.value, "amount": e.amount, "invoice_id": e.invoice_id, "payment_id": e.payment_id,
            "customer_id": e.customer_id, "note": e.note, "created_at": _dt(e.created_at)}


def user(u: StaffUser) -> dict:
    return {"id": u.id, "name": u.name, "phone": u.phone, "role": u.role.value, "active": u.active}


def merchant(m: Merchant) -> dict:
    return {"id": m.id, "business_name": m.business_name, "phone": m.phone, "email": m.email, "plan": m.plan,
            "provider": m.provider, "provider_connected": bool(m.provider_keys) or m.provider == "mock",
            "alert_phone": m.alert_phone, "reminder_days": m.reminder_days,
            "invoice_expiry_days": m.invoice_expiry_days}
