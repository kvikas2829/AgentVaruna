"""Read-mostly endpoints for the dashboard, trace view, incidents and approvals."""
import hashlib
from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from ..db import get_db
from ..models import (Agent, Approval, Assessment, Event, Incident, Notification, Pattern, Session, utcnow)
from ..decision import band_rank
from ..scoring.engine import CATEGORY_LABEL
from ..scoring.text import args_hash
from ..security import admin_user
from ..seed import has_sample
from ..streams import bus
from ..utils import aware, iso, now_utc
from .common import approval_dict, event_dict, incident_dict, notification_dict, session_dict

router = APIRouter(prefix="/v1", tags=["monitor"], dependencies=[Depends(admin_user)])


@router.get("/overview")
def overview(agent_id: str | None = None, hours: int = 24, db: DBSession = Depends(get_db)):
    now = now_utc()
    since = now - timedelta(hours=hours)
    prev_since = since - timedelta(hours=hours)
    q = select(Assessment).where(Assessment.ts >= prev_since)
    if agent_id and agent_id != "all":
        q = q.where(Assessment.agent_id == agent_id)
    rows = [a for a in db.scalars(q)]
    cur = [a for a in rows if aware(a.ts) >= since]
    prev_actions = sum(1 for a in rows if aware(a.ts) < since and a.enforceable)
    acts = [a for a in cur if a.enforceable]
    n = len(acts)
    deviated = sum(1 for a in acts if a.deviation >= 40)
    totals = {"actions": n, "prev_actions": prev_actions, "deviated": deviated,
              "deviation_rate": round(100 * deviated / n, 1) if n else 0.0,
              "avg_risk": round(sum(a.risk for a in acts) / n, 1) if n else 0.0,
              "held": sum(1 for a in acts if a.verdict == "hold"), "blocked": sum(1 for a in acts if a.verdict == "block"),
              "slowed": sum(1 for a in acts if a.verdict == "delay"),
              "alerts": sum(1 for a in cur if band_rank(a.band) >= 1)}
    # hourly series, oldest first
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours - 1)
    buckets = [{"t": iso(start + timedelta(hours=i)), "max_risk": 0.0, "actions": 0} for i in range(hours)]
    for a in acts:
        i = int((aware(a.ts) - start).total_seconds() // 3600)
        if 0 <= i < hours:
            buckets[i]["max_risk"] = max(buckets[i]["max_risk"], a.risk)
            buckets[i]["actions"] += 1
    cats: dict[str, int] = defaultdict(int)
    for a in cur:
        for c, p in (a.likelihoods or {}).items():
            if p >= 0.5:
                cats[c] += 1
    categories = [{"category": c, "label": CATEGORY_LABEL.get(c, c), "events": k} for c, k in sorted(cats.items(), key=lambda x: -x[1])]
    # per agent
    agents_out = []
    for ag in db.scalars(select(Agent).order_by(Agent.created_at)):
        if agent_id and agent_id != "all" and ag.id != agent_id:
            continue
        mine = [a for a in acts if a.agent_id == ag.id]
        m = len(mine)
        rate = round(100 * sum(1 for a in mine if a.deviation >= 40) / m, 1) if m else 0.0
        trend = []
        for k in range(6):
            lo, hi = since + timedelta(hours=k * hours / 6), since + timedelta(hours=(k + 1) * hours / 6)
            seg = [a for a in mine if lo <= aware(a.ts) < hi]
            trend.append(round(100 * sum(1 for a in seg if a.deviation >= 40) / len(seg), 1) if seg else 0.0)
        agents_out.append({"id": ag.id, "name": ag.name, "status": ag.status, "actions": m, "deviation_rate": rate, "trend": trend,
                           "health": "at_risk" if rate >= 6 else "watch" if rate >= 3 else "healthy"})
    pats = db.scalars(select(Pattern).where(Pattern.ts >= since).order_by(Pattern.ts.desc()).limit(40)).all()
    names = {a.id: a.name for a in db.scalars(select(Agent))}
    merged: dict[tuple, dict] = {}
    for p in pats:
        if agent_id and agent_id != "all" and p.agent_id != agent_id:
            continue
        k = (p.agent_id, p.kind)
        if k not in merged:
            merged[k] = {"kind": p.kind, "title": p.title, "agent_id": p.agent_id, "agent_name": names.get(p.agent_id, ""),
                         "detail": p.detail, "sessions": 0, "last_seen": iso(p.ts), "session_id": p.session_id}
        merged[k]["sessions"] += 1
    flagged = sorted([a for a in acts if a.verdict != "allow" or band_rank(a.band) >= 2], key=lambda a: aware(a.ts), reverse=True)[:8]
    ev = {e.id: e for e in db.scalars(select(Event).where(Event.id.in_([a.event_id for a in flagged] or [0])))}
    attention = [{"event_id": a.event_id, "session_id": a.session_id, "ts": iso(a.ts), "agent_id": a.agent_id,
                  "agent_name": names.get(a.agent_id, ""), "summary": ev[a.event_id].summary if a.event_id in ev else "",
                  "deviation": a.deviation, "risk": a.risk, "verdict": a.verdict, "band": a.band} for a in flagged]
    return {"window_hours": hours, "totals": totals, "series": buckets, "categories": categories, "agents": agents_out,
            "patterns": sorted(merged.values(), key=lambda x: -x["sessions"])[:6], "attention": attention,
            "has_sample": has_sample(db)}


@router.get("/sessions")
def list_sessions(agent_id: str | None = None, limit: int = 40, only_flagged: bool = False, db: DBSession = Depends(get_db)):
    q = select(Session).order_by(Session.last_event_at.desc()).limit(limit)
    if agent_id and agent_id != "all":
        q = q.where(Session.agent_id == agent_id)
    if only_flagged:
        q = q.where(Session.max_risk >= 40)
    names = {a.id: a.name for a in db.scalars(select(Agent))}
    return [session_dict(s, names.get(s.agent_id, "")) for s in db.scalars(q)]


@router.get("/sessions/{sid}")
def get_session(sid: str, db: DBSession = Depends(get_db)):
    s = db.get(Session, sid)
    if not s:
        raise HTTPException(404, "Session not found")
    agent = db.get(Agent, s.agent_id)
    rows = db.execute(select(Event, Assessment).join(Assessment, Assessment.event_id == Event.id, isouter=True)
                      .where(Event.session_id == sid).order_by(Event.seq, Event.id)).all()
    approvals = {ap.event_id: ap for ap in db.scalars(select(Approval).where(Approval.session_id == sid))}
    inc = db.scalar(select(Incident).where(Incident.session_id == sid).order_by(Incident.id.desc()))
    return {"session": session_dict(s, agent.name if agent else ""), "agent": {"id": agent.id, "name": agent.name, "status": agent.status,
            "paused_reason": agent.paused_reason} if agent else None,
            "events": [event_dict(e, a, approvals.get(e.id)) for e, a in rows],
            "incident": incident_dict(inc, agent.name if agent else "") if inc else None}


class FeedbackIn(BaseModel):
    kind: str  # false_positive | confirmed | allow_once
    note: str = ""


@router.post("/events/{event_id}/feedback")
def feedback(event_id: int, body: FeedbackIn, user: str = Depends(admin_user), db: DBSession = Depends(get_db)):
    if body.kind not in ("false_positive", "confirmed", "allow_once"):
        raise HTTPException(422, "kind must be false_positive, confirmed or allow_once")
    ev = db.get(Event, event_id)
    a = ev.assessment if ev else None
    if not ev or not a:
        raise HTTPException(404, "Event not found")
    a.feedback, a.feedback_note = body.kind, body.note
    if body.kind == "allow_once" and ev.type == "tool_call":
        tool, args = ev.payload.get("tool", ""), ev.payload.get("args") or {}
        db.add(Approval(agent_id=ev.agent_id, session_id=ev.session_id, event_id=ev.id, tool=tool, args_hash=args_hash(tool, args),
                        payload=ev.payload, status="approved", summary=ev.summary, decision_score=a.decision_score,
                        decided_at=utcnow(), decided_by=user, note=body.note or "Allowed once from the trace view"))
    if body.kind == "false_positive":
        inc = db.scalar(select(Incident).where(Incident.session_id == ev.session_id, Incident.event_id == ev.id))
        if inc:
            inc.status, inc.note = "false_positive", body.note
    bus.publish("assessment", {"session_id": ev.session_id, "event_id": ev.id})
    return {"ok": True}


# ------------------------------------------------------------ approvals / incidents / notifications

class DecideIn(BaseModel):
    decision: str  # approve | reject
    note: str = ""


@router.get("/approvals")
def list_approvals(status: str | None = None, agent_id: str | None = None, db: DBSession = Depends(get_db)):
    q = select(Approval).order_by(Approval.requested_at.desc()).limit(100)
    if status:
        q = q.where(Approval.status.in_(status.split(",")))
    if agent_id and agent_id != "all":
        q = q.where(Approval.agent_id == agent_id)
    names = {a.id: a.name for a in db.scalars(select(Agent))}
    return [approval_dict(ap, names.get(ap.agent_id, "")) for ap in db.scalars(q)]


@router.post("/approvals/{aid}/decide")
def decide(aid: int, body: DecideIn, user: str = Depends(admin_user), db: DBSession = Depends(get_db)):
    ap = db.get(Approval, aid)
    if not ap:
        raise HTTPException(404, "Approval not found")
    if ap.status != "pending":
        raise HTTPException(409, f"Already {ap.status}")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve or reject")
    ap.status = "approved" if body.decision == "approve" else "rejected"
    ap.decided_by, ap.decided_at, ap.note = user, utcnow(), body.note
    bus.publish("approval", {"approval_id": ap.id, "agent_id": ap.agent_id, "status": ap.status})
    return approval_dict(ap)


@router.get("/incidents")
def list_incidents(status: str | None = None, agent_id: str | None = None, db: DBSession = Depends(get_db)):
    q = select(Incident).order_by(Incident.updated_at.desc()).limit(100)
    if status:
        q = q.where(Incident.status.in_(status.split(",")))
    if agent_id and agent_id != "all":
        q = q.where(Incident.agent_id == agent_id)
    names = {a.id: a.name for a in db.scalars(select(Agent))}
    return [incident_dict(i, names.get(i.agent_id, "")) for i in db.scalars(q)]


class IncidentPatch(BaseModel):
    status: str
    note: str | None = None


@router.patch("/incidents/{iid}")
def patch_incident(iid: int, body: IncidentPatch, db: DBSession = Depends(get_db)):
    i = db.get(Incident, iid)
    if not i:
        raise HTTPException(404, "Incident not found")
    if body.status not in ("open", "acknowledged", "resolved", "false_positive"):
        raise HTTPException(422, "bad status")
    i.status, i.updated_at = body.status, utcnow()
    if body.note is not None:
        i.note = body.note
    return incident_dict(i)


@router.get("/notifications")
def list_notifications(agent_id: str | None = None, limit: int = 50, db: DBSession = Depends(get_db)):
    q = select(Notification).order_by(Notification.id.desc()).limit(limit)
    if agent_id and agent_id != "all":
        q = q.where(Notification.agent_id == agent_id)
    names = {a.id: a.name for a in db.scalars(select(Agent))}
    return [notification_dict(n, names.get(n.agent_id, "")) for n in db.scalars(q)]
