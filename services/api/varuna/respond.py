"""Response orchestrator: alerts, incidents, approvals, pause. Notification adapters never block the guard path."""
from __future__ import annotations

import json
import smtplib
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from .config import settings
from .db import session_scope
from .models import Agent, Incident, Notification, ResponsePolicy, Setting, utcnow
from .streams import bus

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="varuna-notify")

DEFAULT_STAKEHOLDERS = [
    {"audience": "owner", "label": "Agent owner (IT manager)", "from_score": 40, "channel": "in_app", "target": ""},
    {"audience": "security", "label": "Security on-call", "from_score": 60, "channel": "slack", "target": ""},
    {"audience": "approver", "label": "Approver", "from_score": 75, "channel": "email", "target": ""},
]


def get_channels(db: DBSession) -> dict:
    row = db.get(Setting, "channels")
    return (row.value if row else {}) or {}


def _send_external(n_id: int, channel: str, target: str, title: str, body: str, channels: dict) -> None:
    """Runs on a worker thread. Records the outcome on the notification row."""
    status, detail = "logged", ""
    try:
        if channel in ("slack", "webhook"):
            url = target or channels.get("slack_webhook_url" if channel == "slack" else "webhook_url", "")
            if not url:
                detail = f"No {channel} URL configured, kept in the app only."
            else:
                payload = {"text": f"*{title}*\n{body}"} if channel == "slack" else {"title": title, "body": body}
                r = httpx.post(url, json=payload, timeout=4)
                status, detail = ("sent", f"HTTP {r.status_code}") if r.status_code < 300 else ("failed", f"HTTP {r.status_code}")
        elif channel == "email":
            cfg = settings.smtp
            to = target or channels.get("email_to", "")
            if not cfg or not to:
                detail = "SMTP or recipient not configured, kept in the app only."
            else:
                msg = EmailMessage()
                msg["Subject"], msg["From"], msg["To"] = title, cfg["sender"], to
                msg.set_content(body)
                with smtplib.SMTP(cfg["host"], cfg["port"], timeout=5) as s:
                    s.starttls()
                    if cfg["user"]:
                        s.login(cfg["user"], cfg["password"])
                    s.send_message(msg)
                status, detail = "sent", f"to {to}"
    except Exception as e:  # noqa: BLE001
        status, detail = "failed", str(e)[:200]
    with session_scope() as db:
        n = db.get(Notification, n_id)
        if n:
            n.status, n.detail = status, detail
    bus.publish("notification", {"id": n_id})


def notify(db: DBSession, agent: Agent, policy: ResponsePolicy | None, decision_score: float, title: str, body: str,
           incident: Incident | None, source: str, only_audiences: set[str] | None = None) -> list[int]:
    """Create one notification per stakeholder whose threshold the score reaches."""
    stakeholders = (policy.stakeholders if policy and policy.stakeholders else DEFAULT_STAKEHOLDERS)
    channels = get_channels(db)
    ids: list[int] = []
    for s in stakeholders:
        if decision_score < s.get("from_score", 100):
            continue
        if only_audiences and s["audience"] not in only_audiences:
            continue
        if incident is not None:
            dup = db.scalar(select(Notification.id).where(
                Notification.incident_id == incident.id, Notification.audience == s["audience"], Notification.title == title))
            if dup:
                continue
        n = Notification(agent_id=agent.id, incident_id=incident.id if incident else None, audience=s["audience"],
                         channel=s.get("channel", "in_app"), target=s.get("target", ""), title=title, body=body,
                         status="sent" if s.get("channel", "in_app") == "in_app" else "logged", source=source)
        db.add(n)
        db.flush()
        ids.append(n.id)
        if n.channel != "in_app" and source != "seed":
            _pool.submit(_send_external, n.id, n.channel, n.target, title, body, channels)
    return ids


def open_or_update_incident(db: DBSession, agent: Agent, session_id: str, event_id: int, title: str, score: float,
                            source: str) -> tuple[Incident, bool]:
    inc = db.scalar(select(Incident).where(Incident.session_id == session_id, Incident.agent_id == agent.id,
                                           Incident.status.in_(["open", "acknowledged"])).order_by(Incident.id.desc()))
    created = False
    if inc is None:
        inc = Incident(agent_id=agent.id, session_id=session_id, event_id=event_id, title=title,
                       severity="high" if score >= 75 else "medium", peak_score=score, source=source)
        db.add(inc)
        db.flush()
        created = True
    else:
        inc.event_count += 1
        inc.updated_at = utcnow()
        if score > inc.peak_score:
            inc.peak_score = score
            inc.event_id = event_id
            inc.title = title
            inc.severity = "critical" if score >= 90 else "high" if score >= 75 else "medium"
    if score >= 90:
        inc.severity = "critical"
    return inc, created
