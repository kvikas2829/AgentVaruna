"""Setup endpoints: agents, charters, risk register, response policies."""
import re
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from .. import compiler, services
from ..db import get_db
from ..decision import DEFAULT_THRESHOLDS
from ..models import Agent, Assessment, Charter, ResponsePolicy, RiskFactor
from ..scoring.judge import CATEGORIES
from ..security import admin_user, new_agent_key
from ..streams import bus
from ..utils import iso, now_utc
from .common import agent_dict

router = APIRouter(prefix="/v1", tags=["setup"], dependencies=[Depends(admin_user)])


def get_agent(db: DBSession, agent_id: str) -> Agent:
    a = db.get(Agent, agent_id)
    if not a:
        raise HTTPException(404, "Agent not found")
    return a


# ----------------------------------------------------------------- agents

def infer_tool(name: str) -> dict:
    n = name.lower()
    kind = ("delete" if re.search(r"delete|remove|drop|purge", n) else "export" if "export" in n else
            "admin" if re.search(r"isolate|quarantine|disable|reset|grant|revoke|suspend|unlock|assign|block_", n) else
            "payment" if re.search(r"pay|charge|book|purchase|refund|transfer", n) else
            "outbound" if re.search(r"send|email|post|publish|message", n) else
            "search" if "search" in n else "read" if re.search(r"read|get|list|fetch|lookup", n) else "write")
    return {"name": name, "description": name.replace("_", " "), "impact": "high" if kind in ("delete", "export", "payment", "outbound", "admin") else "low",
            "kind": kind, "keywords": [x for x in name.lower().split("_") if len(x) > 2]}


class AgentIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = ""
    tools: list[str | dict] = Field(default_factory=list)
    internal_domains: list[str] = Field(default_factory=list)


@router.get("/agents")
def list_agents(db: DBSession = Depends(get_db)):
    return [agent_dict(db, a) for a in db.scalars(select(Agent).order_by(Agent.created_at, Agent.name))]


@router.post("/agents")
def create_agent(body: AgentIn, db: DBSession = Depends(get_db)):
    slug = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-") or "agent"
    if db.scalar(select(Agent).where(Agent.slug == slug)):
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"
    tools = [t if isinstance(t, dict) else infer_tool(t) for t in body.tools]
    a = Agent(id=f"agt_{uuid.uuid4().hex[:8]}", slug=slug, name=body.name, description=body.description, domain="custom",
              status="draft", api_key=new_agent_key(), tools=tools, internal_domains=body.internal_domains)
    db.add(a)
    db.flush()
    services.ensure_policy(db, a)
    bus.publish("agent", {"agent_id": a.id})
    return {**agent_dict(db, a), "api_key": a.api_key}


@router.get("/agents/{agent_id}")
def get_agent_detail(agent_id: str, db: DBSession = Depends(get_db)):
    a = get_agent(db, agent_id)
    return {**agent_dict(db, a), "api_key": a.api_key, "tools": a.tools}


@router.put("/agents/{agent_id}/tools")
def set_tools(agent_id: str, tools: list[dict], db: DBSession = Depends(get_db)):
    a = get_agent(db, agent_id)
    a.tools = tools
    return {"ok": True, "tool_count": len(tools)}


@router.post("/agents/{agent_id}/pause")
def pause(agent_id: str, db: DBSession = Depends(get_db)):
    a = get_agent(db, agent_id)
    services.pause_agent(db, a, "Paused by a person.")
    return agent_dict(db, a)


@router.post("/agents/{agent_id}/resume")
def resume(agent_id: str, db: DBSession = Depends(get_db)):
    a = get_agent(db, agent_id)
    services.resume_agent(db, a)
    return agent_dict(db, a)


# ----------------------------------------------------------------- charters

def charter_dict(c: Charter) -> dict:
    return {"id": c.id, "agent_id": c.agent_id, "version": c.version, "status": c.status, "source_text": c.source_text,
            "data": c.data, "compiled_by": c.compiled_by, "created_at": iso(c.created_at)}


