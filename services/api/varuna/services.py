"""Orchestration: the guard path and the trace path, with all database work."""
from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import event as sa_event, func, select
from sqlalchemy.orm import Session as DBSession

from . import respond
from .decision import DEFAULT_THRESHOLDS, band_for, band_rank, decision_score, verdict_for
import re

from .models import (Agent, Approval, Assessment, Charter, CompanyPolicy, Event, KnownResource, Pattern, ResponsePolicy,
                     RiskFactor, Session, SessionFact, Setting, utcnow)
from .scoring import engine
from .scoring.judge import get_sync_judge, heuristic, model_judge_enabled
from .scoring.rules import amount_of, count_of
from .scoring.text import args_hash
from .scoring.types import HistoryItem, Scored
from .streams import bus
from .utils import aware, iso, now_utc


log = logging.getLogger("varuna.services")


@dataclass
class AgentCtx:
    charter: dict
    version: int | None
    tools: list[dict]
    risk_factors: list[dict]
    policy: ResponsePolicy
    company: dict
    company_version: int | None = None


# ----------------------------------------------------------------- enforcement mode

MODES = ("enforce", "observe")


def get_modes(db: DBSession) -> dict:
    """The company default and per-agent overrides. Enforce: each action waits for its verdict and can be stopped.
    Observe: Varuna answers at once, never stops anything, and scores and alerts in the background."""
    row = db.get(Setting, "enforcement")
    v = dict(row.value) if row and row.value else {}
    return {"default": v.get("default", "enforce") if v.get("default") in MODES else "enforce",
            "agents": {k: m for k, m in (v.get("agents") or {}).items() if m in MODES}}


def set_modes(db: DBSession, default: str | None = None, agent_id: str | None = None, agent_mode: str | None = "keep") -> dict:
    cur = get_modes(db)
    if default:
        cur["default"] = default
    if agent_id:
        if agent_mode in MODES:
            cur["agents"][agent_id] = agent_mode
        elif agent_mode is None:
            cur["agents"].pop(agent_id, None)
    row = db.get(Setting, "enforcement")
    if row is None:
        db.add(Setting(key="enforcement", value=cur))
    else:
        row.value = cur
    bus.publish("settings", {"enforcement": cur})
    return cur


def mode_for(db: DBSession, agent_id: str) -> str:
    m = get_modes(db)
    return m["agents"].get(agent_id, m["default"])


# Observation-mode scoring runs after the guard call has returned. One worker keeps a session's events in order.
_bg = ThreadPoolExecutor(max_workers=1, thread_name_prefix="varuna-observe")
RUN_INLINE = False  # tests set this so background scoring finishes before the call returns
_JOBS = "varuna_after_commit"


def after_commit(db: DBSession, fn, *args) -> None:
    db.info.setdefault(_JOBS, []).append((fn, args))


@sa_event.listens_for(DBSession, "after_commit")
def _run_jobs(sess: DBSession) -> None:
    for fn, args in sess.info.pop(_JOBS, []):
        if RUN_INLINE:
            fn(*args)
        else:
            _bg.submit(_safe, fn, *args)


@sa_event.listens_for(DBSession, "after_rollback")
def _drop_jobs(sess: DBSession) -> None:
    sess.info.pop(_JOBS, None)


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception:  # noqa: BLE001
        log.exception("background scoring failed")


# ----------------------------------------------------------------- what identifiers refer to

_ID = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b")
ID_KEYS = ("doc_id", "host", "alert_id", "asset", "asset_id", "hotel_id", "flight_id", "ticket_id", "request_id", "id")
SKIP_KEYS = {"text", "body", "summary", "note", "notes", "comment", "comments"}


