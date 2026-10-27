"""cBioPortal: gene mutation frequency per lung cancer subtype, from one reference study each.

Used for the R ("subtype relevance") component of the Workflow 4 Tier 3 score. Each subtype's study is set in
CBIOPORTAL_SUBTYPE_STUDIES; subtypes without their own study use a parent's (e.g. LCLC -> NSCLC).
"""

from flask import current_app

from ..models import SubtypeGeneFrequency
from ..text import norm
from . import http_session, replace_tables


def run(echo):
    cfg = current_app.config
    base = cfg["CBIOPORTAL_URL"].rstrip("/")
    session = http_session({"Accept": "application/json"})
    rows = []
    for code, study in cfg["CBIOPORTAL_SUBTYPE_STUDIES"].items():
        echo(f"[cbioportal] {code}: gene frequencies from {study}")
        resp = session.post(f"{base}/mutated-genes/fetch", json={"studyIds": [study]}, timeout=300)
        resp.raise_for_status()
        rows += build_frequencies(resp.json(), code, study)
    replace_tables([(SubtypeGeneFrequency, rows)])
    studies = cfg["CBIOPORTAL_SUBTYPE_STUDIES"]
    return {"version": f"cBioPortal mutation frequencies ({len(studies)} lung studies)", "url": base,
            "notes": ", ".join(f"{c}: {s}" for c, s in studies.items()),
            "counts": {"subtype_gene_frequencies": len(rows)}}


def build_frequencies(genes, code, study):
    out = []
    for g in genes:
        altered, profiled = g.get("numberOfAlteredCases") or 0, g.get("numberOfProfiledCases") or 0
        if not g.get("hugoGeneSymbol") or not profiled:
            continue
        out.append({"oncotree_code": code, "study_id": study, "gene_symbol": norm(g["hugoGeneSymbol"])[:64],
                    "altered": altered, "profiled": profiled, "frequency": round(altered / profiled, 4)})
    return out
