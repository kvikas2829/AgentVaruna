"""Single-organization login (HMAC-signed bearer token) and per-agent API keys."""
import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import Agent

TOKEN_TTL = 12 * 3600


def _sign(payload: bytes) -> str:
    return hmac.new(settings.secret.encode(), payload, hashlib.sha256).hexdigest()


def make_token(user: str, ttl: int = TOKEN_TTL) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"u": user, "exp": int(time.time()) + ttl}).encode())
    return f"{body.decode()}.{_sign(body)}"


def verify_token(token: str) -> str | None:
    try:
        body, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(body.encode())):
            return None
        data = json.loads(base64.urlsafe_b64decode(body))
        return data["u"] if data["exp"] > time.time() else None
    except Exception:
        return None


def check_login(user: str, password: str) -> bool:
    return hmac.compare_digest(user, settings.admin_user) and hmac.compare_digest(password, settings.admin_password)


def new_agent_key() -> str:
    return "vk_" + secrets.token_urlsafe(24)


def admin_user(authorization: str | None = Header(default=None), token: str | None = Query(default=None)) -> str:
    """Bearer header, or ?token= for EventSource, which cannot set headers."""
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:]
    raw = raw or token
    user = verify_token(raw) if raw else None
    if not user:
        raise HTTPException(401, "Sign in required")
    return user


def agent_from_key(x_varuna_key: str | None = Header(default=None), db: Session = Depends(get_db)) -> Agent:
    if not x_varuna_key:
        raise HTTPException(401, "Missing X-Varuna-Key")
    agent = db.scalar(select(Agent).where(Agent.api_key == x_varuna_key))
    if not agent:
        raise HTTPException(401, "Unknown agent key")
    return agent
