"""IntOGen compendium of cancer driver genes per cohort and cancer type (CC0).

Used to decide which genes are drivers for the patient's cancer subtype (e.g. LUAD), the "cancer relevance"
filter of Workflow 4.
"""

import csv
import io
import re
import zipfile

from flask import current_app

from ..models import IntogenDriver
from ..text import norm
from . import http_session, replace_tables


def run(echo):
    url = current_app.config["INTOGEN_DRIVERS_URL"]
    echo(f"[intogen] downloading {url}")
    resp = http_session().get(url, timeout=300)
    resp.raise_for_status()
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    name = next(n for n in archive.namelist() if n.endswith("Compendium_Cancer_Genes.tsv"))
    with archive.open(name) as fh:
        rows = build_drivers(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"), delimiter="\t"))
    replace_tables([(IntogenDriver, rows)])
    release = re.search(r"(\d{4}-\d{2}-\d{2})", name)
    return {"version": f"IntOGen drivers {release.group(1) if release else 'release'}", "url": url,
            "counts": {"intogen_drivers": len(rows), "genes": len({r["gene_symbol"] for r in rows}),
                       "cancer_types": len({r["cancer_type"] for r in rows})}}


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_drivers(rows):
    out = []
    for r in rows:
        if r.get("IS_DRIVER", "True") != "True" or not r.get("SYMBOL"):
            continue
        samples = _float(r.get("SAMPLES"))
        out.append({
            "gene_symbol": norm(r["SYMBOL"])[:64],
            "cancer_type": r["CANCER_TYPE"][:32],
            "cohort": r["COHORT"][:100],
            "role": r.get("ROLE") or None,
            "samples": int(samples) if samples is not None else None,
            "pct_samples": _float(r.get("%_SAMPLES_COHORT")),
            "qvalue": _float(r.get("QVALUE_COMBINATION")),
        })
    return out
