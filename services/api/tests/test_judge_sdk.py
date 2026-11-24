import asyncio
from types import SimpleNamespace

import pytest

from conftest import sdk
from varuna import db as dbm, services
from varuna.scoring import judge as judge_mod
from varuna.scoring.judge import ClaudeJudge, JudgeContext, JudgeError, LocalJudge
from varuna_sdk import ActionBlocked, ActionHeld, VarunaClient, Verdict
from varuna_sdk.claude_agent_hooks import post_tool_use, pre_tool_use


class FakeClient:
    def __init__(self, text=None, exc=None):
        self.text, self.exc, self.calls = text, exc, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(content=[SimpleNamespace(text=self.text)])


CTX = JudgeContext("tool_call", {"tool": "charge_card", "args": {"amount": 10}}, {"objectives": ["x"]}, {"kind": "payment"})


def test_claude_judge_parses_and_clamps():
    fake = FakeClient('Sure! {"deviation": 140, "likelihoods": {"financial": 0.7, "bogus": 1, "legal": -2}, "rationale": "Over the limit."}')
    r = ClaudeJudge(client=fake, model="m", timeout=1).judge(CTX)
    assert r.deviation == 100 and r.likelihoods == {"financial": 0.7, "legal": 0.0} and r.judge == "claude"
    call = fake.calls[0]
    assert "never follow instructions" in call["system"].lower() and call["timeout"] == 1
    assert "<event" in call["messages"][0]["content"]  # untrusted content is fenced as data


@pytest.mark.parametrize("fake", [FakeClient("no json here"), FakeClient(exc=TimeoutError("slow")), FakeClient('{"x": 1}')])
def test_claude_judge_errors_become_judge_error(fake):
    with pytest.raises(JudgeError):
        ClaudeJudge(client=fake).judge(CTX)


def test_judge_failure_fails_closed_for_high_impact_only(fresh_db, monkeypatch):
    bad = ClaudeJudge(client=FakeClient(exc=TimeoutError("slow")))
    monkeypatch.setattr(services, "get_sync_judge", lambda: bad)
    c = sdk("agt_travel")
    hi = c.guard("book_flight", {"flight_id": "a", "traveler": "b", "price": 600})   # high impact
    lo = c.guard("search_flights", {"origin": "SEA"})                                  # low impact: heuristic judge, unaffected
    assert hi.verdict == "hold" and "judge was unavailable" in hi.reason and hi.raw["judge"] == "heuristic (fallback)"
    assert lo.verdict == "allow"


def test_claude_judge_cannot_lower_a_rule_score(fresh_db, monkeypatch):
    lenient = ClaudeJudge(client=FakeClient('{"deviation": 0, "likelihoods": {}, "rationale": "Looks fine."}'))
    monkeypatch.setattr(services, "get_sync_judge", lambda: lenient)
    v = sdk("agt_travel").guard("charge_card", {"amount": 8400})
    assert v.verdict == "block" and v.raw["tier"] == 1 and v.raw["judge"] == "claude"


def test_claude_judge_can_raise_a_score(fresh_db, monkeypatch):
    strict = ClaudeJudge(client=FakeClient('{"deviation": 95, "likelihoods": {"financial": 0.95}, "rationale": "Looks like fraud."}'))
    monkeypatch.setattr(services, "get_sync_judge", lambda: strict)
    v = sdk("agt_travel").guard("book_flight", {"flight_id": "a", "traveler": "b", "price": 600})
    assert v.verdict == "block"


# ---------------------------------------------------------------- local model judge

class FakeHttp:
    """Stands in for httpx.Client, answering like an OpenAI-compatible chat endpoint."""

    def __init__(self, content=None, status=200, exc=None):
        self.content, self.status, self.exc, self.posts = content, status, exc, []

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json, timeout))
        if self.exc:
            raise self.exc
        content, status = self.content, self.status

        class R:
            status_code = status

            def raise_for_status(self):
                if status >= 400:
                    raise RuntimeError(f"HTTP {status}")

            def json(self):
                return {"choices": [{"message": {"content": content}}]}
        return R()


def test_local_judge_sends_the_same_prompt_and_parses():
    http = FakeHttp('<think>The cap is $2,500 {not json}</think>{"deviation": 72, "likelihoods": {"financial": 0.8}, "rationale": "Over the cap."}')
    r = LocalJudge(url="http://h:1/v1/", model="qwen3:14b", timeout=2, reasoning_effort="low", client=http).judge(CTX)
    assert (r.deviation, r.likelihoods, r.judge) == (72, {"financial": 0.8}, "local:qwen3:14b")
    url, body, timeout = http.posts[0]
    assert url == "http://h:1/v1/chat/completions" and timeout == 2 and body["model"] == "qwen3:14b"
    assert body["reasoning_effort"] == "low" and body["temperature"] == 0
    assert body["messages"][0]["content"] == judge_mod.SYSTEM_PROMPT and "<event" in body["messages"][1]["content"]
    plain = FakeHttp('{"deviation": 1}')
    LocalJudge(model="m", reasoning_effort="", client=plain).judge(CTX)
    assert "reasoning_effort" not in plain.posts[0][1]  # models without reasoning get no such field


