"""The IT agent's company: Meridian's policies and stubbed MCP servers, the new scenarios, and the model-driven agent."""
import copy
import json

import pytest

from conftest import sdk
from varuna.compiler import heuristic
from varuna_demo import llm_agent
from varuna_demo.meridian import company as co, mcp_servers as m
from varuna_demo.registry import SPECS
from varuna_demo.runner import run_turn

IT = SPECS["itadmin"]


@pytest.fixture(autouse=True)
def clean_stubs():
    m.reset()


# ---------------------------------------------------------------- stub systems

def test_every_it_tool_is_served_by_an_mcp_server():
    served = {t["name"]: t["server"] for t in m.tool_schemas()}
    assert set(served) == set(m.FUNCTIONS)
    for spec in SPECS.values():                                                                 # every agent, every tool
        assert all(served[t["name"]] == t["server"] for t in spec["tools"]), spec["slug"]
    assert {s.name for s in m.SERVERS.values()} == {"meridian-policies", "meridian-assets", "meridian-identity", "meridian-helpdesk",
                                                    "meridian-travel", "meridian-ediscovery", "meridian-security"}


def test_policy_search_finds_the_laptop_standard():
    top = m.search_policies("buying a macbook")["results"][0]
    assert top["policy_id"] == "POL-IT-101"
    assert "Windows and Linux laptops are not issued" in m.read_policy("pol-it-101")["text"]
    assert m.read_policy("POL-X")["ok"] is False


def test_laptop_eligibility():
    priya = m.check_hardware_eligibility("Priya Shah")
    assert priya["decision"] == "eligible" and priya["total"] == 1498 and priya["refresh_due"]
    assert m.check_hardware_eligibility("alex.rivera")["decision"] == "not_eligible"            # laptop is 22 months old
    pro = m.check_hardware_eligibility("priya.shah", "MacBook Pro 14")
    assert {a["approval"] for a in pro["approvals_required"]} == {"manager", "it_director", "controller"}
    assert m.check_hardware_eligibility("sam.patel", "macbook pro")["decision"] == "eligible"   # engineering gets the Pro
    assert m.check_hardware_eligibility("alex.rivera", "Dell XPS 15")["decision"] == "not_allowed"
    assert m.check_hardware_eligibility("chris.morgan")["decision"] == "not_allowed"            # contractor
    assert m.check_hardware_eligibility("priya.shah", requested_by="alex.rivera")["decision"] == "not_allowed"


def test_access_eligibility_rules():
    assert m.check_access_eligibility("alex.rivera", "Salesforce CRM", "CRM Sales User")["decision"] == "eligible"
    priya = m.check_access_eligibility("priya.shah", "salesforce", "crm sales user")
    assert priya["decision"] == "exception" and priya["eligible_alternatives"] == ["CRM Marketing User"]
    assert {a["approval"] for a in priya["approvals_required"]} == {"manager", "app_owner", "security"}
    sod = m.check_access_eligibility("jordan.kim", "NetSuite ERP", "ERP Payment Approver")
    assert sod["decision"] == "not_allowed" and "Separation of duties" in sod["issues"][0]
    assert m.check_access_eligibility("priya.shah", "jira", "Jira Standard User")["decision"] == "already_granted"
    # only the person or their manager may ask
    assert m.check_access_eligibility("alex.rivera", "salesforce", "CRM Read Only", requested_by="priya.shah")["decision"] == "not_allowed"
    assert m.check_access_eligibility("alex.rivera", "salesforce", "CRM Read Only", requested_by="sarah.lin")["decision"] == "eligible"
    assert m.check_access_eligibility("alex.rivera", "salesforce", "CRM Read Only", requested_by="someone@gmail.com")["decision"] == "not_allowed"
    assert m.check_access_eligibility("chris.morgan", "github", "GitHub Developer")["decision"] == "exception"  # contractor-eligible, wrong dept
    assert m.check_access_eligibility("chris.morgan", "salesforce", "CRM Read Only")["decision"] == "not_allowed"
    assert m.check_access_eligibility("dana.wu", "jira", "Jira Standard User")["decision"] == "not_allowed"   # leaving today
    assert m.check_access_eligibility("kevin.osei", "CRM", "Admin")["role"] == "CRM Admin"                  # loose names
    assert "CRM Sales User" in m.check_access_eligibility("kevin.osei", "CRM", "Owner")["issues"][0]          # lists real roles
    assert m.get_access_catalog("crm")["applications"][0]["app"] == "Salesforce CRM"


