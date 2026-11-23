"""Login, meta, settings, sample data, live stream."""
import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from .. import __version__, seed
from ..config import settings
from ..db import get_db
from ..models import Setting
from ..scoring.judge import claude_enabled
from ..security import admin_user, check_login, make_token
from ..streams import bus

router = APIRouter(prefix="/v1", tags=["system"])


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn):
    if not check_login(body.username, body.password):
        raise HTTPException(401, "Wrong username or password")
    return {"token": make_token(body.username), "user": body.username}


@router.get("/meta")
def meta(db: DBSession = Depends(get_db)):
    return {"version": __version__, "judge": "claude" if claude_enabled() else "heuristic", "judge_model": settings.judge_model if claude_enabled() else None,
            "judge_timeout_s": settings.judge_timeout, "smtp_configured": settings.smtp is not None,
            "has_sample": seed.has_sample(db)}


class ChannelsIn(BaseModel):
    slack_webhook_url: str = ""
    webhook_url: str = ""
    email_to: str = ""


@router.get("/settings/channels", dependencies=[Depends(admin_user)])
def get_channels(db: DBSession = Depends(get_db)):
    row = db.get(Setting, "channels")
    return {"slack_webhook_url": "", "webhook_url": "", "email_to": "", **((row.value if row else {}) or {})}


@router.put("/settings/channels", dependencies=[Depends(admin_user)])
def put_channels(body: ChannelsIn, db: DBSession = Depends(get_db)):
    row = db.get(Setting, "channels")
    if row is None:
        row = Setting(key="channels", value={})
        db.add(row)
    row.value = body.model_dump()
    return row.value


@router.delete("/sample-data", dependencies=[Depends(admin_user)])
def clear_sample(db: DBSession = Depends(get_db)):
    seed.clear_sample(db)
    bus.publish("agent", {})
    return {"ok": True}


@router.post("/sample-data", dependencies=[Depends(admin_user)])
def regenerate_sample(bg: BackgroundTasks, db: DBSession = Depends(get_db)):
    seed.clear_sample(db)
    db.commit()
    bg.add_task(lambda: (seed.seed(), bus.publish("agent", {})))
    return {"ok": True, "note": "Generating sample history in the background"}


@router.get("/stream", dependencies=[Depends(admin_user)])
async def stream():
    q = bus.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
