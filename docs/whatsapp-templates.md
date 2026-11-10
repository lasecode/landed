# WhatsApp message templates

Business-initiated messages (invoices, receipts, reminders, alerts) must use templates that Meta has approved.
Submit each one below in **WhatsApp Manager → Message templates → Create template** with:

- **Category:** Utility
- **Name:** exactly as shown (the code sends it by this name)
- **Language:** English (`en`), or change `WHATSAPP_TEMPLATE_LANG`
- **Body:** the text below. `{{1}}`, `{{2}}` … are filled in automatically, in order.

Meta asks for a sample value for each variable. Use realistic ones ("Tunde", "Adaeze Foods", "₦45,000", "INV-112"). Approval usually takes minutes to a day.
Until they're approved, keep `WHATSAPP_DRY_RUN=true`. Messages are then written to the server log and the notifications table instead of being sent.

Bot replies to merchants are free-form text. That's allowed because the merchant messaged first, within WhatsApp's 24-hour window.

## `invoice_new`
Sent to: customer

```
Hello {{1}}, {{2}} has sent you invoice {{3}} for {{4}} ({{5}}).

Pay by bank transfer to:
{{6}}
{{7}}

Your payment is confirmed automatically once it lands. Invoice: {{8}}
```

## `payment_receipt`
Sent to: customer

```
Hi {{1}}, {{2}} has received your payment of {{3}} for {{4}}. {{5}}

Receipt: {{6}}
```

## `balance_notice`
Sent to: customer

```
Hi {{1}}, {{2}} received {{3}} for {{4}}. {{5}} is remaining.

Pay the balance to {{6}} ({{7}}). Receipt so far: {{8}}
```

## `payment_reminder`
Sent to: customer

```
Hi {{1}}, a friendly reminder from {{2}}: {{3}} is still outstanding on {{4}}.

Pay by transfer to {{5}} ({{6}}). Details: {{7}}
```

## `static_account_assigned`
Sent to: customer

```
Hi {{1}}, {{2}} has set up a dedicated account for your payments to them:
{{3}}
{{4}}

Every transfer into this account is recorded against your name automatically.
```

## `credit_received`
Sent to: customer

```
Hi {{1}}, {{2}} has received your transfer of {{3}}. {{4}}
```

## `merchant_payment_alert`
Sent to: merchant

```
{{1}} paid {{2}} for {{3}}. {{4}}
```

## `merchant_unmatched_alert`
Sent to: merchant

```
A payment of {{1}} from {{2}} could not be matched automatically. Assign it here: {{3}}
```
