from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def naira_to_kobo(value) -> int:
    """'45,000' | '45k' | 45000 | '₦12.5k' -> kobo. Raises ValueError on junk."""
    if isinstance(value, int):
        return value * 100
    s = str(value).strip().lower().replace(",", "").replace("₦", "").replace("ngn", "").replace(" ", "")
    mult = Decimal(1)
    if s.endswith("k"):
        mult, s = Decimal(1000), s[:-1]
    elif s.endswith("m"):
        mult, s = Decimal(1_000_000), s[:-1]
    try:
        return int((Decimal(s) * mult * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise ValueError("Not an amount") from None


def kobo_to_naira(kobo: int) -> Decimal:
    return (Decimal(kobo or 0) / 100).quantize(Decimal("0.01"))


def naira(kobo: int | None, short: bool = False) -> str:
    n = kobo_to_naira(kobo or 0)
    if short and n >= 1_000_000:
        return f"₦{n / 1_000_000:.1f}m".replace(".0m", "m")
    if short and n >= 10_000:
        return f"₦{round(n / 1000)}k"
    if n == n.to_integral_value():
        return f"₦{int(n):,}"
    return f"₦{n:,.2f}"
