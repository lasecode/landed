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
        "Your payment is confirmed automatically. Details: {{8}}\n\nThank you!"
    ),
    "landed_receipt": (
        "Hi {{1}}, this is a payment receipt from {{2}}. We have received your payment of {{3}} for invoice {{4}}. "
        "{{5}}\n\nYou can view your receipt at any time here: {{6}}\n\nThank you for your business!"
    ),
    "landed_balance": (
        "Hi {{1}}, {{2}} has received your part payment of {{3}} for invoice {{4}}. "
        "There is a balance of {{5}} remaining on this invoice.\n\n"
        "You can pay the balance by bank transfer here: {{6}}\n\nThank you for your business!"
    ),
    "landed_reminder": (
        "Hi {{1}}, this is a friendly reminder from {{2}}. A balance of {{3}} is still outstanding on invoice {{4}}.\n\n"
        "You can pay by bank transfer here: {{5}}\n\nIf you have already paid, please ignore this message. Thank you!"
    ),
    "landed_account_assigned": (
        "Hi {{1}}, {{2}} has set up a dedicated account for your payments to them:\n{{3}}\n{{4}}\n\n"
        "Every transfer into it is recorded against your name automatically."
    ),
    "landed_credit": (
        "Hi {{1}}, {{2}} has received your bank transfer of {{3}}. {{4}}\n\n"
        "This has been recorded on your account. Thank you for your business!"
    ),
    "landed_seller_alert": "💰 Payment received: {{1}} paid {{2}} for {{3}}. {{4}}\n\nReply *menu* for options.",
    "landed_payout_sent": "💸 Payout sent: {{1}} from {{2}} has been sent to your {{3}} account. See all payouts: {{4}}\n\nThank you for using Landed.",
    "landed_seller_unmatched": (
        "A payment of {{1}} from {{2}} has landed but could not be matched to an invoice automatically.\n\n"
        "Please tell us who it is from here: {{3}}\n\nIt only takes one tap."
    ),
}


def render(template: str, params: list) -> str:
    if template == "text":
        return str(params[0]) if params else ""
    body = TEMPLATES[template]
    for i, v in enumerate(params, start=1):
        body = body.replace("{{%d}}" % i, str(v))
    return body
