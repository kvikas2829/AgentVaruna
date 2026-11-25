"""Sample history so a fresh install has something on the dashboard. All of it is tagged source='seed' and can be cleared."""
from __future__ import annotations

import random
from datetime import timedelta

from sqlalchemy import delete, select

from varuna_demo.registry import SPECS
from varuna_demo.runner import run_turn
from varuna_sdk import VarunaClient

from .db import session_scope
from .localgw import LocalTransport
from .models import (Agent, Approval, Assessment, Event, Incident, Notification, Pattern, Session)
from .utils import now_utc

NAMES = ["M. Alvarez", "R. Chen", "T. Okafor", "J. Park", "S. Novak", "A. Haddad", "L. Brooks", "P. Iyer", "D. Moreau"]
MATTERS = ["Northwind custodian emails", "Warehouse memos", "Dispatch logs", "Carrier correspondence"]
ALERTS = ["phishing on WS-114", "suspicious PowerShell", "impossible travel login", "malware beacon", "brute-force on VPN"]
REQUESTS = ["password reset", "group change", "access request", "MFA re-enrol", "laptop unlock"]
RISKY = {"travel": ["upgrade", "injection", "export", "retries", "upgrade"], "legal": ["privileged", "cross_matter", "injection", "delete", "privileged", "rubber_stamp", "metadata", "concede"],
         "soc": ["contain_dc", "tamper", "injection", "leak", "contain_dc"], "itadmin": ["privilege", "delete", "injection", "export", "privilege"]}


class Clock:
    def __init__(self, start):
        self.t = start

    def __call__(self):
        self.t += timedelta(seconds=random.uniform(1, 4))
        return self.t


def clear_sample(db) -> None:
    sess_ids = [s for s in db.scalars(select(Session.id).where(Session.source == "seed"))]
    ev_ids = [e for e in db.scalars(select(Event.id).where(Event.source == "seed"))]
    if ev_ids:
        for i in range(0, len(ev_ids), 500):
            db.execute(delete(Assessment).where(Assessment.event_id.in_(ev_ids[i:i + 500])))
    db.execute(delete(Event).where(Event.source == "seed"))
    db.execute(delete(Incident).where(Incident.source == "seed"))
    db.execute(delete(Approval).where(Approval.source == "seed"))
    db.execute(delete(Notification).where(Notification.source == "seed"))
    db.execute(delete(Pattern).where(Pattern.source == "seed"))
    if sess_ids:
        for i in range(0, len(sess_ids), 500):
            db.execute(delete(Session).where(Session.id.in_(sess_ids[i:i + 500])))


def has_sample(db) -> bool:
    return db.scalar(select(Session.id).where(Session.source == "seed").limit(1)) is not None


def seed(hours: int = 24, normal_per_agent: int = 28, risky_per_agent: int = 5, rng_seed: int = 7) -> int:
    random.seed(rng_seed)
    end = now_utc() - timedelta(minutes=20)
    start = end - timedelta(hours=hours)
    n = 0
    with session_scope() as db:
        agents = {k: db.scalar(select(Agent).where(Agent.slug == k)) for k in SPECS}
    for key, agent in agents.items():
        if agent is None:
            continue
        spec = SPECS[key]
        normal_msg = spec["scenarios"][0]["message"]
        plan = [(normal_msg, False)] * normal_per_agent
        for i in range(risky_per_agent):
            sid = RISKY[key][i % len(RISKY[key])]
            plan.append((next(s["message"] for s in spec["scenarios"] if s["id"] == sid), True))
        random.shuffle(plan)
        slots = sorted(random.uniform(0, hours * 3600) for _ in plan)
        for (msg, risky), off in zip(plan, slots):
            clock = Clock(start + timedelta(seconds=off))
            label = {"travel": lambda: f"Booking for {random.choice(NAMES)}", "legal": lambda: f"Review: {random.choice(MATTERS)}",
                     "soc": lambda: f"Triage: {random.choice(ALERTS)}", "itadmin": lambda: f"Request: {random.choice(REQUESTS)} for {random.choice(NAMES)}"}[key]()
            client = VarunaClient(LocalTransport(agent.id, source="seed", now_fn=clock), label=label, sleep=lambda s: None)
            run_turn(key, client, msg)
            n += 1
            with session_scope() as db:  # a seeded session must not leave the agent paused
                a = db.get(Agent, agent.id)
                if a.status == "paused":
                    a.status, a.paused_reason = "active", None
    with session_scope() as db:
        # sample approvals are historical, except the two most recent, which stay pending for the Approvals screen
        pend = list(db.scalars(select(Approval).where(Approval.source == "seed", Approval.status == "pending").order_by(Approval.id.desc())))
        for i, ap in enumerate(pend):
            if i >= 2:
                ap.status, ap.decided_by, ap.decided_at = ("approved" if i % 2 else "rejected"), "sample", end
        for s in db.scalars(select(Session).where(Session.source == "seed")):
            s.blocked_count = 0
    return n
