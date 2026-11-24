"""Company-level policy and risks on top of agent charters, and observation versus enforcement mode."""
import pytest
from sqlalchemy import select

from conftest import sdk
from varuna import db as dbm, services
from varuna.compiler.heuristic import compile_company_policy
from varuna.models import Agent, Approval, Assessment, CompanyPolicy, Event, Incident
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


# ---------------------------------------------------------------- IDs Varuna recognises, and amounts it doesn't take on trust

def test_ids_alone_are_recognised_from_the_inventory(fresh_db):
    v = sdk("agt_legal").guard("read_document", {"doc_id": "D-1090", "matter_id": "2024-CV-1187"})      # no "HR records" in the call
    assert v.verdict == "block" and "HR records" in v.reason
    w = sdk("agt_soc").guard("isolate_host", {"host": "WMS-APP-01"})                                  # no role given
    assert w.verdict == "hold" and "Warehouse management system" in w.reason
    assert sdk("agt_soc").guard("isolate_host", {"host": "WS-114", "reason": "Qakbot"}).verdict == "allow"   # ordinary laptop
    with dbm.session_scope() as db:
        a = db.scalar(select(Assessment).where(Assessment.event_id == v.event_id))
        assert "D-1090" in a.explain["known"]
        ev = db.get(Event, v.event_id)
        assert "_known" not in ev.payload["args"]                                                  # the agent's call is stored as made


def test_known_descriptions_do_not_trip_phrase_checks(fresh_db):
    # D-1051's description mentions email; tagging it privileged must stay routine
    v = sdk("agt_legal").guard("tag_document", {"doc_id": "D-1051", "tag": "privileged", "matter_id": "2024-CV-1187"})
    assert v.verdict == "allow" and v.band == "observe"


def test_ids_are_learned_from_tool_results(fresh_db):
    c = sdk("agt_legal")
    first = c.guard("search_documents", {"query": "drivers", "matter_id": "2024-CV-1187"})
    assert c.guard("read_document", {"doc_id": "X-777", "matter_id": "2024-CV-1187"}).verdict == "allow"   # unknown yet
    c.result("search_documents", True, {"hits": [{"doc_id": "X-777", "title": "Driver file", "collection": "HR records"}]},
             first.event_id)
    v = c.guard("read_document", {"doc_id": "X-777", "matter_id": "2024-CV-1187"})
    assert v.verdict == "block" and "HR records" in v.reason


def test_payment_without_an_amount_is_held(fresh_db):
    v = sdk("agt_travel").guard("book_hotel", {"hotel_id": "The Langham", "traveler": "Alex Rivera", "amount": 0, "nights": 3})
    assert v.verdict == "hold" and "no amount stated" in v.reason


def test_charged_more_than_declared_raises_an_incident_and_counts(fresh_db):
    from varuna_demo.meridian import travel
    c = sdk("agt_travel")
    f = c.guard("book_flight", {"flight_id": "DL412", "traveler": "Alex Rivera", "price": 612})
    c.result("book_flight", True, travel.book_flight("DL412", "Alex Rivera", 612), f.event_id)
    h = c.guard("book_hotel", {"hotel_id": "LG-552", "traveler": "Alex Rivera", "amount": 300, "nights": 3})  # under-declared
    assert h.verdict == "allow"                                                     # $912 declared: within every limit
    out = travel.book_hotel("LG-552", "Alex Rivera", 300, 3)
    assert out["charged"] == 1650                                                   # the stub charges the real rate
    c.result("book_hotel", True, out, h.event_id)
    with dbm.session_scope() as db:
        inc = db.scalar(select(Incident))
        assert inc is not None and "charged $1,650, more than the $300 declared" in inc.title
        call = db.get(Event, h.event_id)
        assert call.payload["actual_amount"] == 1650
        res = db.scalars(select(Assessment).where(Assessment.enforceable.is_(False)).order_by(Assessment.id.desc())).first()
        assert res.rule_hits[0]["id"] == "R-AMT-03" and any(x["id"] == "CO-AMT-01" for x in res.rule_hits)
    # the next booking counts the $1,650 actually spent, not the $300 declared
    n = c.guard("book_hotel", {"hotel_id": "CY-104", "traveler": "Alex Rivera", "amount": 265, "nights": 1})
    assert n.verdict != "allow" and "with earlier bookings" in n.reason


def test_booking_stubs_charge_catalog_prices():
    from varuna_demo.meridian import travel
    travel.reset()
    assert travel.book_flight("DL412", "A", price=0)["charged"] == 612
    assert travel.book_hotel("The Langham", "A", amount=0, nights=3)["charged"] == 1650
    assert travel.book_hotel("HX-218", "A", amount=1020)["charged"] == 1020                       # nights inferred: 3


def test_known_resources_api(client):
    rows = {r["key"]: r["text"] for r in client.get("/v1/company/resources").json()}
    assert "warehouse management system" in rows["WMS-APP-01"] and "HR records" in rows["D-1090"]
