from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from .. import serialize as S
from ..config import settings
from ..deps import Ctx, owner
from ..models import Role, StaffUser
from ..providers import SUPPORTED, ProviderError, build_provider
from ..schemas import BusinessIn, ProviderIn, StaffIn
from ..security import decrypt_keys, encrypt_keys, hash_password, mask_secret, normalize_phone

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _provider_view(ctx: Ctx) -> dict:
    m = ctx.merchant
    keys = decrypt_keys(m.provider_keys)
    return {
        "provider": m.provider,
        "connected": bool(m.provider_keys) or m.provider == "mock",
        "api_key": mask_secret(keys.get("api_key")),
        "secret_key": mask_secret(keys.get("secret_key")),
        "contract_code": keys.get("contract_code", ""),
        "live": bool(keys.get("live")),
        "webhook_url": f"{settings.public_api_url}/webhooks/{m.provider}/{m.webhook_token}",
        "available": [p for p in SUPPORTED if p != "mock" or settings.allow_mock_provider],
    }


@router.get("")
def get_settings(ctx: Ctx = Depends(owner)):
    staff = ctx.db.scalars(select(StaffUser).where(StaffUser.merchant_id == ctx.mid).order_by(StaffUser.id)).all()
    return {"business": S.merchant(ctx.merchant), "provider": _provider_view(ctx), "staff": [S.user(u) for u in staff]}


@router.patch("/business")
def update_business(body: BusinessIn, ctx: Ctx = Depends(owner)):
    m = ctx.merchant
    data = body.model_dump(exclude_unset=True)
    for f in ("phone", "alert_phone"):
        if f in data:
            data[f] = normalize_phone(data[f])
    for k, v in data.items():
        setattr(m, k, v)
    ctx.db.commit()
    return S.merchant(m)


@router.put("/provider")
def set_provider(body: ProviderIn, ctx: Ctx = Depends(owner)):
    m = ctx.merchant
    if body.provider not in SUPPORTED or (body.provider == "mock" and not settings.allow_mock_provider):
        raise HTTPException(400, "Provider not available")
    keys: dict = {}
    if body.provider != "mock":
        old = decrypt_keys(m.provider_keys) if m.provider == body.provider else {}
        # empty fields keep the saved value, so the owner never has to re-type a secret
        keys = {
            "api_key": body.api_key or old.get("api_key"),
            "secret_key": body.secret_key or old.get("secret_key"),
            "contract_code": body.contract_code or old.get("contract_code"),
            "live": body.live,
        }
        if not all(keys[k] for k in ("api_key", "secret_key", "contract_code")):
            raise HTTPException(400, "API key, secret key and contract code are all required")
        try:
            build_provider(body.provider, keys).test_connection()
        except ProviderError as e:
            raise HTTPException(400, f"Couldn't connect: {e}") from None
    m.provider = body.provider
    m.provider_keys = encrypt_keys(keys) if keys else None
    ctx.db.commit()
    return _provider_view(ctx)


@router.post("/staff")
def add_staff(body: StaffIn, ctx: Ctx = Depends(owner)):
    phone = normalize_phone(body.phone)
    if not phone:
        raise HTTPException(400, "Enter a valid phone number")
    try:
        role = Role(body.role)
    except ValueError:
        raise HTTPException(400, "Role must be owner or staff") from None
    existing = ctx.db.scalar(select(StaffUser).where(StaffUser.phone == phone))
    if existing and existing.merchant_id == ctx.mid and not existing.active:
        # re-adding a removed login (also how an owner resets a staff password)
        existing.active, existing.name, existing.role = True, body.name.strip(), role
        existing.password_hash = hash_password(body.password)
        ctx.db.commit()
        return S.user(existing)
    if existing:
        raise HTTPException(409, "That number already has a login")
    u = StaffUser(merchant_id=ctx.mid, name=body.name.strip(), phone=phone, password_hash=hash_password(body.password), role=role)
    ctx.db.add(u)
    ctx.db.commit()
    return S.user(u)


@router.delete("/staff/{uid}")
def remove_staff(uid: int, ctx: Ctx = Depends(owner)):
    u = ctx.db.get(StaffUser, uid)
    if not u or u.merchant_id != ctx.mid:
        raise HTTPException(404, "Not found")
    if u.id == ctx.user.id:
        raise HTTPException(400, "You can't remove yourself")
    u.active = False
    ctx.db.commit()
    return {"ok": True}
