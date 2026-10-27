"""Match a patient's variants to CIViC molecular profiles (variant level, not gene only).

Specificity of a match, best first:
  exact   the same protein change (L858R, E746_A750del)
  codon   the same codon (V600 matches V600E; G12/G13 matches G12C)
  exon    an exon-level class (EGFR Exon 19 Deletion matches an in-frame deletion in exon 19)
  class   a truncating variant in a tumour suppressor matched by gene-level evidence ("STK11 Mutation"): for a
          tumour suppressor that evidence is about inactivating variants (set by pipeline.py, which knows the role)
  gene    any protein-altering (or truncating) variant in the gene ("EGFR Mutation")
A profile with an undetectable part (fusion, amplification, expression…) never matches SNV / indel calls.
"""

import re

from .parse import PROTEIN_ALTERING

SPECIFICITY = ["exact", "codon", "exon", "class", "gene"]
TRUNCATING = {"nonsense", "frameshift", "splice", "truncating"}
EXON_RE = re.compile(r"^EXON (\d+)(?:-(\d+))? (MUTATION|DELETION|INSERTION|SKIPPING MUTATION)$")


def _protein(variant):
    return (variant.get("protein_change") or "").upper()


def match_part(part, variant):
    """Specificity of a profile component matched by one variant, or None."""
    if part["kind"] == "unsupported" or (variant.get("gene") or "").upper() != part["gene"]:
        return None
    protein, cls, wanted = _protein(variant), variant.get("classification"), part["variant"]
    if part["kind"] in ("point", "protein"):
        return "exact" if protein and protein == wanted.replace("TER", "*") else None
    if part["kind"] == "codon":
        codons = wanted.split("/")
        return "codon" if any(re.fullmatch(rf"{c}[A-Z*]", protein) for c in codons) else None
    if part["kind"] == "exon":
        start, end, what = EXON_RE.match(wanted).groups()
        exons = range(int(start), int(end or start) + 1)
        if what == "SKIPPING MUTATION":  # splice variants at either end of the skipped exon
            hit = cls in ("splice", "splice_region") and (
                variant.get("exon") in exons or variant.get("intron") in {e - 1 for e in exons} | set(exons))
            return "exon" if hit else None
        if variant.get("exon") not in exons:
            return None
        ok = {"DELETION": cls == "inframe_deletion" or (cls == "missense" and "DELINS" in protein),
              "INSERTION": cls == "inframe_insertion" or "DUP" in protein
                           or ("INS" in protein and "DELINS" not in protein),
              "MUTATION": cls in PROTEIN_ALTERING}[what]
        return "exon" if ok else None
    if part["kind"] == "gene":
        wanted_cls = TRUNCATING if part["variant"] in ("LOSS-OF-FUNCTION", "FRAMESHIFT TRUNCATION",
                                                        "TRUNCATING MUTATION") else PROTEIN_ALTERING
        return "gene" if cls in wanted_cls else None
    return None


def match_profile(components, variants):
    """(specificity, [variant ids]) when the patient's variants satisfy the profile, else None.

    OR: any part, the most specific wins. AND: every part, and the least specific part sets the match.
    """
    hits = []
    for part in components["parts"]:
        found = [(m, v["id"]) for v in variants if (m := match_part(part, v))]
        if found:
            best = min(found, key=lambda f: SPECIFICITY.index(f[0]))
            hits.append((best[0], [vid for m, vid in found if m == best[0]]))
        elif components["op"] == "AND":
            return None
    if not hits:
        return None
    pick = min if components["op"] == "OR" else max
    specificity = pick((h[0] for h in hits), key=SPECIFICITY.index)
    ids = [vid for m, ids in hits for vid in ids if components["op"] == "AND" or m == specificity]
    return specificity, list(dict.fromkeys(ids))