class CompileIn(BaseModel):
    text: str = Field(min_length=10)


class CharterEdit(BaseModel):
    data: dict
    source_text: str | None = None


def _next_version(db: DBSession, agent_id: str) -> int:
    return (db.scalar(select(func.max(Charter.version)).where(Charter.agent_id == agent_id)) or 0) + 1


@router.get("/agents/{agent_id}/charters")
def list_charters(agent_id: str, db: DBSession = Depends(get_db)):
    get_agent(db, agent_id)
    return [charter_dict(c) for c in db.scalars(select(Charter).where(Charter.agent_id == agent_id).order_by(Charter.version.desc()))]


@router.post("/agents/{agent_id}/charters/compile")
def compile_charter(agent_id: str, body: CompileIn, db: DBSession = Depends(get_db)):
    a = get_agent(db, agent_id)
    data, by = compiler.compile_charter(body.text, a.tools or [])
    c = Charter(agent_id=a.id, version=_next_version(db, a.id), status="draft", source_text=body.text, data=data, compiled_by=by)
    db.add(c)
    db.flush()
    return charter_dict(c)


@router.put("/agents/{agent_id}/charters/{version}")
def edit_charter(agent_id: str, version: int, body: CharterEdit, db: DBSession = Depends(get_db)):
    get_agent(db, agent_id)
    c = db.scalar(select(Charter).where(Charter.agent_id == agent_id, Charter.version == version))
    if not c:
        raise HTTPException(404, "Charter version not found")
    if c.status == "draft":
        c.data = body.data
        if body.source_text is not None:
            c.source_text = body.source_text
        c.compiled_by = c.compiled_by if c.compiled_by != "manual" else "manual"
        return charter_dict(c)
    n = Charter(agent_id=agent_id, version=_next_version(db, agent_id), status="draft", data=body.data,
                source_text=body.source_text if body.source_text is not None else c.source_text, compiled_by="manual")
    db.add(n)
    db.flush()
    return charter_dict(n)


@router.post("/agents/{agent_id}/charters/{version}/activate")
def activate_charter(agent_id: str, version: int, db: DBSession = Depends(get_db)):
    a = get_agent(db, agent_id)
    c = db.scalar(select(Charter).where(Charter.agent_id == agent_id, Charter.version == version))
    if not c:
        raise HTTPException(404, "Charter version not found")
    for old in db.scalars(select(Charter).where(Charter.agent_id == agent_id, Charter.status == "active")):
        old.status = "archived"
    c.status = "active"
    if a.status == "draft":
        a.status = "active"
    bus.publish("agent", {"agent_id": a.id})
    return charter_dict(c)


# ----------------------------------------------------------------- risks

class RiskIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str = ""
    category: str
    severity: int = Field(ge=1, le=5)
    extent_text: str = ""
    extent_factor: float = Field(default=1.0, ge=0.1, le=1.0)
    applies_to: list[str] = Field(default_factory=list)


def risk_dict(r: RiskFactor) -> dict:
    return {"id": r.id, "name": r.name, "description": r.description, "category": r.category, "severity": r.severity,
            "extent_text": r.extent_text, "extent_factor": r.extent_factor, "applies_to": r.applies_to}


def _check_cat(c: str):
    if c not in CATEGORIES:
        raise HTTPException(422, f"category must be one of {CATEGORIES}")


@router.get("/risks")
def list_risks(db: DBSession = Depends(get_db)):
    return [risk_dict(r) for r in db.scalars(select(RiskFactor).order_by(RiskFactor.severity.desc(), RiskFactor.id))]


@router.post("/risks/compile")
def compile_risks(body: CompileIn):
    risks, by = compiler.compile_risks(body.text)
    return {"compiled_by": by, "risks": risks}


@router.post("/risks")
def create_risk(body: RiskIn, db: DBSession = Depends(get_db)):
    _check_cat(body.category)
    r = RiskFactor(**body.model_dump())
    db.add(r)
    db.flush()
    return risk_dict(r)


