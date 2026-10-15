"""CPIC gene–drug pairs, prescribing recommendations and the supporting guideline tables.

The supporting tables (allele functions and frequencies, diplotype → phenotype mappings, example CDS text and
pre-/post-test alerts) are the same data as the cpicpgx/cpic-data database releases, read through the CPIC API.
"""

from datetime import date

from flask import current_app

from ..models import (
    CpicAllele, CpicAlleleFrequency, CpicDiplotype, CpicGeneResult, CpicPair, CpicRecommendation, CpicTestAlert,
)
from ..text import norm
from . import drug_synonym_index, gene_symbol_index, http_session, lookup_drug, replace_tables

PAIR_FIELDS = ("pairid,genesymbol,drugid,guidelineid,cpiclevel,clinpgxlevel,pgxtesting,citations,"
               "usedforrecommendation,drug(name),guideline(name,url)")
RECOMMENDATION_FIELDS = ("id,guidelineid,drug(name),phenotypes,allelestatus,activityscore,lookupkey,"
                         "implications,drugrecommendation,classification,population,comments")
ALLELE_FIELDS = ("id,genesymbol,name,functionalstatus,clinicalfunctionalstatus,clinicalfunctionalsubstrate,"
                 "activityvalue,strength,citations")
DIPLOTYPE_FIELDS = ("genesymbol,diplotype,function1,function2,activityvalue1,activityvalue2,totalactivityscore,"
                    "generesult,lookupkey")
ALERT_FIELDS = "id,population,cdscontext,genes,drugid,lookupkey,alerttext,drug(name)"


def run(echo):
    base = current_app.config["CPIC_API_URL"].rstrip("/")
    session = http_session()
    echo("[cpic] downloading gene–drug pairs and recommendations")
    pairs = _paginate(session, f"{base}/pair", {"removed": "eq.false", "select": PAIR_FIELDS, "order": "pairid"})
    recommendations = _paginate(session, f"{base}/recommendation", {"select": RECOMMENDATION_FIELDS, "order": "id"})

    echo("[cpic] downloading allele functions and frequencies, diplotypes and CDS alerts")
    alleles = _paginate(session, f"{base}/allele", {"select": ALLELE_FIELDS, "order": "id"})
    frequencies = _paginate(session, f"{base}/population_frequency_view", {"order": "genesymbol,name"})
    gene_results = _paginate(session, f"{base}/gene_result", {"order": "id"})
    diplotypes = _paginate(session, f"{base}/diplotype", {"select": DIPLOTYPE_FIELDS,
                                                         "order": "genesymbol,diplotype"}, page_size=20000)
    alerts = _paginate(session, f"{base}/test_alert", {"select": ALERT_FIELDS, "order": "id"})

    tables = [
        (CpicPair, build_pairs(pairs, gene_symbol_index(), drug_synonym_index())),
        (CpicRecommendation, build_recommendations(recommendations)),
        (CpicAllele, build_alleles(alleles)),
        (CpicAlleleFrequency, build_frequencies(frequencies)),
        (CpicGeneResult, build_gene_results(gene_results)),
        (CpicDiplotype, build_diplotypes(diplotypes)),
        (CpicTestAlert, build_test_alerts(alerts)),
    ]
    echo(f"[cpic] writing {sum(len(rows) for _, rows in tables)} rows")
    replace_tables(tables)
    return {"version": f"CPIC API snapshot {date.today().isoformat()}", "url": base,
            "counts": {model.__tablename__: len(rows) for model, rows in tables}}


def _paginate(session, url, params, page_size=1000):
    out, offset = [], 0
    while True:
        resp = session.get(url, params={**params, "limit": page_size, "offset": offset}, timeout=120)
        resp.raise_for_status()
        page = resp.json()
        out += page
        if len(page) < page_size:
            return out
        offset += page_size


