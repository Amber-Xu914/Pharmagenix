"""AlphaGenome in-silico mutagenesis (ISM) around a QC-passed variant (Workflow 4, final section of the report).

Every possible single-base substitution in a small window centred on the variant (window = 2 * flank + 1 positions,
3 alternative bases each) is scored by AlphaGenome against the reference sequence. One scalar per substitution:

  gene-level outputs (RNA_SEQ, SPLICE_SITES, SPLICE_SITE_USAGE)  the score for the variant's gene
  centre-mask outputs (DNASE, ATAC, CAGE, CHIP_HISTONE, ...)    the score for the window around the variant
  both: averaged over the tracks of the chosen tissue (UBERON ontology term; lung by default), or over all tracks
  when the model has none for that tissue.

AlphaGenome models regulatory activity (expression, splicing, chromatin) from DNA sequence. It does not model
protein function, so for a missense variant the map shows the predicted effect on the gene's regulation, not on the
protein. GRCh38 only. Optional: needs `pip install -r requirements-alphagenome.txt` (Python >= 3.11) and an
ALPHAGENOME_API_KEY.
"""

import logging
import re
import threading
from collections import OrderedDict

from flask import current_app

from ...errors import APIError

logger = logging.getLogger(__name__)

BASES = "ACGT"
GENE_OUTPUTS = {"RNA_SEQ", "SPLICE_SITES", "SPLICE_SITE_USAGE"}
CENTRE_OUTPUTS = {"DNASE", "ATAC", "CAGE", "PROCAP", "CHIP_HISTONE", "CHIP_TF"}
OUTPUT_LABELS = {
    "RNA_SEQ": "RNA-seq expression, log fold change (ALT vs REF) for the gene",
    "SPLICE_SITES": "splice-site probability change for the gene",
    "SPLICE_SITE_USAGE": "splice-site usage change for the gene",
    "DNASE": "DNase accessibility, log2 change in a 501 bp window",
    "ATAC": "ATAC accessibility, log2 change in a 501 bp window",
    "CAGE": "CAGE transcription start, log2 change in a 501 bp window",
    "PROCAP": "PRO-cap transcription start, log2 change in a 501 bp window",
    "CHIP_HISTONE": "histone ChIP-seq, log2 change in a 2 kb window",
    "CHIP_TF": "TF ChIP-seq, log2 change in a 501 bp window",
}
SEQUENCE_LENGTHS = {"16KB": 2**14, "100KB": 2**17, "500KB": 2**19, "1MB": 2**20}
CHROM = re.compile(r"^(?:chr)?([1-9]|1[0-9]|2[0-2]|X|Y)$", re.IGNORECASE)
CACHE_SIZE = 64

_client = None
_client_lock = threading.Lock()
_cache = OrderedDict()
_cache_lock = threading.Lock()


def settings():
    """What the frontend shows about the ISM section, and why it is off when it is."""
    cfg = current_app.config
    output = cfg["ALPHAGENOME_ISM_OUTPUT"].upper()
    reason = None
    if not cfg["ALPHAGENOME_API_KEY"]:
        reason = "Set ALPHAGENOME_API_KEY in backend/.env to enable it (free key for non-commercial use)."
    elif not _installed():
        reason = "Install the AlphaGenome SDK: pip install -r requirements-alphagenome.txt (Python 3.11+)."
    elif output not in GENE_OUTPUTS | CENTRE_OUTPUTS:
        reason = f"ALPHAGENOME_ISM_OUTPUT={output} is not supported; use one of {sorted(GENE_OUTPUTS | CENTRE_OUTPUTS)}."
    return {"enabled": reason is None, "reason": reason, "output": output, "output_label": OUTPUT_LABELS.get(output),
            "tissue": cfg["ALPHAGENOME_ISM_TISSUE"], "flank": cfg["ALPHAGENOME_ISM_FLANK"],
            "max_variants": cfg["ALPHAGENOME_ISM_MAX_VARIANTS"]}


def _installed():
    try:
        import alphagenome  # noqa: F401
    except ImportError:
        return False
    return True


def _model():
    global _client
    with _client_lock:
        if _client is None:
            from alphagenome.models import dna_client
            _client = dna_client.create(current_app.config["ALPHAGENOME_API_KEY"],
                                        timeout=current_app.config["ALPHAGENOME_TIMEOUT"])
        return _client


