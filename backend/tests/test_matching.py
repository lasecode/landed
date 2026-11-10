"""Every matching rule from brief section 7, driven through the real webhook path."""
from datetime import timedelta

from app import db as dbmod
from app import models
from app.models import utcnow

from .conftest import get_invoice, make_invoice, notifications, pay


def ledger_types(client, h, iid):
    return [(e["type"], e["amount"]) for e in get_invoice(client, h, iid)["ledger"]]


def test_exact_amount_marks_paid_and_notifies(client, owner):
    inv = make_invoice(client, owner, 45000)
    assert inv["status"] == "unpaid" and inv["account"]["account_number"]
    pay(client, owner, inv["account"]["account_number"], 45000)
    d = get_invoice(client, owner, inv["id"])
    assert d["status"] == "paid" and d["amount_paid"] == 4_500_000 and d["balance"] == 0
    assert ledger_types(client, owner, inv["id"]) == [("payment", 4_500_000)]
    assert d["account"]["active"] is False  # dynamic account closed once paid
    sent = [t for t, *_ in notifications()]
    assert sent == ["invoice_new", "merchant_payment_alert", "payment_receipt"]
    alert = notifications()[1][2]["args"]
    assert alert == ["TUNDE BELLO", "₦45,000", inv["ref"], "Fully paid."]


def test_underpayment_then_accumulates_to_paid(client, owner):
    inv = make_invoice(client, owner, 45000)
    acct = inv["account"]["account_number"]
    pay(client, owner, acct, 40000)
    d = get_invoice(client, owner, inv["id"])
    assert d["status"] == "part_paid" and d["balance"] == 500_000
    assert notifications()[-1][0] == "balance_notice"
    assert "₦5,000" in notifications()[-1][2]["args"]
    pay(client, owner, acct, 5000)
    d = get_invoice(client, owner, inv["id"])
    assert d["status"] == "paid" and len(d["payments"]) == 2
    assert notifications()[-1][0] == "payment_receipt"


def test_overpayment_flags_excess_then_credit_used_on_next_invoice(client, owner):
    inv = make_invoice(client, owner, 45000)
    pay(client, owner, inv["account"]["account_number"], 50000)
    d = get_invoice(client, owner, inv["id"])
    assert d["status"] == "paid" and d["excess_amount"] == 500_000 and d["excess_status"] == "pending"
    assert ("overpayment", 500_000) in ledger_types(client, owner, inv["id"])
    ov = client.get("/api/overview", headers=owner).json()
    assert ov["overpaid"] == 1

    r = client.post(f"/api/invoices/{inv['id']}/excess", headers=owner, json={"decision": "credit"})
    assert r.status_code == 200 and r.json()["excess_status"] == "credited"
    cust = client.get(f"/api/customers/{d['customer']['id']}", headers=owner).json()
    assert cust["credit_balance"] == 500_000

    inv2 = make_invoice(client, owner, 20000)  # same customer
    assert inv2["amount_paid"] == 500_000 and inv2["status"] == "part_paid" and inv2["balance"] == 1_500_000
    cust = client.get(f"/api/customers/{d['customer']['id']}", headers=owner).json()
    assert cust["credit_balance"] == 0
    # can't decide twice
    assert client.post(f"/api/invoices/{inv['id']}/excess", headers=owner, json={"decision": "refund"}).status_code == 400


def test_overpayment_refund_decision(client, owner):
    inv = make_invoice(client, owner, 10000)
    pay(client, owner, inv["account"]["account_number"], 12000)
    r = client.post(f"/api/invoices/{inv['id']}/excess", headers=owner, json={"decision": "refund"})
    assert r.json()["excess_status"] == "refunded"
    assert ("refund", 200_000) in ledger_types(client, owner, inv["id"])


