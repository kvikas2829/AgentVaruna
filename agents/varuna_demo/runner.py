"""Runs one chat turn of a demo agent: every tool call is judged by Varuna through the SDK."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from varuna_sdk import VarunaClient

from . import llm_agent
from .meridian import company
from .plans import route
from .registry import SPECS
from .tools import IMPLS


@dataclass
class CallResult:
    ok: bool
    verdict: str
    reason: str = ""
    result: Any = None
    approval_id: int | None = None


def _preview(x: Any, n: int = 160) -> str:
    s = str(x)
    return s if len(s) <= n else s[: n - 1] + "…"


@dataclass
class Env:
    agent_key: str
    client: VarunaClient
    greeting: str
    state: dict = field(default_factory=dict)
    out: list = field(default_factory=list)

    def __post_init__(self):
        self.impl = IMPLS[self.agent_key]()

    @property
    def handle(self) -> str:
        """Who is chatting (the demo's "Chatting as" picker), as a directory handle."""
        return company.handle_of(self.state.get("as_user") or "") or company.default_persona(self.agent_key)

    @property
    def me(self) -> str:
        return company.email(self.handle)

    @property
    def me_name(self) -> str:
        return company.PEOPLE[self.handle]["name"]

    def think(self, text: str) -> None:
        self.client.thought(text)
        self.out.append({"role": "thought", "text": text})

    def say(self, text: str) -> None:
        self.client.message("agent", text)
        self.out.append({"role": "agent", "text": text})

    def call(self, tool: str, **args) -> CallResult:
        v = self.client.guard(tool, args)
        entry = {"role": "tool", "tool": tool, "args": args, "verdict": v.verdict, "band": v.band,
                 "deviation": v.deviation, "risk": v.risk, "reason": v.reason, "event_id": v.event_id,
                 "approval_id": v.approval_id, "latency_ms": v.latency_ms, "tier": v.raw.get("tier", 0),
                 "judge": v.raw.get("judge", "")}
        result = None
        ok = v.verdict in ("allow", "delay")
        if v.verdict == "delay":
            time.sleep(min(v.delay_seconds, 2))
        if ok:
            result = self.impl[tool](**args)
            self.client.result(tool, True, result, v.event_id)
            entry["result"] = _preview(result)
        self.out.append(entry)
        return CallResult(ok, v.verdict, v.reason, result, v.approval_id)


def _is_scenario(agent_key: str, message: str) -> bool:
    return any(s["message"].strip() == message.strip() for s in SPECS[agent_key]["scenarios"])


def run_turn(agent_key: str, client: VarunaClient, message: str, state: dict | None = None) -> tuple[list, dict]:
    """Scenario buttons always run their script, so a demo is repeatable. Anything typed goes to the model-driven agent
    when one is configured for this agent (see llm_agent.py), otherwise to the closest script."""
    spec = SPECS[agent_key]
    state = dict(state or {})
    env = Env(agent_key, client, spec["greeting"], state)
    client.message("user", message)
    history = list(state.get("history") or [])
    if llm_agent.enabled(agent_key) and not _is_scenario(agent_key, message):
        try:
            history = llm_agent.run(env, message, history)
            return env.out, {**env.state, "history": history}
        except llm_agent.AgentModelError as e:
            env.out.append({"role": "thought", "text": f"The agent's model could not be reached ({e}), so the scripted agent answered."})
    route(agent_key, message)(env, message)
    said = " ".join(m["text"] for m in env.out if m["role"] == "agent")
    history = (history + [{"role": "user", "content": message}, {"role": "assistant", "content": said}])[-llm_agent.HISTORY:]
    return env.out, {**env.state, "history": history}


def retry_pending(agent_key: str, client: VarunaClient, pending: dict, state: dict | None = None) -> tuple[list, dict]:
    spec = SPECS[agent_key]
    env = Env(agent_key, client, spec["greeting"], dict(state or {}))
    c = env.call(pending["tool"], **pending["args"])
    if c.ok:
        env.say("The approval came through, so I completed the action.")
    else:
        env.say("It is still not allowed. " + c.reason)
    return env.out, env.state
