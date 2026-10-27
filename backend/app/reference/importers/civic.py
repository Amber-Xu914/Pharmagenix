"""CIViC predictive (drug response) evidence from the nightly release (CC0).

Each molecular profile name is parsed into components so a patient's variants can be matched to it:
  "EGFR L858R"                          one point mutation
  "EGFR L858R OR EGFR Exon 19 Deletion" any part
  "EML4::ALK Fusion AND ALK G1202R"     every part (a fusion can't be seen in SNV / indel calls, so never matched)
"""

import csv
import io
import re

from flask import current_app

from ..models import CivicEvidence
from ..text import norm
from . import http_session, replace_tables

# Component kinds, from most to least specific. "unsupported" ones can't be detected from SNV / indel calls.
POINT_RE = re.compile(r"^[A-Z]\d+(?:[A-Z]|\*|TER)$")                     # L858R, R213*
CODON_RE = re.compile(r"^[A-Z]\d+(?:/[A-Z]\d+)*$")                      # V600, G12/G13
PROTEIN_RE = re.compile(r"^[A-Z]\d+(?:_[A-Z]\d+)?(?:DEL|DUP|INS|DELINS|FS)[A-Z*]*\d*$")  # E746_A750DEL, W1038FS
EXON_RE = re.compile(r"^EXON (\d+)(?:-(\d+))? (MUTATION|DELETION|INSERTION|SKIPPING MUTATION)$")
GENE_LEVEL = {"MUTATION": "any", "ACTIVATING MUTATION": "any", "TKD MUTATION": "any",
              "LOSS-OF-FUNCTION": "truncating", "FRAMESHIFT TRUNCATION": "truncating", "TRUNCATING MUTATION": "truncating"}


def run(echo):
    url = current_app.config["CIVIC_EVIDENCE_URL"]
    echo(f"[civic] downloading {url}")
    resp = http_session().get(url, timeout=300)
    resp.raise_for_status()
    raw = list(csv.DictReader(io.StringIO(resp.text), delimiter="\t"))
    rows = build_evidence(raw)
    replace_tables([(CivicEvidence, rows)])
    items = len({r["evidence_id"] for r in rows})
    latest = max((r.get("last_review_date") or "" for r in raw), default="")
    version = f"CIViC nightly, latest review {latest[:10]}" if latest else "CIViC nightly"
    return {"version": version, "url": url, "counts": {"civic_evidence_items": items, "civic_evidence_rows": len(rows)}}


def parse_component(text):
    """'EGFR L858R' -> {"gene": "EGFR", "variant": "L858R", "kind": "point"}."""
    gene, _, variant = text.strip().partition(" ")
    variant = norm(variant).replace("P.", "")
    if "::" in gene or variant.endswith("FUSION"):
        return {"gene": gene.split("::")[-1], "variant": variant, "kind": "unsupported"}
    if POINT_RE.match(variant):
        kind = "point"
    elif CODON_RE.match(variant):
        kind = "codon"
    elif PROTEIN_RE.match(variant):
        kind = "protein"
    elif EXON_RE.match(variant):
        kind = "exon"
    elif variant in GENE_LEVEL:
        kind = "gene"
    else:
        kind = "unsupported"  # amplification, expression, loss, methylation, wildtype…
    return {"gene": norm(gene), "variant": variant, "kind": kind}


def parse_profile(name):
    """Split on AND / OR; mixed or NOT profiles are kept but marked unsupported."""
    if " NOT " in f" {name} " or "(" in name or (" AND " in name and " OR " in name):
        return {"op": "AND", "parts": [{"gene": norm(name.split(" ")[0]), "variant": norm(name), "kind": "unsupported"}]}
    op = "OR" if " OR " in name else "AND"
    return {"op": op, "parts": [parse_component(p) for p in re.split(r" (?:AND|OR) ", name)]}


def build_evidence(rows):
    out = []
    for r in rows:
        if r.get("evidence_type") != "Predictive" or r.get("evidence_status") != "accepted":
            continue
        if r.get("variant_origin") in ("Rare Germline", "Common Germline"):
            continue
        therapies = [t.strip() for t in (r.get("therapies") or "").split(",") if t.strip()]
        if not therapies:
            continue
        components = parse_profile(r["molecular_profile"])
        record = {
            "evidence_id": int(r["evidence_id"]),
            "molecular_profile": r["molecular_profile"][:500],
            "molecular_profile_id": int(r["molecular_profile_id"]) if r.get("molecular_profile_id") else None,
            "components": components,
            "disease": (r.get("disease") or "")[:255] or None,
            "doid": r.get("doid") or None,
            "therapies": therapies,
            "therapy_interaction": r.get("therapy_interaction_type") or None,
            "direction": r.get("evidence_direction") or None,
            "level": r.get("evidence_level") or None,
            "significance": r.get("significance") or None,
            "statement": r.get("evidence_statement") or None,
            "pmid": int(r["citation_id"]) if r.get("source_type") == "PubMed" and r.get("citation_id", "").isdigit() else None,
            "citation": (r.get("citation") or "")[:255] or None,
            "rating": int(r["rating"]) if (r.get("rating") or "").isdigit() else None,
            "variant_origin": r.get("variant_origin") or None,
            "url": r.get("evidence_civic_url") or f"https://civicdb.org/links/evidence_items/{r['evidence_id']}",
        }
        for gene in dict.fromkeys(p["gene"] for p in components["parts"]):
            out.append({**record, "gene_symbol": gene[:64]})
    return out
