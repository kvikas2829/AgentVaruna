"""Company-level policy and risks on top of agent charters, and observation versus enforcement mode."""
import pytest
from sqlalchemy import select

from conftest import sdk
from varuna import db as dbm, services
from varuna.compiler.heuristic import compile_company_policy
from varuna.models import Agent, Approval, Assessment, CompanyPolicy, Incident
from varuna.scoring.judge import JudgeContext, build_prompt
from varuna_demo.meridian import company as co
from varuna_demo.registry import SPECS
from varuna_demo.runner import run_turn


def _scenario(key, sid):
    msg = next(s["message"] for s in SPECS[key]["scenarios"] if s["id"] == sid)
    out, _ = run_turn(key, sdk(f"agt_{key}"), msg)
    return out, {m["tool"]: m for m in out if m["role"] == "tool"}


# ---------------------------------------------------------------- compiling the company policy

def test_company_policy_compiles_to_checkable_rules():
    c = compile_company_policy(co.COMPANY_POLICY_TEXT)
    rules = {r["topic"]: r for r in c["limits"]["amount_rules"]}
    assert rules["travel"]["max"] == 2000 and "flight" in rules["travel"]["scope"]
    assert rules[None]["max"] == 5000 and rules[None]["scope"] == []
    assert {"Payroll", "HR records", "Workday"} <= set(c["forbidden_resources"])
    assert "Warehouse management system" in c["limits"]["protected_targets"]
    assert "max_amount_per_action" not in c["limits"] and "expected_actions" not in c   # no scope rule for the company layer


# ---------------------------------------------------------------- a looser charter does not switch company rules off

@pytest.mark.parametrize("key,sid,tool,verdict,rule", [
    ("travel", "company_limit", "book_hotel", "hold", "CO-AMT-01"),
    ("legal", "hr_records", "read_document", "block", "CO-FORB-01"),
    ("soc", "critical_system", "isolate_host", "hold", "CO-PROT-01"),
    ("itadmin", "workday", "grant_access", "block", "CO-FORB-01"),
])
def test_company_policy_catches_what_the_charter_allows(fresh_db, key, sid, tool, verdict, rule):
    out, tools = _scenario(key, sid)
    t = tools[tool]
    assert t["verdict"] == verdict and "Company policy" in t["reason"], t["reason"]
    with dbm.session_scope() as db:
        a = db.scalar(select(Assessment).where(Assessment.event_id == t["event_id"]))
        ids = [h["id"] for h in a.rule_hits]
        assert ids[0] == rule and a.explain["company_policy"] and a.explain["company_policy_version"] == 1
        # the agent's own charter rules did not fire: only the company layer stood in the way
        assert not [i for i in ids if i.startswith("R-")], ids
    assert "company policy" in out[-1]["text"].lower()


def test_trip_limit_counts_the_whole_trip(fresh_db):
    c = sdk("agt_travel")
    assert c.guard("book_flight", {"flight_id": "DL412", "traveler": "Alex Rivera", "price": 612}).verdict == "allow"
    ok = c.guard("book_hotel", {"hotel_id": "HX-218", "traveler": "Alex Rivera", "amount": 1020})          # $1,632 in total
    assert ok.verdict == "allow" and ok.band == "observe"
    over = c.guard("book_hotel", {"hotel_id": "LG-552", "traveler": "Alex Rivera", "amount": 1650})        # $3,282 in total
    assert over.verdict in ("hold", "block") and "with earlier bookings" in over.reason   # 64% over: blocked


def test_editing_the_company_policy_changes_verdicts(client):
    p = client.get("/v1/company/policy").json()
    assert p["active"]["version"] == 1 and "$2,000" in p["active"]["source_text"]
    text = p["active"]["source_text"].replace("Keep any business trip under $2,000", "Keep any business trip under $3,000")
    prev = client.post("/v1/company/policy/compile", json={"text": text}).json()
    assert prev["data"]["limits"]["amount_rules"][0]["max"] == 3000
    assert client.get("/v1/company/policy").json()["active"]["version"] == 1                               # preview saves nothing
    saved = client.put("/v1/company/policy", json={"text": text}).json()
    assert saved["version"] == 2 and saved["status"] == "active"
    c = sdk("agt_travel")
    c.guard("book_flight", {"flight_id": "DL412", "traveler": "Alex Rivera", "price": 612})
    assert c.guard("book_hotel", {"hotel_id": "LG-552", "traveler": "Alex Rivera", "amount": 1650}).verdict == "allow"
    assert client.post("/v1/company/policy/1/activate", json={}).json()["version"] == 1
    hist = client.get("/v1/company/policy").json()["history"]
    assert [(h["version"], h["status"]) for h in hist] == [(2, "archived"), (1, "active")]


