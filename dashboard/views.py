"""Seller dashboard (Django templates + HTMX) and the public invoice/pay page."""
from __future__ import annotations

import csv
import json
from datetime import date, datetime, time, timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.forms import BusinessForm, PayoutForm, SignupForm, StaffForm
from accounts.models import User
from messaging import bot
from payments.models import Customer, Invoice, Payment
from payments.money import kobo_to_naira, naira, naira_to_kobo
from payments.providers import ProviderError, get_provider, is_test_mode
from payments.providers.mock import simulate_transfer
from payments.services import matching
from payments.services.invoicing import (InvoiceError, cancel_invoice, create_invoice, find_or_create_customer,
                                         give_dedicated_account, pay_account, set_payout_account)
from payments.tasks import check_reference


# ---------- helpers ----------

def seller_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *a, **kw):
        if not request.user.business_id:
            return redirect("/admin/") if request.user.is_staff else redirect("login")
        return view(request, *a, **kw)
    return wrapper


def owner_required(view):
    @seller_required
    @wraps(view)
    def wrapper(request, *a, **kw):
        if not request.user.is_owner:
            return HttpResponseForbidden("Only the business owner can do this.")
        return view(request, *a, **kw)
    return wrapper


def toast(response: HttpResponse, text: str, refresh: bool = True) -> HttpResponse:
    events = {"toast": text}
    if refresh:
        events["refresh"] = True
    response["HX-Trigger"] = json.dumps(events)
    return response


def day_start(d: date) -> datetime:
    return timezone.make_aware(datetime.combine(d, time.min))


def received(business, start, end=None) -> tuple[int, int]:
    qs = Payment.objects.filter(business=business, paid_at__gte=start).exclude(status=Payment.Status.REFUNDED)
    if end:
        qs = qs.filter(paid_at__lt=end)
    agg = qs.aggregate(t=Sum("amount"), n=Count("id"))
    return agg["t"] or 0, agg["n"] or 0


def owed(business) -> tuple[int, int]:
    agg = Invoice.objects.filter(business=business, status__in=Invoice.OPEN).aggregate(
        t=Sum(F("amount") - F("amount_paid")), n=Count("id"))
    return agg["t"] or 0, agg["n"] or 0


# ---------- auth ----------

def home(request):
    return redirect("overview" if request.user.is_authenticated else "login")


def signup(request):
    if request.user.is_authenticated:
        return redirect("overview")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return redirect("payout_setup")
    return render(request, "registration/signup.html", {"form": form})


# ---------- overview ----------

@seller_required
def overview(request):
    b = request.user.business
    today = timezone.localdate()
    t, tn = received(b, day_start(today))
    month, _ = received(b, day_start(today.replace(day=1)))
    week, _ = received(b, day_start(today - timedelta(days=today.weekday())))
    out, open_n = owed(b)
    days = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        days.append({"date": d, "amount": received(b, day_start(d), day_start(d + timedelta(days=1)))[0],
                     "today": i == 0})
    top = max([d["amount"] for d in days] + [1])
    for d in days:
        d["pct"] = max(3, round(d["amount"] * 100 / top))
    ctx = {
        "today": t, "today_n": tn, "month": month, "week": week, "owed": out, "open_n": open_n, "days": days,
        "week_total": sum(d["amount"] for d in days),
        "overpaid": Invoice.objects.filter(business=b, excess_status=Invoice.Excess.PENDING).count(),
        "recent": Payment.objects.filter(business=b).select_related("invoice", "customer")[:8],
    }
    from payments.services.payouts import summary

    ctx["payout"] = summary(b)
    return render(request, "dashboard/overview.html", ctx)


# ---------- payouts (where's my money?) ----------

@seller_required
def payouts(request):
    from payments.services.payouts import summary, sync_business

    b = request.user.business
    if request.method == "POST":
        try:
            n = sync_business(b)
            messages.success(request, "Checked with Paystack. " + (f"{n} update{'s' if n != 1 else ''}." if n else "No change yet."))
        except ProviderError as e:
            messages.error(request, f"Couldn't reach Paystack: {e}")
        return redirect("payouts")
    rows = Payment.objects.filter(business=b, status=Payment.Status.MATCHED).select_related("invoice", "customer")[:200]
    return render(request, "dashboard/payouts.html", {"rows": rows, "s": summary(b)})


# ---------- payout setup ----------