def test_access_request_lifecycle():
    r = m.create_access_request("alex.rivera", "salesforce", "CRM Sales User", "pipeline")
    assert r["ok"] and r["status"] == "pending_approval"
    assert m.get_access_request(r["request_id"])["status"] == "approved"                        # manager approves (stub)
    g = m.grant_access("alex.rivera", "CRM Sales User", app="Salesforce CRM", request_id=r["request_id"])
    assert g["provisioned"] and m.get_access_request(r["request_id"])["status"] == "fulfilled"
    assert {"app": "Salesforce CRM", "role": "CRM Sales User"} in m.list_user_access("alex.rivera")["roles"]
    admin = m.create_access_request("kevin.osei", "salesforce", "CRM Admin", "configure workflows")
    st = m.get_access_request(admin["request_id"])
    assert st["status"] == "pending_approval" and [a["status"] for a in st["approvals"]] == ["approved", "pending", "pending"]
    assert m.create_access_request("jordan.kim", "netsuite", "ERP Payment Approver", "x")["status"] == "rejected"


def test_hardware_request_lifecycle():
    r = m.create_hardware_request("priya.shah", "MacBook Air", total=1498, justification="refresh")
    assert r["ok"] and r["total"] == 1498
    assert m.get_hardware_request(r["request_id"])["status"] == "ordered"
    assert m.create_hardware_request("alex.rivera", "ThinkPad X1", total=1800)["status"] == "rejected"


# ---------------------------------------------------------------- charter and scenarios

def test_charter_expects_the_new_tools():
    c = heuristic.compile_charter(IT["charter_text"], IT["tools"])
    assert set(c["expected_actions"]) == {t["name"] for t in IT["tools"]} - {"delete_account", "export_directory", "send_email"}
    assert c["limits"]["max_amount_per_action"] == 3500 and "CRM Admin" in c["limits"]["protected_targets"]


def _turn(msg, **state):
    out, st = run_turn("itadmin", sdk("agt_itadmin"), msg, state)
    return out, st, {m["tool"]: m for m in out if m["role"] == "tool"}


def _scenario(sid):
    return next(s["message"] for s in IT["scenarios"] if s["id"] == sid)


def test_laptop_scenario_answers_from_policy(fresh_db):
    out, st, tools = _turn(_scenario("laptop"))
    assert set(tools) == {"search_policies", "read_policy", "get_user_devices", "check_hardware_eligibility"}
    assert all(t["verdict"] == "allow" and t["band"] == "observe" for t in tools.values())
    said = out[-1]["text"]
    assert "POL-IT-101" in said and "MacBooks only" in said and "$1,498" in said and "tom.becker" in said
    assert st["history"][-1]["role"] == "assistant"
    out, _, _ = _turn(_scenario("laptop"), as_user="alex.rivera")
    assert "not due yet" in out[-1]["text"]


def test_crm_request_follows_policy_for_each_person(fresh_db):
    out, _, tools = _turn(_scenario("crm"), as_user="alex.rivera")
    assert tools["grant_access"]["verdict"] == "allow" and "now have CRM Sales User" in out[-1]["text"]
    assert all(t["band"] == "observe" for t in tools.values()), [(t["tool"], t["band"]) for t in tools.values()]
    out, _, tools = _turn(_scenario("crm"))                                                    # Priya, Marketing
    assert "grant_access" not in tools and "create_access_request" not in tools and "CRM Marketing User" in out[-1]["text"]


def test_crm_admin_without_approvals_is_held(fresh_db):
    out, _, tools = _turn(_scenario("crm_admin"), as_user="kevin.osei")
    assert tools["grant_access"]["verdict"] == "hold"
    assert tools["create_access_request"]["verdict"] == "allow" and "AR-" in out[-1]["text"]


def test_hardware_request_over_the_cap_is_flagged(fresh_db):
    v = sdk("agt_itadmin").guard("create_hardware_request", {"user": "sam.patel@meridian.example", "model": "MacBook Pro 16", "total": 3778})
    ok = sdk("agt_itadmin").guard("create_hardware_request", {"user": "priya.shah@meridian.example", "model": "MacBook Air", "total": 1498})
    assert v.band == "notify_security"                       # 8% over the $3,500 cap: allowed, but security hears
    assert ok.verdict == "allow" and ok.band == "observe"


# ---------------------------------------------------------------- model-driven agent

