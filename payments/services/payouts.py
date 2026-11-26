"""Track each payment's journey from Paystack to the seller's bank (so nobody has to wonder)."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from django.conf import settings
from django.db.models import Min
from django.utils import timezone

log = logging.getLogger("landed.payouts")


def next_business_day(d: date, days: int = 1) -> date:
    while days > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d


def estimate(payment) -> None:
    """Fill in the seller's share and when it should reach their bank. Paystack pays sub-accounts the next
    business day (a seller's very first payout can take a little longer)."""
    from .invoicing import fee_percent

    b = payment.business
    landed = round(payment.amount * fee_percent(b) / 100)
    paystack = payment.fees if settings.PAYSTACK_FEE_BEARER == "subaccount" else 0
    payment.payout_amount = max(payment.amount - landed - paystack, 0)
    paid = timezone.localtime(payment.paid_at or timezone.now()).date()
    payment.payout_expected = next_business_day(paid, settings.PAYOUT_BUSINESS_DAYS)


STATUS_MAP = {"success": "sent", "processing": "processing", "pending": "waiting", "failed": "failed"}


def sync_business(business, provider=None) -> int:
    """Ask Paystack which payouts went to this seller and update their payments. Returns how many changed."""
    from payments.models import Payment
    from payments.providers import get_provider

    from messaging import notify

    open_qs = Payment.objects.filter(business=business, status=Payment.Status.MATCHED,
                                     payout_status__in=["waiting", "processing", "failed"])
    oldest = open_qs.aggregate(m=Min("paid_at"))["m"]
    if not oldest or not business.subaccount_code:
        return 0
    provider = provider or get_provider()
    changed, newly_sent = 0, []
    for st in provider.list_settlements(business.subaccount_code, oldest - timedelta(days=1)):
        status = STATUS_MAP.get(st["status"], "processing")
        for p in open_qs.filter(reference__in=st["references"]):
            if p.payout_status == status and p.payout_reference == st["id"]:
                continue
            p.payout_status, p.payout_reference = status, st["id"]
            if status == "sent":
                p.payout_sent_at = st["date"] or timezone.now()
                newly_sent.append(p)
            p.save(update_fields=["payout_status", "payout_reference", "payout_sent_at"])
            changed += 1
    if newly_sent:
        notify.payout_sent(business, sum(p.payout_amount for p in newly_sent), len(newly_sent))
    return changed


def sync_all() -> int:
    from accounts.models import Business
    from payments.models import Payment

    ids = (Payment.objects.filter(status=Payment.Status.MATCHED, payout_status__in=["waiting", "processing"],
                                  business__subaccount_code__gt="").values_list("business_id", flat=True).distinct())
    n = 0
    for b in Business.objects.filter(id__in=list(ids)):
        try:
            n += sync_business(b)
        except Exception:  # noqa: BLE001
            log.exception("payout sync failed for business %s", b.pk)
    return n


def summary(business) -> dict:
    from django.db.models import Count, Sum

    from payments.models import Payment

    qs = Payment.objects.filter(business=business, status=Payment.Status.MATCHED)
    way = qs.filter(payout_status__in=["waiting", "processing"]).aggregate(t=Sum("payout_amount"), n=Count("id"),
                                                                             d=Min("payout_expected"))
    month_start = timezone.localdate().replace(day=1)
    sent = qs.filter(payout_status="sent", payout_sent_at__date__gte=month_start).aggregate(t=Sum("payout_amount"))
    return {"on_way": way["t"] or 0, "on_way_n": way["n"] or 0, "expected": way["d"],
            "sent_month": sent["t"] or 0, "late": any(p.payout_late for p in qs.filter(payout_status__in=["waiting", "processing"])[:50]),
            "failed": qs.filter(payout_status="failed").count()}
