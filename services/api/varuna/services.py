"""Orchestration: the guard path and the trace path, with all database work."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from . import respond
from .decision import DEFAULT_THRESHOLDS, band_rank, verdict_for
from .models import (Agent, Approval, Assessment, Charter, Event, Pattern, ResponsePolicy, RiskFactor,
                     Session, utcnow)
from .scoring import engine
from .scoring.judge import claude_enabled, get_sync_judge, heuristic
from .scoring.rules import amount_of, count_of, doc_ids_of
from .scoring.text import args_hash
from .scoring.types import HistoryItem, Scored
from .streams import bus
from .utils import aware, iso, now_utc


@dataclass
class AgentCtx:
    charter: dict
    version: int | None
    tools: list[dict]
    risk_factors: list[dict]
    policy: ResponsePolicy


def ensure_policy(db: DBSession, agent: Agent) -> ResponsePolicy:
    pol = db.get(ResponsePolicy, agent.id)
    if pol is None:
        pol = ResponsePolicy(agent_id=agent.id, thresholds=dict(DEFAULT_THRESHOLDS),
                             stakeholders=[dict(s) for s in respond.DEFAULT_STAKEHOLDERS])
        db.add(pol)
        db.flush()
    return pol


def active_charter(db: DBSession, agent: Agent) -> Charter | None:
    return db.scalar(select(Charter).where(Charter.agent_id == agent.id, Charter.status == "active")
                     .order_by(Charter.version.desc()))


def risk_factors_for(db: DBSession, agent: Agent) -> list[dict]:
    out = []
    for f in db.scalars(select(RiskFactor)):
        if not f.applies_to or agent.id in f.applies_to:
            out.append({"id": f.id, "name": f.name, "category": f.category, "severity": f.severity,
                        "extent_factor": f.extent_factor})
    return out


def load_ctx(db: DBSession, agent: Agent) -> AgentCtx:
    ch = active_charter(db, agent)
    return AgentCtx(ch.data if ch else {}, ch.version if ch else None, agent.tools or [],
                    risk_factors_for(db, agent), ensure_policy(db, agent))


def ensure_session(db: DBSession, agent: Agent, session_id: str | None, label: str | None, source: str, now) -> Session:
    sid = session_id or f"ses_{uuid.uuid4().hex[:10]}"
    s = db.get(Session, sid)
    if s is None:
        s = Session(id=sid, agent_id=agent.id, label=label or "", started_at=now, last_event_at=now, source=source)
        db.add(s)
        db.flush()
    elif label and not s.label:
        s.label = label
    return s


def build_history(db: DBSession, session_id: str, kinds: dict[str, str]) -> list[HistoryItem]:
    rows = db.execute(
        select(Event, Assessment).join(Assessment, Assessment.event_id == Event.id)
        .where(Event.session_id == session_id).order_by(Event.seq.desc()).limit(60)).all()
    items = []
    for ev, a in reversed(rows):
        p = ev.payload or {}
        tool = p.get("tool")
        args = p.get("args") or {}
        is_call = ev.type == "tool_call"
        items.append(HistoryItem(
            type=ev.type, tool=tool, args_hash=args_hash(tool, args) if is_call else None, kind=kinds.get(tool or ""),
            amount=amount_of(args) if is_call else None, count=count_of(args) if is_call else None,
            verdict=a.verdict, deviation=a.deviation, band=a.band, hit_ids=[h["id"] for h in (a.rule_hits or [])],
            ts=aware(ev.ts), docs=tuple(doc_ids_of(args)) if is_call else ()))
    return items


def _top_text(scored: Scored) -> str:
    return scored.hits[0]["text"] if scored.hits else (scored.rationale or "high combined score")


def _link(session_id: str) -> str:
    from .config import settings
    return f"{settings.public_url}/#/trace/{session_id}"


def handle_guard(db: DBSession, agent: Agent, tool: str, args: dict, *, session_id: str | None = None,
                 label: str | None = None, source: str = "live", now=None) -> dict:
    now = now or now_utc()
    ctx = load_ctx(db, agent)
    sess = ensure_session(db, agent, session_id, label, source, now)
    kinds = {t["name"]: t.get("kind", "") for t in ctx.tools}
    meta = next((t for t in ctx.tools if t["name"] == tool), None)
    high = (meta or {}).get("impact") == "high" or tool in ctx.charter.get("high_impact_tools", [])

    ev = Event(agent_id=agent.id, session_id=sess.id, seq=sess.event_count + 1, type="tool_call", ts=now,
               payload={"tool": tool, "args": args}, summary=engine.summarize_call(tool, args)[:400], source=source)
    db.add(ev)
    db.flush()

    hist = build_history(db, sess.id, kinds)
    judge = get_sync_judge() if high else heuristic()
    scored = engine.score_event("tool_call", ev.payload, charter=ctx.charter, tools=ctx.tools,
                                risk_factors=ctx.risk_factors, internal_domains=agent.internal_domains or [],
                                tainted=sess.tainted, history=hist, now=now,
                                thresholds=ctx.policy.thresholds, judge=judge)

    verdict = verdict_for(scored.band, True, ctx.policy.slow_action)
    reason = ""
    approval_id = None
    delay = 0

    if scored.judge_failed and high and verdict == "allow" and ctx.policy.fail_mode == "closed_high_impact":
        verdict, reason = "hold", "The judge was unavailable, so this high-impact action is held for a human."

    ah = args_hash(tool, args)
    approved = None
    if verdict in ("hold", "block", "delay"):
        approved = db.scalar(select(Approval).where(Approval.agent_id == agent.id, Approval.args_hash == ah,
                                                    Approval.status == "approved").order_by(Approval.id.desc()))
    if approved:
        approved.status = "used"
        verdict, reason = "allow", f"Approved by {approved.decided_by or 'a reviewer'} (approval #{approved.id})."
        approval_id = approved.id
    if agent.status == "paused":
        verdict = "block"
        reason = f"This agent is paused by Agent Varuna ({agent.paused_reason or 'paused'}) A person must resume it."
    paused_now = False

    if verdict == "hold":
        pend = db.scalar(select(Approval).where(Approval.agent_id == agent.id, Approval.args_hash == ah,
                                                Approval.status == "pending"))
        if pend is None:
            pend = Approval(agent_id=agent.id, session_id=sess.id, event_id=ev.id, tool=tool, args_hash=ah,
                            payload={"tool": tool, "args": args}, summary=ev.summary, decision_score=scored.decision_score,
                            requested_at=now, source=source)
            db.add(pend)
            db.flush()
        approval_id = pend.id
        reason = reason or f"Held for human approval (request #{pend.id}). {_top_text(scored)}. Retry once it is approved."
    elif verdict == "delay":
        delay = ctx.policy.delay_seconds
        reason = f"Slowed by Agent Varuna: wait {delay} seconds before running this. {_top_text(scored)}."
    elif verdict == "block" and not reason:
        reason = (f"Blocked by Agent Varuna. {_top_text(scored)}. Risk {scored.risk:.0f}, deviation "
                  f"{scored.deviation:.0f}. Ask the agent owner for an exception if this is legitimate.")

    a = Assessment(event_id=ev.id, agent_id=agent.id, session_id=sess.id, ts=now, deviation=scored.deviation,
                   risk=scored.risk, decision_score=scored.decision_score, band=scored.band, verdict=verdict,
                   enforceable=True, likelihoods=scored.likelihoods, rule_hits=scored.hits, patterns=scored.patterns,
                   explain=scored.explain, rationale=scored.rationale, reason=reason, judge=scored.judge,
                   tier=scored.tier, latency_ms=scored.latency_ms, charter_version=ctx.version,
                   feedback="allow_once" if approved else None)
    db.add(a)

    sess.event_count += 1
    sess.last_event_at = now
    sess.max_risk = max(sess.max_risk, scored.risk)
    sess.max_deviation = max(sess.max_deviation, scored.deviation)
    if verdict == "block" and agent.status != "paused":
        sess.blocked_count += 1
        if sess.blocked_count >= ctx.policy.pause_after_blocks > 0:
            agent.status, agent.paused_reason = "paused", f"Paused after {sess.blocked_count} blocked actions in one session."
            paused_now = True
            db.add(Event(agent_id=agent.id, session_id=sess.id, seq=sess.event_count + 1, type="message", ts=now,
                         payload={"role": "system", "text": agent.paused_reason}, summary=agent.paused_reason, source=source))
            sess.event_count += 1
    agent.last_seen = now

    for p in scored.patterns:
        _record_pattern(db, agent, sess.id, p, now, source)

    _alerts(db, agent, ctx, sess, ev, scored, verdict, source, f"{tool}")
    if paused_now:
        respond.notify(db, agent, ctx.policy, 100, f"{agent.name} paused", agent.paused_reason + f" {_link(sess.id)}",
                       None, source, only_audiences={"owner", "security"})
    db.flush()
    bus.publish("assessment", {"agent_id": agent.id, "session_id": sess.id, "event_id": ev.id, "verdict": verdict})
    if paused_now:
        bus.publish("agent", {"agent_id": agent.id, "status": "paused"})
    return {"event_id": ev.id, "session_id": sess.id, "verdict": verdict, "band": scored.band,
            "deviation": scored.deviation, "risk": scored.risk, "decision_score": scored.decision_score,
            "reason": reason, "delay_seconds": delay, "approval_id": approval_id, "charter_version": ctx.version,
            "latency_ms": scored.latency_ms, "tier": scored.tier, "judge": scored.judge, "agent_paused": agent.status == "paused"}


def _record_pattern(db: DBSession, agent: Agent, session_id: str, p: dict, now, source: str) -> None:
    row = db.scalar(select(Pattern).where(Pattern.session_id == session_id, Pattern.kind == p["kind"]))
    if row:
        row.count += 1
        row.ts = now
        row.detail = p["detail"]
    else:
        db.add(Pattern(agent_id=agent.id, session_id=session_id, kind=p["kind"], title=p["title"], detail=p["detail"],
                       ts=now, source=source))


def _alerts(db: DBSession, agent: Agent, ctx: AgentCtx, sess: Session, ev: Event, scored: Scored, verdict: str,
            source: str, what: str) -> None:
    rank = band_rank(scored.band)
    if rank < band_rank("notify_owner") and verdict == "allow":
        return
    title_verb = {"block": "Blocked", "hold": "Held", "delay": "Slowed"}.get(verdict, "Alert")
    title = f"{agent.name}: {title_verb} {what}"
    body = (f"{ev.summary}\nDeviation {scored.deviation:.0f}, risk {scored.risk:.0f}. {_top_text(scored)}.\n{_link(sess.id)}")
    inc = None
    if rank >= band_rank("notify_security") or verdict != "allow":
        inc, created = respond.open_or_update_incident(db, agent, sess.id, ev.id, f"{title_verb}: {ev.summary}"[:240],
                                                       scored.decision_score, source)
    respond.notify(db, agent, ctx.policy, scored.decision_score, title, body, inc, source)
    if inc is not None:
        bus.publish("incident", {"agent_id": agent.id, "incident_id": inc.id})


def handle_trace_event(db: DBSession, agent: Agent, etype: str, payload: dict, *, session_id: str | None = None,
                       label: str | None = None, source: str = "live", now=None) -> dict:
    """Thoughts, tool results and messages: recorded, and scored when they can reveal something."""
    now = now or now_utc()
    ctx = load_ctx(db, agent)
    sess = ensure_session(db, agent, session_id, label, source, now)
    kinds = {t["name"]: t.get("kind", "") for t in ctx.tools}
    summary = {"thought": str(payload.get("text", ""))[:300],
               "tool_result": f"{payload.get('tool', '')} returned {'ok' if payload.get('ok', True) else 'an error'}",
               "message": f"{payload.get('role', 'agent')}: {str(payload.get('text', ''))[:260]}"}.get(etype, etype)
    ev = Event(agent_id=agent.id, session_id=sess.id, seq=sess.event_count + 1, type=etype, ts=now, payload=payload,
               summary=summary, source=source)
    db.add(ev)
    db.flush()
    sess.event_count += 1
    sess.last_event_at = now
    agent.last_seen = now
    out = {"event_id": ev.id, "session_id": sess.id}
    if etype in ("thought", "tool_result"):
        hist = build_history(db, sess.id, kinds)
        scored = engine.score_event(etype, payload, charter=ctx.charter, tools=ctx.tools, risk_factors=ctx.risk_factors,
                                    internal_domains=agent.internal_domains or [], tainted=sess.tainted, history=hist,
                                    now=now, thresholds=ctx.policy.thresholds, judge=heuristic())
        if etype == "tool_result" and any(h["id"] == "R-INJ-01" for h in scored.hits):
            sess.tainted = True
        a = Assessment(event_id=ev.id, agent_id=agent.id, session_id=sess.id, ts=now, deviation=scored.deviation,
                       risk=scored.risk, decision_score=scored.decision_score, band=scored.band, verdict="allow",
                       enforceable=False, likelihoods=scored.likelihoods, rule_hits=scored.hits, patterns=[],
                       explain=scored.explain, rationale=scored.rationale, judge=scored.judge, tier=0,
                       latency_ms=scored.latency_ms, charter_version=ctx.version)
        db.add(a)
        sess.max_risk = max(sess.max_risk, scored.risk)
        sess.max_deviation = max(sess.max_deviation, scored.deviation)
        label_ = "untrusted content with instructions" if etype == "tool_result" else "intent signal"
        _alerts(db, agent, ctx, sess, ev, scored, "allow", source, label_)
        out.update({"band": scored.band, "deviation": scored.deviation, "risk": scored.risk})
    db.flush()
    bus.publish("assessment", {"agent_id": agent.id, "session_id": sess.id, "event_id": ev.id})
    return out


def deep_review(db: DBSession, event_id: int) -> None:
    """Tier 2: after an action has run, ask the LLM judge for a second opinion and escalate if it disagrees."""
    if not claude_enabled():
        return
    ev = db.get(Event, event_id)
    a = ev.assessment if ev else None
    if not ev or not a or ev.type != "tool_call" or a.tier >= 1:
        return
    agent = db.get(Agent, ev.agent_id)
    ctx = load_ctx(db, agent)
    sess = db.get(Session, ev.session_id)
    kinds = {t["name"]: t.get("kind", "") for t in ctx.tools}
    hist = [h for h in build_history(db, sess.id, kinds)][:-1]
    scored = engine.score_event("tool_call", ev.payload, charter=ctx.charter, tools=ctx.tools,
                                risk_factors=ctx.risk_factors, internal_domains=agent.internal_domains or [],
                                tainted=sess.tainted, history=hist, now=aware(ev.ts), thresholds=ctx.policy.thresholds,
                                judge=get_sync_judge())
    if scored.judge_failed or band_rank(scored.band) <= band_rank(a.band):
        return
    a.deviation, a.risk, a.decision_score, a.band = scored.deviation, scored.risk, scored.decision_score, scored.band
    a.explain, a.rationale, a.judge, a.tier = scored.explain, scored.rationale, scored.judge, 2
    _alerts(db, agent, ctx, sess, ev, scored, "allow", ev.source, f"{ev.payload.get('tool')} (second review)")


def pause_agent(db: DBSession, agent: Agent, reason: str) -> None:
    agent.status, agent.paused_reason = "paused", reason
    bus.publish("agent", {"agent_id": agent.id, "status": "paused"})


def resume_agent(db: DBSession, agent: Agent) -> None:
    agent.status, agent.paused_reason = "active", None
    # blocked counters restart so one old session does not re-pause the agent immediately
    for s in db.scalars(select(Session).where(Session.agent_id == agent.id, Session.blocked_count > 0)):
        s.blocked_count = 0
    bus.publish("agent", {"agent_id": agent.id, "status": "active"})
