"""Company level: the policy every agent answers to, the company risk register, and observation or enforcement mode."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from .. import compiler, services
from ..db import get_db
from ..models import Agent, CompanyPolicy, KnownResource
from ..security import admin_user
from ..streams import bus
from ..utils import iso

router = APIRouter(prefix="/v1", tags=["company"], dependencies=[Depends(admin_user)])


def policy_dict(p: CompanyPolicy) -> dict:
    return {"version": p.version, "status": p.status, "source_text": p.source_text, "data": p.data,
            "compiled_by": p.compiled_by, "created_by": p.created_by, "created_at": iso(p.created_at)}


def _all_tools(db: DBSession) -> list[dict]:
    return [t for a in db.scalars(select(Agent)) for t in (a.tools or [])]


class PolicyTextIn(BaseModel):
    text: str = Field(min_length=10)


@router.get("/company/policy")
def get_policy(db: DBSession = Depends(get_db)):
    cur = services.active_company(db)
    history = [{"version": p.version, "status": p.status, "created_at": iso(p.created_at), "created_by": p.created_by}
               for p in db.scalars(select(CompanyPolicy).order_by(CompanyPolicy.version.desc()))]
    return {"active": policy_dict(cur) if cur else None, "history": history}


@router.post("/company/policy/compile")
def preview_policy(body: PolicyTextIn, db: DBSession = Depends(get_db)):
    data, by = compiler.compile_company_policy(body.text, _all_tools(db))
    return {"data": data, "compiled_by": by}


@router.put("/company/policy")
def save_policy(body: PolicyTextIn, db: DBSession = Depends(get_db), user: str = Depends(admin_user)):
    """Saving makes a new active version at once; every agent is judged against it from the next action."""
    data, by = compiler.compile_company_policy(body.text, _all_tools(db))
    last = db.scalar(select(CompanyPolicy).order_by(CompanyPolicy.version.desc()))
    for p in db.scalars(select(CompanyPolicy).where(CompanyPolicy.status == "active")):
        p.status = "archived"
    p = CompanyPolicy(version=(last.version + 1) if last else 1, status="active", source_text=body.text, data=data,
                      compiled_by=by, created_by=user if isinstance(user, str) else "admin")
    db.add(p)
    db.flush()
    bus.publish("settings", {"company_policy": p.version})
    return policy_dict(p)


@router.post("/company/policy/{version}/activate")
def activate_policy(version: int, db: DBSession = Depends(get_db)):
    target = db.scalar(select(CompanyPolicy).where(CompanyPolicy.version == version))
    if not target:
        raise HTTPException(404, "No such version")
    for p in db.scalars(select(CompanyPolicy).where(CompanyPolicy.status == "active")):
        p.status = "archived"
    target.status = "active"
    return policy_dict(target)


@router.get("/company/resources")
def known_resources(db: DBSession = Depends(get_db)):
    """What Varuna knows identifiers refer to, imported from the company's inventories."""
    return [{"key": r.key, "text": r.text, "source": r.source} for r in db.scalars(select(KnownResource).order_by(KnownResource.key))]


class ModeIn(BaseModel):
    default: str | None = Field(default=None, pattern="^(enforce|observe)$")


class AgentModeIn(BaseModel):
    mode: str | None = Field(default=None, pattern="^(enforce|observe)$")  # None follows the company default


@router.get("/settings/mode")
def get_mode(db: DBSession = Depends(get_db)):
    return services.get_modes(db)


@router.put("/settings/mode")
def put_mode(body: ModeIn, db: DBSession = Depends(get_db)):
    return services.set_modes(db, default=body.default)


@router.put("/agents/{agent_id}/mode")
def put_agent_mode(agent_id: str, body: AgentModeIn, db: DBSession = Depends(get_db)):
    if not db.get(Agent, agent_id):
        raise HTTPException(404, "Agent not found")
    return services.set_modes(db, agent_id=agent_id, agent_mode=body.mode)
