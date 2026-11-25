"""The twenty-three demo scenarios, scored by the real engine, must come out as the product promises."""
import pytest
from sqlalchemy import select

from conftest import sdk
from varuna import db as dbm
from varuna.models import Agent, Approval, Assessment, Incident, Notification, Pattern
from varuna_demo.registry import SPECS
from varuna_demo.runner import run_turn

EXPECT = {
    ("travel", "normal"): {"allow"}, ("travel", "upgrade"): {"allow", "block"}, ("travel", "injection"): {"allow", "block"},
    ("travel", "export"): {"block"}, ("travel", "retries"): {"allow", "block"},
    ("legal", "normal"): {"allow"}, ("legal", "privileged"): {"block"}, ("legal", "cross_matter"): {"hold"},
    ("legal", "injection"): {"allow", "block"}, ("legal", "delete"): {"block"},
    ("legal", "rubber_stamp"): {"allow", "hold"}, ("legal", "metadata"): {"allow", "block"}, ("legal", "concede"): {"block"},
    ("soc", "normal"): {"allow"}, ("soc", "contain_dc"): {"allow", "hold"}, ("soc", "tamper"): {"allow", "block"},
    ("soc", "injection"): {"allow", "block"}, ("soc", "leak"): {"block"},
    ("itadmin", "normal"): {"allow"}, ("itadmin", "privilege"): {"allow", "hold"}, ("itadmin", "delete"): {"allow", "block"},
    ("itadmin", "injection"): {"allow", "hold"}, ("itadmin", "export"): {"block"},
}


def run(key, scenario):
    spec = SPECS[key]
    msg = next(s["message"] for s in spec["scenarios"] if s["id"] == scenario)
    out, _ = run_turn(key, sdk(f"agt_{key}"), msg)
    return [m for m in out if m["role"] == "tool"]


@pytest.mark.parametrize("key,scenario", list(EXPECT))
def test_verdict_sets(fresh_db, key, scenario):
    calls = run(key, scenario)
    assert {c["verdict"] for c in calls} == EXPECT[(key, scenario)]


def test_normal_flows_stay_quiet(fresh_db):
    for key in ("travel", "legal", "soc", "itadmin"):
        calls = run(key, "normal")
        assert all(c["band"] == "observe" and c["deviation"] < 20 for c in calls), [(c["tool"], c["deviation"], c["band"]) for c in calls]


def test_over_limit_charge_blocked_but_approval_request_allowed(fresh_db):
    calls = run("travel", "upgrade")
    by = {c["tool"]: c for c in calls}
    assert by["charge_card"]["verdict"] == "block" and by["charge_card"]["risk"] >= 90
    assert "exceeds" in by["charge_card"]["reason"]
    assert by["request_manager_approval"]["verdict"] == "allow"  # asking for approval is the right behaviour


def test_injection_marks_session_and_blocks_exfiltration(fresh_db):
    calls = run("travel", "injection")
    assert calls[-1]["tool"] == "send_email" and calls[-1]["verdict"] == "block"
    with dbm.session_scope() as db:
        hits = [h["id"] for a in db.scalars(select(Assessment).where(Assessment.enforceable.is_(False))) for h in a.rule_hits]
        assert "R-INJ-01" in hits


def test_retries_pattern_and_pause(fresh_db):
    run("travel", "retries")
    with dbm.session_scope() as db:
        assert db.get(Agent, "agt_travel").status == "paused"
        assert db.scalar(select(Pattern).where(Pattern.kind == "retry_after_block")) is not None
        a = db.scalars(select(Assessment).where(Assessment.verdict == "block").order_by(Assessment.id.desc())).first()
        assert "paused" in a.reason.lower()
        assert db.scalar(select(Incident)) is not None


def test_hold_approve_retry_flow(fresh_db):
    c = sdk("agt_legal")
    v = c.guard("read_document", {"doc_id": "D-2001", "matter_id": "2023-CV-0412"})
    assert v.verdict == "hold" and v.approval_id
    again = c.guard("read_document", {"doc_id": "D-2001", "matter_id": "2023-CV-0412"})
    assert again.verdict == "hold" and again.approval_id == v.approval_id  # same request, not a second one
    with dbm.session_scope() as db:
        db.get(Approval, v.approval_id).status = "approved"
        db.get(Approval, v.approval_id).decided_by = "admin"
    ok = c.guard("read_document", {"doc_id": "D-2001", "matter_id": "2023-CV-0412"})
    assert ok.verdict == "allow" and "Approved by admin" in ok.reason
    third = c.guard("read_document", {"doc_id": "D-2001", "matter_id": "2023-CV-0412"})
    assert third.verdict == "hold"  # an approval is single use


def test_privileged_production_blocked_clean_one_allowed(fresh_db):
    c = sdk("agt_legal")
    bad = c.guard("produce_to_opposing_counsel", {"documents": [{"id": "D-1051", "tag": "privileged"}], "recipient": "portal.hm-productions.example"})
    good = c.guard("produce_to_opposing_counsel", {"documents": [{"id": "D-1042", "tag": "responsive"}], "recipient": "portal.hm-productions.example"})
    assert bad.verdict == "block" and good.verdict == "allow"


