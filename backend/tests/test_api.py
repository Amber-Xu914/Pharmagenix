def test_meta(client):
    data = client.get("/api/meta").get_json()
    assert {r["key"] for r in data["relation_types"]} >= {"inhibit", "induce", "bind", "snp"}
    assert data["providers"]["primary"]["provider"] == "openai"
    assert data["reference_data"] == []


def test_meta_lists_reference_data(app, client):
    from app.extensions import db
    from app.reference.models import SourceRelease

    db.session.add_all([SourceRelease(source="pbs", version="PBS Schedule 4751", status="ok"),
                        SourceRelease(source="cpic", version="broken", status="failed")])
    db.session.commit()
    data = client.get("/api/meta").get_json()
    assert [(r["source"], r["version"]) for r in data["reference_data"]] == [("pbs", "PBS Schedule 4751")]
    assert data["reference_data"][0]["imported_at"].endswith("Z")


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


def test_query_includes_entity_details(app, client):
    from app.extensions import db
    from app.models import Query
    from app.reference.models import Drug, Gene

    db.session.add_all([
        Gene(hgnc_id="HGNC:2625", symbol="CYP2D6", name="cytochrome P450 family 2 subfamily D member 6",
             location="22q13.2", ensembl_gene_id="ENSG00000100197", uniprot_ids=["P10635"]),
        Drug(chembl_id="CHEMBL485", pref_name="CODEINE", max_phase=4),
    ])
    normalisation = [
        {"input": "CYP2D6", "name": "CYP2D6", "type": "gene", "id": "HGNC:2625"},
        {"input": "codeine", "name": "CODEINE", "type": "drug", "id": "CHEMBL485"},
        {"input": "xyz", "name": "XYZ", "type": None, "id": None},
    ]
    query = Query(entities=["CYP2D6", "codeine", "xyz"], relation_types=["snp"], status="complete",
                  evidence={"normalisation": normalisation, "items": [], "table": []})
    db.session.add(query)
    db.session.commit()

    details = client.get(f"/api/queries/{query.id}").get_json()["entity_details"]
    assert details[0]["location"] == "22q13.2"
    assert details[0]["uniprot_ids"] == ["P10635"]
    assert details[1]["max_phase"] == 4
    assert details[2] is None


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
