"""Public pages customers open from WhatsApp: invoice / receipt (no login, unguessable token)."""
from __future__ import annotations

from html import escape
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Invoice, Merchant, Payment, PaymentStatus, aware
from ..money import fmt

router = APIRouter(tags=["public"])
LAGOS = ZoneInfo("Africa/Lagos")

STATUS = {
    "paid": ("Paid", "#0B6B4B", "#E6F2EC"),
    "part_paid": ("Part-paid", "#9A5A06", "#FBF1E2"),
    "unpaid": ("Awaiting payment", "#1D5FC4", "#E8EFFB"),
    "expired": ("Expired", "#5B6460", "#EEF0EE"),
    "cancelled": ("Cancelled", "#B42318", "#FBEAE8"),
}


@router.get("/r/{token}", response_class=HTMLResponse)
def receipt(token: str, db: Session = Depends(get_db)):
    inv = db.scalar(select(Invoice).where(Invoice.public_token == token))
    if not inv:
        raise HTTPException(404, "Not found")
    m = db.get(Merchant, inv.merchant_id)
    pays = db.scalars(select(Payment).where(Payment.invoice_id == inv.id, Payment.status == PaymentStatus.matched)
                      .order_by(Payment.paid_at)).all()
    label, fg, bg = STATUS[inv.status.value]
    va = inv.virtual_account
    pct = min(100, round(inv.amount_paid * 100 / inv.amount)) if inv.amount else 0
    rows = "".join(
        f"<tr><td>{aware(p.paid_at).astimezone(LAGOS):%d %b %Y, %H:%M}</td><td>{escape(p.payer_name or '')}</td>"
        f"<td class=r>{fmt(p.amount)}</td></tr>" for p in pays) or "<tr><td colspan=3 class=muted>No payments yet</td></tr>"
    pay_box = ""
    if inv.status.value in ("unpaid", "part_paid") and va and va.active:
        pay_box = f"""<div class=pay><div class=muted>Pay {fmt(inv.balance)} by bank transfer to</div>
        <div class=acct>{escape(va.account_number)}</div><div>{escape(va.bank_name)}{(' · ' + escape(va.account_name)) if va.account_name else ''}</div>
        <div class="muted small">Your payment is confirmed automatically once it lands. No need to send a screenshot.</div></div>"""
    title = "Receipt" if inv.status.value == "paid" else "Invoice"
    html = f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{title} {inv.ref} · {escape(m.business_name)}</title>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@500&display=swap" rel=stylesheet>
<style>
:root{{--ink:#121915;--muted:#5B6460;--line:#E3E6E2;--bg:#F6F7F5;--brand:#0B6B4B}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);font:15px/1.5 'Instrument Sans',system-ui,sans-serif;color:var(--ink);padding:24px 16px}}
.card{{max-width:520px;margin:0 auto;background:#fff;border:1px solid var(--line);border-radius:14px;padding:28px}}
h1{{font-size:20px;margin:0}}.muted{{color:var(--muted)}}.small{{font-size:13px}}.r{{text-align:right}}
.top{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:20px}}
.badge{{font-size:13px;font-weight:600;padding:4px 10px;border-radius:99px;color:{fg};background:{bg};white-space:nowrap}}
.amt{{font-size:32px;font-weight:600;letter-spacing:-.02em;margin:8px 0 4px}}
.bar{{height:6px;background:var(--line);border-radius:6px;overflow:hidden;margin:12px 0 4px}}.bar i{{display:block;height:100%;width:{pct}%;background:var(--brand)}}
.pay{{margin:20px 0;padding:16px;border-radius:10px;background:var(--bg);border:1px dashed #C9CFC9}}
.acct{{font:500 26px 'IBM Plex Mono',monospace;letter-spacing:.04em;margin:4px 0}}
table{{width:100%;border-collapse:collapse;margin-top:8px;font-size:14px}}td{{padding:8px 0;border-top:1px solid var(--line)}}
.foot{{margin-top:24px;display:flex;justify-content:space-between;align-items:center}}
button{{font:inherit;border:1px solid var(--line);background:#fff;border-radius:8px;padding:8px 14px;cursor:pointer}}
@media print{{button{{display:none}}body{{background:#fff}}.card{{border:0}}}}
</style></head><body><div class=card>
<div class=top><div><div class="muted small">{title} from</div><h1>{escape(m.business_name)}</h1></div><span class=badge>{label}</span></div>
<div class="muted small">{inv.ref}{(' · ' + escape(inv.customer.name)) if inv.customer else ''} · {aware(inv.created_at).astimezone(LAGOS):%d %b %Y}</div>
<div class=amt>{fmt(inv.amount)}</div><div class=muted>{escape(inv.description or '')}</div>
<div class=bar><i></i></div><div class="muted small">{fmt(inv.amount_paid)} paid · {fmt(inv.balance)} remaining</div>
{pay_box}
<div style="margin-top:20px;font-weight:600">Payments</div><table>{rows}</table>
<div class=foot><span class="muted small">Confirmed directly from the bank, not from a screenshot.</span><button onclick="print()">Save as PDF</button></div>
</div></body></html>"""
    return HTMLResponse(html)
