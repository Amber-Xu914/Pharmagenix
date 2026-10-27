"""Ensembl VEP REST API: gene, consequence, HGVS, exon, ClinVar, gnomAD frequency and pathogenicity scores
(AlphaMissense, REVEL, CADD) for Workflow 4's QC and tiering.

Public, no key; at most 200 variants per request and 15 requests per second. GRCh37 uses Ensembl's GRCh37 server.
"""

import time

from ...http_cache import cached_session
from .parse import classify, short_protein

BATCH = 200


class VEPClient:
    def __init__(self, base_url, timeout=60):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = cached_session()
        self.session.headers.update({"Content-Type": "application/json", "Accept": "application/json",
                                     "User-Agent": "DGI-Explorer/0.1 (academic project)"})

    def annotate(self, variants, fill_only=False):
        """Fill in the annotation fields of each variant (in place). Returns the number annotated.

        fill_only: keep the fields the file already has (gene, protein change from a MAF) and only add the missing
        ones (exon / intron, ClinVar)."""
        done = 0
        for start in range(0, len(variants), BATCH):
            batch = variants[start:start + BATCH]
            lines = {_vcf_line(v): v for v in batch}
            for result in self._post(list(lines)):
                variant = lines.get(result.get("input"))
                if variant is not None:
                    found = from_vep(result)
                    if fill_only:
                        found = {k: v for k, v in found.items() if not variant.get(k)}
                    found["gnomad_af"] = _gnomad_af(result, variant)  # always the VEP value
                    variant.update(found)
                    done += 1
        return done

    def _post(self, lines, attempts=4):
        url = f"{self.base_url}/vep/homo_sapiens/region"
        params = {"hgvs": 1, "numbers": 1, "canonical": 1, "pick": 1, "AlphaMissense": 1, "REVEL": 1, "CADD": 1,
                  "af_gnomade": 1, "af_gnomadg": 1}
        for attempt in range(attempts):
            resp = self.session.post(url, params=params, json={"variants": lines}, timeout=self.timeout)
            # Ensembl's REST servers also return an occasional 400 that succeeds on retry; a real bad request
            # still fails after the last attempt.
            if resp.status_code in (400, 429) or resp.status_code >= 500:
                time.sleep(float(resp.headers.get("Retry-After") or 2 ** attempt))
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return []


def _vcf_line(v):
    """VCF-style input, or Ensembl's 'chrom start end ref/alt strand' for MAF-style indels written with '-'."""
    ref, alt = v.get("ref") or "-", v.get("alt") or "-"
    if "-" not in (ref, alt):
        return f"{v['chrom']} {v['pos']} . {ref} {alt} . . ."
    if ref == "-":  # insertion after pos
        return f"{v['chrom']} {v['pos'] + 1} {v['pos']} -/{alt} 1"
    return f"{v['chrom']} {v['pos']} {v['pos'] + len(ref) - 1} {ref}/- 1"


def _gnomad_af(result, variant):
    """Highest global gnomAD (exomes / genomes) frequency of the variant's ALT allele, or None if unknown."""
    ref, alt = variant.get("ref") or "", variant.get("alt") or ""
    indel = ref and alt and ref[0] == alt[0] and len(ref) != len(alt)
    allele = (alt[1:] or "-") if indel else alt
    best = None
    for c in result.get("colocated_variants") or []:
        freqs = c.get("frequencies") or {}
        entry = freqs.get(allele) or (next(iter(freqs.values())) if len(freqs) == 1 else None)
        for key in ("gnomade", "gnomadg"):
            if entry and entry.get(key) is not None:
                best = max(best or 0, float(entry[key]))
    return best


def from_vep(result):
    t = (result.get("transcript_consequences") or [{}])[0]
    term, cls = classify(t.get("consequence_terms") or [result.get("most_severe_consequence")])
    clinvar = sorted({s for c in result.get("colocated_variants") or [] for s in c.get("clin_sig") or []})
    return {
        "gene": t.get("gene_symbol"),
        "consequence": term,
        "classification": cls,
        "hgvsp": t.get("hgvsp"),
        "hgvsc": t.get("hgvsc"),
        "protein_change": short_protein(t.get("hgvsp")),
        "exon": int(str(t["exon"]).split("/")[0]) if t.get("exon") else None,
        "intron": int(str(t["intron"]).split("/")[0]) if t.get("intron") else None,
        "transcript": t.get("transcript_id"),
        "clinvar": clinvar,
        "alphamissense": (t.get("alphamissense") or {}).get("am_pathogenicity"),
        "revel": t.get("revel"),
        "cadd_phred": t.get("cadd_phred"),
    }