@owner_required
def payout_setup(request):
    b = request.user.business
    try:
        banks = get_provider().list_banks()
    except ProviderError as e:
        banks = []
        messages.error(request, f"Couldn't load the bank list: {e}")
    form = PayoutForm(request.POST or None, banks=banks,
                      initial={"bank_code": b.payout_bank_code, "account_number": b.payout_account_number})
    if request.method == "POST" and form.is_valid():
        try:
            set_payout_account(b, bank_code=form.cleaned_data["bank_code"],
                               account_number=form.cleaned_data["account_number"])
        except InvoiceError as e:
            form.add_error("account_number", str(e))
        else:
            messages.success(request, f"Payouts will go to {b.payout_account_name}, {b.payout_bank_name}.")
            return redirect("overview")
    return render(request, "dashboard/payout_setup.html", {"form": form})


# ---------- invoices ----------

@seller_required
def invoices(request):
    b = request.user.business
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    qs = Invoice.objects.filter(business=b).select_related("customer")
    counts = dict(qs.values_list("status").annotate(n=Count("id")))
    if status == "overpaid":
        qs = qs.filter(excess_status=Invoice.Excess.PENDING)
    elif status:
        qs = qs.filter(status=status)
    if q:
        num = q.upper().replace("INV-", "")
        qs = qs.filter(Q(customer__name__icontains=q) | Q(description__icontains=q)
                       | Q(number=int(num) if num.isdigit() else -1))
    ctx = {"invoices": qs[:300], "status": status, "q": q, "counts": counts,
           "tabs": [("", "All"), ("unpaid", "Unpaid"), ("part_paid", "Part-paid"), ("paid", "Paid"), ("cancelled", "Cancelled")]}
    tpl = "dashboard/partials/invoice_rows.html" if request.headers.get("HX-Target") == "invoice-table" else "dashboard/invoices.html"
    return render(request, tpl, ctx)


@seller_required
def invoice_new(request):
    b = request.user.business
    customer = None
    if request.GET.get("customer"):
        customer = get_object_or_404(Customer, pk=request.GET["customer"], business=b)
    ctx = {"customers": Customer.objects.filter(business=b)[:500], "customer": customer, "errors": []}
    if request.method == "POST":
        p = request.POST
        try:
            amount = naira_to_kobo(p.get("amount", ""))
        except ValueError:
            amount = 0
        name = p.get("customer_name", "").strip()
        due = p.get("due_date") or None
        if not name:
            ctx["errors"].append("Who is this invoice for?")
        if amount < 10000:
            ctx["errors"].append("Enter an amount of at least ₦100")
        if not ctx["errors"]:
            try:
                with transaction.atomic():
                    cust = (Customer.objects.filter(pk=p.get("customer_id"), business=b).first()
                            if p.get("customer_id") else None) or find_or_create_customer(b, name, p.get("customer_phone"))
                    inv = create_invoice(b, amount=amount, customer=cust, description=p.get("description", ""),
                                         due_date=date.fromisoformat(due) if due else None, created_by=request.user,
                                         send=p.get("send") == "on")
            except InvoiceError as e:
                ctx["errors"].append(str(e))
            else:
                resp = render(request, "dashboard/partials/invoice_drawer.html", invoice_ctx(inv, request))
                return toast(resp, f"{inv.ref} created" + (" and sent on WhatsApp" if p.get("send") == "on" and cust.phone else ""))
        ctx["post"] = p
    return render(request, "dashboard/partials/invoice_form.html", ctx)


def invoice_ctx(inv: Invoice, request) -> dict:
    from messaging.notify import pay_url

    return {"inv": inv, "ledger": inv.ledger.select_related("payment").all(),
            "payments": inv.payments.all(), "pay_url": pay_url(inv),
            "dva": inv.customer.dedicated_account if inv.customer else None,
            "pay_acct": inv.current_pay_account()}


@seller_required
def invoice_detail(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("customer", "business"), pk=pk, business=request.user.business)
    return render(request, "dashboard/partials/invoice_drawer.html", invoice_ctx(inv, request))


