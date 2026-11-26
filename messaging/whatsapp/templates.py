"""WhatsApp message templates (Utility category, language `en`).

Business-initiated messages must use templates Meta has approved. Register each one in WhatsApp
Manager with exactly this name and body; {{n}} values are filled in order.
"""
from __future__ import annotations

TEMPLATES: dict[str, str] = {
    "landed_invoice": (
        "Hello {{1}}, {{2}} has sent you invoice {{3}} for {{4}} ({{5}}).\n\n"
        "Pay by bank transfer here: {{6}}\n\n"
        "Your payment is confirmed automatically the moment it lands."
    ),
    "landed_invoice_account": (
        "Hello {{1}}, {{2}} has sent you invoice {{3}} for {{4}} ({{5}}).\n\n"
        "Pay into your account for {{2}}:\n{{6}}\n{{7}}\n\n"
        "Your payment is confirmed automatically. Details: {{8}}"
    ),
    "landed_receipt": "Hi {{1}}, {{2}} has received your payment of {{3}} for {{4}}. {{5}}\n\nReceipt: {{6}}",
    "landed_balance": (
        "Hi {{1}}, {{2}} received {{3}} for {{4}}. {{5}} is remaining.\n\nPay the balance here: {{6}}"
    ),
    "landed_reminder": (
        "Hi {{1}}, a friendly reminder from {{2}}: {{3}} is still outstanding on {{4}}.\n\nPay here: {{5}}"
    ),
    "landed_account_assigned": (
        "Hi {{1}}, {{2}} has set up a dedicated account for your payments to them:\n{{3}}\n{{4}}\n\n"
        "Every transfer into it is recorded against your name automatically."
    ),
    "landed_credit": "Hi {{1}}, {{2}} has received your transfer of {{3}}. {{4}}",
    "landed_seller_alert": "{{1}} paid {{2}} for {{3}}. {{4}}",
    "landed_payout_sent": "💸 {{1}} from {{2}} has been sent to your {{3}} account. See all payouts: {{4}}",
    "landed_seller_unmatched": "A payment of {{1}} from {{2}} could not be matched automatically. Assign it here: {{3}}",
}


def render(template: str, params: list) -> str:
    if template == "text":
        return str(params[0]) if params else ""
    body = TEMPLATES[template]
    for i, v in enumerate(params, start=1):
        body = body.replace("{{%d}}" % i, str(v))
    return body
