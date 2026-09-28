"""Evidence from the local reference tables, in the same shape the aggregator uses for live sources."""

import re

from ..extensions import db
from .models import CpicPair, CpicRecommendation, DrugMechanism, PbsItem, SourceRelease
from .text import norm, strip_salt

CLASSIFICATION_ORDER = {"Strong": 0, "Moderate": 1, "Optional": 2}
CPIC_LEVEL_ORDER = {"A": 0, "A/B": 1, "B": 2, "B/C": 3, "C": 4, "C/D": 5, "D": 6}
# Recommendations that just confirm standard prescribing are less informative than "avoid"/"adjust dose".
STANDARD_CARE_RE = re.compile(r"standard dos|recommended starting dose|per standard|no reason to|label-recommended",
                              re.IGNORECASE)
BENEFIT_TYPES = {"U": "Unrestricted", "R": "Restricted benefit", "A": "Authority required",
                 "S": "Authority required (streamlined)"}


def latest_releases():
    """source -> latest successful SourceRelease, or missing if the source was never imported."""
    rows = db.session.scalars(
        db.select(SourceRelease).where(SourceRelease.status == "ok").order_by(SourceRelease.id)
    ).all()
    return {r.source: r for r in rows}


def chembl_mechanisms(genes, drug_ids, limit=100):
    if not genes and not drug_ids:
        return []
    conditions = []
    if genes:
        conditions.append(DrugMechanism.gene_symbol.in_(genes))
    if drug_ids:
        conditions.append(DrugMechanism.chembl_id.in_(drug_ids))
    rows = db.session.scalars(
        db.select(DrugMechanism).where(db.or_(*conditions))
        .order_by(DrugMechanism.max_phase.desc(), DrugMechanism.mec_id).limit(limit)
    ).all()
    return [{
        "gene": r.gene_symbol,
        "drug": norm(r.drug_name or r.chembl_id),
        "chembl_id": r.chembl_id,
        "hgnc_id": r.hgnc_id,
        "action_type": (r.action_type or "").lower(),
        "mechanism": r.mechanism_of_action,
        "target_name": r.target_name,
        "direct_interaction": r.direct_interaction,
        "max_phase": r.max_phase,
        "pmids": (r.pmids or [])[:10],
        "source_record_id": f"mec_id:{r.mec_id}",
        "url": f"https://www.ebi.ac.uk/chembl/compound_report_card/{r.chembl_id}/",
    } for r in rows]


def cpic_guidelines(genes, drug_names, drug_ids, per_pair=6, limit=30):
    conditions = []
    if genes:
        conditions.append(CpicPair.gene_symbol.in_(genes))
    if drug_names:
        conditions.append(CpicPair.drug_name_norm.in_([norm(d) for d in drug_names]))
    if drug_ids:
        conditions.append(CpicPair.chembl_id.in_(drug_ids))
    if not conditions:
        return []
    pairs = db.session.scalars(db.select(CpicPair).where(db.or_(*conditions))).all()
    pairs = sorted(pairs, key=lambda p: CPIC_LEVEL_ORDER.get(p.cpic_level, 9))[:limit]

    out = []
    for p in pairs:
        recommendations = []
        if p.guideline_id:
            recs = db.session.scalars(db.select(CpicRecommendation).where(
                CpicRecommendation.guideline_id == p.guideline_id,
                CpicRecommendation.drug_name_norm == p.drug_name_norm,
            )).all()
            recs = [r for r in recs if p.gene_symbol in (r.lookup or {})] or recs
            recs.sort(key=lambda r: (r.population != "general",
                                     bool(STANDARD_CARE_RE.search(r.recommendation or "")),
                                     CLASSIFICATION_ORDER.get(r.classification, 9)))
            recommendations = [{
                "lookup": r.lookup,
                "recommendation": r.recommendation,
                "classification": r.classification,
                "population": r.population,
            } for r in recs[:per_pair]]
        out.append({
            "gene": p.gene_symbol,
            "drug": p.drug_name,
            "chembl_id": p.chembl_id,
            "cpic_level": p.cpic_level,
            "clinpgx_level": p.clinpgx_level,
            "guideline_name": p.guideline_name,
            "url": p.guideline_url or "https://cpicpgx.org/guidelines/",
            "pgx_testing": p.pgx_testing,
            "pmids": (p.pmids or [])[:5],
            "recommendations": recommendations,
            "source_record_id": f"pair:{p.pair_id}",
        })
    return out


def pbs_availability(drugs):
    """drugs: [(display name, chembl_id or None)]. Returns listing status for each."""
    out = []
    for name, chembl_id in drugs:
        condition = PbsItem.drug_name_norm == strip_salt(name)
        if chembl_id:
            condition = db.or_(condition, PbsItem.chembl_id == chembl_id)
        rows = db.session.scalars(db.select(PbsItem).where(condition).order_by(PbsItem.pbs_code)).all()
        items = {}
        for r in rows:
            item = items.setdefault(r.pbs_code, {
                "pbs_code": r.pbs_code,
                "form": r.schedule_form or r.form,
                "program_code": r.program_code,
                "benefit_type": BENEFIT_TYPES.get(r.benefit_type_code, r.benefit_type_code),
                "brands": [],
                "url": f"https://www.pbs.gov.au/medicine/item/{r.pbs_code}",
            })
            if r.brand_name and r.brand_name not in item["brands"]:
                item["brands"].append(r.brand_name)
        out.append({
            "drug": name,
            "pbs_listed": bool(items),
            "item_count": len(items),
            "items": list(items.values())[:12],
            "effective_date": rows[0].effective_date.isoformat() if rows else None,
        })
    return out
