"""DGIdb druggable-genome gene categories (e.g. KINASE, CLINICALLY ACTIONABLE), for gene-level filtering.

Interactions are not imported: the app queries them live from the DGIdb API on every search.
genes.tsv is downloaded only to map each source's category claims to normalised genes.
"""

import csv
import io
from email.utils import parsedate_to_datetime

from flask import current_app

from ..models import DgidbGeneCategory
from ..text import norm
from . import gene_symbol_index, http_session, replace_tables


def run(echo):
    base = current_app.config["DGIDB_DOWNLOAD_URL"].rstrip("/")
    session = http_session()
    files, modified = {}, []
    for name in ("categories", "genes"):
        echo(f"[dgidb] downloading {name}.tsv")
        resp = session.get(f"{base}/{name}.tsv", timeout=300)
        resp.raise_for_status()
        files[name] = _read_tsv(resp.content.decode("utf-8"))
        if resp.headers.get("Last-Modified"):
            modified.append(parsedate_to_datetime(resp.headers["Last-Modified"]).date().isoformat())

    categories = build_categories(files["categories"], files["genes"], gene_symbol_index())
    replace_tables([(DgidbGeneCategory, categories)])
    version = f"DGIdb data release of {max(modified)}" if modified else "DGIdb latest data release"
    return {"version": version, "url": base,
            "counts": {"dgidb_gene_categories": len(categories)}}


def _read_tsv(text):
    rows = csv.DictReader(io.StringIO(text.replace("\r\n", "\n")), delimiter="\t")
    return [{k: (None if v in ("NULL", "") else v) for k, v in row.items()} for row in rows]


def _hgnc_id(concept_id, symbol, genes):
    """DGIdb gene concepts are HGNC IDs written as "hgnc:2625"."""
    if concept_id and concept_id.lower().startswith("hgnc:"):
        return "HGNC:" + concept_id.split(":", 1)[1]
    return genes.get(norm(symbol))


def build_categories(rows, gene_claims, genes):
    """categories.tsv names genes by each source's own claim name ("PXR"); genes.tsv maps (claim, source) to the
    normalised gene ("NR1I2"). Claims without a single normalised gene are skipped, and repeated rows dropped."""
    claim_genes = {}
    for g in gene_claims:
        if g.get("gene_name") and g.get("gene_claim_name"):
            claim_genes.setdefault((norm(g["gene_claim_name"]), g.get("source_db_name")), set()).add(g["gene_name"])
    concept_ids = {norm(g["gene_name"]): g.get("concept_id") for g in gene_claims if g.get("gene_name")}

    out, seen = [], set()
    for r in rows:
        claim, category, source = r.get("name"), r.get("name-2"), r.get("source_db_name")
        names = claim_genes.get((norm(claim), source), set())
        if len(names) != 1 or not category:
            continue
        gene = norm(next(iter(names)))
        if (gene, category, source) in seen:
            continue
        seen.add((gene, category, source))
        out.append({"gene_symbol": gene[:64], "hgnc_id": _hgnc_id(concept_ids.get(gene), gene, genes),
                    "gene_claim_name": (claim or "")[:255] or None, "category": category[:128],
                    "source_db": source})
    return out
