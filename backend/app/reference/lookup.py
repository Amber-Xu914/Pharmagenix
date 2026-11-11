"""Evidence from the local reference tables, in the same shape the aggregator uses for live sources."""

import re

from ..extensions import db
from .models import (
    CpicPair, CpicRecommendation, Drug, DrugCypRole, DrugMechanism, DrugSynonym, DrugWarning, Gene, PbsItem,
    PbsItemRestriction, PbsRestriction, SourceRelease,
)
from .text import norm, strip_salt

CLASSIFICATION_ORDER = {"Strong": 0, "Moderate": 1, "Optional": 2}
CPIC_LEVEL_ORDER = {"A": 0, "A/B": 1, "B": 2, "B/C": 3, "C": 4, "C/D": 5, "D": 6}
# Recommendations that just confirm standard prescribing are less informative than "avoid"/"adjust dose".
STANDARD_CARE_RE = re.compile(r"standard dos|recommended starting dose|per standard|no reason to|label-recommended",
                              re.IGNORECASE)
BENEFIT_TYPES = {"U": "Unrestricted", "R": "Restricted benefit", "A": "Authority required",
                 "S": "Authority required (streamlined)"}


def entity_details(normalisation):
    """Descriptive fields for the results page header, one dict per normalised entity (None if unknown).

    Looked up when a query is displayed rather than stored with its evidence, so older queries get them too.
    """
    details = []
    for entry in normalisation or []:
        record = None
        if entry.get("type") == "gene" and entry.get("id"):
            gene = db.session.get(Gene, entry["id"])
            if gene:
                record = {
                    "full_name": gene.name,
                    "location": gene.location,
                    "locus_type": gene.locus_type,
                    "hgnc_id": gene.hgnc_id,
                    "ensembl_gene_id": gene.ensembl_gene_id,
                    "entrez_id": gene.entrez_id,
                    "uniprot_ids": gene.uniprot_ids or [],
                }
        elif entry.get("type") == "drug" and entry.get("id"):
            drug = db.session.get(Drug, entry["id"])
            if drug:
                record = {
                    "full_name": drug.pref_name,
                    "chembl_id": drug.chembl_id,
                    "molecule_type": drug.molecule_type,
                    "max_phase": drug.max_phase,
                    "first_approval": drug.first_approval,
                }
        details.append(record)
    return details


def latest_releases():
    """source -> latest successful SourceRelease, or missing if the source was never imported."""
    rows = db.session.scalars(
        db.select(SourceRelease).where(SourceRelease.status == "ok").order_by(SourceRelease.id)
    ).all()
    return {r.source: r for r in rows}


def chembl_mechanisms(genes, drug_ids, limit=100, pairs=()):
    """pairs: (gene symbol, ChEMBL ID) the user asked about; their rows are always kept, whatever the limit."""
    if not genes and not drug_ids:
        return []
    conditions = []
    if genes:
        conditions.append(DrugMechanism.gene_symbol.in_(genes))
    if drug_ids:
        conditions.append(DrugMechanism.chembl_id.in_(drug_ids))
    pair_rows = []
    for gene, chembl_id in pairs:
        pair_rows += db.session.scalars(db.select(DrugMechanism).where(
            DrugMechanism.gene_symbol == gene, DrugMechanism.chembl_id == chembl_id)).all()
    rows = db.session.scalars(
        db.select(DrugMechanism).where(db.or_(*conditions))
        .order_by(DrugMechanism.max_phase.desc(), DrugMechanism.mec_id).limit(limit)
    ).all()
    rows = list({r.id: r for r in pair_rows + list(rows)}.values())
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


def cpic_guidelines(genes, drug_names, drug_ids, per_pair=6, limit=30, pairs=()):
    """pairs: (gene symbol, drug name) the user asked about; listed first so the limit never drops them."""
    conditions = []
    if genes:
        conditions.append(CpicPair.gene_symbol.in_(genes))
    if drug_names:
        conditions.append(CpicPair.drug_name_norm.in_([norm(d) for d in drug_names]))
    if drug_ids:
        conditions.append(CpicPair.chembl_id.in_(drug_ids))
    if not conditions:
        return []
    wanted = {(gene.upper(), norm(drug)) for gene, drug in pairs}
    rows = db.session.scalars(db.select(CpicPair).where(db.or_(*conditions))).all()
    rows = sorted(rows, key=lambda p: ((p.gene_symbol.upper(), p.drug_name_norm) not in wanted,
                                       CPIC_LEVEL_ORDER.get(p.cpic_level, 9)))[:limit]

    out = []
    for p in rows:
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
    """drugs: [(display name, chembl_id or None)]. Returns listing status for each, with the restrictions
    (indication and criteria, as published) that apply to its items."""
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
            "restrictions": pbs_restrictions(list(items)),
            "effective_date": rows[0].effective_date.isoformat() if rows else None,
        })
    return out


