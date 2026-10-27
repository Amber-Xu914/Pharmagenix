"""Stage 2, Profile Retrieval: basic information about the searched gene or drug.

Gene: HGNC (local) for symbol, aliases and location; UniProt (live) for protein function and domains.
Drug: ChEMBL (local) for its type, clinical phase, first approval, trade names and mechanisms of action.
The profile becomes an evidence item (kind "profile"), so the LLM can cite it. The UniProt lookup is best effort:
a failure is recorded in source_status and the profile keeps its HGNC fields.
"""

import logging

from ...extensions import db
from ...reference.models import Drug, DrugMechanism, DrugSynonym, Gene, GeneAlias

logger = logging.getLogger(__name__)


def build_gene_profile(entry, uniprot, status):
    """entry: the stage 1 normalisation entry for the gene (its id is the HGNC ID when resolved)."""
    gene = db.session.get(Gene, entry["id"]) if entry.get("id") else None
    aliases = []
    if gene:
        aliases = db.session.scalars(
            db.select(GeneAlias.alias).where(GeneAlias.hgnc_id == gene.hgnc_id, GeneAlias.alias_type != "symbol")
        ).all()
    accession = (gene.uniprot_ids or [None])[0] if gene else None
    try:
        protein = uniprot.protein(accession=accession, gene_symbol=entry["name"])
        status["uniprot"] = {"state": "ok", "count": 1 if protein else 0}
    except Exception as exc:  # noqa: BLE001 - a failed lookup leaves the HGNC part of the profile
        logger.warning("UniProt lookup failed: %s", exc)
        status["uniprot"] = {"state": "error", "count": 0, "error": str(exc)[:300]}
        protein = None
    return {
        "kind": "profile",
        "entity_type": "gene",
        "entity": entry["name"],
        "source": "UniProt" if protein else "HGNC",
        "full_name": gene.name if gene else None,
        "aliases": sorted(set(aliases))[:12],
        "location": gene.location if gene else None,
        "locus_type": gene.locus_type if gene else None,
        "hgnc_id": gene.hgnc_id if gene else None,
        "entrez_id": gene.entrez_id if gene else None,
        "protein": protein,
        "url": (protein or {}).get("url")
               or (f"https://www.genenames.org/data/gene-symbol-report/#!/hgnc_id/{gene.hgnc_id}" if gene else None),
    }


def build_drug_profile(entry):
    """entry: the stage 1 normalisation entry for the drug (its id is the ChEMBL ID when resolved)."""
    drug = db.session.get(Drug, entry["id"]) if entry.get("id") else None
    trade_names, mechanisms = [], []
    if drug:
        trade_names = db.session.scalars(db.select(DrugSynonym.synonym).where(
            DrugSynonym.chembl_id == drug.chembl_id, DrugSynonym.syn_type == "TRADE_NAME")).all()
        mechanisms = db.session.scalars(db.select(DrugMechanism.mechanism_of_action).where(
            DrugMechanism.chembl_id == drug.chembl_id)).all()
    return {
        "kind": "profile",
        "entity_type": "drug",
        "entity": entry["name"],
        "source": "ChEMBL",
        "full_name": drug.pref_name if drug else None,
        "chembl_id": drug.chembl_id if drug else None,
        "molecule_type": drug.molecule_type if drug else None,
        "max_phase": drug.max_phase if drug else None,
        "first_approval": drug.first_approval if drug else None,
        "trade_names": _brand_names(trade_names, drug.pref_name if drug else entry["name"]),
        "mechanisms": list(dict.fromkeys(m for m in mechanisms if m))[:8],
        "url": f"https://www.ebi.ac.uk/chembl/compound_report_card/{drug.chembl_id}/" if drug else None,
    }


def _brand_names(names, generic, limit=12):
    """Real brand names only: ChEMBL's trade names also list manufacturer generics ("Clopidogrel Apotex") and
    combination products ("... component of Duoplavin")."""
    generic = (generic or "").lower()
    brands = {n for n in names if "component of" not in n.lower() and not n.lower().startswith(generic)}
    return sorted(brands, key=str.lower)[:limit]
