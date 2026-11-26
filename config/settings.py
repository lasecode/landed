"""Landed settings. Everything secret or environment-specific comes from environment variables
(see .env.example). Safe local defaults; production refuses to start with unsafe values."""
from __future__ import annotations

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


APP_ENV = os.getenv("APP_ENV", "development")  # development | staging | production
DEBUG = env_bool("DEBUG", APP_ENV == "development")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-insecure-key-change-me")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "")
PUBLIC_URL = (os.getenv("PUBLIC_URL") or RENDER_URL or "http://localhost:8000").rstrip("/")
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h.strip()]
if os.getenv("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
CSRF_TRUSTED_ORIGINS = [PUBLIC_URL] if PUBLIC_URL.startswith("https://") else []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "accounts",
    "payments",
    "messaging",
    "dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "accounts.middleware.OnboardingMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "dashboard.context.app_context",
            ],
        },
    },
]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"), conn_max_age=600
    )
}

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "overview"
LOGOUT_REDIRECT_URL = "login"

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Lagos"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
                    if not DEBUG else "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Email (password reset). Console backend until SMTP is configured.
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Landed <no-reply@landed.ng>")

# Behind Render's (or any) HTTPS proxy
if RENDER_URL or APP_ENV != "development":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Security in production
if APP_ENV == "production":
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14

# ---- Background jobs (Celery). Without REDIS_URL, tasks run inline (dev, tests, free hosting).
REDIS_URL = os.getenv("REDIS_URL", "")
CELERY_BROKER_URL = REDIS_URL or "memory://"
CELERY_TASK_ALWAYS_EAGER = not REDIS_URL
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "expire-invoices": {"task": "payments.tasks.expire_invoices", "schedule": 15 * 60},
    "reconcile": {"task": "payments.tasks.reconcile", "schedule": 3 * 60 * 60},
    "reminders": {"task": "payments.tasks.send_reminders", "schedule": 60 * 60},
}
# Single-instance hosting without a worker: run the periodic jobs inside the web process.
EMBED_SCHEDULER = env_bool("EMBED_SCHEDULER", False)

# ---- Payments
PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "mock")  # mock | paystack
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_BASE_URL = os.getenv("PAYSTACK_BASE_URL", "https://api.paystack.co")
PAYSTACK_DVA_BANK = os.getenv("PAYSTACK_DVA_BANK", "wema-bank")  # test-bank in test mode
# Landed's cut of every payment, in percent (Paystack sub-account percentage_charge)
LANDED_FEE_PERCENT = float(os.getenv("LANDED_FEE_PERCENT", "1.0"))
# Who pays Paystack's fee: "subaccount" (seller) or "account" (Landed)
PAYSTACK_FEE_BEARER = os.getenv("PAYSTACK_FEE_BEARER", "subaccount")
PAYOUT_BUSINESS_DAYS = int(os.getenv("PAYOUT_BUSINESS_DAYS", "1"))  # when we tell sellers to expect their money
PAY_LINK_HOURS = int(os.getenv("PAY_LINK_HOURS", "8"))  # Pay with Transfer accounts: 15 min to 8 h
INVOICE_DEFAULT_DUE_DAYS = int(os.getenv("INVOICE_DEFAULT_DUE_DAYS", "7"))

# ---- WhatsApp Cloud API (one platform number)
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v21.0")
WHATSAPP_TEMPLATE_LANG = os.getenv("WHATSAPP_TEMPLATE_LANG", "en")
WHATSAPP_DRY_RUN = env_bool("WHATSAPP_DRY_RUN", True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}

if APP_ENV == "production":
    _problems = []
    if SECRET_KEY.startswith("dev-only"):
        _problems.append("SECRET_KEY")
    if PAYMENT_PROVIDER == "mock":
        _problems.append("PAYMENT_PROVIDER must be paystack")
    if PAYMENT_PROVIDER == "paystack" and not PAYSTACK_SECRET_KEY:
        _problems.append("PAYSTACK_SECRET_KEY")
    if DATABASES["default"]["ENGINE"].endswith("sqlite3"):
        _problems.append("DATABASE_URL must be PostgreSQL")
    if _problems:
        raise RuntimeError("Unsafe production settings: " + ", ".join(_problems))