def _strings(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [x for y in v.values() for x in _strings(y)]
    if isinstance(v, (list, tuple)):
        return [x for y in v for x in _strings(y)]
    return []


def known_context(db: DBSession, session_id: str, args: dict) -> dict[str, str]:
    """Identifiers named in an action that Varuna knows something about: from the company's inventory, or from what a
    tool returned earlier in this session. Lets "isolate WMS-APP-01" be checked as "isolate the warehouse system"."""
    ids = {m.upper() for s in _strings(args) for m in _ID.findall(s)}
    if not ids:
        return {}
    out = {r.key: r.text for r in db.scalars(select(KnownResource).where(KnownResource.key.in_(ids)))}
    for f in db.scalars(select(SessionFact).where(SessionFact.session_id == session_id, SessionFact.key.in_(ids))
                        .order_by(SessionFact.id)):
        out[f.key] = f"{out[f.key]}; {f.text}" if f.key in out and f.text not in out[f.key] else out.get(f.key, f.text)
    return out


def enrich(payload: dict, known: dict[str, str]) -> dict:
    """The payload the rules and the judge see: the agent's own arguments plus what the named identifiers are."""
    if not known:
        return payload
    args = dict(payload.get("args") or {})
    args["_known"] = "; ".join(f"{k} is {v}" for k, v in known.items())
    return {**payload, "args": args}


def facts_from_output(output) -> dict[str, str]:
    """Identifier -> description pairs in a tool's output, e.g. {"doc_id": "D-1090", "collection": "HR records"}."""
    out: dict[str, str] = {}

    def walk(v):
        if isinstance(v, dict):
            key = next((str(v[k]) for k in ID_KEYS if isinstance(v.get(k), str) and _ID.fullmatch(str(v[k]))), None)
            if key:
                desc = ", ".join(f"{k}: {x}" for k, x in v.items() if k not in ID_KEYS and k not in SKIP_KEYS
                                 and isinstance(x, (str, int, float)) and str(x))
                if desc:
                    out[key.upper()] = desc[:380]
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(output)
    return out


def learn_facts(db: DBSession, session_id: str, tool: str, output) -> None:
    for k, text in facts_from_output(output).items():
        if not db.scalar(select(SessionFact.id).where(SessionFact.session_id == session_id, SessionFact.key == k,
                                                      SessionFact.text == text)):
            db.add(SessionFact(session_id=session_id, key=k, text=text, tool=tool))


# ----------------------------------------------------------------- company policy

def active_company(db: DBSession) -> CompanyPolicy | None:
    return db.scalar(select(CompanyPolicy).where(CompanyPolicy.status == "active").order_by(CompanyPolicy.version.desc()))


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
                        "extent_factor": f.extent_factor, "scope": "agent" if f.applies_to else "company"})
    return out


def load_ctx(db: DBSession, agent: Agent) -> AgentCtx:
    ch = active_charter(db, agent)
    co = active_company(db)
    return AgentCtx(ch.data if ch else {}, ch.version if ch else None, agent.tools or [],
                    risk_factors_for(db, agent), ensure_policy(db, agent), co.data if co else {}, co.version if co else None)


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


def build_history(db: DBSession, session_id: str, kinds: dict[str, str], exclude_event: int | None = None) -> list[HistoryItem]:
    q = select(Event, Assessment).join(Assessment, Assessment.event_id == Event.id).where(Event.session_id == session_id)
    if exclude_event:
        q = q.where(Event.id != exclude_event)
    rows = db.execute(q.order_by(Event.seq.desc()).limit(60)).all()
    items = []
    for ev, a in reversed(rows):
        p = ev.payload or {}
        tool = p.get("tool")
        args = p.get("args") or {}
        is_call = ev.type == "tool_call"
        items.append(HistoryItem(
            type=ev.type, tool=tool, args_hash=args_hash(tool, args) if is_call else None, kind=kinds.get(tool or ""),
            amount=(p.get("actual_amount") or amount_of(args)) if is_call else None, count=count_of(args) if is_call else None,
            verdict=a.verdict, deviation=a.deviation, band=a.band, hit_ids=[h["id"] for h in (a.rule_hits or [])],
            ts=aware(ev.ts)))
    return items


def _top_text(scored: Scored) -> str:
    return scored.hits[0]["text"] if scored.hits else (scored.rationale or "high combined score")