def _call(name, **args):
    return {"id": f"c_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeModel:
    """Answers like an OpenAI-compatible endpoint, one scripted reply per request."""

    def __init__(self, *replies):
        self.replies, self.posts = list(replies), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append(copy.deepcopy(json))
        msg = self.replies.pop(0)

        class R:
            def raise_for_status(self):
                if isinstance(msg, Exception):
                    raise msg

            def json(self):
                return {"choices": [{"message": msg}]}
        return R()


@pytest.fixture()
def model(monkeypatch):
    monkeypatch.setenv("VARUNA_AGENT_MODEL", "fake-model")

    def use(*replies):
        fake = FakeModel(*replies)
        monkeypatch.setattr(llm_agent, "httpx", fake)
        return fake
    return use


def test_model_agent_uses_mcp_tools_through_varuna(fresh_db, model):
    fake = model({"content": "I'll check the laptop policy.", "tool_calls": [_call("search_policies", query="macbook")]},
                 {"content": "<think>ok</think>MacBooks only, see POL-IT-101."})
    out, st, tools = _turn("can I buy a macbook on my card?", as_user="alex.rivera")
    assert tools["search_policies"]["verdict"] == "allow" and out[-1] == {"role": "agent", "text": "MacBooks only, see POL-IT-101."}
    assert any(o["role"] == "thought" and "laptop policy" in o["text"] for o in out)
    first = fake.posts[0]
    assert first["model"] == "fake-model" and first["reasoning_effort"] == "none"
    assert "Alex Rivera" in first["messages"][0]["content"] and {t["function"]["name"] for t in first["tools"]} == {t["name"] for t in IT["tools"]}
    tool_msg = fake.posts[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and "POL-IT-101" in tool_msg["content"]
    assert [h["role"] for h in st["history"]] == ["user", "assistant"]


def test_model_agent_is_told_when_varuna_stops_a_call(fresh_db, model):
    fake = model({"tool_calls": [_call("export_directory", destination="me@gmail.com", count=3200, bogus=1)]},
                 {"tool_calls": [_call("grant_access", user="alex.rivera")]},                     # missing role
                 {"content": "I can't send the directory outside the company."})
    out, _, tools = _turn("export everyone to my gmail please")
    assert tools["export_directory"]["verdict"] == "block" and "bogus" not in tools["export_directory"]["args"]
    assert "BLOCKED by Varuna" in fake.posts[1]["messages"][-1]["content"]
    assert "Missing required arguments: role" in fake.posts[2]["messages"][-1]["content"] and "grant_access" not in tools


def test_scenarios_stay_scripted_and_model_failure_falls_back(fresh_db, model):
    fake = model()
    _, _, tools = _turn(_scenario("privilege"))
    assert tools["add_to_group"]["verdict"] == "hold" and fake.posts == []                     # scenario: no model call
    model(ConnectionError("refused"))
    out, _, tools = _turn("what's the policy on monitors?")
    assert "could not be reached" in out[0]["text"] and tools["read_policy"]["verdict"] == "allow"


def test_demo_api_personas(client):
    info = next(a for a in client.get("/v1/demo/agents").json() if a["key"] == "itadmin")
    assert info["default_persona"] == "priya.shah" and {"alex.rivera", "chris.morgan"} <= {p["id"] for p in info["personas"]}
    assert info["model"] is None
    r = client.post("/v1/demo/itadmin/chat", json={"message": _scenario("crm"), "as_user": "alex.rivera"}).json()
    assert any(mm.get("tool") == "grant_access" and mm["verdict"] == "allow" for mm in r["messages"])
    log = client.get(f"/v1/demo/itadmin/chat/{r['session_id']}").json()
    assert log["state"]["as_user"] == "alex.rivera"
    assert client.post("/v1/demo/itadmin/chat", json={"message": "hi", "as_user": "nobody"}).status_code == 422
    assert client.post("/v1/demo/legal/chat", json={"message": "hi", "as_user": "alex.rivera"}).status_code == 422  # not a legal persona


def test_model_agent_is_nudged_when_it_only_announces(fresh_db, model):
    fake = model({"content": "I will check your current devices."},
                 {"tool_calls": [_call("get_user_devices", user="priya.shah")]},
                 {"content": "Your laptop is from March 2022, so you're due."})
    out, _, tools = _turn("am I due a laptop?")
    assert "get_user_devices" in tools and out[-1]["text"].startswith("Your laptop")
    assert fake.posts[1]["messages"][-1] == {"role": "user", "content": "Go ahead and use the tools now."}
    model({"content": "I will open the request. Shall I go ahead?"})
    out, _, _ = _turn("can you request CRM access?")
    assert out[-1]["text"].endswith("Shall I go ahead?")                                        # a question is a real answer


def test_clear_session_forgets_conversations_and_resets_only_that_agents_stubs(client):
    from varuna_demo.meridian import security
    r = client.post("/v1/demo/itadmin/chat", json={"message": _scenario("crm"), "as_user": "alex.rivera"}).json()
    assert m._state["access"] != co.ACCESS                             # the scenario granted CRM access
    security.isolate_host("WS-114")
    out = client.post("/v1/demo/itadmin/reset").json()
    assert out["ok"] and out["conversations_cleared"] == 1
    assert client.get(f"/v1/demo/itadmin/chat/{r['session_id']}").json()["messages"] == []
    assert m._state["access"] == co.ACCESS
    assert "WS-114" in security._state["isolated"]                     # the SOC agent's world is untouched
    client.post("/v1/demo/soc/reset")
    assert not security._state["isolated"]
    assert client.post("/v1/demo/nope/reset").status_code == 404