@seller_required
@require_POST
def invoice_action(request, pk, action):
    b = request.user.business
    inv = get_object_or_404(Invoice, pk=pk, business=b)
    msg = ""
    try:
        if action == "cancel":
            cancel_invoice(inv)
            msg = f"{inv.ref} cancelled"
        elif action == "resend":
            if not (inv.customer and inv.customer.phone):
                raise InvoiceError("This customer has no WhatsApp number saved")
            from messaging.notify import invoice_sent

            with transaction.atomic():
                invoice_sent(inv)
            msg = f"Sent to {inv.customer.name} on WhatsApp"
        elif action in ("refund", "credit"):
            matching.resolve_excess(inv, action, actor=request.user)
            msg = "Kept as credit" if action == "credit" else "Marked as refunded"
        elif action == "simulate" and is_test_mode():
            amount = naira_to_kobo(request.POST.get("amount") or str(kobo_to_naira(inv.balance)))
            acct = pay_account(inv, min(amount, inv.balance)) if amount <= inv.balance else pay_account(inv)
            ref = simulate_transfer(acct.account_number, amount, (inv.customer.name if inv.customer else "Test payer").upper())
            _send_test_webhook(ref)
            msg = "Test transfer sent. It confirmed through the webhook."
        else:
            raise Http404
    except (InvoiceError, matching.ActionError, ProviderError, ValueError) as e:
        resp = render(request, "dashboard/partials/invoice_drawer.html", invoice_ctx(inv, request))
        return toast(resp, str(e), refresh=False)
    inv.refresh_from_db()
    return toast(render(request, "dashboard/partials/invoice_drawer.html", invoice_ctx(inv, request)), msg)


def _send_test_webhook(reference: str) -> None:
    """Deliver a signed test webhook through the real endpoint, exactly like Paystack would."""
    from django.test import Client

    from payments.providers.mock import sign, webhook_body

    raw = webhook_body(reference)
    Client(HTTP_HOST="localhost").post("/webhooks/paystack/", data=raw, content_type="application/json",
                                       HTTP_X_PAYSTACK_SIGNATURE=sign(raw))


# ---------- customers ----------

@seller_required
def customers(request):
    b = request.user.business
    q = request.GET.get("q", "").strip()
    owed_map = {r["customer"]: r for r in Invoice.objects.filter(business=b, status__in=Invoice.OPEN, customer__isnull=False)
                .values("customer").annotate(t=Sum(F("amount") - F("amount_paid")), n=Count("id"))}
    qs = Customer.objects.filter(business=b).prefetch_related("accounts")
    if q:
        qs = qs.filter(name__icontains=q)
    rows = [{"c": c, "owed": owed_map.get(c.id, {}).get("t", 0), "open": owed_map.get(c.id, {}).get("n", 0),
             "acct": c.dedicated_account} for c in qs]
    total = sum(r["owed"] for r in rows)
    tpl = "dashboard/partials/customer_rows.html" if request.headers.get("HX-Target") == "customer-table" else "dashboard/customers.html"
    return render(request, tpl, {"rows": rows, "q": q, "total_owed": total})


@seller_required
def customer_new(request):
    b = request.user.business
    errors = []
    if request.method == "POST":
        from accounts.models import normalize_phone

        name, phone = request.POST.get("name", "").strip(), normalize_phone(request.POST.get("phone"))
        if not name:
            errors.append("Enter a name")
        elif phone and Customer.objects.filter(business=b, phone=phone).exists():
            errors.append("A customer with that number already exists")
        else:
            c = Customer.objects.create(business=b, name=name, phone=phone)
            return toast(render(request, "dashboard/partials/customer_drawer.html", customer_ctx(c)), f"{c.name} added")
    return render(request, "dashboard/partials/customer_form.html", {"errors": errors, "post": request.POST})


def customer_ctx(c: Customer) -> dict:
    invs = c.invoices.all()
    return {"c": c, "invoices": invs, "payments": c.payments.select_related("invoice")[:30], "acct": c.dedicated_account,
            "owed": sum(i.balance for i in invs if i.is_open)}


@seller_required
def customer_detail(request, pk):
    c = get_object_or_404(Customer, pk=pk, business=request.user.business)
    return render(request, "dashboard/partials/customer_drawer.html", customer_ctx(c))


@seller_required
@require_POST
def customer_account(request, pk):
    c = get_object_or_404(Customer, pk=pk, business=request.user.business)
    try:
        give_dedicated_account(c)
    except InvoiceError as e:
        return toast(render(request, "dashboard/partials/customer_drawer.html", customer_ctx(c)), str(e), refresh=False)
    return toast(render(request, "dashboard/partials/customer_drawer.html", customer_ctx(c)), "Permanent account created")


# ---------- unmatched ----------

@seller_required
def unmatched(request):
    rows = Payment.objects.filter(business=request.user.business, status=Payment.Status.UNMATCHED)
    return render(request, "dashboard/unmatched.html", {"rows": rows})


