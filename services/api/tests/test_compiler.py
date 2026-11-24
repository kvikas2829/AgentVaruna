from varuna.compiler import heuristic
from varuna_demo.registry import LEGAL_CHARTER_TEXT, LEGAL_TOOLS, TRAVEL_CHARTER_TEXT, TRAVEL_TOOLS


def test_travel_charter():
    c = heuristic.compile_charter(TRAVEL_CHARTER_TEXT, TRAVEL_TOOLS)
    assert c["objectives"] == ["Complete bookings that match each traveler's request and our travel policy"]
    assert len(c["key_results"]) == 3 and c["key_results"][0].startswith("95% of bookings")
    assert set(c["expected_actions"]) == {"search_flights", "search_hotels", "read_traveler_directory", "read_email", "book_flight",
                                          "book_hotel", "send_itinerary", "request_manager_approval", "check_travel_policy",
                                          "search_policies", "read_policy"}
    assert c["limits"]["max_amount_per_action"] == 2500
    assert c["limits"]["allowed_domains"] == ["meridian.example"]
    assert [x.lower() for x in c["forbidden_resources"]] == ["payroll", "hr records", "customer database"]
    assert any("passport" in a.lower() for a in c["avoid"])
    assert "Corporate travel portal" in c["allowed_resources"]
    # a tool the text never asked for must not be expected
    assert "charge_card" not in c["expected_actions"] and "export_traveler_data" not in c["expected_actions"]
    assert "charge_card" in c["high_impact_tools"]


def test_legal_charter():
    c = heuristic.compile_charter(LEGAL_CHARTER_TEXT, LEGAL_TOOLS)
    assert c["limits"]["scope_ids"] == ["2024-CV-1187"]
    assert set(c["limits"]["allowed_domains"]) == {"portal.hm-productions.example", "harlanmoore.example", "meridian.example"}
    assert set(c["expected_actions"]) == {"search_documents", "read_document", "tag_document", "summarize_document",
                                          "build_production_set", "produce_to_opposing_counsel", "get_matter",
                                          "search_policies", "read_policy"}
    assert not {"delete_document", "export_documents", "send_email"} & set(c["expected_actions"])
    assert any("privileged" in a.lower() for a in c["avoid"])
    # the case name "Northwind Retail v. Meridian Freight" must not split a sentence
    assert any("Northwind Retail v. Meridian Freight" in s for s in c["sentences"])


def test_refs_point_at_real_sentences():
    c = heuristic.compile_charter(TRAVEL_CHARTER_TEXT, TRAVEL_TOOLS)
    n = len(c["sentences"])
    for key, idxs in c["refs"].items():
        assert len(idxs) == len(c[key]) and all(0 <= i < n for i in idxs)


def test_risk_compile():
    r = heuristic.compile_risks(
        "Anything that sends customer data outside the company would be a legal and reputational disaster. "
        "Paying for something over our limit could cost us up to $50,000 per day. A rude message to one executive is a minor annoyance.")
    assert [x["category"] for x in r] == ["data_leakage", "financial", "reputational"]
    assert r[0]["severity"] == 5 and r[1]["extent_text"] == "Up to $50,000 per day" and r[1]["extent_factor"] == 1.0
    assert r[2]["severity"] == 2
    assert r[0]["name"].startswith("Sends customer data")


def test_protected_targets_are_extracted():
    from varuna_demo.registry import SPECS
    for key, want in (("soc", {"Domain controllers", "Production databases", "Executive accounts"}),
                      ("itadmin", {"Domain Admins", "Global Administrators", "Local Admins", "CRM Admin", "HubSpot Admin", "GitHub Org Owner", "Executive accounts"})):
        c = heuristic.compile_charter(SPECS[key]["charter_text"], SPECS[key]["tools"])
        assert set(c["limits"]["protected_targets"]) == want
    soc = heuristic.compile_charter(SPECS["soc"]["charter_text"], SPECS["soc"]["tools"])
    assert "isolate_host" in soc["expected_actions"] and "delete_logs" not in soc["expected_actions"] and "export_logs" not in soc["expected_actions"]
    it = heuristic.compile_charter(SPECS["itadmin"]["charter_text"], SPECS["itadmin"]["tools"])
    assert "delete_account" not in it["expected_actions"] and "export_directory" not in it["expected_actions"]