def _link(session_id: str) -> str:
    from .config import settings
    return f"{settings.public_url}/#/trace/{session_id}"


def handle_guard(db: DBSession, agent: Agent, tool: str, args: dict, *, session_id: str | None = None,
                 label: str | None = None, source: str = "live", now=None) -> dict:
    now = now or now_utc()
    sess = ensure_session(db, agent, session_id, label, source, now)
    ev = Event(agent_id=agent.id, session_id=sess.id, seq=sess.event_count + 1, type="tool_call", ts=now,
               payload={"tool": tool, "args": args}, summary=engine.summarize_call(tool, args)[:400], source=source)
    db.add(ev)
    db.flush()
    sess.event_count += 1
    sess.last_event_at = now
    agent.last_seen = now
    if mode_for(db, agent.id) == "observe":
        after_commit(db, _observe_later, ev.id, source)
        return {"event_id": ev.id, "session_id": sess.id, "verdict": "allow", "band": "pending", "deviation": 0.0,
                "risk": 0.0, "decision_score": 0.0, "reason": "", "delay_seconds": 0, "approval_id": None,
                "charter_version": None, "latency_ms": 0.0, "tier": 0, "judge": "", "agent_paused": False,
                "mode": "observe"}
    return _assess_call(db, agent, sess, ev, tool, args, now, source, enforce=True)


def _observe_later(event_id: int, source: str) -> None:
    from .db import session_scope
    with session_scope() as db:
        ev = db.get(Event, event_id)
        if ev is None or ev.assessment is not None:
            return
        agent = db.get(Agent, ev.agent_id)
        sess = db.get(Session, ev.session_id)
        p = ev.payload or {}
        _assess_call(db, agent, sess, ev, p.get("tool", ""), p.get("args") or {}, aware(ev.ts), source, enforce=False)


