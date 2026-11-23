"""Serializers shared by routes."""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from ..models import Agent, Approval, Assessment, Charter, Event, Incident, Notification, Session
from ..utils import aware, iso, now_utc


def agent_dict(db: DBSession, a: Agent, with_stats: bool = True) -> dict:
    ch = db.scalar(select(Charter).where(Charter.agent_id == a.id, Charter.status == "active"))
    d = {"id": a.id, "slug": a.slug, "name": a.name, "description": a.description, "domain": a.domain, "status": a.status,
         "is_demo": a.is_demo, "paused_reason": a.paused_reason, "last_seen": iso(a.last_seen),
         "charter_version": ch.version if ch else None, "tool_count": len(a.tools or []), "internal_domains": a.internal_domains}
    if with_stats:
        since = now_utc() - timedelta(hours=24)
        rows = db.execute(select(Assessment.deviation, Assessment.verdict).where(
            Assessment.agent_id == a.id, Assessment.ts >= since, Assessment.enforceable.is_(True))).all()
        n = len(rows)
        d.update({"actions_24h": n, "deviation_rate": round(100 * sum(1 for r in rows if r[0] >= 40) / n, 1) if n else 0.0,
                  "blocked_24h": sum(1 for r in rows if r[1] == "block"), "held_24h": sum(1 for r in rows if r[1] == "hold")})
        d["pending_approvals"] = db.scalar(select(func.count()).select_from(Approval).where(
            Approval.agent_id == a.id, Approval.status == "pending")) or 0
    return d


def event_dict(ev: Event, a: Assessment | None, approval: Approval | None = None) -> dict:
    out = {"id": ev.id, "seq": ev.seq, "type": ev.type, "ts": iso(ev.ts), "summary": ev.summary, "payload": ev.payload,
           "source": ev.source, "assessment": None, "approval": None}
    if a:
        out["assessment"] = {"deviation": a.deviation, "risk": a.risk, "decision_score": a.decision_score, "band": a.band,
                             "verdict": a.verdict, "enforceable": a.enforceable, "reason": a.reason, "explain": a.explain,
                             "rule_hits": a.rule_hits, "likelihoods": a.likelihoods, "judge": a.judge, "tier": a.tier,
                             "latency_ms": a.latency_ms, "charter_version": a.charter_version, "feedback": a.feedback,
                             "patterns": a.patterns}
    if approval:
        out["approval"] = {"id": approval.id, "status": approval.status}
    return out


def approval_dict(ap: Approval, agent_name: str = "") -> dict:
    return {"id": ap.id, "agent_id": ap.agent_id, "agent_name": agent_name, "session_id": ap.session_id,
            "event_id": ap.event_id, "tool": ap.tool, "summary": ap.summary, "payload": ap.payload, "status": ap.status,
            "decision_score": ap.decision_score, "requested_at": iso(ap.requested_at), "decided_at": iso(ap.decided_at),
            "decided_by": ap.decided_by, "note": ap.note, "source": ap.source}


def incident_dict(i: Incident, agent_name: str = "") -> dict:
    return {"id": i.id, "agent_id": i.agent_id, "agent_name": agent_name, "session_id": i.session_id, "event_id": i.event_id,
            "title": i.title, "severity": i.severity, "status": i.status, "event_count": i.event_count,
            "peak_score": i.peak_score, "note": i.note, "created_at": iso(i.created_at), "updated_at": iso(i.updated_at),
            "source": i.source}


def session_dict(s: Session, agent_name: str = "") -> dict:
    return {"id": s.id, "agent_id": s.agent_id, "agent_name": agent_name, "label": s.label, "started_at": iso(s.started_at),
            "last_event_at": iso(s.last_event_at), "event_count": s.event_count, "max_risk": s.max_risk,
            "max_deviation": s.max_deviation, "blocked_count": s.blocked_count, "source": s.source}


def notification_dict(n: Notification, agent_name: str = "") -> dict:
    return {"id": n.id, "agent_id": n.agent_id, "agent_name": agent_name, "incident_id": n.incident_id, "audience": n.audience,
            "channel": n.channel, "target": n.target, "title": n.title, "body": n.body, "status": n.status,
            "detail": n.detail, "created_at": iso(n.created_at), "source": n.source}