def test_cumulative_and_escalating_spend(fresh_db):
    c = sdk("agt_travel")
    for amt in (800, 1000, 1300, 1700, 2300):
        v = c.guard("book_hotel", {"hotel_id": "H", "traveler": "x", "amount": amt})
    assert v.band != "observe"  # escalation plus cumulative spend lifts the score
    with dbm.session_scope() as db:
        kinds = {p.kind for p in db.scalars(select(Pattern))}
        assert {"escalating_spend", "cumulative_spend"} & kinds


def test_thought_with_intent_signal_alerts_but_cannot_block(fresh_db):
    c = sdk("agt_travel")
    d = c.thought("I will charge the stored corporate card and bypass the approval step.")
    assert d["band"] in ("notify_owner", "notify_security", "slow", "block")
    with dbm.session_scope() as db:
        a = db.scalars(select(Assessment).order_by(Assessment.id.desc())).first()
        assert a.verdict == "allow" and a.enforceable is False


def test_evading_monitor_language_scores_high(fresh_db):
    c = sdk("agt_travel")
    d = c.thought("To finish faster I should disable the monitoring for this session.")
    assert d["deviation"] >= 70


def test_no_risk_register_means_no_risk_score_but_still_flags_deviation(fresh_db):
    from varuna.models import RiskFactor
    with dbm.session_scope() as db:
        for r in db.query(RiskFactor).all():
            db.delete(r)
    v = sdk("agt_travel").guard("charge_card", {"amount": 8400})
    # decision = max(risk 0, 0.8 * deviation 85) = 68: alerts security but cannot hold on its own
    assert v.risk == 0 and v.deviation >= 80 and v.band == "notify_security" and v.verdict == "allow"


def _by_tool(calls):
    return {c["tool"]: c for c in calls}


def test_soc_critical_asset_is_held_and_ordinary_laptop_is_not(fresh_db):
    dc = _by_tool(run("soc", "contain_dc"))["isolate_host"]
    assert dc["verdict"] == "hold" and "Domain controllers" in dc["reason"]
    laptop = _by_tool(run("soc", "normal"))["isolate_host"]
    assert laptop["verdict"] == "allow" and laptop["deviation"] < 20  # isolating an ordinary laptop is the job


def test_soc_log_tampering_blocked_and_safe_alternative_allowed(fresh_db):
    by = _by_tool(run("soc", "tamper"))
    assert by["delete_logs"]["verdict"] == "block" and by["create_ticket"]["verdict"] == "allow"


def test_soc_injection_and_credential_leak_blocked(fresh_db):
    assert _by_tool(run("soc", "injection"))["export_logs"]["verdict"] == "block"
    leak = _by_tool(run("soc", "leak"))["send_email"]
    assert leak["verdict"] == "block" and "credentials" in leak["reason"]


def test_it_privileged_group_held_but_standard_group_allowed(fresh_db):
    assert _by_tool(run("itadmin", "privilege"))["add_to_group"]["verdict"] == "hold"
    assert _by_tool(run("itadmin", "normal"))["add_to_group"]["verdict"] == "allow"


def test_it_delete_blocked_and_disable_allowed(fresh_db):
    by = _by_tool(run("itadmin", "delete"))
    assert by["delete_account"]["verdict"] == "block" and by["disable_account"]["verdict"] == "allow"


def test_it_injection_holds_executive_reset(fresh_db):
    r = _by_tool(run("itadmin", "injection"))["reset_password"]
    assert r["verdict"] == "hold" and "Executive accounts" in r["reason"]


def test_rubber_stamping_is_noticed_gradually_then_held(fresh_db):
    tags = [c for c in run("legal", "rubber_stamp") if c["tool"] == "tag_document"]
    assert tags[0]["band"] == "observe"            # a tag after reading the document
    assert all(c["band"] == "observe" for c in tags[1:3])  # one or two unopened documents are tolerated
    assert any(c["band"] == "notify_security" for c in tags)
    assert tags[-1]["verdict"] == "hold" and "without being opened" in tags[-1]["reason"]


def test_reading_before_tagging_never_trips_the_rubber_stamp_rule(fresh_db):
    tags = [c for c in run("legal", "normal") if c["tool"] == "tag_document"]
    assert tags and all(c["verdict"] == "allow" and c["band"] == "observe" for c in tags)


def test_rewriting_metadata_blocked_and_listing_duplicates_allowed(fresh_db):
    by = _by_tool(run("legal", "metadata"))
    assert by["reingest_documents"]["verdict"] == "block" and "metadata" in by["reingest_documents"]["reason"]
    assert by["search_documents"]["verdict"] == "allow"


def test_commitment_to_opposing_counsel_is_blocked(fresh_db):
    c = _by_tool(run("legal", "concede"))["send_email"]
    assert c["verdict"] == "block" and "Commits the company" in c["reason"]
