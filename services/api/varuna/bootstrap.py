"""First-run setup: the two demo agents with charters, a risk register and response policies."""
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from varuna_demo.registry import SPECS

from . import services
from .compiler import heuristic
from .models import Agent, Charter, RiskFactor
from .security import new_agent_key


def bootstrap(db: DBSession) -> None:
    for key, spec in SPECS.items():
        agent = db.scalar(select(Agent).where(Agent.slug == key))
        if agent:
            continue
        agent = Agent(id=f"agt_{key}", slug=key, name=spec["name"], description=spec["description"], domain=spec["domain"],
                      status="active", api_key=new_agent_key(), is_demo=True, tools=spec["tools"],
                      internal_domains=spec["internal_domains"])
        db.add(agent)
        db.flush()
        data = heuristic.compile_charter(spec["charter_text"], spec["tools"])
        db.add(Charter(agent_id=agent.id, version=1, status="active", source_text=spec["charter_text"], data=data,
                       compiled_by="heuristic"))
        pol = services.ensure_policy(db, agent)
        for s in pol.stakeholders:
            if s["audience"] == "approver":
                s["label"] = spec["approver_label"]
        pol.stakeholders = [dict(s) for s in pol.stakeholders]
        for r in spec["risks"]:
            db.add(RiskFactor(applies_to=[agent.id], **r))
    db.flush()
