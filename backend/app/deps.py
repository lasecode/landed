from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import Merchant, Role, StaffUser
from .security import decode_token


@dataclass
class Ctx:
    db: Session
    user: StaffUser
    merchant: Merchant

    @property
    def mid(self) -> int:
        return self.merchant.id

    @property
    def is_owner(self) -> bool:
        return self.user.role == Role.owner


def current(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Ctx:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    try:
        data = decode_token(authorization.split(" ", 1)[1])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again") from None
    user = db.get(StaffUser, int(data["sub"]))
    if not user or not user.active or user.merchant_id != data.get("mid"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not active")
    return Ctx(db=db, user=user, merchant=user.merchant)


def owner(ctx: Ctx = Depends(current)) -> Ctx:
    if not ctx.is_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the business owner can do this")
    return ctx


def platform_admin(x_admin_token: str | None = Header(default=None)) -> None:
    import hmac

    if not settings.admin_token or not x_admin_token or not hmac.compare_digest(settings.admin_token, x_admin_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
