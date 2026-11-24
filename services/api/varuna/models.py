"""Storage model. SQLite for the MVP; every column type is portable to Postgres."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    domain: Mapped[str] = mapped_column(String(32), default="custom")  # travel | legal | custom
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | active | paused
    api_key: Mapped[str] = mapped_column(String(64), unique=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    tools: Mapped[list] = mapped_column(JSON, default=list)  # tool registry: name, description, impact, kind, params
    internal_domains: Mapped[list] = mapped_column(JSON, default=list)
    paused_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Charter(Base):
    __tablename__ = "charters"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | active | archived
    source_text: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    compiled_by: Mapped[str] = mapped_column(String(16), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompanyPolicy(Base):
    """Company-level policy: applies to every agent, on top of each agent's charter. Versioned like charters."""
    __tablename__ = "company_policies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | archived
    source_text: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    compiled_by: Mapped[str] = mapped_column(String(16), default="heuristic")
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RiskFactor(Base):
    __tablename__ = "risk_factors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(24))  # financial | data_leakage | reputational | morale | legal | operational
    severity: Mapped[int] = mapped_column(Integer, default=3)  # 1..5
    extent_text: Mapped[str] = mapped_column(String(200), default="")
    extent_factor: Mapped[float] = mapped_column(Float, default=1.0)
    applies_to: Mapped[list] = mapped_column(JSON, default=list)  # agent ids; empty = every agent
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResponsePolicy(Base):
    __tablename__ = "response_policies"
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), primary_key=True)
    thresholds: Mapped[dict] = mapped_column(JSON, default=dict)  # notify_owner, notify_security, slow, block
    slow_action: Mapped[str] = mapped_column(String(8), default="hold")  # hold | delay
    delay_seconds: Mapped[int] = mapped_column(Integer, default=5)
    pause_after_blocks: Mapped[int] = mapped_column(Integer, default=2)
    fail_mode: Mapped[str] = mapped_column(String(24), default="closed_high_impact")
    stakeholders: Mapped[list] = mapped_column(JSON, default=list)  # {audience, label, from_score, channel, target}


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    max_risk: Mapped[float] = mapped_column(Float, default=0)
    max_deviation: Mapped[float] = mapped_column(Float, default=0)
    blocked_count: Mapped[int] = mapped_column(Integer, default=0)
    tainted: Mapped[bool] = mapped_column(Boolean, default=False)  # untrusted content with instructions seen
    source: Mapped[str] = mapped_column(String(12), default="live")  # live | demo | seed


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    type: Mapped[str] = mapped_column(String(16))  # tool_call | tool_result | thought | message
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(String(400), default="")
    source: Mapped[str] = mapped_column(String(12), default="live")
    assessment: Mapped["Assessment | None"] = relationship(back_populates="event", uselist=False)


class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), unique=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(48), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deviation: Mapped[float] = mapped_column(Float, default=0)
    risk: Mapped[float] = mapped_column(Float, default=0)
    decision_score: Mapped[float] = mapped_column(Float, default=0)
    band: Mapped[str] = mapped_column(String(20), default="observe")  # observe | notify_owner | notify_security | slow | block
    verdict: Mapped[str] = mapped_column(String(10), default="allow")  # allow | delay | hold | block
    enforceable: Mapped[bool] = mapped_column(Boolean, default=False)  # True for tool_call events
    likelihoods: Mapped[dict] = mapped_column(JSON, default=dict)
    rule_hits: Mapped[list] = mapped_column(JSON, default=list)
    patterns: Mapped[list] = mapped_column(JSON, default=list)
    explain: Mapped[dict] = mapped_column(JSON, default=dict)  # deviation_reason, risk_reason, factors, components
    rationale: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")  # message returned to the agent
    judge: Mapped[str] = mapped_column(String(16), default="heuristic")
    tier: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    charter_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback: Mapped[str | None] = mapped_column(String(20), nullable=True)  # false_positive | confirmed | allow_once
    feedback_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    event: Mapped[Event] = relationship(back_populates="assessment")


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(48), index=True)
    event_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(240))
    severity: Mapped[str] = mapped_column(String(10), default="medium")  # medium | high | critical
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | acknowledged | resolved | false_positive
    event_count: Mapped[int] = mapped_column(Integer, default=1)
    peak_score: Mapped[float] = mapped_column(Float, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(12), default="live")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(48))
    event_id: Mapped[int] = mapped_column(Integer)
    tool: Mapped[str] = mapped_column(String(120))
    args_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | approved | rejected | used
    summary: Mapped[str] = mapped_column(String(400), default="")
    decision_score: Mapped[float] = mapped_column(Float, default=0)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(12), default="live")


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(32), index=True)
    incident_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audience: Mapped[str] = mapped_column(String(24))  # owner | security | finance | ...
    channel: Mapped[str] = mapped_column(String(12))  # in_app | webhook | slack | email
    target: Mapped[str] = mapped_column(String(300), default="")
    title: Mapped[str] = mapped_column(String(240))
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="sent")  # sent | logged | failed
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[str] = mapped_column(String(12), default="live")


class Pattern(Base):
    __tablename__ = "patterns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(32), index=True)
    session_id: Mapped[str] = mapped_column(String(48), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(160))
    detail: Mapped[str] = mapped_column(Text, default="")
    count: Mapped[int] = mapped_column(Integer, default=1)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(12), default="live")


class ChatLog(Base):
    """Conversation log for the demo agents."""
    __tablename__ = "chat_logs"
    session_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(32))
    messages: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[dict] = mapped_column(JSON, default=dict)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
