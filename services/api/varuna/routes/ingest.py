"""Endpoints called by monitored agents, authenticated with the agent's key."""
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from .. import services
from ..db import get_db, session_scope
from ..models import Agent
from ..security import agent_from_key

router = APIRouter(prefix="/v1", tags=["agent ingest"])


class GuardIn(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)
    session_id: str | None = None
    label: str | None = None


class EventIn(BaseModel):
    type: str = Field(pattern="^(thought|tool_result|message)$")
    payload: dict = Field(default_factory=dict)
    session_id: str | None = None
    label: str | None = None


def _deep(event_id: int):
    with session_scope() as db:
        services.deep_review(db, event_id)


@router.post("/guard")
def guard(body: GuardIn, agent: Agent = Depends(agent_from_key), db: DBSession = Depends(get_db)):
    return services.handle_guard(db, agent, body.tool, body.args, session_id=body.session_id, label=body.label)


@router.post("/events")
def events(body: EventIn, bg: BackgroundTasks, agent: Agent = Depends(agent_from_key), db: DBSession = Depends(get_db)):
    out = services.handle_trace_event(db, agent, body.type, body.payload, session_id=body.session_id, label=body.label)
    if body.type == "tool_result" and body.payload.get("call_event_id"):
        bg.add_task(_deep, int(body.payload["call_event_id"]))
    return out
