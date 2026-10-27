"""Import runner: each importer fetches its source and replaces that source's tables in one transaction."""

import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...extensions import db
from ..models import DrugSynonym, GeneAlias, SourceRelease
from ..text import norm, strip_salt

IMPORT_ORDER = ["hgnc", "chembl", "cpic", "clinpgx", "dgidb", "fda", "oncotree", "intogen", "civic", "hotspots", "cbioportal", "pbs"]  # later sources map names onto earlier ones
BATCH_SIZE = 2000


def http_session(headers=None):
    session = requests.Session()
    session.headers.update({"User-Agent": "DGI-Explorer/0.1 (academic project)", **(headers or {})})
    retry = Retry(total=4, backoff_factor=2, status_forcelist=(500, 502, 503, 504), allowed_methods=("GET",))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def replace_tables(tables):
    """tables: [(Model, rows)] in parent-first order. Deletes children first, inserts parents first."""
    for model, _ in reversed(tables):
        db.session.execute(db.delete(model))
    for model, rows in tables:
        for start in range(0, len(rows), BATCH_SIZE):
            db.session.execute(db.insert(model), rows[start:start + BATCH_SIZE])


def run_import(source, echo=print):
    from . import cbioportal, chembl, civic, clinpgx, cpic, dgidb, fda, hgnc, hotspots, intogen, oncotree, pbs

    importer = {"hgnc": hgnc, "chembl": chembl, "cpic": cpic, "clinpgx": clinpgx, "dgidb": dgidb, "fda": fda,
                "oncotree": oncotree, "intogen": intogen, "civic": civic, "hotspots": hotspots,
                "cbioportal": cbioportal, "pbs": pbs}[source]
    started = time.perf_counter()
    try:
        result = importer.run(echo)
    except Exception as exc:
        db.session.rollback()
        db.session.add(SourceRelease(source=source, status="failed", notes=str(exc)[:2000]))
        db.session.commit()
        raise
    release = SourceRelease(source=source, status="ok", version=result["version"], url=result["url"],
                            record_counts=result["counts"], notes=result.get("notes"))
    db.session.add(release)
    db.session.commit()
    echo(f"[{source}] done in {time.perf_counter() - started:.0f}s: {result['counts']} ({result['version']})")
    return release


def gene_symbol_index():
    """Upper-case symbol -> HGNC ID, from approved symbols first, then previous symbols."""
    index = {}
    rows = db.session.execute(
        db.select(GeneAlias.alias_norm, GeneAlias.hgnc_id, GeneAlias.alias_type)
        .where(GeneAlias.alias_type.in_(("symbol", "previous")))
    ).all()
    for alias, hgnc_id, alias_type in sorted(rows, key=lambda r: r[2] != "symbol"):
        index.setdefault(alias, hgnc_id)
    return index


SYNONYM_PRIORITY = {"PREF_NAME": 0, "INN": 1, "USAN": 2, "BAN": 3, "USP": 4}


def drug_synonym_index():
    """Normalised drug name (salt forms stripped) -> ChEMBL ID, preferring official names."""
    rows = db.session.execute(db.select(DrugSynonym.synonym_norm, DrugSynonym.chembl_id, DrugSynonym.syn_type)).all()
    index = {}
    for name, chembl_id, syn_type in sorted(rows, key=lambda r: SYNONYM_PRIORITY.get(r[2], 9)):
        index.setdefault(name, chembl_id)
        index.setdefault(strip_salt(name), chembl_id)
    return index


def lookup_drug(index, name):
    return index.get(norm(name)) or index.get(strip_salt(name))