def test_static_account_pays_oldest_invoice_first_then_holds_credit(client, owner):
    c = client.post("/api/customers", headers=owner, json={"name": "Kemi Adebayo", "phone": "08035550000"}).json()
    r = client.post(f"/api/customers/{c['id']}/static-account", headers=owner, json={})
    assert r.status_code == 200
    static_no = r.json()["static_account"]["account_number"]
    a = client.post("/api/invoices", headers=owner, json={"customer_id": c["id"], "amount_naira": 10000}).json()
    b = client.post("/api/invoices", headers=owner, json={"customer_id": c["id"], "amount_naira": 8000}).json()
    assert a["account"]["account_number"] == static_no == b["account"]["account_number"]

    pay(client, owner, static_no, 15000)
    assert get_invoice(client, owner, a["id"])["status"] == "paid"
    bb = get_invoice(client, owner, b["id"])
    assert bb["status"] == "part_paid" and bb["balance"] == 300_000

    pay(client, owner, static_no, 7000)  # 3,000 clears b, 4,000 becomes credit
    assert get_invoice(client, owner, b["id"])["status"] == "paid"
    cust = client.get(f"/api/customers/{c['id']}", headers=owner).json()
    assert cust["credit_balance"] == 400_000 and cust["owed"] == 0
    assert notifications()[-1][0] == "credit_received"


def test_unknown_account_goes_to_unmatched_and_can_be_assigned(client, owner):
    inv = make_invoice(client, owner, 30000)
    pay(client, owner, "9999999999", 30000, payer="MUSA IBRAHIM")
    unmatched = client.get("/api/payments?status=unmatched", headers=owner).json()
    assert len(unmatched) == 1 and unmatched[0]["unmatched_reason"] == "Account not recognised"
    assert notifications()[-1][0] == "merchant_unmatched_alert"
    pid = unmatched[0]["id"]
    r = client.post(f"/api/payments/{pid}/assign", headers=owner, json={"invoice_id": inv["id"]})
    assert r.status_code == 200 and r.json()["status"] == "matched"
    assert get_invoice(client, owner, inv["id"])["status"] == "paid"
    # can't assign twice
    assert client.post(f"/api/payments/{pid}/assign", headers=owner, json={"invoice_id": inv["id"]}).status_code == 400


def test_expired_invoice_payment_goes_to_unmatched(client, owner):
    inv = make_invoice(client, owner, 30000)
    with dbmod.session_scope() as s:
        va = s.get(models.VirtualAccount, inv["account"]["id"])
        va.expires_at = utcnow() - timedelta(minutes=1)
    from app.jobs import expire_invoices

    assert expire_invoices() == 1
    assert get_invoice(client, owner, inv["id"])["status"] == "expired"
    pay(client, owner, inv["account"]["account_number"], 30000)
    p = client.get("/api/payments?status=unmatched", headers=owner).json()
    assert len(p) == 1 and "expired" in p[0]["unmatched_reason"]


def test_cancelled_invoice_payment_goes_to_unmatched(client, owner):
    inv = make_invoice(client, owner, 30000)
    assert client.post(f"/api/invoices/{inv['id']}/cancel", headers=owner).json()["status"] == "cancelled"
    pay(client, owner, inv["account"]["account_number"], 30000)
    assert len(client.get("/api/payments?status=unmatched", headers=owner).json()) == 1


def test_cannot_cancel_part_paid_invoice(client, owner):
    inv = make_invoice(client, owner, 30000)
    pay(client, owner, inv["account"]["account_number"], 1000)
    assert client.post(f"/api/invoices/{inv['id']}/cancel", headers=owner).status_code == 400


def test_unmatched_refund_is_owner_only_and_recorded(client, owner):
    pay(client, owner, "1234567890", 5000)
    pid = client.get("/api/payments?status=unmatched", headers=owner).json()[0]["id"]
    r = client.post(f"/api/payments/{pid}/refund", headers=owner)
    assert r.status_code == 200 and r.json()["status"] == "refunded"
    assert client.get("/api/overview", headers=owner).json()["today"] == 0
