from datetime import date

import pytest

from app.extensions import db
from app.reference.importers import drug_synonym_index, gene_symbol_index, replace_tables
from app.reference.importers.chembl import build_drugs, build_mechanisms
from app.reference.importers.cpic import build_pairs, build_recommendations
from app.reference.importers.hgnc import parse_hgnc
from app.reference.importers.pbs import build_items
from app.reference.models import (
    CpicPair, CpicRecommendation, Drug, DrugMechanism, DrugSynonym, Gene, GeneAlias, PbsItem, SourceRelease,
)
from app.reference.normalise import resolve_entities
from app.reference.text import strip_salt

HGNC_ROWS = [
    {"hgnc_id": "HGNC:11998", "symbol": "TP53", "name": "tumor protein p53", "status": "Approved",
     "alias_symbol": "p53|LFS1", "prev_symbol": "", "entrez_id": "7157", "ensembl_gene_id": "ENSG00000141510",
     "uniprot_ids": "P04637", "date_modified": "2026-01-01"},
    {"hgnc_id": "HGNC:2621", "symbol": "CYP2C19", "name": "cytochrome P450 2C19", "status": "Approved",
     "alias_symbol": "CPCJ|SHARED", "prev_symbol": "", "entrez_id": "1557", "ensembl_gene_id": "ENSG00000165841",
     "uniprot_ids": "P33261", "date_modified": "2026-02-01"},
    {"hgnc_id": "HGNC:18124", "symbol": "P2RY12", "name": "purinergic receptor P2Y12", "status": "Approved",
     "alias_symbol": "SHARED", "prev_symbol": "", "entrez_id": "64805", "ensembl_gene_id": "ENSG00000169313",
     "uniprot_ids": "Q9H244", "date_modified": "2025-01-01"},
    {"hgnc_id": "HGNC:1", "symbol": "OLD", "name": "withdrawn", "status": "Entry Withdrawn"},
]

CHEMBL_MOLECULES = [
    {"molecule_chembl_id": "CHEMBL1771", "pref_name": "CLOPIDOGREL", "max_phase": "4.0",
     "molecule_type": "Small molecule", "first_approval": 1997,
     "molecule_hierarchy": {"parent_chembl_id": "CHEMBL1771"},
     "molecule_synonyms": [{"molecule_synonym": "Clopidogrel", "syn_type": "INN"}]},
    # Trade names live on salt forms in ChEMBL; they must end up on the parent drug.
    {"molecule_chembl_id": "CHEMBL1083385", "pref_name": "CLOPIDOGREL BISULFATE",
     "molecule_hierarchy": {"parent_chembl_id": "CHEMBL1771"},
     "molecule_synonyms": [{"molecule_synonym": "Plavix", "syn_type": "TRADE_NAME"}]},
]
CHEMBL_TARGETS = {
    "CHEMBL2001": {"target_chembl_id": "CHEMBL2001", "pref_name": "P2Y12 receptor", "target_type": "SINGLE PROTEIN",
                   "organism": "Homo sapiens", "target_components": [
                       {"target_component_synonyms": [{"component_synonym": "P2RY12", "syn_type": "GENE_SYMBOL"}]}]},
    "CHEMBL9": {"target_chembl_id": "CHEMBL9", "pref_name": "Bacterial thing", "organism": "Escherichia coli",
                "target_components": []},
}
CHEMBL_MECHANISMS = [
    {"mec_id": 1, "molecule_chembl_id": "CHEMBL1771", "parent_molecule_chembl_id": "CHEMBL1771",
     "target_chembl_id": "CHEMBL2001", "action_type": "ANTAGONIST", "mechanism_of_action": "P2Y12 antagonist",
     "direct_interaction": 1, "disease_efficacy": 1, "max_phase": 4,
     "mechanism_refs": [{"ref_type": "PubMed", "ref_id": "123", "ref_url": "http://x"}]},
    {"mec_id": 2, "molecule_chembl_id": "CHEMBL1771", "target_chembl_id": "CHEMBL9", "action_type": "INHIBITOR"},
    # Same mechanism filed again for a salt form: merged into the first row.
    {"mec_id": 3, "molecule_chembl_id": "CHEMBL1083385", "parent_chembl_id": "CHEMBL1771",
     "parent_molecule_chembl_id": "CHEMBL1771", "target_chembl_id": "CHEMBL2001", "action_type": "ANTAGONIST",
     "mechanism_refs": [{"ref_type": "PubMed", "ref_id": "456"}]},
]
CPIC_PAIRS = [{"pairid": 10, "genesymbol": "CYP2C19", "drugid": "RxNorm:32968", "guidelineid": 7, "cpiclevel": "A",
               "clinpgxlevel": "1A", "citations": ["21716271"], "usedforrecommendation": True,
               "drug": {"name": "clopidogrel"}, "guideline": {"name": "CYP2C19 and Clopidogrel", "url": "http://g"}}]
