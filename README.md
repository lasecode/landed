# Landed (Django)

Payment confirmation for Nigerian sellers who get paid by bank transfer, including sellers with no CAC registration.

**How the money moves:** Landed holds one Paystack account, and each seller is a Paystack sub-account that pays out to their own bank. When a customer opens an invoice link and taps **Pay**, Landed gets a one-time account number from Paystack, tagged with that seller. The customer transfers to it, and Paystack splits the money automatically: the seller's share goes to their bank, and Landed's fee goes to Landed. Landed never holds seller money. Regular customers can also get a permanent account number, with the same split.

Every payment is **verified with Paystack** before it counts. A fake screenshot or a forged webhook does nothing. Then the ledger is updated and WhatsApp messages go out to the seller and the customer.

## Stack

Django 5.2 · PostgreSQL · Celery + Redis (optional; jobs run inline without it) · Django templates + HTMX · WhiteNoise · Gunicorn.

```
accounts/    User (phone login, owner/staff roles), Business (a seller, with payout bank and Paystack sub-account)
payments/    Customer, Invoice, VirtualAccount, Payment, LedgerEntry, WebhookEvent
  providers/   Provider interface · paystack.py (live) · mock.py (test mode)
  services/    matching.py (the matching rules) · invoicing.py (invoices, payout setup, account numbers)
  tasks.py     webhook processing, reconciliation, expiry, reminders
  views.py     Paystack webhook
messaging/   WhatsApp Cloud API client, templates, seller bot, outbox (Notification), WhatsApp webhook
dashboard/   seller dashboard pages, the public pay page (/pay/<token>/), demo-data command
tests/       33 tests: every matching rule, security rules, pages, the pay flow, bot, Paystack adapter
```

## Run it locally

```bash
pip install -r requirements-dev.txt
cp .env.example .env              # optional; the defaults work with SQLite in test mode
python manage.py migrate
python manage.py seed_demo --password choose-a-password   # demo seller 07000000001 with sample data
python manage.py createsuperuser  # your Landed admin login (/admin/)
python manage.py runserver
pytest                            # the test suite
```

**Test mode** (`PAYMENT_PROVIDER=mock`) behaves like Paystack without real money:
- Bank account names are "resolved".
- Every invoice drawer has a **Test a payment** box. It sends a transfer through the real signed-webhook and verification path.
- **Try the WhatsApp bot** in the sidebar runs the same commands sellers will send on WhatsApp.

## The flows

1. **Seller signs up:** phone number and password, no CAC. They then add the bank account they want to be paid into (any bank, personal accounts included). Landed checks the account name with Paystack and creates their sub-account, taking `LANDED_FEE_PERCENT` as Landed's cut.
2. **Invoice:** the seller creates it in the dashboard, or on WhatsApp ("invoice Tunde 45000 for rice"). The customer gets a WhatsApp message with a pay link.
3. **Pay page:** the customer taps **Pay**, and Paystack's Pay with Transfer issues a one-time account number for that amount. It's valid for up to `PAY_LINK_HOURS`, and the customer can choose to pay part of the balance. The page checks every 5 seconds and turns into a receipt when the money lands. An "I've sent it" button asks Paystack directly, in case a webhook is late.
4. **Confirmation:** Paystack sends a signed webhook. Landed checks the signature, then calls `GET /transaction/verify` before any money counts, and then applies the matching rules:

   | What arrived | What Landed does |
   |---|---|
   | Exact amount | Paid |
   | Less than the balance | Part-paid, plus a balance notice to the customer |
   | More than the balance | Paid, with the excess flagged for the seller to refund or keep as credit |
   | Payment into a customer's permanent account | Applied to their oldest invoices first, the rest held as credit |
   | Payment for a cancelled invoice, or to an unknown account | The Unmatched queue |
   | The same reference again | Ignored |

5. **Safety net:** reconciliation runs every 3 hours and picks up any payment whose webhook never arrived.

## Landed's own back office

`/admin/` (Django admin, for `is_staff` users) gives you:
- **Businesses** (sellers), with their payout details and sub-account codes, plus suspend and re-activate.
- **Payments, invoices, customers and account numbers.**
- **Webhook events**, with a "re-process" action.
- **Reconciliation runs.**
- **Every WhatsApp message sent.**

## Going live

1. **Landed's Paystack account.** Register Landed with CAC, open a Paystack Registered Business account, and ask Paystack support to switch on:
   - **dedicated virtual accounts**,
   - **Pay with Transfer through the Charge API**, and
   - **split payments / sub-accounts** for your use case.

   Confirm three things with them: that sub-accounts can settle to sellers' personal accounts, how fast sub-accounts are settled, and what customers see as the account name.
2. **Configure:** set `PAYMENT_PROVIDER=paystack`, `PAYSTACK_SECRET_KEY` (test key first), `PAYSTACK_DVA_BANK=test-bank`, then switch to the live key with `wema-bank` or `titan-paystack`. In the Paystack dashboard, set the webhook URL to `https://<your-domain>/webhooks/paystack/`.
3. **Test with Paystack test keys:** sign up a seller, add a payout account, pay an invoice, and check the split in Paystack's dashboard.
4. **Check one behaviour:** what Paystack does when a customer sends a **different amount** to a one-time account. The pay page asks for the exact amount, but a wrong amount may be accepted or reversed. Test it in test mode.
5. **WhatsApp:** submit the 9 templates in `messaging/whatsapp/templates.py` (category Utility, language `en`, names exactly as written). When they're approved, set `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET` and `WHATSAPP_DRY_RUN=false`, and point Meta's webhook at `https://<your-domain>/webhooks/whatsapp/`.
6. **Production mode:** set `APP_ENV=production`. The app refuses to start without a real `SECRET_KEY`, PostgreSQL and a Paystack key, and it switches on HTTPS-only cookies and HSTS.
7. **Email:** set SMTP variables so password-reset emails go out. Until then they're printed to the log.

## Deploy (Render)

`render.yaml` describes a Docker web service and a Postgres database. On every start the container runs migrations, then `bootstrap` (creates the admin login from `ADMIN_PHONE`/`ADMIN_PASSWORD` and, in test mode, the demo seller from `DEMO_PASSWORD`), then Gunicorn. With one web instance and no Redis, background jobs run inline and the scheduler runs inside the web process (`EMBED_SCHEDULER=true`). When volume grows, add Redis, set `REDIS_URL`, run `celery -A config worker -B` as a worker, and set `EMBED_SCHEDULER=false`.

Use a paid web plan for real money. A free instance sleeps, and the first webhook after a sleep can time out (Paystack retries, and reconciliation catches it, but it's slower).

## Not built yet

- Password reset by WhatsApp code. Email reset works; a WhatsApp reset needs an approved Authentication template.
- Seller identity checks beyond the bank-account name match. Review new sellers in the admin, and add BVN verification once Paystack's identity API is available to Landed.
- A Paystack payout/settlement report inside the dashboard. Settlement details are in Paystack's own dashboard for now.
