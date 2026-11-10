from __future__ import annotations

import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import serialize as S
from ..config import settings
from ..db import get_db
from ..deps import Ctx, current
from ..models import Merchant, Role, StaffUser
from ..schemas import LoginIn, SignupIn
from ..security import create_token, hash_password, normalize_phone, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

_attempts: dict[str, list[float]] = defaultdict(list)  # simple per-process login throttle


def _throttle(key: str) -> None:
    now = time.time()
    _attempts[key] = [t for t in _attempts[key] if now - t < 600]
    if len(_attempts[key]) >= 10:
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    _attempts[key].append(now)


def _session(u: StaffUser) -> dict:
    return {"token": create_token(u.id, u.merchant_id, u.role.value), "user": S.user(u), "merchant": S.merchant(u.merchant)}


@router.post("/signup")
def signup(body: SignupIn, db: Session = Depends(get_db)):
    phone = normalize_phone(body.phone)
    if not phone:
        raise HTTPException(400, "Enter a valid phone number")
    if db.scalar(select(StaffUser).where(StaffUser.phone == phone)):
        raise HTTPException(409, "That phone number already has an account. Sign in instead.")
    m = Merchant(business_name=body.business_name.strip(), phone=phone, email=body.email, alert_phone=phone,
                 provider="mock" if settings.allow_mock_provider else "monnify")
    db.add(m)
    db.flush()
    u = StaffUser(merchant_id=m.id, name=body.owner_name.strip(), phone=phone, password_hash=hash_password(body.password),
                  role=Role.owner)
    db.add(u)
    db.commit()
    db.refresh(u)
    return _session(u)


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    phone = normalize_phone(body.phone) or ""
    _throttle(f"{request.client.host if request.client else '-'}:{phone}")
    u = db.scalar(select(StaffUser).where(StaffUser.phone == phone))
    if not u or not u.active or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Phone number or password is wrong")
    return _session(u)


@router.get("/me")
def me(ctx: Ctx = Depends(current)):
    return {"user": S.user(ctx.user), "merchant": S.merchant(ctx.merchant)}