@seller_required
def payment_assign(request, pk):
    b = request.user.business
    p = get_object_or_404(Payment, pk=pk, business=b)
    mode = request.GET.get("mode") or request.POST.get("mode") or "invoice"
    q = (request.GET.get("q") or "").strip()
    if request.method == "POST":
        try:
            if mode == "invoice":
                matching.assign(p, b, invoice=get_object_or_404(Invoice, pk=request.POST.get("target"), business=b),
                                actor=request.user)
            else:
                matching.assign(p, b, customer=get_object_or_404(Customer, pk=request.POST.get("target"), business=b),
                                actor=request.user)
        except matching.ActionError as e:
            return toast(HttpResponse(status=204), str(e), refresh=False)
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = json.dumps({"toast": "Payment assigned", "refresh": True, "closeDrawer": True})
        return resp
    if mode == "invoice":
        opts = list(Invoice.objects.filter(business=b, status__in=Invoice.OPEN).select_related("customer")
                    .filter(Q(customer__name__icontains=q) | Q(description__icontains=q) if q else Q()))
        opts.sort(key=lambda i: abs(i.balance - p.amount))
    else:
        opts = list(Customer.objects.filter(business=b, name__icontains=q)[:50])
    ctx = {"p": p, "mode": mode, "opts": opts[:30], "q": q}
    tpl = "dashboard/partials/assign_options.html" if request.headers.get("HX-Target") == "assign-options" else "dashboard/partials/assign_drawer.html"
    return render(request, tpl, ctx)


@owner_required
@require_POST
def payment_refund(request, pk):
    p = get_object_or_404(Payment, pk=pk, business=request.user.business)
    try:
        matching.refund_unmatched(p, request.user.business, actor=request.user)
    except matching.ActionError as e:
        return toast(HttpResponse(status=204), str(e), refresh=False)
    resp = HttpResponse(status=204)
    resp["HX-Trigger"] = json.dumps({"toast": "Marked as sent back", "refresh": True, "closeDrawer": True})
    return resp


# ---------- reports ----------

def _range(request):
    today = timezone.localdate()
    try:
        end = date.fromisoformat(request.GET["end"]) if request.GET.get("end") else today
        start = date.fromisoformat(request.GET["start"]) if request.GET.get("start") else end.replace(day=1)
    except ValueError:
        start, end = today.replace(day=1), today
    if start > end:
        start, end = end, start
    return start, end


@seller_required
def reports(request):
    b = request.user.business
    start, end = _range(request)
    s, e = day_start(start), day_start(end + timedelta(days=1))
    pays = Payment.objects.filter(business=b, paid_at__gte=s, paid_at__lt=e)
    invs = Invoice.objects.filter(business=b, created_at__gte=s, created_at__lt=e).exclude(status=Invoice.Status.CANCELLED)
    daily = {start + timedelta(days=i): 0 for i in range((end - start).days + 1)}
    for p in pays.exclude(status=Payment.Status.REFUNDED):
        daily[timezone.localtime(p.paid_at).date()] = daily.get(timezone.localtime(p.paid_at).date(), 0) + p.amount
    top = max(list(daily.values()) + [1])
    today = timezone.localdate()
    presets = [("This month", today.replace(day=1), today),
               ("Last month", (today.replace(day=1) - timedelta(days=1)).replace(day=1), today.replace(day=1) - timedelta(days=1)),
               ("Last 7 days", today - timedelta(days=6), today), ("Last 30 days", today - timedelta(days=29), today)]
    ctx = {
        "start": start, "end": end, "presets": presets,
        "received": sum(p.amount for p in pays if p.status != Payment.Status.REFUNDED),
        "fees": sum(p.fees for p in pays if p.status != Payment.Status.REFUNDED),
        "n_pay": pays.count(), "unmatched": pays.filter(status=Payment.Status.UNMATCHED).count(),
        "invoiced": sum(i.amount for i in invs), "n_inv": invs.count(),
        "n_paid": sum(1 for i in invs if i.status == Invoice.Status.PAID),
        "outstanding": sum(i.balance for i in invs if i.is_open),
        "daily": [{"d": d, "amount": a, "pct": max(2, round(a * 100 / top))} for d, a in sorted(daily.items())],
    }
    ctx["show_labels"] = len(ctx["daily"]) <= 14
    return render(request, "dashboard/reports.html", ctx)