CPIC_RECOMMENDATIONS = [
    {"id": 1, "guidelineid": 7, "drug": {"name": "clopidogrel"}, "phenotypes": {"CYP2C19": "Poor Metabolizer"},
     "drugrecommendation": "Avoid clopidogrel", "classification": "Strong", "population": "general",
     "comments": "n/a"},
    {"id": 2, "guidelineid": 7, "drug": {"name": "clopidogrel"}, "phenotypes": {"CYP2C19": "Normal Metabolizer"},
     "drugrecommendation": "Use standard dose", "classification": "Moderate", "population": "general"},
]
PBS_ITEMS = [
    {"pbs_code": "13365K", "drug_name": "Clopidogrel", "brand_name": "Clovix 75", "li_form": "Tablet 75 mg",
     "schedule_form": "clopidogrel 75 mg tablet, 28", "program_code": "GE", "benefit_type_code": "R",
     "manner_of_administration": "ORAL", "schedule_code": 4333},
    {"pbs_code": "13365K", "drug_name": "Clopidogrel", "brand_name": "Plidogrel", "li_form": "Tablet 75 mg",
     "program_code": "GE", "benefit_type_code": "R", "schedule_code": 4333},
]


@pytest.fixture
def reference_data(app):
    """Load the sample payloads through the real importer functions."""
    genes, aliases, _ = parse_hgnc(HGNC_ROWS)
    replace_tables([(Gene, genes), (GeneAlias, aliases)])
    drugs, synonyms = build_drugs(CHEMBL_MOLECULES, {"CHEMBL1771"})
    mechanisms, _ = build_mechanisms(CHEMBL_MECHANISMS, CHEMBL_TARGETS, {"CHEMBL1771": "CLOPIDOGREL"},
                                     gene_symbol_index())
    replace_tables([(Drug, drugs), (DrugSynonym, synonyms), (DrugMechanism, mechanisms)])
    replace_tables([(CpicPair, build_pairs(CPIC_PAIRS, gene_symbol_index(), drug_synonym_index())),
                    (CpicRecommendation, build_recommendations(CPIC_RECOMMENDATIONS))])
    replace_tables([(PbsItem, build_items(PBS_ITEMS, date(2026, 9, 1), drug_synonym_index()))])
    for source in ("hgnc", "chembl", "cpic", "pbs"):
        db.session.add(SourceRelease(source=source, version=f"{source} test", status="ok"))
    db.session.commit()


def test_strip_salt():
    assert strip_salt("Clopidogrel bisulfate") == "CLOPIDOGREL"
    assert strip_salt("metoprolol (succinate)") == "METOPROLOL"
    assert strip_salt("SODIUM") == "SODIUM"  # never strip a name down to nothing
    assert strip_salt("Magnesium sulfate") == "MAGNESIUM SULFATE"  # the salt is the drug


def test_parsers(reference_data):
    assert Gene.query.count() == 3  # withdrawn entry skipped
    assert {a.alias_type for a in GeneAlias.query.filter_by(hgnc_id="HGNC:11998")} == {"symbol", "alias"}
    mechanism = DrugMechanism.query.one()  # non-human target skipped, salt duplicate merged
    assert (mechanism.gene_symbol, mechanism.hgnc_id, mechanism.pmids) == ("P2RY12", "HGNC:18124", [123, 456])
    assert Drug.query.one().pref_name == "CLOPIDOGREL"  # salt forms are not separate drugs
    assert DrugSynonym.query.filter_by(synonym_norm="PLAVIX").one().chembl_id == "CHEMBL1771"
    pair = CpicPair.query.one()
    assert (pair.hgnc_id, pair.chembl_id) == ("HGNC:2621", "CHEMBL1771")
    assert CpicRecommendation.query.filter_by(id=1).one().comments is None
    assert {i.chembl_id for i in PbsItem.query} == {"CHEMBL1771"}