@router.put("/risks/{rid}")
def update_risk(rid: int, body: RiskIn, db: DBSession = Depends(get_db)):
    _check_cat(body.category)
    r = db.get(RiskFactor, rid)
    if not r:
        raise HTTPException(404, "Risk not found")
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    return risk_dict(r)


@router.delete("/risks/{rid}")
def delete_risk(rid: int, db: DBSession = Depends(get_db)):
    r = db.get(RiskFactor, rid)
    if r:
        db.delete(r)
    return {"ok": True}


# ----------------------------------------------------------------- responses

class PolicyIn(BaseModel):
    thresholds: dict
    slow_action: str = Field(default="hold", pattern="^(hold|delay)$")
    delay_seconds: int = Field(default=5, ge=1, le=300)
    pause_after_blocks: int = Field(default=2, ge=0, le=20)
    fail_mode: str = Field(default="closed_high_impact", pattern="^(closed_high_impact|open)$")
    stakeholders: list[dict] = Field(default_factory=list)


def policy_dict(p: ResponsePolicy) -> dict:
    return {"agent_id": p.agent_id, "thresholds": {**DEFAULT_THRESHOLDS, **(p.thresholds or {})}, "slow_action": p.slow_action,
            "delay_seconds": p.delay_seconds, "pause_after_blocks": p.pause_after_blocks, "fail_mode": p.fail_mode,
            "stakeholders": p.stakeholders}


def _check_thresholds(th: dict):
    try:
        v = [float(th[k]) for k in ("notify_owner", "notify_security", "slow", "block")]
    except (KeyError, ValueError):
        raise HTTPException(422, "thresholds need notify_owner, notify_security, slow and block")
    if not (0 < v[0] < v[1] < v[2] < v[3] <= 100):
        raise HTTPException(422, "Thresholds must rise: notify owner < notify security < slow < block, all at most 100.")


@router.get("/agents/{agent_id}/responses")
def get_policy(agent_id: str, db: DBSession = Depends(get_db)):
    return policy_dict(services.ensure_policy(db, get_agent(db, agent_id)))


@router.put("/agents/{agent_id}/responses")
def put_policy(agent_id: str, body: PolicyIn, db: DBSession = Depends(get_db)):
    p = services.ensure_policy(db, get_agent(db, agent_id))
    _check_thresholds(body.thresholds)
    p.thresholds = {k: float(body.thresholds[k]) for k in ("notify_owner", "notify_security", "slow", "block")}
    p.slow_action, p.delay_seconds, p.pause_after_blocks, p.fail_mode = body.slow_action, body.delay_seconds, body.pause_after_blocks, body.fail_mode
    if body.stakeholders:
        p.stakeholders = body.stakeholders
    return policy_dict(p)


@router.post("/agents/{agent_id}/responses/preview")
def preview_policy(agent_id: str, body: PolicyIn, hours: int = 24, db: DBSession = Depends(get_db)):
    """Replay recent assessments against proposed thresholds. Decision scores are stored, so this needs no re-scoring."""
    a = get_agent(db, agent_id)
    _check_thresholds(body.thresholds)
    cur = {**DEFAULT_THRESHOLDS, **(services.ensure_policy(db, a).thresholds or {})}
    new = {k: float(body.thresholds[k]) for k in ("notify_owner", "notify_security", "slow", "block")}
    since = now_utc() - timedelta(hours=hours)
    rows = db.execute(select(Assessment.decision_score, Assessment.feedback).where(
        Assessment.agent_id == agent_id, Assessment.ts >= since, Assessment.enforceable.is_(True))).all()

    def tally(th):
        return {"alerts": sum(1 for s, _ in rows if s >= th["notify_owner"]), "slowed": sum(1 for s, _ in rows if th["slow"] <= s < th["block"]),
                "blocked": sum(1 for s, _ in rows if s >= th["block"])}
    return {"window_hours": hours, "actions": len(rows), "current": tally(cur), "proposed": tally(new),
            "false_positives_marked": sum(1 for _, f in rows if f == "false_positive")}
