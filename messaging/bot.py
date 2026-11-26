"""Seller bot on WhatsApp: a numbered menu, like MTN's *131# codes.

Sellers reply with a number (or a letter: a=1, b=2 ...). "menu", "hi" or 0 always goes back to the
main menu. The old typed shortcuts still work: "invoice Tunde 45000 for rice", "today", "unpaid".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.db.models import Count, F, Sum
from django.utils import timezone

from payments.models import Customer, Invoice, Payment
from payments.money import naira, naira_to_kobo
from payments.services.invoicing import InvoiceError, create_invoice, find_or_create_customer

from .models import BotSession

SESSION_MINUTES = 10
PHONE = r"(?:\+?234|0)[789][01]\d{8}"
INVOICE_RE = re.compile(
    rf"^\s*(?:invoice|inv)\s+(?P<name>.+?)\s+(?:(?P<phone>{PHONE})\s+)?(?P<amount>₦?\d[\d,]*(?:\.\d+)?\s*[km]?)\b"
    rf"\s*(?:for\s+(?P<desc>.+))?\s*$", re.IGNORECASE)

MENU = ("*Landed* 👋 Reply with a number:\n\n"
        "1. New invoice\n"
        "2. Today's money\n"
        "3. Unpaid invoices\n"
        "4. Check a customer\n"
        "5. Recent payments\n"
        "6. Payments to assign\n"
        "7. Money on its way to my bank\n\n"
        "Reply *menu* or *0* any time to come back here.")
BACK = "\n\n0. Main menu"


@dataclass
class Parsed:
    name: str
    amount: int
    description: str
    phone: str | None


def parse_invoice(text: str) -> Parsed | None:
    m = INVOICE_RE.match(text.strip())
    if not m:
        return None
    try:
        amount = naira_to_kobo(m.group("amount"))
    except ValueError:
        return None
    return Parsed(m.group("name").strip().title(), amount, (m.group("desc") or "").strip(), m.group("phone"))


def choice(text: str) -> int | None:
    """'2', ' 2. ', 'b', 'B' -> 2. Anything else -> None."""
    t = text.strip().lower().rstrip(".)")
    if t.isdigit():
        return int(t)
    if len(t) == 1 and "a" <= t <= "i":
        return ord(t) - ord("a") + 1
    return None


# ---------- session ----------

def _session(phone: str) -> BotSession:
    s, _ = BotSession.objects.get_or_create(phone=phone)
    if timezone.now() - s.updated_at > timedelta(minutes=SESSION_MINUTES):
        s.state, s.data = "menu", {}
    return s


def _go(s: BotSession, state: str, **data) -> None:
    s.state, s.data, s.updated_at = state, data, timezone.now()
    s.save()


def _reset(s: BotSession) -> str:
    _go(s, "menu")
    return MENU


# ---------- screens ----------

def _today(b) -> str:
    start = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    agg = Payment.objects.filter(business=b, paid_at__gte=start).exclude(status=Payment.Status.REFUNDED) \
        .aggregate(total=Sum("amount"), n=Count("id"))
    owed = Invoice.objects.filter(business=b, status__in=Invoice.OPEN) \
        .aggregate(t=Sum(F("amount") - F("amount_paid")))["t"] or 0
    unmatched = Payment.objects.filter(business=b, status=Payment.Status.UNMATCHED).count()
    n = agg["n"] or 0
    msg = (f"*Today so far*\nReceived: {naira(agg['total'] or 0)} from {n} payment{'s' if n != 1 else ''}\n"
           f"Still owed to you: {naira(owed)}")
    if unmatched:
        msg += f"\n⚠️ {unmatched} payment{'s' if unmatched != 1 else ''} to assign (reply 6)"
    return msg


def _unpaid(b) -> list[Invoice]:
    return list(Invoice.objects.filter(business=b, status__in=Invoice.OPEN).select_related("customer")
                .order_by("created_at")[:9])


def _invoice_line(i: Invoice) -> str:
    who = i.customer.name if i.customer else "No name"
    return f"{who}, {i.ref}: {naira(i.balance)}" + (" (part-paid)" if i.status == Invoice.Status.PART_PAID else "")


def _recent_customers(b) -> list[Customer]:
    from django.db.models import Max

    return list(Customer.objects.filter(business=b).annotate(last=Max("invoices__created_at"))
                .order_by(F("last").desc(nulls_last=True), "-created_at")[:5])


def _created_reply(inv: Invoice, customer: Customer) -> str:
    from .notify import pay_url

    b = inv.business
    head = f"✅ {inv.ref} created for {customer.name}: {naira(inv.amount)}"
    if inv.amount_paid:
        head += f" ({naira(inv.amount_paid)} covered by their credit)"
    if inv.status == Invoice.Status.PAID:
        return head + "\nFully covered by their credit. Nothing to pay." + BACK
    if customer.phone:
        return head + f"\n\nSent to {customer.name} on WhatsApp. I'll tell you the moment they pay." + BACK
    return (head + "\n\nForward this to your customer 👇\n\n"
            f"Hello {customer.first_name}, here is your invoice from {b.name}: {inv.ref}, {naira(inv.balance)}"
            + (f" for {inv.description}" if inv.description else "")
            + f".\n\nPay by bank transfer here: {pay_url(inv)}\nYour payment is confirmed automatically." + BACK)


# ---------- main entry ----------

def handle(user, text: str) -> str:
    b = user.business
    raw = text.strip()
    low = raw.lower()
    s = _session(user.phone)

    # always-available words
    if low in {"menu", "hi", "hello", "start", "help", "0", "#", "*", "home"}:
        return _reset(s)
    if low == "today":
        _go(s, "menu")
        return _today(b) + BACK
    if low == "unpaid":
        return _screen_unpaid(s, b)
    if low.startswith(("invoice ", "inv ")):
        return _quick_invoice(s, user, raw)

    c = choice(raw)
    st = s.state

    if st == "menu":
        if c == 1:
            return _start_invoice(s, b)
        if c == 2:
            return _today(b) + BACK
        if c == 3:
            return _screen_unpaid(s, b)
        if c == 4:
            return _screen_customers(s, b)
        if c == 5:
            pays = Payment.objects.filter(business=b).select_related("invoice", "customer")[:5]
            if not pays:
                return "No payments yet." + BACK
            return "*Recent payments*\n" + "\n".join(
                f"• {timezone.localtime(p.paid_at):%d %b %H:%M} {p.customer.name if p.customer else p.payer_name}: "
                f"{naira(p.amount)}" + (f" ({p.invoice.ref})" if p.invoice else "") for p in pays) + BACK
        if c == 6:
            from .notify import dashboard_url

            n = Payment.objects.filter(business=b, status=Payment.Status.UNMATCHED).count()
            if not n:
                return "Nothing to assign. Every payment is matched ✅" + BACK
            return (f"{n} payment{'s' if n != 1 else ''} couldn't be matched to an invoice. "
                    f"Assign {'them' if n != 1 else 'it'} here: {dashboard_url('unmatched')}" + BACK)
        if c == 7:
            from payments.services.payouts import summary

            from .notify import dashboard_url, short_bank

            sm = summary(b)
            bank = short_bank(b.payout_bank_name) if b.payout_bank_name else "your bank"
            if not sm["on_way"]:
                msg = f"Nothing waiting. Everything has been sent to {bank} ✅"
            elif sm["late"]:
                msg = (f"*{naira(sm['on_way'])}* is on its way to {bank} but taking longer than usual. "
                       "Your money is safe and confirmed; we'll message you the moment Paystack sends it.")
            else:
                msg = (f"*{naira(sm['on_way'])}* from {sm['on_way_n']} payment{'s' if sm['on_way_n'] != 1 else ''} "
                       f"is on its way to {bank}, expected by {sm['expected']:%a %d %b}.")
            if sm["sent_month"]:
                msg += f"\nSent to {bank} this month: {naira(sm['sent_month'])}"
            return msg + f"\nDetails: {dashboard_url('payouts')}" + BACK
        return "Please reply with a number from the menu.\n\n" + MENU

    # ---- new invoice: who -> amount -> what for -> (phone) -> confirm
    if st == "inv_who":
        ids = s.data.get("ids", [])
        if c is not None and 1 <= c <= len(ids):
            cust = Customer.objects.filter(pk=ids[c - 1], business=b).first()
            if not cust:
                return _reset(s)
            _go(s, "inv_amount", customer_id=cust.pk, name=cust.name)
        elif c is not None:
            return "Reply with a number from the list, or type the customer's name."
        else:
            name = " ".join(raw.split()).title()[:120]
            if len(name) < 2:
                return "Type the customer's name, e.g. *Tunde Bello*."
            _go(s, "inv_amount", name=name)
        return f"How much is {s.data['name']}'s invoice?\nType the amount, e.g. *45000* or *45k*" + BACK

    if st == "inv_amount":
        try:
            amount = naira_to_kobo(raw)
        except ValueError:
            amount = 0
        if amount < 10000:
            return "Type the amount in naira, e.g. *45000* or *45k* (at least ₦100)."
        _go(s, "inv_desc", **{**s.data, "amount": amount})
        return "What is it for?\nE.g. *3 bags of rice*\n\nOr reply *1* to skip."

    if st == "inv_desc":
        desc = "" if c == 1 else raw[:300]
        data = {**s.data, "desc": desc}
        cust = Customer.objects.filter(pk=data.get("customer_id"), business=b).first() if data.get("customer_id") else None
        if not cust:
            cust = find_or_create_customer(b, data["name"])
            data["customer_id"] = cust.pk
        if not cust.phone:
            _go(s, "inv_phone", **data)
            return (f"What's {cust.first_name}'s WhatsApp number? I'll send the invoice to them.\n\n"
                    "Or reply *1* to skip (you'll get a message to forward).")
        _go(s, "inv_confirm", **data)
        return _confirm_text(cust, data)

    if st == "inv_phone":
        from accounts.models import normalize_phone

        data = dict(s.data)
        cust = Customer.objects.filter(pk=data["customer_id"], business=b).first()
        if not cust:
            return _reset(s)
        if c != 1:
            phone = normalize_phone(raw)
            if len(phone) < 12:
                return "That doesn't look like a phone number. Type it like *0803 123 4567*, or reply *1* to skip."
            cust.phone = phone
            cust.save(update_fields=["phone"])
        _go(s, "inv_confirm", **data)
        return _confirm_text(cust, data)

    if st == "inv_confirm":
        if c not in (1, 2):
            return "Reply *1* to create and send, *2* to create only, or *0* to cancel."
        data = s.data
        cust = Customer.objects.filter(pk=data["customer_id"], business=b).first()
        try:
            inv = create_invoice(b, amount=data["amount"], customer=cust, description=data.get("desc", ""),
                                 created_by=user, send=(c == 1 and bool(cust and cust.phone)))
        except InvoiceError as e:
            _go(s, "menu")
            return f"Couldn't create the invoice: {e}" + BACK
        _go(s, "menu")
        if c == 2 and cust:
            phone, cust.phone = cust.phone, ""  # show the forwardable text instead
            reply = _created_reply(inv, cust)
            cust.phone = phone
            return reply
        return _created_reply(inv, cust)

    # ---- unpaid list -> one invoice -> action
    if st == "unpaid":
        ids = s.data.get("ids", [])
        if c is None or not 1 <= c <= len(ids):
            return "Reply with a number from the list." + BACK
        inv = Invoice.objects.filter(pk=ids[c - 1], business=b).select_related("customer").first()
        if not inv or not inv.is_open:
            return "That invoice is already settled." + BACK
        from .notify import pay_url

        _go(s, "unpaid_one", id=inv.pk)
        opts = "1. Send a reminder on WhatsApp\n2. Get the pay link\n" if inv.customer and inv.customer.phone \
            else "2. Get the pay link\n"
        return (f"*{inv.ref}* · {inv.customer.name if inv.customer else 'No name'}\n"
                f"{inv.description or 'No description'}\n{naira(inv.amount_paid)} paid · {naira(inv.balance)} left\n\n"
                + opts + "0. Main menu")

    if st == "unpaid_one":
        from . import notify

        inv = Invoice.objects.filter(pk=s.data.get("id"), business=b).select_related("customer").first()
        if not inv:
            return _reset(s)
        if c == 1 and inv.customer and inv.customer.phone:
            notify.queue(b, inv.customer.phone, "landed_reminder",
                         [inv.customer.first_name, b.name, naira(inv.balance), inv.ref, notify.pay_url(inv)], inv)
            _go(s, "menu")
            return f"Reminder sent to {inv.customer.name} ✅" + BACK
        if c == 2:
            _go(s, "menu")
            return f"Pay link for {inv.ref}:\n{notify.pay_url(inv)}\n\nForward it to your customer." + BACK
        return "Reply *1* or *2*, or *0* for the main menu."

    # ---- customers
    if st == "customers":
        ids = s.data.get("ids", [])
        if c is not None and 1 <= c <= len(ids):
            cust = Customer.objects.filter(pk=ids[c - 1], business=b).first()
        elif c is None:
            cust = Customer.objects.filter(business=b, name__icontains=raw).first()
        else:
            cust = None
        if not cust:
            return "I couldn't find that customer. Type part of their name, or *0* for the menu."
        _go(s, "menu")
        invs = list(cust.invoices.filter(status__in=Invoice.OPEN))
        owed = sum(i.balance for i in invs)
        lines = "\n".join(f"• {i.ref}: {naira(i.balance)} left" for i in invs[:5]) or "No open invoices 🎉"
        acct = cust.dedicated_account
        return (f"*{cust.name}*\nOwes you: {naira(owed)}\nCredit with you: {naira(cust.credit_balance)}\n"
                + (f"Their account: {acct.account_number} ({acct.bank_name})\n" if acct else "")
                + f"\n{lines}" + BACK)

    return _reset(s)


def _start_invoice(s, b) -> str:
    recent = _recent_customers(b)
    _go(s, "inv_who", ids=[c.pk for c in recent])
    if not recent:
        return "*New invoice*\nWho is it for? Type the customer's name." + BACK
    return ("*New invoice*\nWho is it for? Reply with a number, or type a new name:\n\n"
            + "\n".join(f"{n}. {c.name}" for n, c in enumerate(recent, 1)) + BACK)


def _confirm_text(cust: Customer, data: dict) -> str:
    what = f" for {data['desc']}" if data.get("desc") else ""
    send = f"Create and send to {cust.first_name} on WhatsApp" if cust.phone else "Create (you'll get a message to forward)"
    return (f"*Check this:*\nInvoice for {cust.name}: {naira(data['amount'])}{what}\n\n"
            f"1. {send}\n2. Create only, I'll forward it myself\n0. Cancel")


def _screen_unpaid(s, b) -> str:
    invs = _unpaid(b)
    if not invs:
        _go(s, "menu")
        return "Nothing unpaid. Everyone has paid up 🎉" + BACK
    _go(s, "unpaid", ids=[i.pk for i in invs])
    total = sum(i.balance for i in invs)
    return (f"*Unpaid invoices* ({naira(total)} owed)\nReply with a number to remind or get the link:\n\n"
            + "\n".join(f"{n}. {_invoice_line(i)}" for n, i in enumerate(invs, 1)) + BACK)


def _screen_customers(s, b) -> str:
    recent = _recent_customers(b)
    _go(s, "customers", ids=[c.pk for c in recent])
    if not recent:
        _go(s, "menu")
        return "No customers yet. Reply *1* from the menu to create your first invoice." + BACK
    return ("*Check a customer*\nReply with a number, or type part of a name:\n\n"
            + "\n".join(f"{n}. {c.name}" for n, c in enumerate(recent, 1)) + BACK)


def _quick_invoice(s, user, raw: str) -> str:
    b = user.business
    p = parse_invoice(raw)
    if not p:
        return "I couldn't read that. Reply *1* from the menu and I'll walk you through it." + BACK
    try:
        customer = find_or_create_customer(b, p.name, p.phone)
        inv = create_invoice(b, amount=p.amount, customer=customer, description=p.description,
                             created_by=user, send=bool(customer.phone))
    except InvoiceError as e:
        return f"Couldn't create the invoice: {e}" + BACK
    _go(s, "menu")
    return _created_reply(inv, customer)
