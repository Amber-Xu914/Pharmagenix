"""Stage 2, Gene Profile Retrieval: basic information about the searched gene.

HGNC (local) for symbol, aliases and location; UniProt (live) for protein function and domains.
The profile becomes an evidence item (kind "profile"), so the LLM can cite it. The UniProt lookup is best effort:
a failure is recorded in source_status and the profile keeps its HGNC fields.
"""

import logging

from ...extensions import db
from ...reference.models import Gene, GeneAlias

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
