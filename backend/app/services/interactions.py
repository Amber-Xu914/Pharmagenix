"""The relational view of a query's results: one query_interactions row per drug–gene evidence item.

The evidence bundle (queries.evidence, JSON) stays the source for the results page; these rows make the same
drug–gene pairs available to SQL (e.g. "which searches found osimertinib for EGFR, and from which source?") and link
them to the genes / drugs reference tables by HGNC and ChEMBL ID.
"""

from ..extensions import db
from ..models import QueryInteraction
from ..reference.models import DrugSynonym, GeneAlias
from ..reference.text import norm, strip_salt


def _pairs(item):
    """(gene, drug, relation, level, score) for each drug–gene pair an evidence item states."""
    kind = item.get("kind")
    if kind == "interaction":  # DGIdb
        yield item.get("gene"), item.get("drug"), ", ".join(item.get("interaction_types") or []) or None, None, item.get("score")
    elif kind == "mechanism":  # ChEMBL
        yield item.get("gene"), item.get("drug"), item.get("action_type"), None, None
    elif kind == "guideline":  # CPIC
        yield item.get("gene"), item.get("drug"), "prescribing guideline", item.get("cpic_level"), None
    elif kind == "variant_annotation":  # PharmGKB clinical annotation
        for gene in item.get("genes") or []:
            for drug in item.get("drugs") or []:
                yield gene, drug, ", ".join(item.get("phenotype_categories") or []) or None, item.get("level"), None
    elif kind == "civic":  # gene = the molecular profile's first gene ("EGFR L858R AND EGFR T790M")
        gene = (item.get("molecular_profile") or "").split(" ")[0]
        for drug in item.get("therapies") or []:
            yield gene, drug, item.get("significance"), item.get("level"), None
    elif kind == "oncokb":
        for drug in (item.get("drug") or "").split(" + "):
            yield item.get("gene"), drug, item.get("significance"), item.get("level"), None


def interaction_rows(evidence):
    rows = []
    for item in (evidence or {}).get("items") or []:
        for gene, drug, relation, level, score in _pairs(item):
            if gene and drug:
                rows.append({"evidence_id": item["id"], "source": item.get("source") or item["kind"],
                             "gene_symbol": gene.upper()[:64], "drug_name": drug.upper()[:255],
                             "hgnc_id": item.get("hgnc_id"), "chembl_id": item.get("chembl_id"),
                             "relation": (relation or "")[:255] or None, "level": (str(level) if level else None),
                             "score": float(score) if isinstance(score, (int, float)) else None})
    _link_ids(rows)
    return rows


def _link_ids(rows):
    """Fill in missing HGNC / ChEMBL IDs from the reference tables (two queries for the whole set)."""
    genes = {r["gene_symbol"] for r in rows if not r["hgnc_id"]}
    if genes:
        found = dict(db.session.execute(db.select(GeneAlias.alias_norm, GeneAlias.hgnc_id)
                                        .where(GeneAlias.alias_type == "symbol", GeneAlias.alias_norm.in_(genes))).all())
        for r in rows:
            r["hgnc_id"] = r["hgnc_id"] or found.get(r["gene_symbol"])
    keys = {k for r in rows if not r["chembl_id"] for k in (norm(r["drug_name"]), strip_salt(r["drug_name"]))}
    if keys:
        found = dict(db.session.execute(db.select(DrugSynonym.synonym_norm, DrugSynonym.chembl_id)
                                        .where(DrugSynonym.synonym_norm.in_(keys))).all())
        for r in rows:
            r["chembl_id"] = r["chembl_id"] or found.get(norm(r["drug_name"])) or found.get(strip_salt(r["drug_name"]))


def record_interactions(query):
    """Replace the query's rows with those of its current evidence (the caller commits)."""
    query.interactions = [QueryInteraction(**row) for row in interaction_rows(query.evidence)]
