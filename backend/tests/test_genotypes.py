"""The patient's genotypes (services/genotypes.py): parsing, diplotype -> CPIC phenotype, matching CPIC
recommendations (single- and multi-gene keys, HLA allele status), phenoconversion notes, and the prompt / API."""

import pytest

from app.errors import APIError
from app.extensions import db
from app.reference.models import (
    CpicDiplotype, CpicGeneResult, CpicPair, CpicRecommendation, DrugCypRole,
)
from app.services import genotypes as G
from app.services.prompts import format_evidence_item, format_pair, genotypes_block

CLOPIDOGREL_PM = "Avoid standard dose clopidogrel if possible. Use prasugrel or ticagrelor at standard dose."


@pytest.fixture
def cpic(app):
    for gene, result in [("CYP2C19", "Poor Metabolizer"), ("CYP2C19", "Intermediate Metabolizer"),
                         ("CYP2C19", "Normal Metabolizer"), ("CYP2D6", "Poor Metabolizer"),
                         ("CYP2D6", "Normal Metabolizer")]:
        db.session.add(CpicGeneResult(gene_symbol=gene, result=result))
    for dip, pheno in [("*1/*1", "Normal Metabolizer"), ("*1/*2", "Intermediate Metabolizer"),
                       ("*2/*2", "Poor Metabolizer"), ("*2/*17", "Intermediate Metabolizer")]:
        db.session.add(CpicDiplotype(gene_symbol="CYP2C19", diplotype=dip, phenotype=pheno,
                                     lookup_key={"CYP2C19": pheno}))
    db.session.add_all([
        CpicPair(pair_id=1, gene_symbol="CYP2C19", drug_name="clopidogrel", drug_name_norm="CLOPIDOGREL",
                 guideline_id=10, guideline_name="CYP2C19 and clopidogrel", cpic_level="A",
                 guideline_url="https://cpicpgx.org/guidelines/clopidogrel"),
        CpicPair(pair_id=2, gene_symbol="CYP2C19", drug_name="amitriptyline", drug_name_norm="AMITRIPTYLINE",
                 guideline_id=20, cpic_level="A"),
        CpicPair(pair_id=3, gene_symbol="CYP2D6", drug_name="amitriptyline", drug_name_norm="AMITRIPTYLINE",
                 guideline_id=20, cpic_level="A"),
        CpicPair(pair_id=4, gene_symbol="HLA-B", drug_name="abacavir", drug_name_norm="ABACAVIR",
                 guideline_id=30, cpic_level="A"),
        CpicRecommendation(id=1, guideline_id=10, drug_name="clopidogrel", drug_name_norm="CLOPIDOGREL",
                           population="CVI ACS PCI ", lookup={"CYP2C19": "Poor Metabolizer"},
                           implications={"CYP2C19": "Reduced active metabolite"},
                           recommendation=CLOPIDOGREL_PM, classification="Strong"),
        CpicRecommendation(id=2, guideline_id=10, drug_name="clopidogrel", drug_name_norm="CLOPIDOGREL",
                           population="CVI ACS PCI", lookup={"CYP2C19": "Normal Metabolizer"},
                           recommendation="Use standard dose.", classification="Strong"),
        CpicRecommendation(id=3, guideline_id=20, drug_name="amitriptyline", drug_name_norm="AMITRIPTYLINE",
                           population="general", lookup={"CYP2C19": "Poor Metabolizer", "CYP2D6": "Normal Metabolizer"},
                           recommendation="Avoid amitriptyline use.", classification="Optional"),
        CpicRecommendation(id=4, guideline_id=30, drug_name="abacavir", drug_name_norm="ABACAVIR",
                           population="general", lookup={"HLA-B": "HLA-B*57:01 positive"},
                           recommendation="Abacavir is not recommended.", classification="Strong"),
        DrugCypRole(drug_name="fluoxetine", drug_name_norm="FLUOXETINE", system="CYP", pathway="CYP2C19",
                    gene_symbol="CYP2C19", role="inhibitor", strength="strong"),
    ])
    db.session.commit()


def resolve(text):
    return G.resolve(G.parse_genotypes(text)[0])


def test_parse_genotypes(app):
    parsed = G.parse_genotypes("CYP2C19 *2/*2; CYP2D6 poor metabolizer\nHLA-B*57:01 positive, TPMT*3A/*3A")
    assert parsed == [{"gene": "CYP2C19", "value": "*2/*2"}, {"gene": "CYP2D6", "value": "poor metabolizer"},
                      {"gene": "HLA-B", "value": "*57:01 positive"}, {"gene": "TPMT", "value": "*3A/*3A"}]
    assert G.parse_genotypes(["cyp2c19 *1/*2"]) == [{"gene": "CYP2C19", "value": "*1/*2"}]
    assert G.parse_genotypes("") == []
    with pytest.raises(APIError, match="Could not read"):
        G.parse_genotypes("CYP2C19")
    with pytest.raises(APIError, match="twice"):
        G.parse_genotypes("CYP2C19 *1/*1; CYP2C19 *2/*2")


