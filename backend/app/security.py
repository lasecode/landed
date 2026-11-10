"""Encryption of provider keys, password hashing, JWT and phone helpers."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .config import settings

# ---------- provider key encryption (at rest) ----------

_DEV_KEY = Fernet.generate_key()  # used only when ENCRYPTION_KEYS is unset outside production


def _fernet() -> MultiFernet:
    raw = [k.strip() for k in settings.encryption_keys.split(",") if k.strip()]
    if not raw:
        if settings.is_production:
            raise RuntimeError("ENCRYPTION_KEYS is required in production")
        raw = [_DEV_KEY.decode()]
    return MultiFernet([Fernet(_as_fernet_key(k)) for k in raw])


def _as_fernet_key(k: str) -> bytes:
    """Accept a real Fernet key, or derive one from any long random secret (e.g. a host-generated value)."""
    import base64
    import hashlib

    try:
        Fernet(k.encode())
        return k.encode()
    except ValueError:
        return base64.urlsafe_b64encode(hashlib.sha256(k.encode()).digest())


def encrypt_keys(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_keys(token: str | None) -> dict:
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as e:  # never include the token in the message
        raise RuntimeError("Could not decrypt provider keys (wrong ENCRYPTION_KEYS?)") from e


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    return ("•" * 6) + value[-4:]


# ---------- passwords ----------

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


# ---------- JWT ----------

def create_token(user_id: int, merchant_id: int, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "mid": merchant_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])


# ---------- phones ----------

def normalize_phone(phone: str | None) -> str | None:
    """Return a Nigerian number in international digits form, e.g. 2348031234567."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return None
    if digits.startswith("0") and len(digits) == 11:
        digits = "234" + digits[1:]
    elif len(digits) == 10 and digits[0] in "789":
        digits = "234" + digits
    return digits
