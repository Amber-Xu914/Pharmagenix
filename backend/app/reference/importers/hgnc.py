"""HGNC complete set: approved human gene symbols, previous symbols and aliases (CC0)."""

import csv
import io

from flask import current_app

from ..models import Gene, GeneAlias
from ..text import norm
from . import http_session, replace_tables


def run(echo):
    url = current_app.config["HGNC_URL"]
    echo(f"[hgnc] downloading {url}")
    resp = http_session().get(url, timeout=180)
    resp.raise_for_status()
    genes, aliases, last_modified = parse_hgnc(csv.DictReader(io.StringIO(resp.text), delimiter="\t"))
    echo(f"[hgnc] {len(genes)} genes, {len(aliases)} names; writing to database")
    replace_tables([(Gene, genes), (GeneAlias, aliases)])
    return {"version": f"HGNC complete set, last modified {last_modified}", "url": url,
            "counts": {"genes": len(genes), "gene_aliases": len(aliases)}}


def _split(value):
    return [v.strip() for v in (value or "").split("|") if v.strip()]


def parse_hgnc(rows):
    genes, aliases, seen, last_modified = [], [], set(), ""
    for row in rows:
        if row.get("status") != "Approved":
            continue
        hgnc_id = row["hgnc_id"]
        genes.append({
            "hgnc_id": hgnc_id,
            "symbol": row["symbol"],
            "name": (row.get("name") or "")[:255],
            "locus_group": row.get("locus_group"),
            "locus_type": row.get("locus_type"),
            "location": (row.get("location") or "")[:64],
            "entrez_id": row.get("entrez_id") or None,
            "ensembl_gene_id": row.get("ensembl_gene_id") or None,
            "uniprot_ids": _split(row.get("uniprot_ids")),
        })
        last_modified = max(last_modified, row.get("date_modified") or "")
        names = [(row["symbol"], "symbol")]
        names += [(s, "previous") for s in _split(row.get("prev_symbol"))]
        names += [(s, "alias") for s in _split(row.get("alias_symbol"))]
        for alias, alias_type in names:
            key = (hgnc_id, norm(alias))
            if key in seen or len(alias) > 64:
                continue
            seen.add(key)
            aliases.append({"hgnc_id": hgnc_id, "alias": alias, "alias_norm": norm(alias), "alias_type": alias_type})
    return genes, aliases, last_modified