def ism_heatmap(chrom, pos, gene=None, ref=None, alt=None, assembly="GRCh38"):
    """The (position x base) score matrix around chrom:pos (1-based), as JSON-ready data."""
    s = settings()
    if not s["enabled"]:
        raise APIError(f"AlphaGenome ISM is not enabled. {s['reason']}", 503)
    if (assembly or "GRCh38").upper() not in ("GRCH38", "HG38"):
        raise APIError("AlphaGenome supports GRCh38 only; this VCF is on " + str(assembly))
    match = CHROM.match(str(chrom or ""))
    if not match:
        raise APIError(f"Unsupported chromosome: {chrom}")
    try:
        pos = int(pos)
    except (TypeError, ValueError):
        raise APIError("pos must be an integer (1-based)") from None
    if pos < 1:
        raise APIError("pos must be >= 1")
    chrom = f"chr{match.group(1).upper()}"
    cfg = current_app.config
    output, tissue, flank = s["output"], s["tissue"], int(s["flank"])
    length_key = cfg["ALPHAGENOME_SEQUENCE_LENGTH"].upper()
    if length_key not in SEQUENCE_LENGTHS:
        raise APIError(f"ALPHAGENOME_SEQUENCE_LENGTH must be one of {list(SEQUENCE_LENGTHS)}", 500)
    gene = gene if output in GENE_OUTPUTS else None

    key = (chrom, pos, gene, output, tissue, flank, length_key)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return {**_cache[key], "variant": _observed(pos, ref, alt), "cached": True}

    result = _score(chrom, pos, gene, output, tissue, flank, SEQUENCE_LENGTHS[length_key])
    result.update({"sequence_length": length_key, "output_label": OUTPUT_LABELS[output]})
    with _cache_lock:
        _cache[key] = result
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return {**result, "variant": _observed(pos, ref, alt), "cached": False}


def _observed(pos, ref, alt):
    """The patient's own variant, so its cell can be outlined (SNVs only: an indel has no single cell)."""
    snv = bool(ref and alt and len(ref) == 1 and len(alt) == 1 and alt.upper() in BASES)
    return {"pos": pos, "ref": ref, "alt": alt, "snv": snv}


def _score(chrom, pos, gene, output, tissue, flank, length):
    from alphagenome.data import genome
    from alphagenome.models import variant_scorers

    scorer = variant_scorers.RECOMMENDED_VARIANT_SCORERS[output]
    # Interval is 0-based, half-open: [pos - 1, pos) is the variant base.
    interval = genome.Interval(chrom, pos - 1, pos).resize(length)
    ism_interval = genome.Interval(chrom, pos - 1 - flank, pos + flank)
    try:
        scores = _model().score_ism_variants(interval=interval, ism_interval=ism_interval, variant_scorers=[scorer],
                                             progress_bar=False)
    except Exception as exc:  # noqa: BLE001 - gRPC / quota errors become a readable message
        logger.warning("AlphaGenome ISM failed for %s:%s: %s", chrom, pos, exc)
        raise APIError(f"AlphaGenome request failed: {str(exc)[:200]}", 502) from exc

    positions = list(range(ism_interval.start + 1, ism_interval.end + 1))
    index = {p: i for i, p in enumerate(positions)}
    ref_bases = [None] * len(positions)
    matrix = {b: [None] * len(positions) for b in BASES}
    tracks_used, tissue_matched, gene_found = None, None, None
    for per_scorer in scores:
        adata = per_scorer[0]
        variant = adata.uns["variant"]
        i = index.get(variant.position)
        if i is None or variant.alternate_bases not in BASES:
            continue
        value, tracks_used, tissue_matched, gene_found = _scalar(adata, gene, tissue)
        ref_bases[i] = variant.reference_bases
        matrix[variant.alternate_bases][i] = value

    values = [v for col in matrix.values() for v in col if v is not None]
    if not values:
        raise APIError("AlphaGenome returned no scores for this window", 502)
    return {
        "chrom": chrom, "positions": positions, "ref": ref_bases, "scores": matrix,
        "max_abs": max(abs(v) for v in values), "output": output, "tissue": tissue,
        "tissue_matched": tissue_matched, "tracks": tracks_used, "gene": gene_found, "flank": flank,
        "model": "AlphaGenome (Google DeepMind)",
    }


def _scalar(adata, gene, tissue):
    """One number per substitution: the gene's row (gene-level scorers) and the mean over the tissue's tracks."""
    import numpy as np

    x = np.asarray(adata.X, dtype=float)
    row, gene_found = 0, None
    if adata.obs is not None and "gene_name" in adata.obs.columns and len(adata.obs):
        names = adata.obs["gene_name"].astype(str).str.upper().tolist()
        if gene and gene.upper() in names:
            row = names.index(gene.upper())
        else:  # gene not named or not found: the gene whose score moves most for this substitution
            row = int(np.nanargmax(np.nanmean(np.abs(x), axis=1)))
        gene_found = adata.obs["gene_name"].iloc[row]
    values = x[row]
    mask = np.ones(values.shape, dtype=bool)
    matched = False
    if tissue and "ontology_curie" in adata.var.columns:
        hits = (adata.var["ontology_curie"].astype(str) == tissue).to_numpy()
        if hits.any():
            mask, matched = hits, True
    return float(np.nanmean(values[mask])), int(mask.sum()), matched, gene_found