def test_company_risks_apply_to_every_agent(client):
    risks = client.get("/v1/risks").json()
    company = [r for r in risks if r["scope"] == "company"]
    assert {r["name"] for r in company} == {r["name"] for r in co.COMPANY_RISKS}
    assert all(r["applies_to"] == [] for r in company)
    with dbm.session_scope() as db:
        for key in SPECS:
            names = {f["name"] for f in services.risk_factors_for(db, db.get(Agent, f"agt_{key}"))}
            assert {r["name"] for r in co.COMPANY_RISKS} <= names


def test_judge_sees_the_company_policy():
    c = compile_company_policy(co.COMPANY_POLICY_TEXT)
    prompt = build_prompt(JudgeContext("tool_call", {"tool": "book_hotel", "args": {}}, {"objectives": ["x"]}, None, company=c))
    assert prompt.startswith("<company_policy>") and "$2,000" in prompt
    from varuna.scoring.judge import SYSTEM_PROMPT
    assert "company policy wins" in SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------- observation and enforcement

@pytest.fixture()
def inline(monkeypatch):
    monkeypatch.setattr(services, "RUN_INLINE", True)


def _set_mode(default=None, agent=None, mode="keep"):
    with dbm.session_scope() as db:
        services.set_modes(db, default=default, agent_id=agent, agent_mode=mode)


def test_observation_mode_lets_actions_through_and_scores_them_later(fresh_db, monkeypatch):
    jobs = []
    monkeypatch.setattr(services, "_bg", type("Q", (), {"submit": lambda self, *a: jobs.append(a)})())
    _set_mode(default="observe")
    v = sdk("agt_travel").guard("export_traveler_data", {"destination": "me@gmail.com", "fields": ["passport"], "count": 412})
    assert v.verdict == "allow" and v.band == "pending" and v.raw["mode"] == "observe"
    with dbm.session_scope() as db:                                    # returned before any scoring happened
        assert db.scalar(select(Assessment).where(Assessment.event_id == v.event_id)) is None
    for fn, *args in jobs:                                             # the background worker runs
        fn(*args)
    with dbm.session_scope() as db:
        a = db.scalar(select(Assessment).where(Assessment.event_id == v.event_id))
        assert a.verdict == "allow" and a.band == "block" and a.explain["would_verdict"] == "block"
        assert a.reason.startswith("Observation mode: Varuna would have blocked this")
        assert db.scalar(select(Incident)).title.startswith("Would have blocked")
        assert db.scalar(select(Approval)) is None and db.get(Agent, "agt_travel").status == "active"


def test_observation_mode_never_holds_or_pauses(fresh_db, inline):
    _set_mode(default="observe")
    c = sdk("agt_travel")
    verdicts = [c.guard("charge_card", {"amount": 8400}).verdict for _ in range(4)]
    assert verdicts == ["allow"] * 4
    out, tools = _scenario("soc", "critical_system")
    assert tools["isolate_host"]["verdict"] == "allow"                  # it went ahead, and Varuna recorded what it would have done
    with dbm.session_scope() as db:
        a = db.scalar(select(Assessment).where(Assessment.event_id == tools["isolate_host"]["event_id"]))
        assert a.explain["would_verdict"] == "hold" and a.explain["company_policy"]
        assert db.get(Agent, "agt_travel").status == "active" and db.scalar(select(Approval)) is None


def test_per_agent_mode_overrides_the_company_default(fresh_db, inline):
    _set_mode(default="observe")
    _set_mode(agent="agt_itadmin", mode="enforce")
    assert sdk("agt_itadmin").guard("export_directory", {"destination": "me@gmail.com", "count": 3200}).verdict == "block"
    assert sdk("agt_soc").guard("delete_logs", {"host": "WS-114"}).verdict == "allow"
    _set_mode(agent="agt_itadmin", mode=None)                          # back to the company default
    assert sdk("agt_itadmin").guard("export_directory", {"destination": "me@gmail.com", "count": 3200}).verdict == "allow"


def test_mode_api(client):
    assert client.get("/v1/settings/mode").json() == {"default": "enforce", "agents": {}}
    assert client.put("/v1/settings/mode", json={"default": "observe"}).json()["default"] == "observe"
    assert client.put("/v1/agents/agt_legal/mode", json={"mode": "enforce"}).json()["agents"] == {"agt_legal": "enforce"}
    assert client.put("/v1/agents/agt_legal/mode", json={"mode": None}).json()["agents"] == {}
    assert client.put("/v1/settings/mode", json={"default": "sometimes"}).status_code == 422
    assert client.put("/v1/agents/nope/mode", json={"mode": "observe"}).status_code == 404
    agents = {a["id"]: a for a in client.get("/v1/agents").json()}
    assert agents["agt_legal"]["mode"] == "observe" and agents["agt_legal"]["mode_override"] is None
    assert client.get("/v1/meta").json()["mode"]["default"] == "observe"
