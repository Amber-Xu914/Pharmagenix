def test_meta(client):
    data = client.get("/api/meta").get_json()
    assert {r["key"] for r in data["relation_types"]} >= {"inhibit", "induce", "bind", "snp"}
    assert data["providers"]["primary"]["provider"] == "openai"


def test_explore_with_comparison(client, fake_sources):
    resp = client.post("/api/explore", json={"entities": "CYP2C19, clopidogrel", "relation_types": ["snp"],
                                             "compare": True})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["status"] == "complete"
    assert data["entities"] == ["CYP2C19", "clopidogrel"]
    assert [r["role"] for r in data["reports"]] == ["primary", "comparison"]
    assert all(r["status"] == "success" for r in data["reports"])
    assert data["reports"][1]["citations"] == ["E1", "E2"]
    assert len(data["evidence"]["table"]) == 2


def test_explore_validation(client, fake_sources):
    assert client.post("/api/explore", json={"entities": ""}).status_code == 400
    assert client.post("/api/explore", json={"entities": "BRAF", "relation_types": ["nope"]}).status_code == 400
    assert client.post("/api/explore", json={"entities": "BRAF", "entity_type": "x"}).status_code == 400


def test_hallucinated_citation_is_flagged(client, fake_sources):
    fake_sources["answers"]["openai"] = "Claim [E1] and made-up [E42]."
    report = client.post("/api/explore", json={"entities": ["BRAF"]}).get_json()["reports"][0]
    assert report["status"] == "flagged"
    assert report["invalid_citations"] == ["E42"]


def test_followup_reuses_evidence(client, fake_sources):
    first = client.post("/api/explore", json={"entities": ["CYP2C19"]}).get_json()
    resp = client.post(f"/api/queries/{first['id']}/ask", json={"followup_question": "What about *17?"})
    assert resp.status_code == 201
    second = resp.get_json()
    assert second["parent_id"] == first["id"]
    assert second["followup_question"] == "What about *17?"
    assert fake_sources["calls"]["collect"] == 1


def test_feedback(client, fake_sources):
    report_id = client.post("/api/explore", json={"entities": ["BRAF"]}).get_json()["reports"][0]["id"]
    resp = client.post(f"/api/reports/{report_id}/feedback", json={"rating": 4, "suggestion": "More SNPs"})
    assert resp.status_code == 201
    assert resp.get_json()["report"]["avg_rating"] == 4
    assert client.post(f"/api/reports/{report_id}/feedback", json={"rating": 9}).status_code == 400


def test_panel_upload(client):
    from io import BytesIO

    resp = client.post("/api/panels/parse", data={"file": (BytesIO(b"GENE\nBRAF\nEGFR\n"), "panel.txt")},
                       content_type="multipart/form-data")
    assert resp.get_json()["genes"] == ["BRAF", "EGFR"]
