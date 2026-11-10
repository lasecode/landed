"""App settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _db_url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./landed.db")
    # Render/Heroku give postgres://…; SQLAlchemy + psycopg 3 wants postgresql+psycopg://…
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


_render_url = os.getenv("RENDER_EXTERNAL_URL", "")


@dataclass
class Settings:
    app_name: str = os.getenv("APP_NAME", "Landed")
    env: str = os.getenv("APP_ENV", "development")  # development | production | test
    database_url: str = field(default_factory=_db_url)
    redis_url: str | None = os.getenv("REDIS_URL") or None
    # Public URL of this API, used to build webhook and receipt links
    public_api_url: str = os.getenv("PUBLIC_API_URL") or _render_url or "http://localhost:8000"
    public_web_url: str = os.getenv("PUBLIC_WEB_URL") or _render_url or "http://localhost:5173"
    # Folder with the built dashboard; when present the API serves it too (one service, one URL)
    web_dist: str = os.getenv("WEB_DIST", "")
    # Run the periodic jobs inside the API process (for single-service hosting without a worker)
    embed_scheduler: bool = _bool(os.getenv("EMBED_SCHEDULER"), False)
    cors_origins: list[str] = field(
        default_factory=lambda: [o.strip() for o in (os.getenv("CORS_ORIGINS") or _render_url or "http://localhost:5173").split(",") if o.strip()]
    )

    # Secrets
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-only-change-me")
    jwt_ttl_minutes: int = int(os.getenv("JWT_TTL_MINUTES", "720"))
    # Fernet key(s) for encrypting merchant provider keys. Comma separated: first one encrypts, all decrypt (rotation).
    encryption_keys: str = os.getenv("ENCRYPTION_KEYS", "")
    admin_token: str = os.getenv("ADMIN_TOKEN", "")  # platform admin API

    # Provider defaults
    monnify_base_url: str = os.getenv("MONNIFY_BASE_URL", "https://sandbox.monnify.com")
    allow_mock_provider: bool = _bool(os.getenv("ALLOW_MOCK_PROVIDER"), True)
    default_invoice_expiry_days: int = int(os.getenv("DEFAULT_INVOICE_EXPIRY_DAYS", "7"))

    # WhatsApp Cloud API (one platform number for all merchants in the MVP)
    wa_token: str = os.getenv("WHATSAPP_TOKEN", "")
    wa_phone_number_id: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    wa_app_secret: str = os.getenv("WHATSAPP_APP_SECRET", "")
    wa_verify_token: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    wa_api_version: str = os.getenv("WHATSAPP_API_VERSION", "v21.0")
    wa_template_lang: str = os.getenv("WHATSAPP_TEMPLATE_LANG", "en")
    # When WhatsApp credentials are empty, messages are logged instead of sent
    wa_dry_run: bool = _bool(os.getenv("WHATSAPP_DRY_RUN"), True)

    reconcile_every_hours: int = int(os.getenv("RECONCILE_EVERY_HOURS", "3"))
    free_plan_invoice_cap: int = int(os.getenv("FREE_PLAN_INVOICE_CAP", "50"))

    @property
    def is_production(self) -> bool:
        return self.env == "production"


settings = Settings()


def validate_production_settings() -> None:
    if not settings.is_production:
        return
    problems = []
    if settings.jwt_secret in ("", "dev-only-change-me"):
        problems.append("JWT_SECRET must be set")
    if not settings.encryption_keys:
        problems.append("ENCRYPTION_KEYS must be set")
    if settings.allow_mock_provider:
        problems.append("ALLOW_MOCK_PROVIDER must be false in production")
    if settings.database_url.startswith("sqlite"):
        problems.append("DATABASE_URL must point to PostgreSQL")
    if problems:
        raise RuntimeError("Unsafe production config: " + "; ".join(problems))
