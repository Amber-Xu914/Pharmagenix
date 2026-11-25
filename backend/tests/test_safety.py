"""Drug safety checks (services/safety.py): the FDA CYP table import, the four drug–drug interaction rules, ChEMBL
warnings, and how they reach the VCF ranking. No network: labels come from the fake_labels fixture (conftest.py)."""

from app.extensions import db
from app.reference.importers import replace_tables
from app.reference.importers.chembl import build_warnings
from app.reference.importers.fda_ddi import build_roles, parse_page
from app.reference.models import CpicPair, Drug, DrugCypRole, DrugSynonym, DrugWarning
from app.reference.text import norm
from app.services.interactions import drug_interaction_rows
from app.services.safety import check_interactions, parse_medications, resolve_drug, warning_summary
from app.services.workflow.evidence import grade

from .conftest import log_in

FDA_PAGE = """<main><table><tr><th>Drug or Other Substance</th><th>CYP Strg INH</th><th>CYP Mod INH</th>
<th>CYP WK INH</th><th>CYP Strg IND</th><th>CYP Mod IND</th><th>CYP WK IND</th><th>CYP SENS SUB</th>
<th>CYP Mod SENS SUB</th><th>TRNSP INH</th><th>TRNSP SUB</th></tr>
<tr><td>carbamazepine</td><td></td><td></td><td></td><td>2B6; 3A strong inducer</td><td></td><td>2C9 weak inducer</td>
<td></td><td></td><td></td><td></td></tr>
<tr><td>fluoxetine</td><td>2C19; 2D6 strong inhibitor</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td>
<td></td><td></td></tr>
<tr><td>itraconazole</td><td>3A strong inhibitor</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td>
<td>P-gp inhibitor</td><td></td></tr>
<tr><td>efavirenz</td><td></td><td></td><td></td><td></td><td>3A moderate inducer</td><td></td><td></td><td></td><td></td>
<td></td></tr>
<tr><td>midazolam</td><td></td><td></td><td></td><td></td><td></td><td></td><td>3A sensitive substrate</td><td></td><td></td>
<td></td></tr>
<tr><td>ritonavir 14</td><td>3A strong inhibitor</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td>
<td></td></tr>
</table><h3>Footnotes</h3><p>14 Ritonavir is approved for use in combination with other drugs.</p>
<p>Content current as of:</p><p>05/29/2026</p></main>"""

DRUGS = {"CHEMBL1": "OSIMERTINIB", "CHEMBL2": "CARBAMAZEPINE", "CHEMBL3": "CLOPIDOGREL", "CHEMBL4": "FLUOXETINE",
         "CHEMBL5": "OMEPRAZOLE", "CHEMBL6": "EFAVIRENZ", "CHEMBL7": "MIDAZOLAM", "CHEMBL8": "ITRACONAZOLE",
         "CHEMBL9": "ERLOTINIB"}


def load_reference():
    drugs = [{"chembl_id": i, "pref_name": n, "molecule_type": "Small molecule", "max_phase": 4.0,
              "first_approval": 2000} for i, n in DRUGS.items()]
    synonyms = [{"chembl_id": i, "synonym": n, "synonym_norm": norm(n), "syn_type": "PREF_NAME"} for i, n in DRUGS.items()]
    rows, notes, _ = parse_page(FDA_PAGE)
    roles = build_roles(rows, notes, {"CYP3A4": "HGNC:2637", "CYP2C19": "HGNC:2621"},
                        {norm(n): i for i, n in DRUGS.items()})
    replace_tables([(Drug, drugs), (DrugSynonym, synonyms), (DrugCypRole, roles)])
    db.session.add(CpicPair(pair_id=1, gene_symbol="CYP2C19", drug_name="clopidogrel", drug_name_norm="CLOPIDOGREL",
                            chembl_id="CHEMBL3", cpic_level="A"))
    db.session.commit()


def alerts_for(drug, med):
    return check_interactions([resolve_drug(drug)], [resolve_drug(med)], [], {})


def test_fda_table_import():
    rows, notes, current = parse_page(FDA_PAGE)
    assert current == "05/29/2026" and notes == {14: "Ritonavir is approved for use in combination with other drugs."}
    roles = build_roles(rows, notes, {"CYP3A4": "HGNC:2637"}, {})
    carbamazepine = {(r["pathway"], r["role"], r["strength"]) for r in roles if r["drug_name"] == "carbamazepine"}
    assert carbamazepine == {("CYP2B6", "inducer", "strong"), ("CYP3A", "inducer", "strong"), ("CYP2C9", "inducer", "weak")}
    assert {r["gene_symbol"] for r in roles if r["pathway"] == "CYP3A"} == {"CYP3A4"}  # FDA's CYP3A -> CYP3A4
    assert [r["gene_symbol"] for r in roles if r["pathway"] == "P-gp"] == ["ABCB1"]
    ritonavir = [r for r in roles if r["drug_name"] == "ritonavir"]  # footnote number removed, its text kept
    assert ritonavir and ritonavir[0]["notes"].startswith("[14] Ritonavir is approved")


