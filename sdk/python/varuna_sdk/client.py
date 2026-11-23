"""Client for the Varuna guard and trace APIs.

Typical use::

    varuna = VarunaClient(HttpTransport("http://localhost:8000", api_key="vk_..."), label="Booking for M. Alvarez")

    @varuna.tool()
    def charge_card(amount: float, card: str): ...

    varuna.thought("Traveler wants first class, I will charge the corporate card.")
    charge_card(amount=8400, card="corp-4421")      # raises ActionBlocked if Varuna says no
"""
from __future__ import annotations

import functools
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import httpx


@dataclass
class Verdict:
    verdict: str  # allow | delay | hold | block
    band: str = "observe"
    deviation: float = 0.0
    risk: float = 0.0
    reason: str = ""
    delay_seconds: int = 0
    approval_id: int | None = None
    event_id: int | None = None
    session_id: str | None = None
    latency_ms: float = 0.0
    raw: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.verdict in ("allow", "delay")


class ActionBlocked(Exception):
    def __init__(self, verdict: Verdict):
        super().__init__(verdict.reason or "Blocked by Agent Varuna")
        self.verdict = verdict


class ActionHeld(ActionBlocked):
    """The action is waiting for a person. Retry the same call once it has been approved."""


class Transport(Protocol):
    def guard(self, tool: str, args: dict, session_id: str | None, label: str | None) -> dict: ...
    def event(self, type: str, payload: dict, session_id: str | None, label: str | None) -> dict: ...


class HttpTransport:
    def __init__(self, base_url: str, api_key: str, timeout: float = 5.0, client: httpx.Client | None = None):
        self.http = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout,
                                           headers={"X-Varuna-Key": api_key})

    def guard(self, tool, args, session_id, label):
        r = self.http.post("/v1/guard", json={"tool": tool, "args": args, "session_id": session_id, "label": label})
        r.raise_for_status()
        return r.json()

    def event(self, type, payload, session_id, label):
        r = self.http.post("/v1/events", json={"type": type, "payload": payload, "session_id": session_id, "label": label})
        r.raise_for_status()
        return r.json()


class VarunaClient:
    def __init__(self, transport: Transport, label: str | None = None, session_id: str | None = None,
                 fail_open: bool = False, sleep: Callable[[float], None] = time.sleep):
        """fail_open: if Varuna cannot be reached, allow the action (default is to raise, so the agent stops)."""
        self.transport, self.label, self.session_id = transport, label, session_id
        self.fail_open, self._sleep = fail_open, sleep

    # -- guard path ---------------------------------------------------
    def guard(self, tool: str, args: dict | None = None) -> Verdict:
        try:
            d = self.transport.guard(tool, args or {}, self.session_id, self.label)
        except Exception:
            if self.fail_open:
                return Verdict("allow", reason="Varuna unreachable, failing open")
            raise
        self.session_id = d.get("session_id", self.session_id)
        return Verdict(verdict=d["verdict"], band=d.get("band", "observe"), deviation=d.get("deviation", 0),
                       risk=d.get("risk", 0), reason=d.get("reason", ""), delay_seconds=d.get("delay_seconds", 0),
                       approval_id=d.get("approval_id"), event_id=d.get("event_id"), session_id=self.session_id,
                       latency_ms=d.get("latency_ms", 0), raw=d)

    # -- trace path ---------------------------------------------------
    def _event(self, type: str, payload: dict) -> dict:
        try:
            d = self.transport.event(type, payload, self.session_id, self.label)
            self.session_id = d.get("session_id", self.session_id)
            return d
        except Exception:
            if self.fail_open:
                return {}
            raise

    def thought(self, text: str) -> dict:
        return self._event("thought", {"text": text})

    def message(self, role: str, text: str) -> dict:
        return self._event("message", {"role": role, "text": text})

    def result(self, tool: str, ok: bool, output: Any = None, call_event_id: int | None = None) -> dict:
        return self._event("tool_result", {"tool": tool, "ok": ok, "output": output, "call_event_id": call_event_id})

    # -- decorator ----------------------------------------------------
    def tool(self, name: str | None = None):
        """Wrap a tool function. Raises ActionBlocked or ActionHeld; sleeps when Varuna slows the call."""
        def deco(fn):
            tname = name or fn.__name__
            sig = inspect.signature(fn)

            @functools.wraps(fn)
            def wrapper(*a, **kw):
                bound = sig.bind(*a, **kw)
                bound.apply_defaults()
                args = dict(bound.arguments)
                v = self.guard(tname, args)
                if v.verdict == "block":
                    raise ActionBlocked(v)
                if v.verdict == "hold":
                    raise ActionHeld(v)
                if v.verdict == "delay":
                    self._sleep(v.delay_seconds)
                try:
                    out = fn(*a, **kw)
                except Exception as e:
                    self.result(tname, False, str(e), v.event_id)
                    raise
                self.result(tname, True, out, v.event_id)
                return out
            return wrapper
        return deco