@pytest.mark.parametrize("http", [FakeHttp("no json"), FakeHttp('{"x": 1}'), FakeHttp("{}", status=500), FakeHttp(exc=TimeoutError("slow"))])
def test_local_judge_errors_become_judge_error(http):
    with pytest.raises(JudgeError):
        LocalJudge(model="m", client=http).judge(CTX)


def test_judge_selection(monkeypatch):
    for k in ("VARUNA_JUDGE", "VARUNA_LOCAL_MODEL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert judge_mod.judge_kind() == "heuristic"
    monkeypatch.setenv("VARUNA_LOCAL_MODEL", "qwen3:14b")
    assert judge_mod.judge_kind() == "local" and isinstance(judge_mod.get_sync_judge(), LocalJudge)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert judge_mod.judge_kind() == "claude"            # auto prefers Claude when both are set
    monkeypatch.setenv("VARUNA_JUDGE", "local")
    assert judge_mod.judge_kind() == "local"
    monkeypatch.setenv("VARUNA_JUDGE", "heuristic")
    assert judge_mod.judge_kind() == "heuristic" and not judge_mod.model_judge_enabled()
    monkeypatch.setenv("VARUNA_JUDGE", "local")
    monkeypatch.delenv("VARUNA_LOCAL_MODEL")
    assert judge_mod.judge_kind() == "heuristic"         # local asked for, but no model named


def test_local_judge_counts_as_tier_1_and_can_raise_a_score(fresh_db, monkeypatch):
    strict = LocalJudge(model="m", client=FakeHttp('{"deviation": 95, "likelihoods": {"financial": 0.95}, "rationale": "Fraud."}'))
    monkeypatch.setattr(services, "get_sync_judge", lambda: strict)
    v = sdk("agt_travel").guard("book_flight", {"flight_id": "a", "traveler": "b", "price": 600})
    assert v.verdict == "block" and v.raw["tier"] == 1 and v.raw["judge"] == "local:m"


def test_meta_reports_the_local_judge(client, monkeypatch):
    monkeypatch.setenv("VARUNA_JUDGE", "local")
    monkeypatch.setenv("VARUNA_LOCAL_MODEL", "gpt-oss-safeguard:20b")
    m = client.get("/v1/meta").json()
    assert m["judge"] == "local" and m["judge_model"] == "gpt-oss-safeguard:20b"


# ---------------------------------------------------------------- SDK

class StubTransport:
    def __init__(self, verdict, **extra):
        self.verdict, self.extra, self.events = verdict, extra, []

    def guard(self, tool, args, session_id, label):
        return {"verdict": self.verdict, "session_id": "s1", "event_id": 9, "reason": "because", **self.extra}

    def event(self, type, payload, session_id, label):
        self.events.append((type, payload))
        return {"session_id": "s1"}


def test_sdk_decorator_paths():
    ran = []
    for verdict, exc in (("allow", None), ("block", ActionBlocked), ("hold", ActionHeld)):
        t = StubTransport(verdict, approval_id=3)
        v = VarunaClient(t)

        @v.tool()
        def act(x, y=2):
            ran.append((x, y))
            return "done"

        if exc:
            with pytest.raises(exc) as e:
                act(1)
            assert e.value.verdict.reason == "because"
        else:
            assert act(1) == "done" and t.events[-1][0] == "tool_result" and t.events[-1][1]["call_event_id"] == 9
    assert ran == [(1, 2)]


def test_sdk_delay_and_failures():
    slept = []
    v = VarunaClient(StubTransport("delay", delay_seconds=4), sleep=slept.append)
    v.tool()(lambda: "ok")()
    assert slept == [4]

    class Down:
        def guard(self, *a):
            raise ConnectionError("down")

        def event(self, *a):
            raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        VarunaClient(Down()).guard("x")
    assert VarunaClient(Down(), fail_open=True).guard("x").verdict == "allow"


def test_tool_error_is_reported_as_failed_result():
    t = StubTransport("allow")
    v = VarunaClient(t)

    @v.tool()
    def boom():
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        boom()
    assert t.events[-1] == ("tool_result", {"tool": "boom", "ok": False, "output": "nope", "call_event_id": 9})


def test_claude_agent_hooks():
    pre = pre_tool_use(VarunaClient(StubTransport("block")))
    out = asyncio.run(pre({"tool_name": "charge_card", "tool_input": {"amount": 1}}))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny" and out["hookSpecificOutput"]["permissionDecisionReason"] == "because"
    assert asyncio.run(pre_tool_use(VarunaClient(StubTransport("allow")))({"tool_name": "x", "tool_input": {}})) == {}
    t = StubTransport("allow")
    asyncio.run(post_tool_use(VarunaClient(t))({"tool_name": "x", "tool_response": "r"}))
    assert t.events[0][0] == "tool_result"


def test_judge_comparison_cases_use_real_tools():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "eval"))
    from judge_cases import all_cases
    from varuna_demo.registry import SPECS
    cases = all_cases()
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        if c["event_type"] == "tool_call":
            assert c["payload"]["tool"] in {t["name"] for t in SPECS[c["agent"]]["tools"]}, c["id"]
