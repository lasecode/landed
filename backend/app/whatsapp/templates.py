"""WhatsApp message templates.

Business-initiated messages must use templates pre-approved by Meta (brief 5.3). Register each
template below in WhatsApp Manager with exactly this name, category UTILITY, language `en`, and
this body text. {{n}} placeholders are filled in order from `params`.
"""
from __future__ import annotations

TEMPLATES: dict[str, str] = {
    "invoice_new": (
        "Hello {{1}}, {{2}} has sent you invoice {{3}} for {{4}} ({{5}}).\n\n"
        "Pay by bank transfer to:\n{{6}}\n{{7}}\n\n"
        "Your payment is confirmed automatically once it lands. Invoice: {{8}}"
    ),
    "payment_receipt": (
        "Hi {{1}}, {{2}} has received your payment of {{3}} for {{4}}. {{5}}\n\nReceipt: {{6}}"
    ),
    "balance_notice": (
        "Hi {{1}}, {{2}} received {{3}} for {{4}}. {{5}} is remaining.\n\n"
        "Pay the balance to {{6}} ({{7}}). Receipt so far: {{8}}"
    ),
    "payment_reminder": (
        "Hi {{1}}, a friendly reminder from {{2}}: {{3}} is still outstanding on {{4}}.\n\n"
        "Pay by transfer to {{5}} ({{6}}). Details: {{7}}"
    ),
    "static_account_assigned": (
        "Hi {{1}}, {{2}} has set up a dedicated account for your payments to them:\n{{3}}\n{{4}}\n\n"
        "Every transfer into this account is recorded against your name automatically."
    ),
    "credit_received": (
        "Hi {{1}}, {{2}} has received your transfer of {{3}}. {{4}}"
    ),
    "merchant_payment_alert": "{{1}} paid {{2}} for {{3}}. {{4}}",
    "merchant_unmatched_alert": (
        "A payment of {{1}} from {{2}} could not be matched automatically. Assign it here: {{3}}"
    ),
}


def render(template: str, params: list[str] | dict) -> str:
    if template == "text":
        return params["body"] if isinstance(params, dict) else str(params[0])
    body = TEMPLATES[template]
    values = params if isinstance(params, list) else params.get("args", [])
    for i, v in enumerate(values, start=1):
        body = body.replace("{{%d}}" % i, str(v))
    return body