def _assess_call(db: DBSession, agent: Agent, sess: Session, ev: Event, tool: str, args: dict, now, source: str,
                 enforce: bool) -> dict:
    """Scores a tool call. Enforcing, the verdict is applied (holds, blocks, pauses). Observing, the same verdict is
    worked out and recorded as what enforcement would have done, but the action has already gone ahead."""
    ctx = load_ctx(db, agent)
    kinds = {t["name"]: t.get("kind", "") for t in ctx.tools}
    meta = next((t for t in ctx.tools if t["name"] == tool), None)
    high = (meta or {}).get("impact") == "high" or tool in ctx.charter.get("high_impact_tools", [])

    hist = [h for h in build_history(db, sess.id, kinds)]
    judge = get_sync_judge() if high else heuristic()
    known = known_context(db, sess.id, args)
    scored = engine.score_event("tool_call", enrich(ev.payload, known), charter=ctx.charter, tools=ctx.tools,
                                risk_factors=ctx.risk_factors, internal_domains=agent.internal_domains or [],
                                tainted=sess.tainted, history=hist, now=now,
                                thresholds=ctx.policy.thresholds, judge=judge, company=ctx.company)

    verdict = verdict_for(scored.band, True, ctx.policy.slow_action)
    reason = ""
    approval_id = None
    delay = 0

    if scored.judge_failed and high and verdict == "allow" and ctx.policy.fail_mode == "closed_high_impact":
        verdict, reason = "hold", "The judge was unavailable, so this high-impact action is held for a human."

    ah = args_hash(tool, args)
    approved = None
    if enforce and verdict in ("hold", "block", "delay"):
        approved = db.scalar(select(Approval).where(Approval.agent_id == agent.id, Approval.args_hash == ah,
                                                    Approval.status == "approved").order_by(Approval.id.desc()))
    if approved:
        approved.status = "used"
        verdict, reason = "allow", f"Approved by {approved.decided_by or 'a reviewer'} (approval #{approved.id})."
        approval_id = approved.id
    if enforce and agent.status == "paused":
        verdict = "block"
        reason = f"This agent is paused by Agent Varuna ({agent.paused_reason or 'paused'}) A person must resume it."
    paused_now = False

    if not enforce:
        would = verdict
        verdict = "allow"
        verb = {"block": "blocked", "hold": "held for approval", "delay": "slowed"}.get(would)
        reason = (f"Observation mode: Varuna would have {verb} this. {_top_text(scored)}." if verb else "")
        scored.explain["mode"] = "observe"
        scored.explain["would_verdict"] = would
    elif verdict == "hold":
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
    if enforce:
        scored.explain["mode"] = "enforce"
    if known:
        scored.explain["known"] = known
    if ctx.company_version:
        scored.explain["company_policy_version"] = ctx.company_version

    a = Assessment(event_id=ev.id, agent_id=agent.id, session_id=sess.id, ts=now, deviation=scored.deviation,
                   risk=scored.risk, decision_score=scored.decision_score, band=scored.band, verdict=verdict,
                   enforceable=True, likelihoods=scored.likelihoods, rule_hits=scored.hits, patterns=scored.patterns,
                   explain=scored.explain, rationale=scored.rationale, reason=reason, judge=scored.judge,
                   tier=scored.tier, latency_ms=scored.latency_ms, charter_version=ctx.version,
                   feedback="allow_once" if approved else None)
    db.add(a)

    sess.max_risk = max(sess.max_risk, scored.risk)
    sess.max_deviation = max(sess.max_deviation, scored.deviation)
    if enforce and verdict == "block" and agent.status != "paused":
        sess.blocked_count += 1
        if sess.blocked_count >= ctx.policy.pause_after_blocks > 0:
            agent.status, agent.paused_reason = "paused", f"Paused after {sess.blocked_count} blocked actions in one session."
            paused_now = True
            db.add(Event(agent_id=agent.id, session_id=sess.id, seq=sess.event_count + 1, type="message", ts=now,
                         payload={"role": "system", "text": agent.paused_reason}, summary=agent.paused_reason, source=source))
            sess.event_count += 1

    for p in scored.patterns:
        _record_pattern(db, agent, sess.id, p, now, source)

    _alerts(db, agent, ctx, sess, ev, scored, verdict, source, f"{tool}",
            would=None if enforce else scored.explain.get("would_verdict"))
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
            "latency_ms": scored.latency_ms, "tier": scored.tier, "judge": scored.judge, "agent_paused": agent.status == "paused",
            "mode": "enforce" if enforce else "observe", "would_verdict": scored.explain.get("would_verdict"),
            "company_policy": scored.explain.get("company_policy", [])}


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
            source: str, what: str, would: str | None = None) -> None:
    rank = band_rank(scored.band)
    if rank < band_rank("notify_owner") and verdict == "allow":
        return
    title_verb = {"block": "Blocked", "hold": "Held", "delay": "Slowed"}.get(verdict) or \
        {"block": "Would have blocked", "hold": "Would have held", "delay": "Would have slowed"}.get(would or "", "Alert")
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
    if etype == "tool_result":
        learn_facts(db, sess.id, str(payload.get("tool", "")), payload.get("output"))
    if etype in ("thought", "tool_result"):
        hist = build_history(db, sess.id, kinds)
        scored = engine.score_event(etype, payload, charter=ctx.charter, tools=ctx.tools, risk_factors=ctx.risk_factors,
                                    internal_domains=agent.internal_domains or [], tainted=sess.tainted, history=hist,
                                    now=now, thresholds=ctx.policy.thresholds, judge=heuristic(), company=ctx.company)
        if etype == "tool_result" and any(h["id"] == "R-INJ-01" for h in scored.hits):
            sess.tainted = True
        if etype == "tool_result" and payload.get("call_event_id"):
            spent = _check_spend(db, agent, ctx, sess, int(payload["call_event_id"]), payload.get("output"), now)
            if spent and band_rank(spent.band) >= band_rank(scored.band):
                scored = spent
                c = spent.explain["charged"]
                ev.summary = (f"{payload.get('tool')} charged ${c['actual']:,.0f}, "
                              f"more than the ${c['declared']:,.0f} declared")[:400]
        a = Assessment(event_id=ev.id, agent_id=agent.id, session_id=sess.id, ts=now, deviation=scored.deviation,
                       risk=scored.risk, decision_score=scored.decision_score, band=scored.band, verdict="allow",
                       enforceable=False, likelihoods=scored.likelihoods, rule_hits=scored.hits, patterns=[],
                       explain=scored.explain, rationale=scored.rationale, judge=scored.judge, tier=0,
                       latency_ms=scored.latency_ms, charter_version=ctx.version)
        db.add(a)
        sess.max_risk = max(sess.max_risk, scored.risk)
        sess.max_deviation = max(sess.max_deviation, scored.deviation)
        label_ = ("untrusted content with instructions" if etype == "tool_result" else "intent signal") \
            if not scored.explain.get("charged") else "charged more than declared"
        _alerts(db, agent, ctx, sess, ev, scored, "allow", source, label_)
        out.update({"band": scored.band, "deviation": scored.deviation, "risk": scored.risk})
    db.flush()
    bus.publish("assessment", {"agent_id": agent.id, "session_id": sess.id, "event_id": ev.id})
    return out


