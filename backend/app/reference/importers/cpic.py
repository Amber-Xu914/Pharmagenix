"""CPIC gene–drug pairs and genotype/phenotype-based prescribing recommendations."""

from datetime import date

from flask import current_app

from ..models import CpicPair, CpicRecommendation
from ..text import norm
from . import drug_synonym_index, gene_symbol_index, http_session, lookup_drug, replace_tables

PAIR_FIELDS = ("pairid,genesymbol,drugid,guidelineid,cpiclevel,clinpgxlevel,pgxtesting,citations,"
               "usedforrecommendation,drug(name),guideline(name,url)")
RECOMMENDATION_FIELDS = ("id,guidelineid,drug(name),phenotypes,allelestatus,activityscore,lookupkey,"
                         "implications,drugrecommendation,classification,population,comments")


def run(echo):
    base = current_app.config["CPIC_API_URL"].rstrip("/")
    session = http_session()
    echo("[cpic] downloading gene–drug pairs and recommendations")
    pairs = _paginate(session, f"{base}/pair", {"removed": "eq.false", "select": PAIR_FIELDS, "order": "pairid"})
    recommendations = _paginate(session, f"{base}/recommendation", {"select": RECOMMENDATION_FIELDS, "order": "id"})

    pair_rows = build_pairs(pairs, gene_symbol_index(), drug_synonym_index())
    rec_rows = build_recommendations(recommendations)
    replace_tables([(CpicPair, pair_rows), (CpicRecommendation, rec_rows)])
    return {"version": f"CPIC API snapshot {date.today().isoformat()}", "url": base,
            "counts": {"cpic_pairs": len(pair_rows), "cpic_recommendations": len(rec_rows)}}


def _paginate(session, url, params, page_size=1000):
    out, offset = [], 0
    while True:
        page = session.get(url, params={**params, "limit": page_size, "offset": offset}, timeout=120).json()
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
