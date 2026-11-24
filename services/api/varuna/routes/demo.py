"""Chat endpoints for the demo agents. Each turn runs the agent (scripted, or model-driven when configured) through the Varuna SDK."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from varuna_demo import llm_agent
from varuna_demo.meridian import company
from varuna_demo.registry import SPECS
from varuna_demo.runner import retry_pending, run_turn
from varuna_sdk import VarunaClient

from .. import services
from ..db import session_scope
from ..localgw import LocalTransport
from ..models import Agent, Approval, ChatLog
from ..security import admin_user

router = APIRouter(prefix="/v1/demo", tags=["demo agents"], dependencies=[Depends(admin_user)])


class ChatIn(BaseModel):
    message: str
    session_id: str | None = None
    as_user: str | None = None   # who is chatting, for agents with a company directory (the IT agent)


class RetryIn(BaseModel):
    session_id: str
    approval_id: int


def _spec(key: str) -> dict:
    if key not in SPECS:
        raise HTTPException(404, "Unknown demo agent")
    return SPECS[key]


def _agent_info(key: str) -> dict:
    spec = SPECS[key]
    with session_scope() as db:
        a = db.scalar(select(Agent).where(Agent.slug == key))
        if not a:
            raise HTTPException(404, "Demo agent not set up")
        return {"key": key, "agent_id": a.id, "name": a.name, "domain": a.domain, "status": a.status,
                "paused_reason": a.paused_reason, "description": spec["description"], "greeting": spec["greeting"],
                "scenarios": spec["scenarios"], "personas": _personas(key),
                "default_persona": company.default_persona(key) if company.PERSONAS.get(key) else None,
                "model": llm_agent.model_name() if llm_agent.enabled(key) else None,
                "mode": services.mode_for(db, a.id)}


def _personas(key: str) -> list[dict]:
    return [{"id": h, "name": company.PEOPLE[h]["name"],
             "label": f"{company.PEOPLE[h]['name']}, {company.PEOPLE[h]['title']} ({company.PEOPLE[h]['department']})"}
            for h in company.PERSONAS.get(key, [])]


@router.get("/agents")
def demo_agents():
    return [_agent_info(k) for k in SPECS]


@router.get("/{key}/chat/{session_id}")
def get_chat(key: str, session_id: str):
    _spec(key)
    with session_scope() as db:
        log = db.get(ChatLog, session_id)
        return {"session_id": session_id, "messages": log.messages if log else [], "state": log.state if log else {}}


@router.post("/{key}/chat")
def chat(key: str, body: ChatIn):
    _spec(key)
    info = _agent_info(key)
    sid = body.session_id or f"ses_{uuid.uuid4().hex[:10]}"
    with session_scope() as db:
        log = db.get(ChatLog, sid)
        state = dict(log.state) if log else {}
    if body.as_user:
        if company.handle_of(body.as_user) not in company.PERSONAS.get(key, []):
            raise HTTPException(422, "Unknown person for this agent")
        state["as_user"] = company.handle_of(body.as_user)
    client = VarunaClient(LocalTransport(info["agent_id"], source="demo"), label=body.message[:48], session_id=sid)
    out, new_state = run_turn(key, client, body.message, state)
    new = [{"role": "user", "text": body.message}] + out
    with session_scope() as db:
        log = db.get(ChatLog, sid)
        if log is None:
            log = ChatLog(session_id=sid, agent_id=info["agent_id"], messages=[], state={})
            db.add(log)
        log.messages = list(log.messages) + new
        log.state = {**log.state, **new_state}
    return {"session_id": sid, "messages": new, "agent": _agent_info(key)}


@router.post("/{key}/retry")
def retry(key: str, body: RetryIn):
    _spec(key)
    info = _agent_info(key)
    with session_scope() as db:
        ap = db.get(Approval, body.approval_id)
        if not ap or ap.session_id != body.session_id:
            raise HTTPException(404, "Approval not found for this session")
        if ap.status != "approved":
            raise HTTPException(409, f"Approval is {ap.status}, not approved")
        pending = {"tool": ap.payload["tool"], "args": ap.payload["args"]}
        log = db.get(ChatLog, body.session_id)
        state = dict(log.state) if log else {}
    client = VarunaClient(LocalTransport(info["agent_id"], source="demo"), label="", session_id=body.session_id)
    out, _ = retry_pending(key, client, pending, state)
    with session_scope() as db:
        log = db.get(ChatLog, body.session_id)
        if log:
            log.messages = list(log.messages) + out
            log.state = {k: v for k, v in log.state.items() if k != "pending"}
    return {"session_id": body.session_id, "messages": out, "agent": _agent_info(key)}