def test_diplotype_and_phenotype_resolution(cpic):
    r = resolve("CYP2C19 *17/*2")  # either allele order
    assert (r["status"], r["diplotype"], r["phenotype"]) == ("ok", "*2/*17", "Intermediate Metabolizer")
    assert resolve("CYP2C19 PM")["phenotype"] == "Poor Metabolizer"
    assert resolve("CYP2C19 poor metaboliser")["phenotype"] == "Poor Metabolizer"
    assert resolve("HLA-B*57:01 positive")["key"] == "HLA-B*57:01 positive"
    unknown = resolve("CYP2C19 *99/*2")
    assert unknown["status"] == "unknown" and "not a CYP2C19 diplotype" in unknown["note"]
    assert resolve("ABC1 *1/*1")["status"] == "no_data"


def test_recommendations_match_the_phenotype_exactly(cpic):
    resolved = [resolve("CYP2C19 *2/*2")]
    recs, incomplete = G.recommendations(resolved, [{"name": "CLOPIDOGREL"}, {"name": "AMITRIPTYLINE"}])
    clop = [r for r in recs if r["drug"] == "CLOPIDOGREL"]
    assert [r["recommendation"] for r in clop] == [CLOPIDOGREL_PM]  # not the normal-metabolizer row
    assert clop[0]["population"] == "CVI ACS PCI" and clop[0]["short_recommendation"].startswith("Avoid standard dose")
    assert clop[0]["population_label"] == "Cardiovascular: acute coronary syndrome and/or PCI"
    assert G.population_label("general") == "" and G.population_label("new code") == "new code"
    # amitriptyline needs CYP2D6 too: no recommendation, but the missing gene is reported
    assert not any(r["drug"] == "AMITRIPTYLINE" for r in recs)
    assert incomplete == [{"drug": "AMITRIPTYLINE", "needs": ["CYP2D6"]}]


def test_multi_gene_and_hla_recommendations(cpic):
    resolved = [resolve("CYP2C19 PM"), resolve("CYP2D6 normal metabolizer"), resolve("HLA-B*57:01 positive")]
    recs, incomplete = G.recommendations(resolved, [{"name": "amitriptyline"}, {"name": "ABACAVIR"}])
    by_drug = {r["drug"]: r for r in recs}
    assert by_drug["AMITRIPTYLINE"]["phenotype"] == "CYP2C19 Poor Metabolizer; CYP2D6 Normal Metabolizer"
    assert by_drug["ABACAVIR"]["phenotype"] == "HLA-B*57:01 positive"
    assert recs[0]["classification"] == "Strong" and incomplete == []


def test_check_adds_evidence_items_and_phenoconversion(cpic):
    items = [{"id": "E1", "kind": "interaction"}]
    block = G.check(G.parse_genotypes("CYP2C19 *2/*2"), [{"name": "CLOPIDOGREL"}],
                    [{"name": "Fluoxetine", "chembl_id": None}], items, {}, "CPIC test")
    assert block["recommendations"] == ["E2"] and items[1]["kind"] == "genotype" and items[1]["release"] == "CPIC test"
    assert "phenoconversion" in block["entered"][0]["phenoconversion"][0]
    line = format_evidence_item(items[1])
    assert line.startswith("[E2] Patient genotype") and "CYP2C19 *2/*2 → CYP2C19 Poor Metabolizer" in line
    assert "Fluoxetine is a strong CYP2C19 inhibitor" in genotypes_block(block)
    row = {"gene": "CYP2C19", "drug": "CLOPIDOGREL", "grade_label": "Strong", "grade_reason": "CPIC A",
           "categories": ["snp"], "evidence_ids": ["E1"],
           "genotype": [{"evidence_id": "E2", "phenotype": "CYP2C19 Poor Metabolizer", "classification": "Strong",
                         "summary": "Avoid standard dose clopidogrel if possible."}]}
    assert "PATIENT GENOTYPE (CPIC): CYP2C19 Poor Metabolizer" in format_pair(row, {"E1", "E2"})


def test_explore_passes_genotypes(client, fake_sources, monkeypatch):
    seen = {}
    real = __import__("app.services.explorer", fromlist=["collect_evidence"]).collect_evidence

    def recording(*args, genotypes=(), **kwargs):
        seen["genotypes"] = list(genotypes)
        return real(*args, genotypes=genotypes, **kwargs)

    monkeypatch.setattr("app.services.explorer.collect_evidence", recording)
    resp = client.post("/api/explore", json={"gene": "CYP2C19", "genotypes": "CYP2C19 *2/*2; HLA-B*57:01 negative"})
    assert resp.status_code == 201
    assert seen["genotypes"] == [{"gene": "CYP2C19", "value": "*2/*2"}, {"gene": "HLA-B", "value": "*57:01 negative"}]
    bad = client.post("/api/explore", json={"gene": "CYP2C19", "genotypes": "nonsense"})
    assert bad.status_code == 400 and "Could not read" in bad.get_json()["error"]
