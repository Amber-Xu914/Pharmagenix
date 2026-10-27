from datetime import date

import pytest

from app.extensions import db
from app.reference.importers import drug_synonym_index, gene_symbol_index, replace_tables
from app.reference.importers.chembl import build_drugs, build_mechanisms
from app.reference.importers.cpic import build_pairs, build_recommendations
from app.reference.importers.hgnc import parse_hgnc
from app.reference.importers.pbs import build_items, build_restrictions
from app.reference.models import (
    ClinpgxGuideline, ClinpgxGuidelinePair, CpicAllele, CpicAlleleFrequency, CpicDiplotype, CpicGeneResult,
    CpicPair, CpicRecommendation, CpicTestAlert, Drug, DrugMechanism, DrugSynonym, Gene, GeneAlias, PbsItem, PbsItemRestriction, PbsRestriction,
    SourceRelease,
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
PBS_RESTRICTIONS = [
    {"res_code": "1_R", "treatment_phase": None, "authority_method": None, "schedule_code": 4333,
     "li_html_text": "<h1>Listing</h1><p>Acute coronary syndrome</p>"},
    {"res_code": "2_R", "treatment_phase": "Initial treatment", "authority_method": "STREAMLINED", "schedule_code": 4333,
     "li_html_text": "<p>Stent</p>"},
]
PBS_LINKS = [  # 2_R twice (duplicate link), and one link to an unknown restriction
    {"pbs_code": "13365K", "res_code": "1_R", "benefit_type_code": "R"},
    {"pbs_code": "13365K", "res_code": "2_R", "benefit_type_code": "R"},
    {"pbs_code": "13365K", "res_code": "2_R", "benefit_type_code": "R"},
    {"pbs_code": "13365K", "res_code": "9_R", "benefit_type_code": "R"},
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
    restrictions, links = build_restrictions(PBS_RESTRICTIONS, PBS_LINKS)
    replace_tables([(PbsItem, build_items(PBS_ITEMS, date(2026, 9, 1), drug_synonym_index())),
                    (PbsRestriction, restrictions), (PbsItemRestriction, links)])
    for source in ("hgnc", "chembl", "cpic", "pbs"):
        db.session.add(SourceRelease(source=source, version=f"{source} test", status="ok"))
    db.session.commit()


def test_strip_salt():
    assert strip_salt("Clopidogrel bisulfate") == "CLOPIDOGREL"
    assert strip_salt("CODEINE ANHYDROUS") == strip_salt("Codeine polistirex") == "CODEINE"  # DGIdb lists these apart
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
    monkeypatch.setattr("app.services.biomed.uniprot.UniProtClient.protein", lambda self, **kw: None)
    return calls


def test_collect_evidence_uses_reference_data(app, reference_data, stub_live_sources):
    from app.services.biomed import collect_evidence
    from app.services.relations import DEFAULT_RELATION_TYPES

    evidence = collect_evidence("CPCJ", DEFAULT_RELATION_TYPES)

    assert stub_live_sources["dgidb"] == (["CYP2C19"], [])  # DGIdb queried with the normalised symbol
    assert evidence["normalisation"][0]["matched_by"] == "HGNC alias"
    kinds = [i["kind"] for i in evidence["items"]]
    assert kinds == ["interaction", "guideline", "profile"]  # salt form merged into one interaction
    assert (evidence["items"][0]["score"], evidence["items"][0]["sources"]) == (2.0, ["CPIC", "PharmGKB"])
    guideline = evidence["items"][1]
    assert guideline["recommendations"][0]["recommendation"] == "Avoid clopidogrel"  # actionable first
    assert evidence["source_status"]["chembl"] == {"state": "ok", "count": 0, "version": "chembl test"}
    assert evidence["outcome"]["status"] == "found"
    assert len(evidence["australian_availability"]) == 1  # one row per drug, not per salt form
    assert evidence["australian_availability"][0]["pbs_listed"] is True
    assert evidence["australian_availability"][0]["items"][0]["brands"] == ["Clovix 75", "Plidogrel"]
    restrictions = evidence["australian_availability"][0]["restrictions"]
    assert [(r["res_code"], r["pbs_codes"]) for r in restrictions] == [("1_R", ["13365K"]), ("2_R", ["13365K"])]
    assert restrictions[1]["treatment_phase"] == "Initial treatment"
    assert restrictions[0]["html"] == "<h1>Listing</h1><p>Acute coronary syndrome</p>"  # as published
    categories = {e["data"]["category"] for e in evidence["network"]["edges"]}
    assert categories == {"other", "snp"}
    assert [s["stage"] for s in evidence["trace"]] == ["resolution", "profiles", "retrieval", "evidence"]


def test_collect_evidence_mechanisms(app, reference_data, stub_live_sources):
    from app.services.biomed import collect_evidence

    evidence = collect_evidence("P2RY12", [])
    mechanism = next(i for i in evidence["items"] if i["kind"] == "mechanism")
    assert (mechanism["drug"], mechanism["categories"]) == ("CLOPIDOGREL", ["inhibit"])  # antagonist → inhibition


def test_collect_evidence_for_a_drug(app, reference_data, stub_live_sources):
    from app.services.biomed import collect_evidence

    evidence = collect_evidence("Plavix", [], entity_type="drug")  # a trade name

    entry = evidence["normalisation"][0]
    assert (entry["type"], entry["name"], entry["id"]) == ("drug", "CLOPIDOGREL", "CHEMBL1771")
    assert stub_live_sources["dgidb"] == ([], ["CLOPIDOGREL"])  # DGIdb queried by drug, not by gene
    by_kind = {i["kind"]: i for i in evidence["items"]}
    assert by_kind["mechanism"]["gene"] == "P2RY12"  # ChEMBL mechanisms of the drug
    assert by_kind["guideline"]["gene"] == "CYP2C19"  # CPIC guidelines that name the drug
    profile = by_kind["profile"]
    assert (profile["entity_type"], profile["full_name"], profile["first_approval"]) == ("drug", "CLOPIDOGREL", 1997)
    assert profile["trade_names"] == ["Plavix"] and profile["mechanisms"] == ["P2Y12 antagonist"]
    assert evidence["australian_availability"][0]["drug"] == "CLOPIDOGREL"  # the searched drug first
    assert evidence["trace"][1]["detail"] == "drug profile · ChEMBL"
    assert {n["data"]["label"] for n in evidence["network"]["nodes"] if n["data"]["is_query"]} == {"CLOPIDOGREL"}


def test_unknown_drug_gets_drug_suggestions(app, reference_data, client):
    resp = client.post("/api/explore", json={"entity_type": "drug", "drug": "clopidogrol"})
    assert resp.status_code == 422
    problem = resp.get_json()["resolution"][0]
    assert problem["suggestions"][0]["name"] == "CLOPIDOGREL" and problem["suggestions"][0]["type"] == "drug"


def test_collect_evidence_without_reference_data(app, stub_live_sources):
    from app.services.biomed import collect_evidence

    evidence = collect_evidence("CYP2C19", ["snp", "other"])
    assert stub_live_sources["dgidb"] == (["CYP2C19"], [])  # unresolved: searched as typed
    assert evidence["source_status"]["chembl"]["state"] == "disabled"
    assert evidence["source_status"]["pbs"]["state"] == "disabled"
    assert evidence["australian_availability"] is None  # frontend shows "not imported"


# ---------- CPIC supporting tables, ClinPGx, DGIdb, FDA ----------

FDA_PAGE = """<html><body><table><thead><tr><th>Drug</th><th>Therapeutic Area*</th><th>Biomarker &dagger;</th>
<th>Labeling Sections</th></tr></thead><tbody>
<tr><td>Clopidogrel</td><td>Cardiology</td><td>CYP2C19</td><td>Boxed Warning, Dosage and Administration</td></tr>
<tr><td>Abemaciclib (2)</td><td>Oncology</td><td>ERBB2 (HER2)</td><td>Indications and Usage</td></tr>
<tr><td>Imatinib (1)</td><td>Oncology</td><td>BCR-ABL1 (Philadelphia chromosome)</td><td>Indications and Usage</td></tr>
<tr><td>Lenalidomide</td><td>Hematology</td><td>Chromosome 5q</td><td>Indications and Usage</td></tr>
</tbody></table><p>Content current as of:</p><p><time>08/12/2026</time></p></body></html>"""


def test_fda_biomarker_table(reference_data):
    from app.reference.importers.fda import biomarker_genes, build_biomarkers, parse_table

    rows, current_as_of = parse_table(FDA_PAGE)
    assert current_as_of == "2026-08-12"
    assert rows[1] == ["Abemaciclib (2)", "Oncology", "ERBB2 (HER2)", "Indications and Usage"]
    genes = {"ERBB2": "HGNC:3430", "BCR": "HGNC:1014", "ABL1": "HGNC:76", "HLA-B": "HGNC:4932", "PGR": "HGNC:8910"}
    assert biomarker_genes("BCR-ABL1 (Philadelphia chromosome)", genes) == ["BCR", "ABL1"]
    assert biomarker_genes("HLA-B", genes) == ["HLA-B"]  # a hyphenated symbol is not split
    assert biomarker_genes("ESR, PGR (Hormone Receptor)", genes) == ["PGR"]
    assert biomarker_genes("Chromosome 5q", genes) == []

    records = build_biomarkers(rows, {**gene_symbol_index(), **genes}, drug_synonym_index())
    assert [(r["drug_name"], r["gene_symbol"]) for r in records] == [
        ("Clopidogrel", "CYP2C19"), ("Abemaciclib", "ERBB2"), ("Imatinib", "BCR"), ("Imatinib", "ABL1"),
        ("Lenalidomide", None)]
    assert (records[0]["chembl_id"], records[0]["hgnc_id"]) == ("CHEMBL1771", "HGNC:2621")
    assert records[0]["labeling_sections"] == ["Boxed Warning", "Dosage and Administration"]

    with pytest.raises(ValueError):
        parse_table("<table><tr><th>Something else</th></tr></table>")


def test_dgidb_categories(reference_data):
    from app.reference.importers.dgidb import _read_tsv, build_categories

    gene_claims = _read_tsv("gene_claim_name\tnomenclature\tconcept_id\tgene_name\tsource_db_name\n"
                            "P2Y12R\tsymbol\thgnc:18124\tP2RY12\tBaderLab\n")
    categories = _read_tsv("name\tname-2\tsource_db_name\n"
                           "P2Y12R\tG PROTEIN COUPLED RECEPTOR\tBaderLab\n"
                           "P2Y12R\tG PROTEIN COUPLED RECEPTOR\tBaderLab\n"
                           "UNKNOWN\tKINASE\tBaderLab\n")
    assert build_categories(categories, gene_claims, gene_symbol_index()) == [
        {"gene_symbol": "P2RY12", "hgnc_id": "HGNC:18124", "gene_claim_name": "P2Y12R",
         "category": "G PROTEIN COUPLED RECEPTOR", "source_db": "BaderLab"}]


def test_clinpgx_builders(reference_data):
    from app.reference.importers.clinpgx import (
        build_drug_labels, build_guidelines, build_relationships,
    )

    genes, drugs = gene_symbol_index(), drug_synonym_index()
    relationships = build_relationships([
        {"Entity1_id": "PA124", "Entity1_name": "CYP2C19", "Entity1_type": "Gene", "Entity2_id": "PA449053",
         "Entity2_name": "clopidogrel", "Entity2_type": "Chemical", "Evidence": "ClinicalAnnotation,LabelAnnotation",
         "Association": "associated", "PK": "PK", "PD": "", "PMIDs": "1;2"},
        {"Entity1_id": "PA449053", "Entity1_name": "clopidogrel", "Entity1_type": "Chemical", "Entity2_id": "PA124",
         "Entity2_name": "CYP2C19", "Entity2_type": "Gene"},  # reverse direction: dropped
    ], genes, drugs)
    assert len(relationships) == 1
    assert (relationships[0]["evidence"], relationships[0]["pk"], relationships[0]["pmids"]) == (
        ["ClinicalAnnotation", "LabelAnnotation"], True, [1, 2])

    labels = build_drug_labels([{
        "PharmGKB ID": "PA166104789", "Name": "Annotation of FDA Label for clopidogrel and CYP2C19", "Source": "FDA",
        "Testing Level": "Actionable PGx", "Has Dosing Info": "", "Has Alternate Drug": "Alternate Drug",
        "Chemicals": "clopidogrel", "Genes": "CYP2C19", "Latest History Date (YYYY-MM-DD)": "2026-01-02",
    }], genes, drugs)
    assert (labels[0]["has_alternate_drug"], labels[0]["has_dosing_info"], labels[0]["hgnc_id"]) == (
        True, False, "HGNC:2621")

    guidelines, pairs = build_guidelines([{"guideline": {
        "id": "PA166104948", "name": "Annotation of DPWG Guideline for clopidogrel and CYP2C19", "source": "DPWG",
        "recommendation": True, "summaryMarkdown": {"html": "<p>Avoid clopidogrel in <b>poor</b> metabolizers.</p>"},
        "literature": [{"crossReferences": [{"resource": "PubMed", "resourceId": "35000000"}]}],
        "relatedGenes": [{"symbol": "CYP2C19"}], "relatedChemicals": [{"name": "clopidogrel"}],
    }}], genes, drugs)
    assert guidelines[0]["summary"] == "Avoid clopidogrel in poor metabolizers."
    assert guidelines[0]["pmids"] == [35000000]
    assert pairs == [{"guideline_id": "PA166104948", "gene_symbol": "CYP2C19", "hgnc_id": "HGNC:2621",
                      "drug_name": "clopidogrel", "drug_name_norm": "CLOPIDOGREL", "chembl_id": "CHEMBL1771"}]
    replace_tables([(ClinpgxGuideline, guidelines), (ClinpgxGuidelinePair, pairs)])
    assert ClinpgxGuidelinePair.query.one().guideline_id == "PA166104948"


def test_cpic_supporting_tables(reference_data):
    from app.reference.importers.cpic import (
        build_alleles, build_diplotypes, build_frequencies, build_gene_results, build_test_alerts,
    )

    alleles = build_alleles([{"id": 1, "genesymbol": "CYP2C19", "name": "*2", "functionalstatus": "No function",
                              "activityvalue": "n/a", "strength": "Definitve", "citations": ["123", "x"]}])
    assert (alleles[0]["activity_value"], alleles[0]["strength"], alleles[0]["pmids"]) == (None, "Definitive", [123])
    diplotypes = build_diplotypes([{"genesymbol": "CYP2C19", "diplotype": "*2/*2", "function1": "No function",
                                    "function2": "No function", "generesult": "Poor Metabolizer",
                                    "lookupkey": {"CYP2C19": "Poor Metabolizer"}}])
    gene_results = build_gene_results([{"id": 5, "genesymbol": "CYP2C19", "result": "Poor Metabolizer",
                                        "activityscore": "n/a", "consultationtext": "Two no function alleles.",
                                        "frequency": {"European": 0.024}}])
    frequencies = build_frequencies([{"genesymbol": "CYP2C19", "name": "*2", "population_group": "European",
                                      "subjectcount": 1000, "freq_weighted_avg": 0.15},
                                     {"genesymbol": "CYP2C19", "name": "*2", "population_group": None}])
    alerts = build_test_alerts([{"id": 9, "population": "general", "cdscontext": "Post-test", "genes": ["CYP2C19"],
                                 "drugid": "RxNorm:32968", "lookupkey": {"CYP2C19": "Poor Metabolizer"},
                                 "alerttext": ["Avoid clopidogrel."], "drug": {"name": "clopidogrel"}}])
    replace_tables([(CpicAllele, alleles), (CpicAlleleFrequency, frequencies), (CpicGeneResult, gene_results),
                    (CpicDiplotype, diplotypes), (CpicTestAlert, alerts)])
    db.session.commit()
    assert CpicDiplotype.query.one().phenotype == "Poor Metabolizer"
    assert CpicAlleleFrequency.query.count() == 1  # rows without a population group are dropped
    assert CpicTestAlert.query.one().drug_name_norm == "CLOPIDOGREL"