@owner_required
def export_csv(request):
    b = request.user.business
    start, end = _range(request)
    s, e = day_start(start), day_start(end + timedelta(days=1))
    kind = request.GET.get("kind", "payments")
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{kind}-{start}-to-{end}.csv"'
    w = csv.writer(resp)
    if kind == "invoices":
        w.writerow(["Invoice", "Created", "Customer", "Phone", "Description", "Amount (NGN)", "Paid (NGN)", "Balance (NGN)", "Status", "Due"])
        for i in Invoice.objects.filter(business=b, created_at__gte=s, created_at__lt=e).select_related("customer").order_by("created_at"):
            w.writerow([i.ref, timezone.localtime(i.created_at).strftime("%Y-%m-%d %H:%M"), i.customer.name if i.customer else "",
                        i.customer.phone if i.customer else "", i.description, kobo_to_naira(i.amount),
                        kobo_to_naira(i.amount_paid), kobo_to_naira(i.balance), i.get_status_display(), i.due_date or ""])
    else:
        w.writerow(["Paid at", "Reference", "Payer", "Amount (NGN)", "Fees (NGN)", "Invoice", "Customer", "Status", "Note", "Source"])
        for p in Payment.objects.filter(business=b, paid_at__gte=s, paid_at__lt=e).select_related("invoice", "customer").order_by("paid_at"):
            w.writerow([timezone.localtime(p.paid_at).strftime("%Y-%m-%d %H:%M"), p.reference, p.payer_name,
                        kobo_to_naira(p.amount), kobo_to_naira(p.fees), p.invoice.ref if p.invoice else "",
                        p.customer.name if p.customer else "", p.get_status_display(), p.unmatched_reason, p.source])
    return resp


# ---------- settings ----------

@owner_required
def settings_view(request):
    b = request.user.business
    form = BusinessForm(request.POST or None, instance=b, prefix="biz")
    staff_form = StaffForm(prefix="staff", business=b)
    if request.method == "POST" and request.POST.get("section") == "business" and form.is_valid():
        form.save()
        messages.success(request, "Saved")
        return redirect("settings")
    if request.method == "POST" and request.POST.get("section") == "staff":
        staff_form = StaffForm(request.POST, prefix="staff", business=b)
        if staff_form.is_valid():
            u = staff_form.save()
            messages.success(request, f"Login created for {u.name}")
            return redirect("settings")
    return render(request, "dashboard/settings.html", {
        "form": form, "staff_form": staff_form, "show_staff_form": request.POST.get("section") == "staff",
        "staff": User.objects.filter(business=b, is_active=True).order_by("date_joined")})


@owner_required
@require_POST
def staff_remove(request, pk):
    u = get_object_or_404(User, pk=pk, business=request.user.business)
    if u.pk == request.user.pk:
        messages.error(request, "You can't remove yourself")
    else:
        u.is_active = False
        u.save(update_fields=["is_active"])
        messages.success(request, f"{u.name}'s login was removed")
    return redirect("settings")


# ---------- bot tester (test mode) ----------

@seller_required
def bot_tester(request):
    if not is_test_mode():
        raise Http404
    if request.method == "POST":
        text = request.POST.get("text", "").strip()
        if not text:
            return HttpResponse("")
        with transaction.atomic():
            reply = bot.handle(request.user, text)
        return render(request, "dashboard/partials/bot_exchange.html", {"text": text, "reply": reply})
    return render(request, "dashboard/bot.html", {"samples": ["menu", "1", "2", "3", "0"]})


# ---------- public invoice / pay page ----------

def public_invoice(request, token):
    inv = get_object_or_404(Invoice.objects.select_related("business", "customer"), public_token=token)
    return render(request, "public/invoice.html", {
        "inv": inv, "payments": inv.payments.filter(status=Payment.Status.MATCHED),
        "acct": inv.current_pay_account(), "dva": inv.customer.dedicated_account if inv.customer else None})


@require_POST
def public_pay(request, token):
    inv = get_object_or_404(Invoice.objects.select_related("business", "customer"), public_token=token)
    amount = None
    if request.POST.get("amount"):
        try:
            amount = naira_to_kobo(request.POST["amount"])
        except ValueError:
            amount = -1
    try:
        acct = pay_account(inv, amount)
    except InvoiceError as e:
        return render(request, "public/partials/pay_box.html", {"inv": inv, "error": str(e)})
    return render(request, "public/partials/pay_box.html", {"inv": inv, "acct": acct})


def public_status(request, token):
    """Polled by the pay page. Also double-checks with the provider so a missed webhook doesn't stall the page."""
    inv = get_object_or_404(Invoice, public_token=token)
    acct = inv.current_pay_account()
    if acct and request.GET.get("check") and not Payment.objects.filter(reference=acct.provider_reference).exists():
        check_reference(acct.provider_reference)
        inv.refresh_from_db()
    paid_now = request.GET.get("paid_before") is not None and inv.amount_paid > int(request.GET.get("paid_before") or 0)
    resp = render(request, "public/partials/status.html", {"inv": inv, "paid_now": paid_now})
    if paid_now:
        resp["HX-Refresh"] = "true"
    return resp


def healthz(request):
    return HttpResponse("ok")
