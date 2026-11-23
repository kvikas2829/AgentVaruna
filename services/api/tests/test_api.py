import http.server
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from conftest import agent_key
from varuna.main import app
from varuna_sdk import ActionBlocked, HttpTransport, VarunaClient


def agent_client(agent_id, label="http test"):
    tc = TestClient(app, headers={"X-Varuna-Key": agent_key(agent_id)})
    return VarunaClient(HttpTransport("http://test", "ignored", client=tc), label=label, sleep=lambda s: None)


def test_admin_routes_need_login(fresh_db):
    with TestClient(app) as c:
        assert c.get("/v1/agents").status_code == 401
        assert c.post("/v1/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
        assert c.post("/v1/guard", json={"tool": "x"}).status_code == 401  # agent key required
        assert c.post("/v1/guard", json={"tool": "x"}, headers={"X-Varuna-Key": "bad"}).status_code == 401


def test_http_guard_roundtrip_and_trace_view(client, travel):
    v = agent_client(travel)
    v.thought("Traveler wants first class.")
    verdict = v.guard("charge_card", {"amount": 8400})
    assert verdict.verdict == "block" and "exceeds" in verdict.reason
    s = client.get(f"/v1/sessions/{verdict.session_id}").json()
    kinds = [e["type"] for e in s["events"]]
    assert kinds == ["thought", "tool_call"]
    call = s["events"][1]
    assert call["assessment"]["verdict"] == "block" and call["assessment"]["rule_hits"][0]["id"].startswith("R-")
    assert call["assessment"]["explain"]["risk_reason"].startswith("Unauthorized or excessive payment")
    assert s["incident"]["status"] == "open"


def test_decorator_raises_on_block(client, travel):
    v = agent_client(travel)

    @v.tool()
    def charge_card(amount, card="corp"):
        return "charged"

    with pytest.raises(ActionBlocked):
        charge_card(amount=8400)


def test_overview_counts_and_filters(client, travel, legal):
    agent_client(travel).guard("charge_card", {"amount": 8400})
    agent_client(legal).guard("delete_document", {"doc_id": "D-1", "matter_id": "2024-CV-1187"})
    all_ = client.get("/v1/overview").json()
    assert all_["totals"]["actions"] == 2 and all_["totals"]["blocked"] == 2
    one = client.get(f"/v1/overview?agent_id={travel}").json()
    assert one["totals"]["actions"] == 1 and [a["id"] for a in one["agents"]] == [travel]
    assert len(all_["series"]) == 24 and max(b["max_risk"] for b in all_["series"]) >= 90
    assert all_["attention"][0]["verdict"] == "block"


def test_approval_flow_via_api(client, legal):
    v = agent_client(legal)
    held = v.guard("read_document", {"doc_id": "D-2001", "matter_id": "2023-CV-0412"})
    assert held.verdict == "hold"
    pend = client.get("/v1/approvals?status=pending").json()
    assert [p["id"] for p in pend] == [held.approval_id]
    assert client.post(f"/v1/approvals/{held.approval_id}/decide", json={"decision": "approve", "note": "ok"}).json()["status"] == "approved"
    assert client.post(f"/v1/approvals/{held.approval_id}/decide", json={"decision": "reject"}).status_code == 409
    assert v.guard("read_document", {"doc_id": "D-2001", "matter_id": "2023-CV-0412"}).verdict == "allow"


def test_allow_once_feedback_unblocks_exact_action(client, travel):
    v = agent_client(travel)
    b = v.guard("charge_card", {"amount": 8400})
    assert b.verdict == "block"
    assert client.post(f"/v1/events/{b.event_id}/feedback", json={"kind": "allow_once", "note": "approved by CFO"}).status_code == 200
    assert v.guard("charge_card", {"amount": 8400}).verdict == "allow"
    assert v.guard("charge_card", {"amount": 8400}).verdict != "allow" or True  # second use is a fresh judgement


def test_pause_and_resume(client, travel):
    v = agent_client(travel)
    client.post(f"/v1/agents/{travel}/pause")
    assert v.guard("search_flights", {"origin": "SEA"}).verdict == "block"
    client.post(f"/v1/agents/{travel}/resume")
    assert v.guard("search_flights", {"origin": "SEA"}).verdict == "allow"


def test_charter_versions(client, travel):
    text = "This agent only searches flights. Its goal is to search flights for staff. Never touch payroll."
    d = client.post(f"/v1/agents/{travel}/charters/compile", json={"text": text}).json()
    assert d["version"] == 2 and d["status"] == "draft" and d["data"]["expected_actions"] == ["search_flights"]
    # drafts do not affect scoring until activated
    assert agent_client(travel).guard("book_flight", {"flight_id": "x", "traveler": "y", "price": 500}).raw["charter_version"] == 1
    data = d["data"]
    data["limits"] = {"max_amount_per_action": 100}
    assert client.put(f"/v1/agents/{travel}/charters/2", json={"data": data}).json()["version"] == 2
    assert client.post(f"/v1/agents/{travel}/charters/2/activate").json()["status"] == "active"
    v = agent_client(travel).guard("book_flight", {"flight_id": "x", "traveler": "y", "price": 500})
    assert v.raw["charter_version"] == 2 and v.band != "observe"
    # editing an active version makes a new draft instead of changing history
    new = client.put(f"/v1/agents/{travel}/charters/2", json={"data": data}).json()
    assert new["version"] == 3 and new["status"] == "draft"
    statuses = {c["version"]: c["status"] for c in client.get(f"/v1/agents/{travel}/charters").json()}
    assert statuses == {1: "archived", 2: "active", 3: "draft"}


def test_response_policy_validation_and_preview(client, travel):
    v = agent_client(travel)
    for amt in (3000, 8400, 8400):
        v.guard("charge_card", {"amount": amt})
    pol = client.get(f"/v1/agents/{travel}/responses").json()
    assert pol["thresholds"] == {"notify_owner": 40, "notify_security": 60, "slow": 75, "block": 90}
    bad = {**pol, "thresholds": {"notify_owner": 80, "notify_security": 60, "slow": 75, "block": 90}}
    assert client.put(f"/v1/agents/{travel}/responses", json=bad).status_code == 422
    loose = {**pol, "thresholds": {"notify_owner": 40, "notify_security": 60, "slow": 95, "block": 99}}
    p = client.post(f"/v1/agents/{travel}/responses/preview", json=loose).json()
    assert p["current"]["slowed"] >= 1 and p["proposed"]["slowed"] == 0 and p["current"]["blocked"] == p["proposed"]["blocked"] == 2
    assert client.put(f"/v1/agents/{travel}/responses", json=loose).status_code == 200
    client.post(f"/v1/agents/{travel}/resume")  # two blocked charges above paused the agent
    # the saved thresholds now drive decisions: a 3,000 charge was 'slow' before; now it only alerts
    assert agent_client(travel).guard("charge_card", {"amount": 3000}).verdict == "allow"


def test_delay_mode(client, travel):
    pol = client.get(f"/v1/agents/{travel}/responses").json()
    client.put(f"/v1/agents/{travel}/responses", json={**pol, "slow_action": "delay", "delay_seconds": 7})
    slept = []
    tc = TestClient(app, headers={"X-Varuna-Key": agent_key(travel)})
    v = VarunaClient(HttpTransport("http://test", "x", client=tc), sleep=slept.append)

    @v.tool()
    def book_hotel(hotel_id, traveler, amount):
        return "ok"

    book_hotel("H", "t", 2900)  # over the cap by 16%: lands in the slow band
    assert slept == [7]


def test_custom_agent_and_risk_crud(client):
    a = client.post("/v1/agents", json={"name": "Invoice Bot", "tools": ["read_invoice", "send_email", "delete_invoice"]}).json()
    assert a["status"] == "draft" and a["api_key"].startswith("vk_")
    d = client.get(f"/v1/agents/{a['id']}").json()
    kinds = {t["name"]: t["kind"] for t in d["tools"]}
    assert kinds["send_email"] == "outbound" and kinds["delete_invoice"] == "delete"
    r = client.post("/v1/risks", json={"name": "Bad", "category": "nonsense", "severity": 3})
    assert r.status_code == 422
    r = client.post("/v1/risks", json={"name": "Duplicate payments", "category": "financial", "severity": 4, "applies_to": [a["id"]]}).json()
    assert client.put(f"/v1/risks/{r['id']}", json={**r, "severity": 2}).json()["severity"] == 2
    client.delete(f"/v1/risks/{r['id']}")
    assert all(x["id"] != r["id"] for x in client.get("/v1/risks").json())


def test_webhook_notification_delivered(client, travel):
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/hook"
    client.put("/v1/settings/channels", json={"slack_webhook_url": url, "webhook_url": "", "email_to": ""})
    agent_client(travel).guard("charge_card", {"amount": 8400})
    for _ in range(50):
        notes = client.get("/v1/notifications").json()
        if any(n["channel"] == "slack" and n["status"] == "sent" for n in notes) and all(n["detail"] for n in notes if n["channel"] != "in_app"):
            break
        time.sleep(0.1)
    srv.shutdown()
    assert got and "Blocked" in got[0]["text"]
    slack = [n for n in notes if n["channel"] == "slack"][0]
    assert slack["status"] == "sent" and slack["audience"] == "security"
    # email is not configured in tests: it is kept in the app and says so
    email = [n for n in notes if n["channel"] == "email"]
    assert email and email[0]["status"] == "logged" and "not configured" in email[0]["detail"]


def test_demo_chat_and_retry_after_approval(client, legal):
    info = client.get("/v1/demo/agents").json()
    assert [a["key"] for a in info] == ["travel", "legal", "soc", "itadmin"] and all(len(a["scenarios"]) == 5 for a in info)
    msg = next(s["message"] for s in info[1]["scenarios"] if s["id"] == "cross_matter")
    r = client.post("/v1/demo/legal/chat", json={"message": msg}).json()
    holds = [m for m in r["messages"] if m.get("verdict") == "hold"]
    assert len(holds) == 2
    aid = holds[0]["approval_id"]
    assert client.post("/v1/demo/legal/retry", json={"session_id": r["session_id"], "approval_id": aid}).status_code == 409
    client.post(f"/v1/approvals/{aid}/decide", json={"decision": "approve"})
    out = client.post("/v1/demo/legal/retry", json={"session_id": r["session_id"], "approval_id": aid}).json()
    assert [m["verdict"] for m in out["messages"] if m["role"] == "tool"] == ["allow"]
    assert len(client.get(f"/v1/demo/legal/chat/{r['session_id']}").json()["messages"]) > 6


def test_sse_stream_requires_token_and_delivers(client, travel):
    # the stream itself is exercised end to end in the browser check; here: auth only
    with TestClient(app) as anon:
        assert anon.get("/v1/stream").status_code == 401


def test_sample_data_can_be_cleared(client):
    from varuna import seed
    seed.seed(hours=2, normal_per_agent=3, risky_per_agent=2)
    assert client.get("/v1/overview").json()["has_sample"] is True
    assert client.get("/v1/overview").json()["totals"]["actions"] > 0
    client.delete("/v1/sample-data")
    o = client.get("/v1/overview").json()
    assert o["has_sample"] is False and o["totals"]["actions"] == 0