CHARGE_KEYS = ("charged", "amount", "price", "total")


def _charged(output) -> float | None:
    if not isinstance(output, dict):
        return None
    for k in CHARGE_KEYS:
        try:
            if output.get(k) is not None:
                return float(str(output[k]).replace("$", "").replace(",", ""))
        except ValueError:
            continue
    return None


def _check_spend(db: DBSession, agent: Agent, ctx: AgentCtx, sess: Session, call_id: int, output, now) -> Scored | None:
    """After a payment has run: if the system charged more than the agent declared, judge the real amount (limits
    included) and record it, so later limits count what was actually spent. It can't be undone, so it alerts."""
    call = db.get(Event, call_id)
    if call is None or call.type != "tool_call" or call.session_id != sess.id:
        return None
    p = call.payload or {}
    meta = next((t for t in ctx.tools if t["name"] == p.get("tool")), None) or {}
    if meta.get("kind") != "payment":
        return None
    declared = amount_of(p.get("args") or {}) or 0.0
    actual = _charged(output)
    if actual is None or actual <= declared + 0.5:
        return None
    call.payload = {**p, "actual_amount": actual}  # later limits count what was really spent
    kinds = {t["name"]: t.get("kind", "") for t in ctx.tools}
    hist = build_history(db, sess.id, kinds, exclude_event=call_id)
    args = {k: v for k, v in (p.get("args") or {}).items() if k.lower() not in ("amount", "price", "total", "cost", "fare")}
    scored = engine.score_event("tool_call", {"tool": p.get("tool"), "args": {**args, "amount": actual}}, charter=ctx.charter,
                                tools=ctx.tools, risk_factors=ctx.risk_factors, internal_domains=agent.internal_domains or [],
                                tainted=sess.tainted, history=hist, now=now, thresholds=ctx.policy.thresholds,
                                judge=heuristic(), company=ctx.company)
    mismatch = {"id": "R-AMT-03", "category": "financial", "strength": 0.7, "dev_floor": 70,
                "text": f"The agent declared ${declared:,.0f} but the system charged ${actual:,.0f}"}
    scored.hits = [mismatch] + scored.hits
    scored.deviation = max(scored.deviation, 70.0)
    scored.decision_score = round(decision_score(scored.risk, scored.deviation), 1)
    scored.band = band_for(scored.decision_score, ctx.policy.thresholds)
    scored.explain["deviation_reason"] = mismatch["text"] + ". " + scored.explain.get("deviation_reason", "")
    scored.explain["charged"] = {"declared": declared, "actual": actual, "call_event_id": call_id}
    return scored


def deep_review(db: DBSession, event_id: int) -> None:
    """Tier 2: after an action has run, ask the LLM judge for a second opinion and escalate if it disagrees."""
    if not model_judge_enabled():
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
                                judge=get_sync_judge(), company=ctx.company)
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
