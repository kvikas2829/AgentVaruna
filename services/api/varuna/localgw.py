"""In-process transport so demo agents and the evaluation harness call the same code as the HTTP API."""
from .db import session_scope
from .models import Agent
from . import services


class LocalTransport:
    def __init__(self, agent_id: str, source: str = "demo", now_fn=None, extra_ctx=None):
        self.agent_id, self.source, self.now_fn = agent_id, source, now_fn

    def guard(self, tool, args, session_id, label):
        with session_scope() as db:
            agent = db.get(Agent, self.agent_id)
            return services.handle_guard(db, agent, tool, args, session_id=session_id, label=label,
                                         source=self.source, now=self.now_fn() if self.now_fn else None)

    def event(self, type, payload, session_id, label):
        with session_scope() as db:
            agent = db.get(Agent, self.agent_id)
            return services.handle_trace_event(db, agent, type, payload, session_id=session_id, label=label,
                                               source=self.source, now=self.now_fn() if self.now_fn else None)