def build_pairs(pairs, gene_index, drug_index):
    rows = []
    for p in pairs:
        drug_name = (p.get("drug") or {}).get("name")
        if not drug_name:
            continue
        guideline = p.get("guideline") or {}
        rows.append({
            "pair_id": p["pairid"],
            "gene_symbol": norm(p["genesymbol"]),
            "hgnc_id": gene_index.get(norm(p["genesymbol"])),
            "drug_rxnorm_id": p.get("drugid"),
            "drug_name": drug_name,
            "drug_name_norm": norm(drug_name),
            "chembl_id": lookup_drug(drug_index, drug_name),
            "guideline_id": p.get("guidelineid"),
            "guideline_name": guideline.get("name"),
            "guideline_url": guideline.get("url"),
            "cpic_level": p.get("cpiclevel"),
            "clinpgx_level": p.get("clinpgxlevel"),
            "pgx_testing": p.get("pgxtesting"),
            "used_for_recommendation": p.get("usedforrecommendation"),
            "pmids": [int(c) for c in p.get("citations") or [] if str(c).isdigit()],
        })
    return rows


def build_recommendations(recommendations):
    rows = []
    for r in recommendations:
        drug_name = (r.get("drug") or {}).get("name") or ""
        # Most guidelines key recommendations by phenotype; HLA ones by allele status.
        lookup = next((r[k] for k in ("phenotypes", "allelestatus", "activityscore", "lookupkey") if r.get(k)), {})
        rows.append({
            "id": r["id"],
            "guideline_id": r.get("guidelineid"),
            "drug_name": drug_name,
            "drug_name_norm": norm(drug_name),
            "population": (r.get("population") or "")[:128],
            "lookup": lookup,
            "implications": r.get("implications") or {},
            "recommendation": r.get("drugrecommendation"),
            "classification": r.get("classification"),
            "comments": None if r.get("comments") in (None, "n/a") else r["comments"],
        })
    return rows


def _text(value):
    """CPIC uses "n/a" for empty text fields."""
    return None if value in (None, "", "n/a") else value


def build_alleles(alleles):
    return [{
        "id": a["id"],
        "gene_symbol": norm(a["genesymbol"]),
        "allele_name": a["name"],
        "function_status": _text(a.get("functionalstatus")),
        "clinical_function_status": _text(a.get("clinicalfunctionalstatus")),
        "clinical_function_substrate": _text(a.get("clinicalfunctionalsubstrate")),
        "activity_value": _text(a.get("activityvalue")),
        # The source spells the same levels several ways ("strong", "Definitve").
        "strength": _text(a.get("strength")) and a["strength"].replace("Definitve", "Definitive").capitalize(),
        "pmids": [int(c) for c in a.get("citations") or [] if str(c).isdigit()],
    } for a in alleles]


def build_frequencies(frequencies):
    return [{
        "gene_symbol": norm(f["genesymbol"]),
        "allele_name": f["name"],
        "population_group": f["population_group"],
        "subject_count": f.get("subjectcount"),
        "freq_weighted_avg": f.get("freq_weighted_avg"),
        "freq_avg": f.get("freq_avg"),
        "freq_min": f.get("freq_min"),
        "freq_max": f.get("freq_max"),
    } for f in frequencies if f.get("population_group")]


def build_gene_results(gene_results):
    return [{
        "id": g["id"],
        "gene_symbol": norm(g["genesymbol"]),
        "result": g["result"],
        "activity_score": _text(g.get("activityscore")),
        "ehr_priority": _text(g.get("ehrpriority")),
        "consultation_text": _text(g.get("consultationtext")),
        "frequency": g.get("frequency") or {},
    } for g in gene_results]


def build_diplotypes(diplotypes):
    return [{
        "gene_symbol": norm(d["genesymbol"]),
        "diplotype": d["diplotype"],
        "function1": _text(d.get("function1")),
        "function2": _text(d.get("function2")),
        "activity_value1": _text(d.get("activityvalue1")),
        "activity_value2": _text(d.get("activityvalue2")),
        "total_activity_score": _text(d.get("totalactivityscore")),
        "phenotype": _text(d.get("generesult")),
        "lookup_key": d.get("lookupkey") or {},
    } for d in diplotypes]


def build_test_alerts(alerts):
    rows = []
    for a in alerts:
        drug_name = (a.get("drug") or {}).get("name")
        rows.append({
            "id": a["id"],
            "drug_rxnorm_id": a.get("drugid"),
            "drug_name": drug_name,
            "drug_name_norm": norm(drug_name) or None,
            "cds_context": a.get("cdscontext"),
            "population": (a.get("population") or "")[:128],
            "genes": a.get("genes") or [],
            "lookup_key": a.get("lookupkey") or {},
            "alert_text": [t for t in a.get("alerttext") or [] if _text(t)],
        })
    return rows