@pytest.mark.parametrize("raw, entity_type, name, matched_by", [
    ("tp53", "auto", "TP53", "HGNC symbol"),
    ("P53", "auto", "TP53", "HGNC alias"),
    ("ENSG00000141510", "gene", "TP53", "Ensembl ID"),
    ("7157", "gene", "TP53", "NCBI Gene ID"),
    ("Plavix", "auto", "CLOPIDOGREL", "ChEMBL synonym (trade name)"),
    ("clopidogrel bisulfate", "drug", "CLOPIDOGREL", "ChEMBL synonym (salt name)"),
    ("clopidogrel mesylate", "drug", "CLOPIDOGREL", "parent drug of salt form"),  # salt not in ChEMBL
    ("Plidogrel", "auto", "CLOPIDOGREL", "PBS brand"),
])
def test_resolve_entities(reference_data, raw, entity_type, name, matched_by):
    [result] = resolve_entities([raw], entity_type)
    assert (result["name"], result["matched_by"]) == (name, matched_by)


def test_resolve_ambiguous_and_unknown(reference_data):
    ambiguous, unknown, wrong_type = resolve_entities(["SHARED", "XYZ123"], "auto") + resolve_entities(["Plavix"], "gene")
    assert ambiguous["type"] is None and "CYP2C19, P2RY12" in ambiguous["note"]
    assert unknown["type"] is None and unknown["note"] == "not found in local reference data"
    assert wrong_type["type"] is None  # drug names are not resolved when the user said "gene"


@pytest.fixture
def stub_live_sources(monkeypatch):
    calls = {}

    def dgidb(self, genes=(), drugs=()):
        calls["dgidb"] = (list(genes), list(drugs))
        return [{"gene": "CYP2C19", "drug": "CLOPIDOGREL", "interaction_types": [], "score": 1.0,
                 "sources": ["PharmGKB"], "pmids": [1], "drug_approved": True},
                {"gene": "CYP2C19", "drug": "CLOPIDOGREL BISULFATE", "interaction_types": [], "score": 2.0,
                 "sources": ["CPIC"], "pmids": [2], "drug_approved": True}]

    monkeypatch.setattr("app.services.biomed.dgidb.DGIdbClient.fetch_interactions", dgidb)
    monkeypatch.setattr("app.services.biomed.pharmgkb.PharmGKBClient.clinical_annotations", lambda self, **kw: [])
    monkeypatch.setattr("app.services.biomed.pubtator.PubTatorClient.search_pairs", lambda self, *a, **kw: [])
    return calls


def test_collect_evidence_uses_reference_data(app, reference_data, stub_live_sources):
    from app.services.biomed import collect_evidence
    from app.services.relations import DEFAULT_RELATION_TYPES

    evidence = collect_evidence(["Plavix"], "auto", DEFAULT_RELATION_TYPES)

    assert stub_live_sources["dgidb"] == ([], ["CLOPIDOGREL"])  # DGIdb queried with the normalised name
    assert evidence["normalisation"][0]["matched_by"] == "ChEMBL synonym (trade name)"
    kinds = [i["kind"] for i in evidence["items"]]
    assert kinds == ["interaction", "mechanism", "guideline"]  # salt form merged into one interaction
    assert (evidence["items"][0]["score"], evidence["items"][0]["sources"]) == (2.0, ["CPIC", "PharmGKB"])
    guideline = evidence["items"][2]
    assert guideline["recommendations"][0]["recommendation"] == "Avoid clopidogrel"  # actionable first
    assert evidence["source_status"]["chembl"] == {"state": "ok", "count": 1, "version": "chembl test"}
    assert len(evidence["australian_availability"]) == 1  # one row per drug, not per salt form
    assert evidence["australian_availability"][0]["pbs_listed"] is True
    assert evidence["australian_availability"][0]["items"][0]["brands"] == ["Clovix 75", "Plidogrel"]
    categories = {e["data"]["category"] for e in evidence["network"]["edges"]}
    assert categories == {"other", "inhibit", "snp"}  # ChEMBL "antagonist" counts as inhibition


def test_collect_evidence_without_reference_data(app, stub_live_sources):
    from app.services.biomed import collect_evidence

    evidence = collect_evidence(["CYP2C19"], "auto", ["snp", "other"])
    assert stub_live_sources["dgidb"] == (["CYP2C19"], ["CYP2C19"])  # unresolved: tried as both
    assert evidence["source_status"]["chembl"]["state"] == "disabled"
    assert evidence["source_status"]["pbs"]["state"] == "disabled"
    assert evidence["australian_availability"] is None  # frontend shows "not imported"
