# Landed: payment confirmation & reconciliation

Every invoice (or regular customer) gets its own bank account number. When the customer transfers, the provider tells us, we **verify the payment with the provider**, match it to the invoice, update the ledger, and send WhatsApp confirmations to the merchant and the customer. A fake transfer screenshot does nothing.

"Landed" is a placeholder name. To rename, change `APP_NAME` and the word "Landed" in `web/src` (sidebar, login page, `index.html`).

```
landed/
├── backend/            FastAPI API, matching engine, worker, scheduler (Python)
│   ├── app/
│   │   ├── providers/  PaymentProvider interface + Monnify + Mock (test mode)
│   │   ├── services/   matching.py (the engine), invoicing.py, notify.py
│   │   ├── whatsapp/   Cloud API client, templates, bot command parser
│   │   ├── routers/    HTTP endpoints (dashboard API, webhooks, receipts, admin)
│   │   ├── jobs.py     webhook processing, reconciliation, reminders, expiry
│   │   ├── worker.py   RQ worker        scheduler.py  periodic jobs
│   ├── migrations/     Alembic (database schema)
│   └── tests/          33 tests: every matching rule, security rules, bot, Monnify adapter
├── web/                React dashboard (Vite)
├── docs/whatsapp-templates.md
└── docker-compose.yml  Postgres, Redis, API, worker, scheduler, dashboard
```

## Run it

### With Docker (everything in one go)
```bash
cp .env.example .env        # set JWT_SECRET and ENCRYPTION_KEYS (commands are in the file)
docker compose up --build
```
Open http://localhost:8080 and create an account. You start in **test mode**:
- Open any invoice → **Test a payment** sends a simulated transfer. It goes through the real path: signed webhook → queue → worker → verify → matching → WhatsApp (logged).
- **Try the WhatsApp bot** in the sidebar runs the same commands merchants will send on WhatsApp.

### Without Docker (development)
Needs Python 3.11+, Node 20+, and optionally PostgreSQL and Redis.
```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload              # SQLite by default; tables are created automatically in development
# optional: export REDIS_URL=redis://localhost:6379/0 and run `python -m app.worker` + `python -m app.scheduler`
# without REDIS_URL, background jobs run inline. Fine for local work.

cd ../web && npm install && npm run dev    # http://localhost:5173 (proxies /api to :8000)
```

### Tests
```bash
cd backend && pytest                                                             # SQLite
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/landed_test pytest   # PostgreSQL
```

## How the brief maps to the code

| Brief | Where |
|---|---|
| §4.3 Provider interface (`create_static_account`, `create_dynamic_account`, `verify_transaction`, `list_transactions`, `verify_webhook_signature`) | `providers/base.py`; Monnify in `providers/monnify.py` |
| §7 Matching engine: exact, under, over, accumulate, static → oldest invoice then credit, expired/unknown → unmatched, duplicates ignored | `services/matching.py` (tests: `tests/test_matching.py`) |
| §8.1 Data model | `app/models.py` (money stored as integer kobo) |
| §8.2 Webhook flow: verify signature → queue → 200; worker: idempotency → verify with provider → match → ledger → notify | `routers/webhooks.py` → `jobs.process_webhook_event` |
| §9 Security: signatures, idempotency (unique `provider_reference`), verify-before-trust, reconciliation job, ledger, encrypted keys, merchant scoping | tests: `tests/test_security.py` |
| §5 WhatsApp: bot (`invoice`, `today`, `unpaid`), invoice/receipt/balance/reminder messages via templates, 24h rule | `whatsapp/`, `routers/whatsapp.py`, `services/notify.py` |
| §6 Dashboard pages + Owner/Staff roles | `web/src/pages/*`, `deps.owner` |
| §8 Scheduler: reminders, expiry, reconciliation | `scheduler.py` |
| §10 Freemium cap | `FREE_PLAN_INVOICE_CAP`, `services/invoicing.PLAN_CAPS` |
| Admin (us): merchants, webhook monitor, replay, reconcile | `routers/admin.py` (header `X-Admin-Token`) |

