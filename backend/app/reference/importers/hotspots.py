"""cancerhotspots.org single-residue and in-frame indel hotspots (Chang et al., Cancer Discov 2018).

Used by the Workflow 4 tiering tool: a variant at a known hotspot is Tier 2 when there is no subtype-specific evidence.
COSMIC's hotspot data needs a licence, so it is not used.
"""

import ast
import re

from flask import current_app

from ..models import CancerHotspot
from ..text import norm
from . import http_session, replace_tables


def run(echo):
    url = current_app.config["CANCER_HOTSPOTS_URL"]
    echo(f"[hotspots] downloading {url}")
    resp = http_session().get(url, timeout=180)
    resp.raise_for_status()
    rows = build_hotspots(resp.json())
    replace_tables([(CancerHotspot, rows)])
    return {"version": "cancerhotspots.org v2 (single residue and in-frame indel)", "url": url,
            "counts": {"cancer_hotspots": len(rows), "genes": len({r["gene_symbol"] for r in rows})}}


def _parse(value):
    """The API returns some nested fields as Python-style strings: "{'start': 61, 'end': 61}"."""
    if isinstance(value, (dict, list)) or value is None:
        return value
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return None


def build_hotspots(items):
    out = []
    for h in items:
        position = _parse(h.get("aminoAcidPosition")) or {}
        start, end = position.get("start"), position.get("end")
        if start is None:  # fall back to the residue name: V600 or 745-759
            numbers = [int(n) for n in re.findall(r"\d+", h.get("residue") or "")]
            if not numbers:
                continue
            start, end = numbers[0], numbers[-1]
        out.append({
            "gene_symbol": norm(h["hugoSymbol"])[:64],
            "residue": (h.get("residue") or "")[:32],
            "start": int(start), "end": int(end or start),
            "hotspot_type": h.get("type"),
            "tumor_count": h.get("tumorCount"),
            "variant_amino_acids": _parse(h.get("variantAminoAcid")) or {},
        })
    return out