def pbs_restrictions(pbs_codes):
    """The distinct restrictions on these PBS items, each with the item codes it applies to."""
    if not pbs_codes:
        return []
    links = db.session.execute(
        db.select(PbsItemRestriction, PbsRestriction)
        .join(PbsRestriction, PbsRestriction.res_code == PbsItemRestriction.res_code)
        .where(PbsItemRestriction.pbs_code.in_(pbs_codes))
        .order_by(PbsRestriction.res_code, PbsItemRestriction.pbs_code)).all()
    out = {}
    for link, r in links:
        entry = out.setdefault(r.res_code, {
            "res_code": r.res_code,
            "treatment_phase": r.treatment_phase,
            "authority_method": r.authority_method,
            "benefit_type": BENEFIT_TYPES.get(link.benefit_type_code, link.benefit_type_code),
            "html": r.html_text,
            "pbs_codes": [],
        })
        if link.pbs_code not in entry["pbs_codes"]:
            entry["pbs_codes"].append(link.pbs_code)
    return list(out.values())


# ---------- Drug safety (services/safety.py) ----------

OFFICIAL_NAMES = ("PREF_NAME", "INN", "USAN", "BAN", "USP", "JAN")


def drug_warnings(chembl_ids):
    """{chembl_id: [warning]} from ChEMBL: withdrawals first, then boxed warnings."""
    ids = [i for i in dict.fromkeys(chembl_ids) if i]
    if not ids:
        return {}
    rows = db.session.scalars(db.select(DrugWarning).where(DrugWarning.chembl_id.in_(ids))).all()
    out = {}
    for w in sorted(rows, key=lambda w: (w.warning_type != "Withdrawn", w.year or 0)):
        out.setdefault(w.chembl_id, []).append({
            "type": w.warning_type, "class": w.warning_class, "description": w.description, "country": w.country,
            "year": w.year, "refs": w.refs or [],
            "url": f"https://www.ebi.ac.uk/chembl/web_components/explore/compound/{w.chembl_id}"})
    return out


def cyp_roles(chembl_id=None, name=None):
    """The drug's FDA CYP / transporter roles, matched by ChEMBL ID or by (salt-stripped) name."""
    conditions = []
    if chembl_id:
        conditions.append(DrugCypRole.chembl_id == chembl_id)
    if name:
        conditions += [DrugCypRole.drug_name_norm == norm(name), DrugCypRole.drug_name_norm == strip_salt(name)]
    if not conditions:
        return []
    rows = db.session.scalars(db.select(DrugCypRole).where(db.or_(*conditions))).all()
    return [{"drug": r.drug_name, "system": r.system, "pathway": r.pathway, "gene": r.gene_symbol, "role": r.role,
             "strength": r.strength, "notes": r.notes} for r in rows]


def cyp_roles_for_genes(genes):
    """FDA rows naming these genes (e.g. CYP2D6 -> paroxetine, strong inhibitor), for the gene -> drugs search."""
    if not genes:
        return []
    rows = db.session.scalars(db.select(DrugCypRole).where(DrugCypRole.gene_symbol.in_(genes))).all()
    return [_role_record(r) for r in rows]


def cyp_roles_for_drug(chembl_id, name):
    conditions = [DrugCypRole.drug_name_norm.in_({norm(name), strip_salt(name)})] if name else []
    if chembl_id:
        conditions.append(DrugCypRole.chembl_id == chembl_id)
    if not conditions:
        return []
    return [_role_record(r) for r in db.session.scalars(db.select(DrugCypRole).where(db.or_(*conditions))).all()]


def _role_record(r):
    return {"drug": r.drug_name.upper(), "chembl_id": r.chembl_id, "gene": r.gene_symbol, "hgnc_id": r.hgnc_id,
            "pathway": r.pathway, "system": r.system, "role": r.role, "strength": r.strength, "notes": r.notes}


def official_names(chembl_id):
    """The drug's official names (preferred name, INN, USAN, ...), for finding it named in another drug's label."""
    if not chembl_id:
        return []
    rows = db.session.scalars(db.select(DrugSynonym.synonym).where(
        DrugSynonym.chembl_id == chembl_id, DrugSynonym.syn_type.in_(OFFICIAL_NAMES))).all()
    return list(dict.fromkeys(rows))


def cpic_genes(chembl_id, name):
    """{gene: CPIC level} for the drug's CPIC gene–drug pairs."""
    conditions = [CpicPair.drug_name_norm == norm(name)] if name else []
    if chembl_id:
        conditions.append(CpicPair.chembl_id == chembl_id)
    if not conditions:
        return {}
    out = {}
    for p in db.session.scalars(db.select(CpicPair).where(db.or_(*conditions))).all():
        if p.cpic_level and CPIC_LEVEL_ORDER.get(p.cpic_level, 9) < CPIC_LEVEL_ORDER.get(out.get(p.gene_symbol), 9):
            out[p.gene_symbol] = p.cpic_level
    return out
