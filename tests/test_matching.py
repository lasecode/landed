"""Every matching rule, driven through the real path: pay link -> transfer -> signed webhook -> verify -> ledger."""
import pytest

from payments.models import Invoice, LedgerEntry, Payment
from payments.services import matching
from payments.services.invoicing import (InvoiceError, cancel_invoice, create_invoice, find_or_create_customer,
                                         give_dedicated_account, pay_account)

from .conftest import pay, sent

pytestmark = pytest.mark.django_db


def invoice(b, naira=45000, name="Tunde Bello", phone="08031112222", desc="3 bags of rice"):
    return create_invoice(b, amount=naira * 100, customer=find_or_create_customer(b, name, phone), description=desc)


def ledger(inv):
    return list(LedgerEntry.objects.filter(invoice=inv).values_list("type", "amount"))


def test_seller_payout_setup_creates_subaccount(seller):
    b, _ = seller
    assert b.can_receive_payments and b.subaccount_code.startswith("ACCT_")
    assert b.payout_account_name == "TEST ACCOUNT 6789" and b.payout_bank_name == "Guaranty Trust Bank"


def test_invoice_message_has_pay_link_not_account(seller):
    b, _ = seller
    inv = invoice(b)
    t, to, params = sent()[0]
    assert t == "landed_invoice" and to == "2348031112222"
    assert params[5] == f"http://testserver/pay/{inv.public_token}/"


def test_exact_payment_marks_paid(seller):
    b, _ = seller
    inv = invoice(b)
    acct = pay_account(inv)
    assert acct.amount == 4_500_000 and acct.expires_at
    pay(acct.account_number, 45000)
    inv.refresh_from_db()
    assert inv.status == "paid" and inv.balance == 0
    assert ledger(inv) == [("payment", 4_500_000)]
    assert [t for t, *_ in sent()][-2:] == ["landed_seller_alert", "landed_receipt"]
    acct.refresh_from_db()
    assert acct.active is False


def test_part_payment_then_rest(seller):
    b, _ = seller
    inv = invoice(b, 45000)
    pay(pay_account(inv, 4_000_000).account_number, 40000)
    inv.refresh_from_db()
    assert inv.status == "part_paid" and inv.balance == 500_000
    assert sent()[-1][0] == "landed_balance"
    assert inv.current_pay_account() is None  # the used one-time account is closed
    second = pay_account(inv)
    assert second.amount == 500_000
    pay(second.account_number, 5000)
    inv.refresh_from_db()
    assert inv.status == "paid" and inv.payments.count() == 2


def test_overpayment_then_credit_used_next_time(seller):
    b, _ = seller
    inv = invoice(b, 20000)
    pay(pay_account(inv).account_number, 25000)
    inv.refresh_from_db()
    assert inv.status == "paid" and inv.excess_amount == 500_000 and inv.excess_status == "pending"
    matching.resolve_excess(inv, "credit")
    inv.customer.refresh_from_db()
    assert inv.customer.credit_balance == 500_000
    nxt = create_invoice(b, amount=1_200_000, customer=inv.customer)
    assert nxt.amount_paid == 500_000 and nxt.status == "part_paid"
    with pytest.raises(matching.ActionError):
        matching.resolve_excess(inv, "refund")


def test_permanent_account_pays_oldest_first_then_credit(seller):
    b, _ = seller
    c = find_or_create_customer(b, "Kemi Adebayo", "08035550000")
    va = give_dedicated_account(c)
    assert sent()[-1][0] == "landed_account_assigned"
    a = create_invoice(b, amount=1_000_000, customer=c)
    b2 = create_invoice(b, amount=800_000, customer=c)
    assert sent()[-1][0] == "landed_invoice_account"  # message carries her permanent account number
    pay(va.account_number, 15000)
    a.refresh_from_db(), b2.refresh_from_db()
    assert a.status == "paid" and b2.status == "part_paid" and b2.balance == 300_000
    pay(va.account_number, 7000)
    b2.refresh_from_db(), c.refresh_from_db()
    assert b2.status == "paid" and c.credit_balance == 400_000
    assert sent()[-1][0] == "landed_credit"


def test_unknown_account_goes_to_unmatched_and_assign(seller):
    b, _ = seller
    inv = invoice(b, 30000)
    ref = pay("9900000001", 30000, "MUSA IBRAHIM")
    p = Payment.objects.get(reference=ref)
    # unknown account but the split tells us nothing either -> no business
    assert p.status == "unmatched" and p.business is None
    p.business = b
    p.save()
    matching.assign(p, b, invoice=inv)
    inv.refresh_from_db()
    assert inv.status == "paid"
    with pytest.raises(matching.ActionError):
        matching.assign(p, b, invoice=inv)


def test_cancelled_invoice_payment_is_unmatched_for_that_seller(seller):
    b, _ = seller
    inv = invoice(b, 8000)
    acct = pay_account(inv)
    cancel_invoice(inv)
    from payments.models import VirtualAccount

    VirtualAccount.objects.filter(pk=acct.pk).update(active=True)  # money arrives anyway
    pay(acct.account_number, 8000)
    p = Payment.objects.get()
    assert p.business == b and p.status == "unmatched" and "cancelled" in p.unmatched_reason
    assert sent()[-1][0] == "landed_seller_unmatched"


def test_pay_link_rules(seller):
    b, _ = seller
    inv = invoice(b, 10000)
    with pytest.raises(InvoiceError):
        pay_account(inv, 2_000_000)  # more than the balance
    a1 = pay_account(inv)
    assert pay_account(inv) == a1  # same live account reused
    a2 = pay_account(inv, 500_000)
    a1.refresh_from_db()
    assert a2 != a1 and a1.active is False


def test_cannot_pay_seller_without_payout(db):
    from .conftest import make_seller

    b, _ = make_seller(phone="08030000099", payout=False)
    inv = create_invoice(b, amount=500_000, customer=None)
    with pytest.raises(InvoiceError):
        pay_account(inv)
