"""The workflow stages: resolution gate, grading, the "interactions found?" decision and the LLM skip."""

from app.extensions import db
from app.models import PromptTemplate, Query
from app.services.workflow.evidence import consolidate_pairs, decide_outcome

from .test_reference import reference_data, stub_live_sources  # noqa: F401 - fixtures


# ---------- Stage 1: entity resolution ----------

def test_unknown_gene_asks_for_confirmation(client, reference_data, fake_sources):
    resp = client.post("/api/explore", json={"gene": "CYP2C91"})
    assert resp.status_code == 422
    data = resp.get_json()
    assert data["code"] == "needs_confirmation"
    [problem] = data["resolution"]
    assert problem["reason"] == "not_found"
    assert problem["suggestions"][0]["name"] == "CYP2C19"
    assert fake_sources["calls"]["collect"] == 0  # no API or LLM calls
    assert Query.query.count() == 0


def test_ambiguous_alias(client, reference_data, fake_sources):
    [problem] = client.post("/api/explore", json={"gene": "SHARED"}).get_json()["resolution"]
    assert problem["reason"] == "ambiguous"
    assert [s["name"] for s in problem["suggestions"]] == ["CYP2C19", "P2RY12"]


def test_drug_name_is_not_a_gene(client, reference_data, fake_sources):
    """Only genes are searched: a drug name gets gene suggestions (or can be searched as typed)."""
    assert client.post("/api/explore", json={"gene": "clopidogrel"}).status_code == 422


def test_search_as_typed_after_confirmation(client, reference_data, fake_sources):
    resp = client.post("/api/explore", json={"gene": "CYP2C91", "confirm_unresolved": True})
    assert resp.status_code == 201
    assert fake_sources["calls"]["collect"] == 1


def test_no_gate_without_reference_data(client, fake_sources):
    assert client.post("/api/explore", json={"gene": "CYP2C91"}).status_code == 201


# ---------- Stage 4: evidence integration ----------

def _item(eid, kind, **fields):
    return {"id": eid, "kind": kind, "gene": "DRD2", "drug": "ARIPIPRAZOLE", "pmids": [], **fields}


def test_grading_and_conflicts():
    items = [
        _item("E1", "interaction", source="DGIdb", interaction_types=["agonist"], categories=["induce"],
              sources=["TTD"], score=1.0),
        _item("E2", "mechanism", source="ChEMBL", action_type="antagonist", categories=["inhibit"],
              mechanism="Dopamine D2 receptor partial agonist", max_phase=4),
        {"id": "E3", "kind": "interaction", "source": "DGIdb", "gene": "DRD2", "drug": "HALOPERIDOL",
         "interaction_types": [], "categories": ["other"], "sources": ["DTC"], "score": 0.1, "pmids": []},
    ]
    strong, weak = consolidate_pairs(items)
    assert strong["grade"] == "moderate" and "approved" in strong["grade_reason"]
    assert strong["categories"] == ["inhibit", "induce"]
    assert strong["conflicts"] and "agonist (DGIdb)" in strong["conflicts"][0]
    assert (weak["grade"], weak["conflicts"]) == ("limited", [])


def test_no_interaction_outcome():
    status = {"dgidb": {"state": "ok", "count": 0}, "pharmgkb": {"state": "error", "error": "timeout"}}
    outcome = decide_outcome([], status)
    assert outcome == {"status": "none", "checked_sources": ["dgidb"], "unavailable_sources": ["pharmgkb"]}


def test_llm_skipped_when_nothing_found(client, fake_sources, monkeypatch):
    def empty(gene, relation_types, resolved=None, entity_type="gene", medications=()):
        return {"items": [], "table": [], "network": {"nodes": [], "edges": []}, "source_status": {},
                "outcome": {"status": "none"}}

    monkeypatch.setattr("app.services.explorer.collect_evidence", empty)
    data = client.post("/api/explore", json={"gene": "ORPHANGENE"}).get_json()
    assert data["status"] == "complete"
    assert data["reports"] == []


# ---------- Stage 5: prompts ----------

def test_prompt_has_gene_report_sections(app):
    from app.services.prompts import format_pair, get_active_template, render_prompt

    items = [{"id": "E1", "kind": "interaction", "source": "DGIdb", "gene": "CYP2D6", "drug": "CODEINE",
              "interaction_types": ["substrate"], "categories": ["metabolize"], "score": 3.0, "sources": ["CPIC"],
              "pmids": []}]
    evidence = {"items": items, "table": consolidate_pairs(items), "outcome": {"status": "found"}}
    query = Query(entities=["CYP2D6"], entity_type="gene", relation_types=[])

    _, user, ids = render_prompt(get_active_template(), query, evidence, 80)
    assert ids == ["E1"]
    assert "Gene: CYP2D6" in user and "## Drugs that interact with CYP2D6" in user
    assert "CYP2D6 – CODEINE" in user

    # Evidence cut from the prompt is not listed under the pair either, so the LLM can't cite it.
    row = {**consolidate_pairs(items)[0], "evidence_ids": ["E1", "E349"]}
    assert format_pair(row, {"E1"}).endswith("evidence: E1")

    legacy = PromptTemplate(system_prompt="Rules.", user_template="Evidence:\n{{ evidence_block }}")
    _, user, _ = render_prompt(legacy, query, evidence, 80)
    assert "take precedence" in user and "## Pharmacogenomic (SNP-specific) findings" in user


def test_seed_upgrades_untouched_default_only(app):
    from app.services.prompts import DEFAULT_TEMPLATE_VERSION, seed_default_template

    db.session.add(PromptTemplate(name="dgi_report", version=2, system_prompt="s", user_template="u",
                                  notes="Default template", is_active=True))
    db.session.commit()
    assert seed_default_template().version == DEFAULT_TEMPLATE_VERSION

    custom = PromptTemplate(name="mine", version=1, system_prompt="s", user_template="u", is_active=True)
    PromptTemplate.query.update({PromptTemplate.is_active: False})
    db.session.add(custom)
    db.session.commit()
    assert seed_default_template().name == "mine"  # an admin's choice is kept