def test_label_class_rule_and_severity(app, fake_labels):
    load_reference()
    fake_labels["osimertinib"] = ("7 DRUG INTERACTIONS Strong CYP3A Inducers: Avoid concomitant use. No dose "
                                  "adjustments are required when used with moderate and/or weak CYP3A inducers.")
    [alert] = alerts_for("osimertinib", "carbamazepine")
    assert (alert["drug"], alert["other"], alert["severity"], alert["rule"]) == \
        ("Osimertinib", "Carbamazepine", "major", "label class")
    assert alert["mechanism"] == "strong CYP3A inducer" and "Avoid concomitant use" in alert["label_text"]
    assert alerts_for("osimertinib", "efavirenz") == []  # the label says no adjustment for moderate inducers

    # "Avoid" two sentences later, about the same pathway, still makes it major
    fake_labels["erlotinib"] = ("Co-administration with a strong CYP3A4 inhibitor increased erlotinib exposure. Erlotinib "
                                "is metabolised by CYP3A4. Avoid co-administering erlotinib with strong CYP3A4 inhibitors.")
    [alert] = alerts_for("erlotinib", "itraconazole")
    assert alert["severity"] == "major" and alert["mechanism"] == "strong CYP3A inhibitor"


def test_label_names_drug_counts_once_per_pair(app, fake_labels):
    load_reference()
    fake_labels["clopidogrel"] = "Omeprazole or Esomeprazole: Avoid concomitant use of clopidogrel with omeprazole."
    fake_labels["omeprazole"] = "CYP2C19 substrates (e.g., clopidogrel): reduced active metabolite of clopidogrel."
    alerts = alerts_for("clopidogrel", "omeprazole")
    assert [(a["rule"], a["severity"]) for a in alerts] == [("label names drug", "major")]


def test_fda_table_rule_without_labels(app):
    load_reference()
    [alert] = alerts_for("midazolam", "itraconazole")
    assert (alert["rule"], alert["severity"]) == ("FDA table", "major")  # strong inhibitor + sensitive substrate
    assert "may raise the exposure of Midazolam" in alert["summary"]


def test_phenoconversion(app):
    load_reference()
    alerts = alerts_for("clopidogrel", "fluoxetine")
    pheno = [a for a in alerts if a["rule"] == "phenoconversion"]
    assert len(pheno) == 1 and pheno[0]["severity"] == "major"
    assert pheno[0]["cpic"] == {"gene": "CYP2C19", "level": "A", "phenotype": "poor"}
    assert "CPIC CYP2C19–Clopidogrel guideline (level A)" in pheno[0]["summary"]


def test_no_medications_no_checks(app):
    status = {}
    assert check_interactions([resolve_drug("osimertinib")], [], [], status) == []
    assert status["openfda"]["state"] == "skipped"
    assert parse_medications("omeprazole, fluoxetine;omeprazole\n warfarin") == ["omeprazole", "fluoxetine", "warfarin"]


def test_warnings_import_and_summary(app):
    load_reference()
    records = build_warnings([
        {"warning_id": 1, "parent_molecule_chembl_id": "CHEMBL1", "warning_type": "Withdrawn",
         "warning_class": "cardiotoxicity", "warning_country": "United States", "warning_year": 2005},
        {"warning_id": 2, "molecule_chembl_id": "CHEMBL2", "warning_type": "Black Box Warning",
         "warning_class": "dermatological toxicity"},
        {"warning_id": 3, "molecule_chembl_id": "CHEMBL999", "warning_type": "Withdrawn"},  # not an imported drug
    ], set(DRUGS))
    assert [r["chembl_id"] for r in records] == ["CHEMBL1", "CHEMBL2"]
    replace_tables([(DrugWarning, records)])
    db.session.commit()
    from app.reference.lookup import drug_warnings
    found = drug_warnings(["CHEMBL1", "CHEMBL2"])
    assert warning_summary(found["CHEMBL1"]) == ["Withdrawn (United States, 2005): cardiotoxicity"]
    assert warning_summary(found["CHEMBL2"]) == ["Boxed warning: dermatological toxicity"]


def test_fda_role_grade_and_relational_rows():
    row = {"cpic_level": None, "pgkb_level": None, "databases": [], "pmids": [], "sources": ["FDA DDI table"],
           "max_phase": None, "level": None, "fda_role": {"label": "strong CYP2D6 inhibitor", "strength": "strong"}}
    assert grade(row) == ("moderate", "FDA clinical drug-interaction classification: strong CYP2D6 inhibitor")
    row["fda_role"] = {"label": "weak CYP2D6 inhibitor", "strength": "weak"}
    assert grade(row)[0] == "supportive"
    evidence = {"safety": {"interactions": [{"evidence_id": "E9", "drug": "Osimertinib", "chembl_id": "CHEMBL1",
                                             "other": "Carbamazepine", "other_chembl_id": "CHEMBL2",
                                             "context": "current medication", "severity": "major",
                                             "mechanism": "strong CYP3A inducer", "rule": "label class"}]}}
    assert drug_interaction_rows(evidence)[0]["other_drug_name"] == "Carbamazepine"


def test_explore_passes_current_medications(client, fake_sources, monkeypatch):
    seen = {}
    real = __import__("app.services.explorer", fromlist=["collect_evidence"]).collect_evidence

    def recording(*args, medications=(), **kwargs):
        seen["medications"] = list(medications)
        return real(*args, medications=medications, **kwargs)

    monkeypatch.setattr("app.services.explorer.collect_evidence", recording)
    body = {"gene": "CYP2C19", "medications": "omeprazole, fluoxetine"}
    assert client.post("/api/explore", json=body).status_code == 401  # patient data needs an account
    log_in(client)
    resp = client.post("/api/explore", json=body)
    assert resp.status_code == 201 and seen["medications"] == ["omeprazole", "fluoxetine"]
