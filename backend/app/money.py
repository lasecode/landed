from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def naira_to_kobo(value) -> int:
    """Accepts 45000, '45,000', '45k', '45000.50', Decimal → kobo int."""
    if isinstance(value, int):
        return value * 100
    s = str(value).strip().lower().replace(",", "").replace("₦", "").replace("ngn", "").replace("naira", "").strip()
    mult = Decimal(1)
    if s.endswith("k"):
        mult, s = Decimal(1000), s[:-1]
    elif s.endswith("m"):
        mult, s = Decimal(1_000_000), s[:-1]
    d = (Decimal(s) * mult * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(d)


def kobo_to_naira(kobo: int) -> Decimal:
    return (Decimal(kobo) / 100).quantize(Decimal("0.01"))


def fmt(kobo: int) -> str:
    n = kobo_to_naira(kobo)
    if n == n.to_integral_value():
        return f"₦{int(n):,}"
    return f"₦{n:,.2f}"
