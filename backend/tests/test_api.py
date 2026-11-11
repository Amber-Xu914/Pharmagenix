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
    resp = client.post("/api/explore", json={"gene": "CYP2C19", "relation_types": ["snp"], "compare": True})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["status"] == "complete"
    assert (data["entities"], data["entity_type"]) == (["CYP2C19"], "gene")
    assert [r["role"] for r in data["reports"]] == ["primary", "comparison"]
    assert all(r["status"] == "success" for r in data["reports"])
    assert data["reports"][1]["citations"] == ["E1", "E2"]
    assert len(data["evidence"]["table"]) == 1  # DGIdb + PharmGKB records for one pair, merged


def test_query_includes_entity_details(app, client):
    from app.extensions import db
    from app.models import Query
    from app.reference.models import Gene

    db.session.add(Gene(hgnc_id="HGNC:2625", symbol="CYP2D6", name="cytochrome P450 family 2 subfamily D member 6",
                        location="22q13.2", ensembl_gene_id="ENSG00000100197", uniprot_ids=["P10635"]))
    queries = [
        Query(entities=["cyp2d6"], relation_types=["snp"], status="complete", evidence={
            "normalisation": [{"input": "cyp2d6", "name": "CYP2D6", "type": "gene", "id": "HGNC:2625"}]}),
        Query(entities=["xyz"], relation_types=["snp"], status="complete", evidence={
            "normalisation": [{"input": "xyz", "name": "XYZ", "type": None, "id": None}]}),
    ]
    db.session.add_all(queries)
    db.session.commit()

    [details] = client.get(f"/api/queries/{queries[0].id}").get_json()["entity_details"]
    assert (details["location"], details["uniprot_ids"]) == ("22q13.2", ["P10635"])
    assert client.get(f"/api/queries/{queries[1].id}").get_json()["entity_details"] == [None]


def test_explore_validation(client, fake_sources):
    assert client.post("/api/explore", json={"gene": ""}).status_code == 400
    assert client.post("/api/explore", json={"gene": ["BRAF"]}).status_code == 400
    assert client.post("/api/explore", json={"gene": "BRAF, EGFR"}).status_code == 400  # one gene at a time
    assert client.post("/api/explore", json={"gene": "BRAF", "relation_types": ["nope"]}).status_code == 400
    assert fake_sources["calls"]["collect"] == 0


def test_hallucinated_citation_is_flagged(client, fake_sources):
    fake_sources["answers"]["openai"] = "Claim [E1] and made-up [E42]."
    report = client.post("/api/explore", json={"gene": "BRAF"}).get_json()["reports"][0]
    assert report["status"] == "flagged"
    assert report["invalid_citations"] == ["E42"]


def test_followup_reuses_evidence(client, fake_sources):
    first = client.post("/api/explore", json={"gene": "CYP2C19"}).get_json()
    resp = client.post(f"/api/queries/{first['id']}/ask", json={"followup_question": "What about *17?"})
    assert resp.status_code == 201
    second = resp.get_json()
    assert second["parent_id"] == first["id"]
    assert second["followup_question"] == "What about *17?"
    assert fake_sources["calls"]["collect"] == 1


def test_feedback(client, fake_sources):
    report_id = client.post("/api/explore", json={"gene": "BRAF"}).get_json()["reports"][0]["id"]
    resp = client.post(f"/api/reports/{report_id}/feedback", json={"rating": 4, "suggestion": "More SNPs"})
    assert resp.status_code == 201
    assert resp.get_json()["report"]["avg_rating"] == 4
    assert client.post(f"/api/reports/{report_id}/feedback", json={"rating": 9}).status_code == 400



def test_explore_a_drug(client, fake_sources, monkeypatch):
    import app.services.explorer as explorer
    from app.models import PromptLog

    patched, seen = explorer.collect_evidence, {}

    def recording(name, relation_types, resolved=None, entity_type="gene"):
        seen["args"] = (name, entity_type)
        return patched(name, relation_types, resolved, entity_type)

    monkeypatch.setattr(explorer, "collect_evidence", recording)
    data = client.post("/api/explore", json={"entity_type": "drug", "drug": "clopidogrel"}).get_json()
    assert seen["args"] == ("clopidogrel", "drug")
    assert (data["entities"], data["entity_type"]) == (["clopidogrel"], "drug")
    prompt = PromptLog.query.order_by(PromptLog.id.desc()).first().user_prompt
    assert prompt.startswith("Drug: clopidogrel") and "## Genes that interact with clopidogrel" in prompt
    # a follow-up keeps the entity type
    follow = client.post(f"/api/queries/{data['id']}/ask", json={"followup_question": "Which genes matter most?"})
    assert follow.get_json()["entity_type"] == "drug"


def test_explore_rejects_unknown_entity_type(client):
    assert client.post("/api/explore", json={"entity_type": "protein", "protein": "x"}).status_code == 400


def test_default_models_and_temperatures(monkeypatch):
    """Claude is the primary model at temperature 0.3; gpt-6-luna (a reasoning model) gets no temperature."""
    import importlib

    import app.config as config

    for var in ("PRIMARY_PROVIDER", "COMPARISON_PROVIDER", "ANTHROPIC_MODEL", "ANTHROPIC_TEMPERATURE",
                "OPENAI_TEMPERATURE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: False)  # ignore the developer's .env
    defaults = importlib.reload(config).Config
    try:
        assert (defaults.PRIMARY_PROVIDER, defaults.COMPARISON_PROVIDER) == ("anthropic", "openai")
        assert (defaults.ANTHROPIC_MODEL, defaults.ANTHROPIC_TEMPERATURE) == ("claude-sonnet-4-6", 0.3)
        assert defaults.OPENAI_TEMPERATURE is None
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_claude_temperature_goes_in_extra_body(monkeypatch):
    """anthropic SDK 1.x has no temperature argument; the API takes it from the request body."""
    from types import SimpleNamespace

    from app.services.llm.anthropic_client import AnthropicClient

    sent = {}

    class FakeMessages:
        def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="OK")], model="m", stop_reason="end_turn",
                                   usage=SimpleNamespace(input_tokens=1, output_tokens=1))

    monkeypatch.setattr("anthropic.Anthropic", lambda **kw: SimpleNamespace(messages=FakeMessages()))
    AnthropicClient(api_key="k", model="claude-sonnet-4-6", temperature=0.3).complete("s", "u")
    assert sent["extra_body"] == {"temperature": 0.3} and "temperature" not in sent