## Going live

### 1. Deploy

**Quickest: Render free plan, one service.** Push this folder to a GitHub repo. In Render, go to **New → Blueprint** and pick the repo. `render.yaml` creates the database and a single web service that serves the dashboard, the API and the scheduled jobs at one URL. Secrets are generated for you. Test mode is on, so this is ready for client demos. Free services sleep after ~15 minutes idle, so move to the Starter plan before real payments.

**Full setup:** The API, worker and scheduler use the **same image** with different commands (see `docker-compose.yml`).
- **Render / Railway:** one Postgres, one Redis, one web service (`backend/Dockerfile`), two background workers (`python -m app.worker`, `python -m app.scheduler`).
- **Dashboard:** serve `web/` behind the same domain via `web/nginx.conf`, or put it on Netlify (`web/netlify.toml`, set `VITE_API_URL` to the API URL).
- Set `APP_ENV=production`. The API **refuses to start** if `JWT_SECRET`, `ENCRYPTION_KEYS` or Postgres is missing, or if the mock provider is still enabled.
- The container runs `alembic upgrade head` on start. After changing models: `alembic revision --autogenerate -m "..."`.
- Back up `ENCRYPTION_KEYS` somewhere safe. Without them, saved merchant keys can't be decrypted.

### 2. Monnify (each merchant, Option A)
1. The merchant completes Monnify KYC: CAC documents, TIN, director BVN/NIN, and a settlement account in the business name.
2. Dashboard → **Settings → Payment provider**: paste the API key, secret key and contract code. We test them before saving, then encrypt them at rest.
3. Copy the **webhook URL** shown there (`…/webhooks/monnify/<merchant-token>`) into Monnify → Settings → Webhook URLs → *Transaction completion*.
4. Do one sandbox transfer, then tick "live keys".

**Check in the Monnify sandbox before the pilot:**
- Do invoice accounts accept a **part payment**, or reject anything but the exact amount? It depends on the contract's under/over-payment settings. If partials are rejected, part payments only happen through static (per-customer) accounts.
- Static accounts need the **customer's BVN or NIN** (CBN rule). The dashboard asks for it, passes it to Monnify, and does not store it.
- Monnify sends webhooks only from IP `35.242.133.146`. You can also allowlist it at your load balancer.

### 3. WhatsApp Cloud API
1. Create a Meta app with the WhatsApp product and add the business phone number. Set `WHATSAPP_TOKEN` (a permanent system-user token), `WHATSAPP_PHONE_NUMBER_ID` and `WHATSAPP_APP_SECRET`.
2. Webhook: callback URL `https://<api>/webhooks/whatsapp`, verify token = `WHATSAPP_VERIFY_TOKEN`. Subscribe to `messages`.
3. Submit the 8 templates in [`docs/whatsapp-templates.md`](docs/whatsapp-templates.md). Once they're approved, set `WHATSAPP_DRY_RUN=false`.
4. Merchants and staff use the bot from the phone number on their login. Unknown numbers get no reply.

### 4. Pilot checklist (Phase 4)
- [ ] `APP_ENV=production`, real secrets, `ALLOW_MOCK_PROVIDER=false`
- [ ] Each pilot merchant: Monnify keys saved, webhook URL pasted, one ₦100 live test
- [ ] Templates approved; send yourself an invoice end to end
- [ ] Watch `GET /admin/health` daily: failed webhooks, failed messages, unmatched payments, reconciliation runs

## What's not built yet
- **Paystack and Flutterwave adapters.** The interface is ready; they're stubs in `providers/__init__.py`.
- **Admin UI.** The admin endpoints exist; use them with curl or the `/docs` page in non-production.
- **Self-service password reset.** For now an owner resets a staff password by removing the login and adding it again; an owner's own reset is done manually by us.
- **PDF receipts.** The receipt is a printable page ("Save as PDF"). A server-side PDF can be added if merchants need it.
- **Login throttling** is per process. With several API instances, move it to Redis.
- **Option B** (sub-accounts, take rate) waits on the legal opinion.
