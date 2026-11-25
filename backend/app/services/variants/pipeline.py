"""the deterministic tools (stages 1-5 and the rule-based ranking of stage 7; no LLM here):

  1 VCF QC tool            configurable hard filter (PASS, DP >= 20, ALT reads >= 5, VAF >= 5 %); germline removal
                           (gnomAD AF > 1 % or present in the matched normal); annotation; protein-altering only.
                           Every failed variant is kept in an audit table with its reason.
  2 Entity resolution      HGNC / Entrez / UniProt for the genes, HGVS for the variants, OncoTree for the cancer
  3 Variant tiering tool   Tier 1 subtype-specific · Tier 2 known hotspot or evidence in other cancers ·
                           Tier 3 score S = wF·F + wR·R + wB·B; below the threshold -> "low priority" (kept).
                           Every tiered variant continues downstream (no top-N cut here).
  4 DGI retrieval          for every tiered variant: CIViC / OncoKB (variant level), DGIdb and ChEMBL (gene level)
  5 Evidence classification merge per drug, link drug -> variant -> sources, check co-occurring variants together,
                           flag resistance and conflicts, drop unsupported claims; split into two tracks:
                             established  CIViC A/B (OncoKB 1/2) in this subtype for a Tier 1 variant
                             exploratory  levels C-E, other cancer types, DGIdb / ChEMBL-only links, Tier 3 variants
  6 (agents.py)            the exploration agents (LLM) work on the exploratory track only; the literature search
                           they use is run here, so it can count as "literature support" for ranking
  7 Ranking                each track separately: established by subtype match, level, resistance; exploratory by
                           Tier 3 score and literature support. Top drugs in the report, the full lists in the appendix.
"""

import logging
import re

from flask import current_app

from ... import tracking
from ...errors import APIError

from ...extensions import db
from ...reference import lookup
from ...reference.importers import drug_synonym_index, lookup_drug
from ...reference.models import (
    CancerHotspot, CivicEvidence, Drug, Gene, IntogenDriver, SubtypeGeneFrequency,
)
from ...reference.normalise import resolve_entities
from ...reference.text import norm, strip_salt
from ..biomed.dgidb import DGIdbClient
from ..biomed.pubtator import PubTatorClient
from .biology import co_mutation_patterns, pathways_of
from .cancer_types import MATCH_LABELS, disease_match, oncokb_in_subtype
from .literature import Query as LiteratureQuery, retrieve
from .matching import SPECIFICITY, match_profile
from .oncokb import ESTABLISHED as ONCOKB_ESTABLISHED, OncoKBClient
from .parse import PROTEIN_ALTERING, TRUNCATING
from .vep import VEPClient
from .. import safety

logger = logging.getLogger(__name__)

WORKFLOW = {"key": "vcf", "number": 4, "label": "Patient VCF + cancer type → candidate drugs"}
LEVELS = ["A", "B", "C", "D", "E"]
LEVEL_LABELS = {"A": "validated", "B": "clinical", "C": "case study", "D": "preclinical", "E": "inferential"}
SENSITIVE = {"Sensitivity/Response"}
RESISTANT = {"Resistance", "Reduced Sensitivity"}
# A truncating variant matched as a class ("STK11 Mutation") makes the variant relevant (tiering), but a drug is
# only established on variant-specific evidence.
TIERING_SPECIFIC = ("exact", "codon", "exon", "class")
ESTABLISHED_SPECIFIC = ("exact", "codon", "exon")
MAX_ONCOKB_LOOKUPS = 30
MAX_LISTED = 1500
DGIDB_PER_GENE = 5

# Drug score points (stage 7)
LEVEL_POINTS = {"A": 50, "B": 40, "C": 25, "D": 15, "E": 10}
ONCOKB_POINTS = {"LEVEL_1": 50, "LEVEL_2": 45, "LEVEL_3A": 30, "LEVEL_3B": 25, "LEVEL_4": 15}
SUBTYPE_POINTS = {3: 30, 2: 15, 1: 0}
SPECIFICITY_POINTS = {"exact": 15, "codon": 12, "exon": 10, "class": 8, "gene": 3}
GENE_LEVEL_POINTS = {"ChEMBL": 20, "DGIdb": 10}  # drug -> gene links without variant-level evidence
LITERATURE_POINTS, LITERATURE_MAX = 5, 15
STRONG_RESISTANCE_PENALTY = -60
WEAK_RESISTANCE_PENALTY = -10
NO_BENEFIT_PENALTY = -5


def label(v):
    return f"{v.get('gene') or '?'} {v.get('protein_change') or v.get('hgvsc') or v.get('consequence') or ''}".strip()


def _safe(status, name, fn, count=len):
    with tracking.span(f"source:{name}", span_type="RETRIEVER") as s:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - a failed source is reported, not raised
            logger.warning("Source %s failed: %s", name, exc)
            status[name] = {"state": "error", "count": 0, "error": str(exc)[:300]}
            tracking.set_outputs(s, status[name])
            return None
        status[name] = {"state": "ok", "count": count(result) if result is not None else 0}
        tracking.set_outputs(s, status[name])
        return result


def _short_error(error):
    """'503 Server Error: Service Unavailable for url: ...' -> '503 Service Unavailable'; timeouts -> 'timed out'."""
    match = re.match(r"(\d{3}) [A-Za-z ]*Error: ([^:]+?)(?: for url|$)", error or "")
    if match:
        return f"{match.group(1)} {match.group(2).strip()}"
    return "timed out" if "timed out" in (error or "").lower() else (error or "no answer")[:80]


def _drop(v, stage, reason):
    v["status"], v["dropped_at"], v["reason"] = "dropped", stage, reason


# ---------- Stage 1: VCF QC tool ----------

def qc(variants, info, status, notes):
    """Sets v["status"] = "dropped" (with the reason, for the audit table) or "qc_passed"."""
    cfg = current_app.config
    has_reads = any(v.get("depth") is not None for v in variants)
    if not has_reads:
        notes.append("The file has no read depth / allele counts (DP, AD), so the depth, ALT-read and VAF filters "
                     "were skipped.")
    if not any(v.get("normal_alt_reads") is not None for v in variants):
        notes.append("No matched-normal read counts in the file; germline variants are removed by gnomAD frequency only.")

    survivors = []
    for v in variants:
        dp, alt, vaf = v.get("depth"), v.get("alt_reads"), v.get("vaf")
        if v["filter"] != "PASS":
            _drop(v, "qc", f"FILTER is {v['filter']}, not PASS")
        elif has_reads and (dp is None or dp < cfg["VCF_QC_MIN_DEPTH"]):
            _drop(v, "qc", f"depth {dp} < {cfg['VCF_QC_MIN_DEPTH']}")
        elif has_reads and (alt is None or alt < cfg["VCF_QC_MIN_ALT_READS"]):
            _drop(v, "qc", f"{alt} ALT reads < {cfg['VCF_QC_MIN_ALT_READS']}")
        elif has_reads and (vaf is None or vaf < cfg["VCF_QC_MIN_VAF"]):
            _drop(v, "qc", f"tumour VAF {vaf:.1%} < {cfg['VCF_QC_MIN_VAF']:.0%}" if vaf is not None else "no VAF")
        elif (v.get("normal_alt_reads") or 0) > cfg["VCF_QC_MAX_NORMAL_ALT_READS"]:
            _drop(v, "germline", f"germline: present in the matched normal ({v['normal_alt_reads']} ALT reads)")
        else:
            survivors.append(v)

    _annotate(survivors, info, status)
    passed = []
    for v in survivors:
        if v.get("status") == "dropped":
            continue
        if (v.get("gnomad_af") or 0) > cfg["VCF_QC_MAX_POPULATION_AF"]:
            _drop(v, "germline", f"germline: gnomAD AF {v['gnomad_af']:.1%} > {cfg['VCF_QC_MAX_POPULATION_AF']:.0%}")
        elif v.get("classification") not in PROTEIN_ALTERING:
            _drop(v, "consequence", f"not protein-altering ({v.get('consequence') or 'non-coding'})")
        else:
            v["status"] = "qc_passed"
            v["gene"] = (v.get("gene") or "").upper() or None
            passed.append(v)
    return passed


def _annotate(variants, info, status):
    """VEP for every variant that passed the read filters: the full annotation for an unannotated file, otherwise
    only the missing fields (gnomAD frequency and pathogenicity scores are always taken from VEP)."""
    cfg = current_app.config
    if not variants:
        status["vep"] = {"state": "skipped", "count": 0}
        return
    url = cfg["VEP_GRCH37_URL"] if (info.get("assembly") or "GRCh38") == "GRCh37" else cfg["VEP_URL"]
    vep = VEPClient(url, timeout=max(cfg["HTTP_TIMEOUT"], 60))
    todo = variants[: cfg["MAX_VCF_VARIANTS"]]
    _safe(status, "vep", lambda: vep.annotate(todo, fill_only=info.get("annotation") is not None), count=lambda n: n)
    for v in variants[cfg["MAX_VCF_VARIANTS"]:]:
        _drop(v, "qc", f"over the {cfg['MAX_VCF_VARIANTS']}-variant annotation limit")
    if status["vep"]["state"] == "error" and info.get("annotation") is None:
        # The 503 / timeout the public server sometimes gives is reported to the user, not as a 500
        raise APIError("Ensembl VEP, the variant annotation service, is not responding right now "
                       f"({_short_error(status['vep']['error'])}), and it has no stored answer for this file. "
                       "Please try again in a few minutes.", 503)


# ---------- Stage 2: entity resolution ----------

def resolve(passed):
    genes = {}
    for v in passed:
        if v["gene"] not in genes:
            entry = resolve_entities([v["gene"]], "gene")[0] if v["gene"] else {"name": None}
            gene = db.session.get(Gene, entry["id"]) if entry.get("id") else None
            genes[v["gene"]] = {
                "symbol": entry["name"], "hgnc_id": entry.get("id"), "matched_by": entry.get("matched_by"),
                "full_name": gene.name if gene else None, "entrez_id": gene.entrez_id if gene else None,
                "uniprot_ids": (gene.uniprot_ids or []) if gene else [],
            }
        v["resolved"] = genes[v["gene"]]
        v["notation"] = f"{v['gene']} p.{v['protein_change']}" if v.get("protein_change") else label(v)


# ---------- Stage 3: variant tiering tool ----------

def civic_matches(variants, cancer):
    """CIViC evidence whose molecular profile the variants satisfy, one record per evidence item."""
    genes = sorted({v["gene"] for v in variants if v.get("gene")})
    if not genes:
        return []
    rows = db.session.scalars(db.select(CivicEvidence).where(CivicEvidence.gene_symbol.in_(genes))).all()
    out, seen = [], set()
    for r in rows:
        if r.evidence_id in seen:
            continue
        seen.add(r.evidence_id)
        matched = match_profile(r.components, variants)
        if not matched:
            continue
        specificity, variant_ids = matched
        out.append({
            "evidence_id": r.evidence_id, "molecular_profile": r.molecular_profile, "variant_ids": variant_ids,
            "specificity": specificity, "combination": r.components["op"] == "AND" and len(r.components["parts"]) > 1,
            "disease": r.disease, "doid": r.doid, "disease_match": disease_match(r.doid, cancer),
            "therapies": r.therapies, "therapy_interaction": r.therapy_interaction, "direction": r.direction,
            "level": r.level, "significance": r.significance, "statement": r.statement, "pmid": r.pmid,
            "citation": r.citation, "rating": r.rating, "url": r.url,
        })
    return out


def oncokb_matches(variants, cancer, status):
    cfg = current_app.config
    client = OncoKBClient(cfg["ONCOKB_URL"], cfg["ONCOKB_API_TOKEN"], timeout=cfg["HTTP_TIMEOUT"])
    if not client.enabled:
        status["oncokb"] = {"state": "disabled", "count": 0, "error": "ONCOKB_API_TOKEN not set"}
        return []
    candidates = [v for v in variants if v.get("protein_change")][:MAX_ONCOKB_LOOKUPS]

    def fetch():
        out = []
        for v in candidates:
            for t in client.annotate(v["gene"], v["protein_change"], cancer["code"]):
                out.append({**t, "variant_ids": [v["id"]], "subtype": oncokb_in_subtype(t, cancer)})
        return out

    return _safe(status, "oncokb", fetch) or []


def _position(protein_change):
    match = re.match(r"^[A-Z*]?(\d+)", protein_change or "")
    return int(match.group(1)) if match else None


def hotspot_for(v, hotspots):
    """The cancerhotspots.org hotspot the variant falls on: same residue for a missense or truncating change, an
    overlapping region for an in-frame indel."""
    position = _position(v.get("protein_change"))
    if position is None:
        return None
    indel = v.get("classification") in ("inframe_insertion", "inframe_deletion")
    for h in hotspots.get(v["gene"], []):
        if (h.hotspot_type == "in-frame indel") == indel and h.start <= position <= h.end:
            return {"residue": h.residue, "type": h.hotspot_type, "tumor_count": h.tumor_count}
    return None


def functional_impact(v):
    """F (0–1): AlphaMissense > REVEL > CADD (phred / 40); 0.9 for truncating variants; 0.5 when unknown."""
    if v.get("classification") in TRUNCATING:
        return 0.9, "predicted loss of function"
    for key, source in (("alphamissense", "AlphaMissense"), ("revel", "REVEL")):
        if v.get(key) is not None:
            return round(float(v[key]), 3), source
    if v.get("cadd_phred") is not None:
        return round(min(float(v["cadd_phred"]) / 40, 1), 3), "CADD"
    return 0.5, "no score available"


def subtype_frequencies(genes, cancer):
    """Gene -> (frequency, OncoTree code it came from): the most specific cBioPortal study in the subtype's lineage."""
    rows = db.session.execute(
        db.select(SubtypeGeneFrequency.gene_symbol, SubtypeGeneFrequency.oncotree_code, SubtypeGeneFrequency.frequency)
        .where(SubtypeGeneFrequency.gene_symbol.in_(genes), SubtypeGeneFrequency.oncotree_code.in_(cancer["lineage"]))
    ).all()
    order = {code: i for i, code in enumerate(cancer["lineage"])}
    out = {}
    for gene, code, freq in sorted(rows, key=lambda r: order[r[1]]):
        out.setdefault(gene, (freq, code))
    return out


def tier3_score(v, freq, roles, cfg):
    """S = wF·F + wR·R + wB·B with every component in 0–1; returns the components for the report."""
    weights, parts = cfg["VCF_TIER3_WEIGHTS"], cfg["VCF_TIER3_B_PARTS"]
    f, f_source = functional_impact(v)
    frequency, freq_source = freq or (0.0, None)
    r = round(min(1.0, frequency / cfg["VCF_TIER3_R_SATURATION"]), 3)
    role = roles.get(v["gene"])
    pathways = pathways_of(v["gene"])
    tsg_loss = v["classification"] in TRUNCATING and role == "LoF"
    b_parts = {"cancer_role": parts["cancer_role"] if role else 0, "pathway": parts["pathway"] if pathways else 0,
               "tsg_loss": parts["tsg_loss"] if tsg_loss else 0}
    b = round(min(1.0, sum(b_parts.values())), 3)
    s = round(weights["F"] * f + weights["R"] * r + weights["B"] * b, 3)
    return {
        "F": f, "F_source": f_source,
        "R": r, "R_frequency": round(frequency, 4), "R_source": f"cBioPortal {freq_source}" if freq_source else "not mutated in the reference cohort",
        "B": b, "B_parts": b_parts, "role": role, "pathways": pathways,
        "weights": weights, "S": s,
    }


def _evidence_key(e):
    """Sort key for the strongest evidence: level, then disease match, then specificity."""
    return (LEVELS.index(e["level"]) if e.get("level") in LEVELS else 9, -e["disease_match"],
            SPECIFICITY.index(e["specificity"]))


def tier(passed, cancer, status):
    """Tier 1 / 2 / 3 for every QC-passed variant, or "low priority" below the Tier 3 threshold (kept).
    Returns (tiered variants, CIViC matches, OncoKB matches)."""
    cfg = current_app.config
    genes = sorted({v["gene"] for v in passed if v.get("gene")})
    hotspots = {}
    for h in db.session.scalars(db.select(CancerHotspot).where(CancerHotspot.gene_symbol.in_(genes))):
        hotspots.setdefault(h.gene_symbol, []).append(h)
    roles, subtype_tsgs = {}, set()
    for gene, role, cancer_type in db.session.execute(
            db.select(IntogenDriver.gene_symbol, IntogenDriver.role, IntogenDriver.cancer_type)
            .where(IntogenDriver.gene_symbol.in_(genes))):
        if role in ("Act", "LoF") and roles.get(gene) != "LoF":
            roles[gene] = role  # IntOGen cancer gene role (the Cancer Gene Census needs a licence)
        if role == "LoF" and cancer_type in cancer["lineage"]:
            subtype_tsgs.add(gene)  # a tumour suppressor driver in this subtype (or a parent type)
    frequencies = subtype_frequencies(genes, cancer)

    civic = civic_matches(passed, cancer)
    # Gene-level evidence for a tumour suppressor ("STK11 Mutation", "... Loss-of-function") is about inactivating
    # variants, so a truncating variant in a LoF gene matches it as a class, which counts for tiering.
    by_id = {v["id"]: v for v in passed}
    for e in civic:
        if e["specificity"] == "gene" and all(
                by_id[vid]["classification"] in TRUNCATING and roles.get(by_id[vid]["gene"]) == "LoF"
                for vid in e["variant_ids"]):
            e["specificity"] = "class"
    status["civic"] = {"state": "ok", "count": len(civic)}
    oncokb = oncokb_matches(passed, cancer, status)

    by_variant = {}  # variant-specific evidence only: gene-level matches ("EGFR Mutation") don't tier a missense
    for e in civic:
        if e["specificity"] in TIERING_SPECIFIC:
            for vid in e["variant_ids"]:
                by_variant.setdefault(vid, []).append(e)
    oncokb_by_variant = {}
    for t in oncokb:
        for vid in t["variant_ids"]:
            oncokb_by_variant.setdefault(vid, []).append(t)

    for v in passed:
        evidence = sorted(by_variant.get(v["id"], []), key=_evidence_key)
        kb = oncokb_by_variant.get(v["id"], [])
        v["hotspot"] = hotspot_for(v, hotspots)
        v["subtype_frequency"] = (frequencies.get(v["gene"]) or (None,))[0]
        v["pathways"] = pathways_of(v["gene"])
        v["driver_role"] = roles.get(v["gene"])  # IntOGen: Act (oncogene) / LoF (tumour suppressor)
        best = evidence[0] if evidence else None
        v["best_evidence"] = best and {"level": best["level"], "specificity": best["specificity"],
                                       "disease_match": MATCH_LABELS[best["disease_match"]],
                                       "significance": best["significance"], "evidence_id": best["evidence_id"]}
        # Truncating mutation in a tumour suppressor driver of this subtype: the TSG counterpart of an oncogene
        # hotspot (OncoKB annotates such truncations as a class); these are rarely recurrent at one residue.
        v["tsg_truncation"] = v["classification"] in TRUNCATING and v["gene"] in subtype_tsgs
        subtype_civic = [e for e in evidence if e["level"] in ("A", "B") and e["disease_match"] == 3]
        subtype_kb = [t for t in kb if t["subtype"] and (t["level"] in ONCOKB_ESTABLISHED or t["level"] == "LEVEL_R1")]
        if subtype_civic or subtype_kb:
            v["tier"] = 1
            first = subtype_civic[0] if subtype_civic else None
            v["tier_reason"] = (f"CIViC level {first['level']} {first['significance'].lower()} evidence in "
                                f"{cancer['code']}" + (f" ({first['molecular_profile']}: truncating variant in a tumour "
                                                       "suppressor)" if first["specificity"] == "class" else "")
                                if first else f"OncoKB {subtype_kb[0]['level']}")
        elif v["hotspot"] or v["tsg_truncation"] or evidence or kb:
            v["tier"] = 2
            parts = []
            if v["hotspot"]:
                parts.append(f"known hotspot {v['gene']} {v['hotspot']['residue']} "
                             f"({v['hotspot']['tumor_count']} tumours, cancerhotspots.org)")
            if v["tsg_truncation"]:
                parts.append(f"truncating mutation in {v['gene']}, a tumour suppressor driver in {cancer['code']} "
                             "(IntOGen); likely oncogenic")
            if best:
                parts.append(f"CIViC level {best['level']} in {MATCH_LABELS[best['disease_match']]}")
            v["tier_reason"] = "; ".join(parts) or "OncoKB evidence outside this subtype"
        else:
            score = tier3_score(v, frequencies.get(v["gene"]), roles, cfg)
            v["tier3"] = score
            formula = (f"S {score['S']:.2f} = {score['weights']['F']}·F {score['F']:.2f} + "
                       f"{score['weights']['R']}·R {score['R']:.2f} + {score['weights']['B']}·B {score['B']:.2f}")
            if score["S"] < cfg["VCF_TIER3_MIN_SCORE"]:
                v["tier"], v["status"] = None, "low_priority"
                v["reason"] = f"low priority: {formula} < {cfg['VCF_TIER3_MIN_SCORE']}"
                continue
            v["tier"], v["tier_reason"] = 3, formula
        v["status"], v["reason"] = f"tier{v['tier']}", v["tier_reason"]
    tiered = [v for v in passed if v.get("tier")]
    tiered.sort(key=lambda v: (v["tier"], _evidence_key(by_variant[v["id"]][0]) if v["id"] in by_variant else (9, 0, 9),
                               -(v.get("tier3") or {}).get("S", 0), -((v.get("hotspot") or {}).get("tumor_count") or 0)))
    return tiered, civic, oncokb


# ---------- Stage 4: DGI retrieval (every tiered variant) ----------

def gene_context(genes, status):
    """Gene-level links for the tiered genes: DGIdb interactions (approved drugs) and ChEMBL mechanisms."""
    cfg = current_app.config
    releases = lookup.latest_releases()
    dgidb = DGIdbClient(cfg["DGIDB_URL"], cfg["HTTP_TIMEOUT"])
    interactions = _safe(status, "dgidb", lambda: dgidb.fetch_interactions(genes=genes)) or []
    per_gene = {}
    for it in sorted((i for i in interactions if i.get("drug_approved")), key=lambda i: -(i["score"] or 0)):
        per_gene.setdefault(it["gene"], [])
        if len(per_gene[it["gene"]]) < DGIDB_PER_GENE and strip_salt(it["drug"]) not in {
                strip_salt(x["drug"]) for x in per_gene[it["gene"]]}:
            per_gene[it["gene"]].append(it)
    interactions = [it for rows in per_gene.values() for it in rows]
    if status.get("dgidb", {}).get("state") == "ok":
        status["dgidb"]["count"] = len(interactions)
    if "chembl" in releases:
        mechanisms = _safe(status, "chembl", lambda: lookup.chembl_mechanisms(genes, [], limit=80)) or []
    else:
        status["chembl"] = {"state": "disabled", "count": 0, "error": "not imported"}
        mechanisms = []
    return interactions, mechanisms


# ---------- Stage 5: evidence items and classification ----------

def variant_url(gene, in_civic):
    """Link for a patient-variant item: the gene's CIViC page when CIViC has evidence for the variant (CIViC's
    /links/entrez_name/ redirect; its old search URL now lands on the home page), otherwise the Ensembl gene page."""
    if in_civic:
        return f"https://civicdb.org/links/entrez_name/{gene}"
    return f"https://www.ensembl.org/Homo_sapiens/Gene/Summary?g={gene}"


def build_items(tiered, civic, oncokb, interactions, mechanisms, releases):
    items = []
    civic_variants = {vid for e in civic for vid in e["variant_ids"]}

    def add(item):
        item["id"] = f"E{len(items) + 1}"
        items.append(item)
        return item["id"]

    for v in tiered:
        v["evidence_id"] = add({
            "kind": "variant", "source": v.get("annotation_source") or "VCF", "variant_id": v["id"],
            "gene": v["gene"], "notation": v["notation"], "protein_change": v.get("protein_change"),
            "hgvsc": v.get("hgvsc"), "consequence": v.get("consequence"), "exon": v.get("exon"),
            "vaf": v.get("vaf"), "clinvar": v.get("clinvar") or [], "tier": v["tier"], "tier_reason": v["tier_reason"],
            "hotspot": v.get("hotspot"), "tier3": v.get("tier3"), "pathways": v.get("pathways"),
            "subtype_frequency": v.get("subtype_frequency"),
            "url": variant_url(v["gene"], v["id"] in civic_variants),
        })
    release = releases["civic"].version if "civic" in releases else None
    for e in civic:
        e["item_id"] = add({"kind": "civic", "source": "CIViC", "release": release, **e,
                            "disease_match_label": MATCH_LABELS[e["disease_match"]],
                            "pmids": [e["pmid"]] if e.get("pmid") else []})
    for t in oncokb:
        t["item_id"] = add({"kind": "oncokb", "source": "OncoKB", **t,
                            "url": f"https://www.oncokb.org/gene/{t['gene']}/{t['protein_change']}"})
    for it in interactions:
        it["item_id"] = add({"kind": "interaction", "source": "DGIdb", **{k: it[k] for k in (
            "gene", "drug", "interaction_types", "score", "sources", "pmids", "drug_approved")},
            "url": f"https://dgidb.org/genes/{it['gene']}"})
    for m in mechanisms:
        m["item_id"] = add({"kind": "mechanism", "source": "ChEMBL",
                            "release": releases["chembl"].version if "chembl" in releases else None, **m})
    return items


def _drug_names(therapies, interaction):
    """CIViC therapies -> candidate drugs: a combination is one candidate, substitutes are separate ones."""
    if interaction == "Combination" and len(therapies) > 1:
        return [" + ".join(sorted(therapies))]
    return list(therapies)


def _display_name(name):
    """DGIdb / ChEMBL write names in capitals; show them like CIViC does."""
    return name.title() if name.isupper() else name


def classify(tiered, civic, oncokb, interactions, mechanisms):
    """Merge the evidence per drug, validate it and put each drug on one track. Returns the candidates
    (unranked) and the strong resistance-only drugs."""
    ids = {v["id"] for v in tiered}
    by_gene = {}
    for v in tiered:
        by_gene.setdefault(v["gene"], []).append(v["id"])
    candidates = {}

    def candidate(name):
        return candidates.setdefault(strip_salt(name), {
            "drug": _display_name(name), "sensitivity": [], "resistance": [], "no_benefit": [], "oncokb": [],
            "gene_links": [], "variant_ids": []})

    for e in civic:
        hit = [vid for vid in e["variant_ids"] if vid in ids]
        if not hit:
            continue
        entry = {**{k: e[k] for k in ("level", "specificity", "disease_match", "rating", "pmid", "significance",
                                      "disease", "molecular_profile", "combination")},
                 "item_id": e["item_id"], "variant_ids": hit}
        for drug in _drug_names(e["therapies"], e["therapy_interaction"]):
            c = candidate(drug)
            if e["significance"] in SENSITIVE and e["direction"] == "Supports":
                c["sensitivity"].append(entry)
                c["variant_ids"] += hit
            elif e["significance"] in RESISTANT and e["direction"] == "Supports":
                c["resistance"].append(entry)
            elif e["significance"] in SENSITIVE:  # "does not support" a response
                c["no_benefit"].append(entry)
    for t in oncokb:
        if not set(t["variant_ids"]) & ids:
            continue
        c = candidate(t["drug"])
        c["oncokb"].append({"level": t["level"], "significance": t["significance"], "item_id": t["item_id"],
                            "variant_ids": t["variant_ids"], "subtype": t["subtype"]})
        if t["significance"] != "Resistance":
            c["variant_ids"] += t["variant_ids"]
    # gene-level links (exploratory track only): ChEMBL mechanisms and DGIdb interactions for the tiered genes
    for source, rows in (("ChEMBL", mechanisms), ("DGIdb", interactions)):
        for r in rows:
            gene_ids = by_gene.get(r["gene"], [])
            if not gene_ids:
                continue
            c = candidate(r["drug"])
            c["gene_links"].append({"source": source, "gene": r["gene"], "item_id": r["item_id"], "variant_ids": gene_ids,
                                    "label": r.get("action_type") or ", ".join(r.get("interaction_types") or [])})
            c["variant_ids"] += gene_ids
    return candidates


def _best(entries):
    return min(entries, key=lambda e: (LEVELS.index(e["level"]) if e["level"] in LEVELS else 9, -e["disease_match"],
                                       SPECIFICITY.index(e["specificity"]))) if entries else None


def rank(candidates, tiered, mechanisms, literature):
    """Stage 7: validate, score and rank each track separately."""
    cfg = current_app.config
    variants = {v["id"]: v for v in tiered}
    labels = {v["id"]: v["notation"] for v in tiered}
    drug_index = drug_synonym_index() if db.session.scalar(db.select(Drug.chembl_id).limit(1)) else {}
    tracks = {"established": [], "exploratory": []}
    resistance_only = []
    for key, c in candidates.items():
        responds = c["sensitivity"] or any(o["significance"] != "Resistance" for o in c["oncokb"]) or c["gene_links"]
        _validate(c, labels)
        if not responds:
            if c["strong_resistance"]:  # listed so the report can say why the drug should be avoided
                resistance_only.append(c)
            continue
        _score(c, variants, labels, drug_index, mechanisms, literature.get(key, []))
        tracks[c["track"]].append(c)
    _inherit_resistance(candidates, tracks)
    for name, rows in tracks.items():
        # resistance down-ranks within the track; then by score
        rows.sort(key=lambda c: (bool(c["strong_resistance"]), -c["score"], c["drug"]))
        for i, c in enumerate(rows, start=1):
            c["rank"] = i
            c["top"] = i <= cfg["VCF_TOP_PER_TRACK"]
    return tracks, resistance_only


def _inherit_resistance(candidates, tracks):
    """A regimen inherits strong resistance from any of its drugs: if T790M makes erlotinib ineffective, it makes
    "Erlotinib + Ramucirumab" ineffective too, although CIViC records the resistance for erlotinib alone."""
    resistant = {key: c for key, c in candidates.items() if c.get("strong_resistance") and " + " not in c["drug"]}
    for rows in tracks.values():
        for c in rows:
            if c["strong_resistance"] or " + " not in c["drug"]:
                continue
            hits = [resistant[strip_salt(part)] for part in c["drug"].split(" + ") if strip_salt(part) in resistant]
            if not hits:
                continue
            via = hits[0]
            c["strong_resistance"] = True
            c["strong_resistance_text"] = via["strong_resistance_text"]
            c["resistance_variant_ids"] = list(dict.fromkeys(c["resistance_variant_ids"] + via["resistance_variant_ids"]))
            c["resistance_notes"] = [f"{via['drug']} in this regimen: {n}" for n in via["resistance_notes"][:2]] \
                + c["resistance_notes"]
            c["score_parts"].append({"label": f"resistance to {via['drug']} in the regimen",
                                     "points": STRONG_RESISTANCE_PENALTY})
            c["score"] += STRONG_RESISTANCE_PENALTY
            c["track_reason"] += (f"; down-ranked: resistance from the patient's {via['strong_resistance_text']} "
                                  f"to {via['drug']}")
            c["evidence_ids"] = list(dict.fromkeys(c["evidence_ids"] + via["evidence_ids"][:4]))


def _validate(c, labels):
    sens, res = c["sensitivity"], c["resistance"]
    res_kb = [o for o in c["oncokb"] if o["significance"] == "Resistance"]

    def names(vids):
        return ", ".join(dict.fromkeys(labels[v] for v in vids if v in labels))

    c["variant_ids"] = list(dict.fromkeys(c["variant_ids"]))
    c["variants"] = [labels[v] for v in c["variant_ids"] if v in labels]
    strong = [e for e in res if e["level"] in ("A", "B", "C") and e["disease_match"] >= 2] + res_kb
    weak = [e for e in res if e not in strong]
    c["strong_resistance"] = bool(strong)
    c["resistance_variant_ids"] = list(dict.fromkeys(v for e in res + res_kb for v in e["variant_ids"]))
    c["resistance_notes"] = [
        f"{names(e['variant_ids'])}: " + (f"CIViC level {e['level']} resistance ({e['disease']})" if "disease" in e
                                         else f"OncoKB {e['level']}") for e in res + res_kb]
    # Co-occurring variants checked together (L858R + T790M, T790M + C797S)
    response_ids = list(dict.fromkeys(v for e in sens for v in e["variant_ids"]))
    others = [v for v in c["resistance_variant_ids"] if v not in response_ids]
    c["co_occurrence"] = []
    if others and response_ids:
        c["co_occurrence"].append(f"response evidence for {names(response_ids)}, but the co-occurring "
                                  f"{names(others)} confers resistance")
    for e in sens + res:
        if e.get("combination"):
            c["co_occurrence"].append(f"evidence for the combination {e['molecular_profile']} (all present)")
    c["co_occurrence"] = list(dict.fromkeys(c["co_occurrence"]))
    c["conflicts"] = [f"{len(c['no_benefit'])} evidence item(s) report no response"] if c["no_benefit"] else []
    if weak:
        c["conflicts"].append("weaker resistance evidence (preclinical, or another tumour type)")
    c["strong_resistance_text"] = names(v for e in strong for v in e["variant_ids"])
    c["evidence_ids"] = list(dict.fromkeys(
        [e["item_id"] for e in sens + c["oncokb"] + res + c["no_benefit"]] + [g["item_id"] for g in c["gene_links"]]))
    c["pmids"] = list(dict.fromkeys(e["pmid"] for e in sens + res if e.get("pmid")))[:15]


def _score(c, variants, labels, drug_index, mechanisms, papers):
    sens = c["sensitivity"]
    resp_kb = [o for o in c["oncokb"] if o["significance"] != "Resistance"]
    best = _best(sens)
    c["best"] = best and {"level": best["level"], "disease_match": MATCH_LABELS[best["disease_match"]],
                          "specificity": best["specificity"], "disease": best["disease"]}
    c["oncokb_level"] = min((o["level"] for o in resp_kb), default=None)
    response_variants = [variants[v] for e in sens + resp_kb for v in e["variant_ids"] if v in variants]
    established = (
        (best and best["level"] in ("A", "B") and best["disease_match"] == 3 and best["specificity"] in ESTABLISHED_SPECIFIC
         and any(variants[v]["tier"] == 1 for v in best["variant_ids"] if v in variants))
        or (c["oncokb_level"] in ONCOKB_ESTABLISHED and any(o["subtype"] for o in resp_kb)))
    c["track"] = "established" if established else "exploratory"

    parts = []
    if best:
        parts += [(f"CIViC level {best['level']}", LEVEL_POINTS.get(best["level"], 0)),
                  (MATCH_LABELS[best["disease_match"]], SUBTYPE_POINTS[best["disease_match"]]),
                  (f"{best['specificity']} variant match", SPECIFICITY_POINTS[best["specificity"]])]
    if c["oncokb_level"] and ONCOKB_POINTS.get(c["oncokb_level"], 0) > (parts[0][1] if parts else 0):
        kb_subtype = any(o["subtype"] for o in resp_kb)
        parts = [(f"OncoKB {c['oncokb_level']}", ONCOKB_POINTS[c["oncokb_level"]]),
                 ("same cancer type" if kb_subtype else "other tumour type", 30 if kb_subtype else 0),
                 ("exact variant match", SPECIFICITY_POINTS["exact"])]
    if not parts and c["gene_links"]:  # DGIdb / ChEMBL-only: a gene-level link, not variant-level evidence
        source = "ChEMBL" if any(g["source"] == "ChEMBL" for g in c["gene_links"]) else "DGIdb"
        parts.append((f"{source} gene-level link (no variant-level evidence)", GENE_LEVEL_POINTS[source]))
    if len(sens) > 1:
        parts.append((f"{len(sens) - 1} more supporting item(s)", min(10, 2 * (len(sens) - 1))))

    if c["track"] == "exploratory":
        # Variant priority: Tier 1–2 variants count fully, Tier 3 ones by their score S.
        linked = response_variants or [variants[v] for v in c["variant_ids"] if v in variants]
        factor = max((1.0 if v["tier"] in (1, 2) else v["tier3"]["S"]) for v in linked) if linked else 0
        if factor < 1:
            base = sum(points for _, points in parts)
            parts = [(f"evidence {base} × Tier 3 score S {factor:.2f}", round(base * factor))]
            c["tier3_only"] = True
        c["literature"] = papers
        if papers:
            parts.append((f"literature support ({len(papers)} paper{'s' if len(papers) > 1 else ''})",
                          min(LITERATURE_MAX, LITERATURE_POINTS * len(papers))))
    first = c["drug"].split(" + ")[0]
    c["chembl_id"] = lookup_drug(drug_index, first) if drug_index else None
    drug = db.session.get(Drug, c["chembl_id"]) if c["chembl_id"] else None
    c["approved"] = bool(drug and (drug.max_phase or 0) >= 4)
    if c["approved"]:
        parts.append(("marketed drug (ChEMBL phase 4, any indication)", 5))
    if c["strong_resistance"]:
        parts.append(("resistance from the patient's variants", STRONG_RESISTANCE_PENALTY))
    elif c["resistance"]:
        parts.append(("weaker resistance evidence", WEAK_RESISTANCE_PENALTY))
    if c["no_benefit"]:
        parts.append(("reports of no response", max(-15, NO_BENEFIT_PENALTY * len(c["no_benefit"]))))
    c["score_parts"] = [{"label": text, "points": points} for text, points in parts]
    c["score"] = sum(points for _, points in parts)

    if best:
        c["track_reason"] = (f"CIViC level {best['level']} ({LEVEL_LABELS.get(best['level'], '?')}) in "
                             f"{MATCH_LABELS[best['disease_match']]}, {best['specificity']} variant match")
    elif c["oncokb_level"]:
        c["track_reason"] = f"OncoKB {c['oncokb_level']}"
    else:
        c["track_reason"] = "gene-level link only (" + ", ".join(sorted({g["source"] for g in c["gene_links"]})) + ")"
    if c.get("tier3_only"):
        c["track_reason"] += "; Tier 3 variant (prediction)"
    if c["strong_resistance"]:
        c["track_reason"] += f"; down-ranked: resistance from the patient's {c['strong_resistance_text']}"
    c["mechanism"] = next((m["mechanism"] for m in mechanisms if m.get("chembl_id") == c["chembl_id"]), None)
    c["n_evidence"] = len(sens)


# ---------- Stage 6 input: the literature search behind the Literature agent ----------

def literature_search(tiered, candidates, cancer, status, items):
    """Retrieval-augmented literature (literature.py) for Tier 3 variants and level 3–4 (C–E) drug pairs. Kept
    papers become citable publication items, with the abstract sentences that answer the query, and count as
    literature support for the exploratory ranking. Never changes tiers or evidence levels."""
    cfg = current_app.config
    budget = cfg["VCF_LITERATURE_QUERIES"]
    if not budget:
        status["pubtator"] = {"state": "skipped", "count": 0}
        return {}
    by_id = {v["id"]: v for v in tiered}

    def query(kind, key, search, v, drug=None):
        return LiteratureQuery(kind, key, search, v["gene"], (v.get("resolved") or {}).get("entrez_id"),
                               v.get("protein_change"), drug, cancer["name"])

    queries = []
    for v in sorted((v for v in tiered if v["tier"] == 3), key=lambda v: -v["tier3"]["S"]):
        queries.append(query("variant", v["id"], f"{v['gene']} {v.get('protein_change') or ''} lung cancer"
                             .replace("  ", " "), v))
    pairs = []
    for key, c in candidates.items():
        best = _best(c["sensitivity"])
        if best and best["level"] in ("C", "D", "E"):
            v = by_id.get(best["variant_ids"][0])
            drug = re.sub(r"\s+Regimen$", "", c["drug"].split(" + ")[0], flags=re.IGNORECASE)
            if v:
                pairs.append((LEVELS.index(best["level"]), key, f"{v['gene']} AND {drug}", v, drug))
    queries += [query("drug", key, search, v, drug) for _, key, search, v, drug in sorted(pairs, key=lambda p: p[:2])]
    queries = queries[:budget]
    client = PubTatorClient(cfg["PUBTATOR_URL"], cfg["HTTP_TIMEOUT"])
    by_drug, by_variant = {}, {}

    def fetch():
        found, seen = 0, {}
        for (kind, key), papers in retrieve(queries, client, status).items():
            for paper in papers:
                passages = paper.pop("passages")
                if paper["pmid"] not in seen:
                    seen[paper["pmid"]] = f"E{len(items) + 1}"
                    items.append({"id": seen[paper["pmid"]], "kind": "publication", "source": "PubTator",
                                  "url": f"https://pubmed.ncbi.nlm.nih.gov/{paper['pmid']}/", **paper, "passages": []})
                    found += 1
                item = next(i for i in items if i["id"] == seen[paper["pmid"]])
                for p in passages:  # one paper may answer several queries, with different sentences
                    if p not in item["passages"]:
                        item["passages"].append(p)
                item["passages"].sort(key=lambda p: p["n"])
                item["score"] = max(item["score"], paper["score"])
                ref = {"pmid": paper["pmid"], "item_id": seen[paper["pmid"]], "title": paper.get("title"),
                       "score": paper["score"], "sentences": [p["n"] for p in passages]}
                (by_drug if kind == "drug" else by_variant).setdefault(key, []).append(ref)
        return found

    _safe(status, "pubtator", fetch, count=lambda n: n)
    for v in tiered:
        if v["id"] in by_variant:
            v["literature"] = by_variant[v["id"]]
    return by_drug


# ---------- Network, trace, bundle ----------

def build_network(tiered, tracks, resistance_only):
    """gene -> variant -> drug. Response edges from the variants with evidence, resistance edges from the variants
    that cause the resistance (e.g. EGFR T790M -> gefitinib). Only the top drugs of each track are drawn."""
    nodes, edges = {}, {}

    def node(node_id, text, node_type, is_query=False):
        nodes.setdefault(node_id, {"data": {"id": node_id, "label": text, "type": node_type, "is_query": is_query}})

    def edge(source, target, category, label_text, evidence_ids):
        key = (source, target, category)
        if key in edges:
            edges[key]["data"]["evidence_ids"] += [i for i in evidence_ids if i not in edges[key]["data"]["evidence_ids"]]
            return
        edges[key] = {"data": {"id": f"e{len(edges) + 1}", "source": source, "target": target, "category": category,
                               "label": label_text, "evidence_ids": list(evidence_ids)}}

    for v in tiered:
        node(f"gene:{v['gene']}", v["gene"], "gene")
        node(f"variant:{v['id']}", v.get("protein_change") or label(v), "variant", is_query=v["tier"] in (1, 2))
        edge(f"gene:{v['gene']}", f"variant:{v['id']}", "variant_of", f"Tier {v['tier']}", [v["evidence_id"]])
    drawn = [c for rows in tracks.values() for c in rows if c["top"]] + resistance_only
    for c in drawn:
        drug_id = f"drug:{norm(c['drug'])}"
        node(drug_id, c["drug"], "drug")
        for e in c["sensitivity"] + [o for o in c["oncokb"] if o["significance"] != "Resistance"]:
            for vid in e["variant_ids"]:
                edge(f"variant:{vid}", drug_id, "sensitive", e.get("level") or "", [e["item_id"]])
        if not c["sensitivity"]:
            for g in c["gene_links"]:
                for vid in g["variant_ids"]:
                    edge(f"variant:{vid}", drug_id, "gene_link", g["source"], [g["item_id"]])
        for e in c["resistance"] + [o for o in c["oncokb"] if o["significance"] == "Resistance"]:
            for vid in e["variant_ids"]:
                edge(f"variant:{vid}", drug_id, "resistant", e.get("level") or "", [e["item_id"]])
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def _public_candidate(c):
    drop = {"sensitivity", "resistance", "no_benefit", "oncokb", "gene_links"}
    out = {k: v for k, v in c.items() if k not in drop}
    out["gene_links"] = [{"source": g["source"], "gene": g["gene"], "label": g["label"], "item_id": g["item_id"]}
                         for g in c["gene_links"]]
    return out


def run_pipeline(variants, info, cancer, medications=(), clinical=None):
    """variants: parsed records (parse.py); info: the parser's file info; cancer: the resolved cancer type;
    medications: the patient's current medications (names), checked against the candidates in stage 7."""
    status, notes = {}, []
    releases = lookup.latest_releases()
    missing = [s for s in ("civic", "intogen", "hotspots", "cbioportal") if s not in releases]
    if missing:
        raise RuntimeError(f"Reference data needed by this workflow is not imported: {', '.join(missing)}. "
                           f"Run: flask --app run import-data {' '.join(missing)}")
    for i, v in enumerate(variants, start=1):
        v["id"] = f"V{i}"

    with tracking.span("1_qc", inputs={"variants": len(variants)}) as s:
        passed = qc(variants, info, status, notes)
        tracking.set_outputs(s, {"passed": len(passed), "dropped": {
            st: sum(1 for v in variants if v.get("dropped_at") == st) for st in ("qc", "germline", "consequence")}})
    with tracking.span("2_entity_resolution", inputs={"variants": len(passed)}):
        resolve(passed)
    with tracking.span("3_variant_tiering", inputs={"variants": len(passed), "cancer_type": cancer["code"]}) as s:
        tiered, civic, oncokb = tier(passed, cancer, status)
        tracking.set_outputs(s, {v["notation"]: v.get("tier") or "low priority" for v in passed})
    status["civic"]["version"] = releases["civic"].version
    source = f"VCF ({info.get('annotation') or 'Ensembl VEP'} annotation)" if info["format"] == "vcf" else "MAF"
    for v in tiered:
        v["annotation_source"] = source

    items, tracks, resistance_only, availability = [], {"established": [], "exploratory": []}, [], None
    safety_block = {"medications": [], "interactions": [], "checked_drugs": 0, "warned_drugs": 0}
    if tiered:
        genes = sorted({v["gene"] for v in tiered})
        with tracking.span("4_dgi_retrieval", inputs={"genes": genes}):
            interactions, mechanisms = gene_context(genes, status)
        tiered_ids = {v["id"] for v in tiered}
        civic = [e for e in civic if set(e["variant_ids"]) & tiered_ids]
        oncokb = [t for t in oncokb if set(t["variant_ids"]) & tiered_ids]
        with tracking.span("5_evidence_classification") as s:
            items = build_items(tiered, civic, oncokb, interactions, mechanisms, releases)
            candidates = classify(tiered, civic, oncokb, interactions, mechanisms)
            tracking.set_outputs(s, {"candidate_drugs": len(candidates), "evidence_items": len(items)})
        with tracking.span("6_literature_search", span_type="RETRIEVER"):  # input to the Literature agent
            literature = literature_search(tiered, candidates, cancer, status, items)
        with tracking.span("7_ranking") as s:
            tracks, resistance_only = rank(candidates, tiered, mechanisms, literature)
            tracking.set_outputs(s, {name: [(c["drug"], c["score"]) for c in rows[:10]] for name, rows in tracks.items()})
        with tracking.span("7b_safety", inputs={"medications": list(medications)}) as s:
            # ChEMBL warnings and drug–drug interactions (services/safety.py); re-ranks with the safety points
            safety_block = safety.check_candidates(tracks, list(medications), items, status)
            tracking.set_outputs(s, {"interactions": len(safety_block["interactions"]),
                                     "warned_drugs": safety_block["warned_drugs"]})
        if "pbs" in releases:
            ranked = [c for c in tracks["established"] + tracks["exploratory"] if c["top"]]
            pbs = [(c["drug"].split(" + ")[0].upper(), c.get("chembl_id")) for c in ranked][: current_app.config["MAX_PBS_DRUGS"]]
            availability = _safe(status, "pbs", lambda: lookup.pbs_availability(pbs))
        else:
            status["pbs"] = {"state": "disabled", "count": 0, "error": "not imported"}
    else:
        for name in ("dgidb", "chembl", "pubtator", "openfda"):
            status[name] = {"state": "skipped", "count": 0}

    variant_drugs = {v for rows in tracks.values() for c in rows if c["sensitivity"] or c["oncokb"]
                     for v in c["variant_ids"]}
    counts = {
        "total": len(variants),
        "qc_passed": len(passed),
        "tier1": sum(1 for v in tiered if v["tier"] == 1),
        "tier2": sum(1 for v in tiered if v["tier"] == 2),
        "tier3": sum(1 for v in tiered if v["tier"] == 3),
        "low_priority": sum(1 for v in passed if v.get("status") == "low_priority"),
        "dropped": {stage: sum(1 for v in variants if v.get("dropped_at") == stage)
                    for stage in ("qc", "germline", "consequence")},
    }
    established_ok = [c for c in tracks["established"] if not c["strong_resistance"]]
    outcome = {
        "status": "found" if tiered else "none",
        "checked_sources": [k for k, s in status.items() if s.get("state") == "ok" and k not in ("pbs", "vep")],
        "unavailable_sources": [k for k, s in status.items() if s.get("state") == "error"],
    }
    if not established_ok:
        outcome["no_targeted_therapy"] = (
            f"No approved targeted therapy (CIViC level A/B, OncoKB level 1–2) was found for this sample's variants "
            f"in {cancer['name']}.")
    tiered_genes = sorted({v["gene"] for v in tiered})
    return {
        "workflow": WORKFLOW,
        "input": {**{k: info.get(k) for k in ("format", "filename", "tumour_sample", "normal_sample", "annotation",
                                              "example")},
                  "assembly": info.get("assembly") or "GRCh38", "cancer_type": cancer,
                  "medications": list(medications), "clinical": clinical or {}},
        "parameters": _parameters(),
        "outcome": outcome,
        "trace": _trace(counts, tiered, status, tracks, cancer),
        "variants": {
            "counts": counts,
            "tiered": [_public(v, full=True) for v in tiered],
            "low_priority": [_public(v, full=True) for v in passed if v.get("status") == "low_priority"],
            "qc_audit": [_public(v) for v in variants if v.get("status") == "dropped"][:MAX_LISTED],
        },
        # tiered variants without a variant-level targeted drug (e.g. TP53, STK11, RB1), shown as context
        "co_mutations": [_public(v, full=True) for v in tiered if v["id"] not in variant_drugs],
        "co_mutation_patterns": co_mutation_patterns(tiered_genes),
        "items": items,
        "tracks": {name: [_public_candidate(c) for c in rows] for name, rows in tracks.items()},
        "resistance_only": [_public_candidate(c) for c in resistance_only],
        "network": build_network(tiered, tracks, resistance_only) if tiered else {"nodes": [], "edges": []},
        "safety": safety_block,
        "australian_availability": availability,
        "limitations": _limitations(info, status, notes, safety_block),
        "source_status": status,
    }


def _parameters():
    cfg = current_app.config
    return {"qc": {"min_depth": cfg["VCF_QC_MIN_DEPTH"], "min_alt_reads": cfg["VCF_QC_MIN_ALT_READS"],
                   "min_vaf": cfg["VCF_QC_MIN_VAF"], "max_population_af": cfg["VCF_QC_MAX_POPULATION_AF"],
                   "max_normal_alt_reads": cfg["VCF_QC_MAX_NORMAL_ALT_READS"]},
            "tier3": {"weights": cfg["VCF_TIER3_WEIGHTS"], "b_parts": cfg["VCF_TIER3_B_PARTS"],
                      "r_saturation": cfg["VCF_TIER3_R_SATURATION"], "min_score": cfg["VCF_TIER3_MIN_SCORE"]}}


def _public(v, full=False):
    keys = ["id", "chrom", "pos", "ref", "alt", "gene", "protein_change", "consequence", "classification",
            "status", "dropped_at", "reason", "vaf", "depth", "alt_reads", "tier", "notation"]
    if full:
        keys += ["hgvsp", "hgvsc", "exon", "intron", "transcript", "clinvar", "best_evidence", "hotspot",
                 "tier_reason", "tier3", "gnomad_af", "alphamissense", "revel", "cadd_phred", "resolved",
                 "evidence_id", "annotation_source", "literature", "normal_alt_reads", "subtype_frequency", "pathways",
                 "driver_role"]
    return {k: v.get(k) for k in keys}


def _trace(counts, tiered, status, tracks, cancer):
    def src(keys):
        names = {"civic": "CIViC", "oncokb": "OncoKB", "dgidb": "DGIdb", "chembl": "ChEMBL", "pubtator": "PubTator",
                 "medcpt": "MedCPT re-ranked"}
        parts = []
        for key in keys:
            s = status.get(key, {})
            if s.get("state") == "ok":
                parts.append(f"{names[key]} {s['count']}")
            elif s.get("state") == "error":
                parts.append(f"{names[key]} failed")
        return " · ".join(parts) or "no sources"

    dropped = counts["dropped"]
    n_tiered = len(tiered)
    return [
        {"stage": "qc", "status": "done" if counts["qc_passed"] else "warning",
         "detail": f"{counts['total']} variants → {counts['qc_passed']} passed (QC −{dropped['qc']}, "
                   f"germline −{dropped['germline']}, consequence −{dropped['consequence']}; all in the audit table)"},
        {"stage": "resolution", "status": "done" if counts["qc_passed"] else "skipped",
         "detail": f"{counts['qc_passed']} variants · {cancer['code']}"},
        {"stage": "tiering", "status": "done" if n_tiered else "warning",
         "detail": f"Tier 1: {counts['tier1']} · Tier 2: {counts['tier2']} · Tier 3: {counts['tier3']} · "
                   f"low priority: {counts['low_priority']}"},
        {"stage": "retrieval", "status": "done" if n_tiered else "skipped",
         "detail": src(["civic", "oncokb", "dgidb", "chembl"]) if n_tiered else "no tiered variant"},
        {"stage": "classification", "status": "done" if n_tiered else "skipped",
         "detail": f"established {len(tracks['established'])} · exploratory {len(tracks['exploratory'])}"
                   if n_tiered else "not run"},
        {"stage": "exploration", "status": "done" if n_tiered else "skipped",
         "detail": f"literature: {src(['pubtator', 'medcpt'])}" if n_tiered else "not run"},
    ]


def _limitations(info, status, notes, safety_block=None):
    out = [
        "SNVs / small indels only: fusions (ALK, ROS1, RET, NTRK) and copy-number changes (e.g. MET or ERBB2 "
        "amplification) are not detected, so drugs that depend on them cannot be found from this file.",
        "Prototype for lung cancer only; evidence from other tumour types is labelled and scored lower.",
        "Tier 3 scores and exploratory predictions are not evidence of drug response; agent hypotheses are for "
        "follow-up only.",
        "Gene frequency R counts every mutation in the reference cohort, so large passenger genes (e.g. TTN, MUC16) "
        "also score high; B (biological evidence) is there to offset this.",
        *notes,
    ]
    if status.get("oncokb", {}).get("state") == "disabled":
        out.append("OncoKB is not configured (needs a licensed token), so tiers and levels come from CIViC and "
                   "cancerhotspots.org; IntOGen replaces the Cancer Gene Census for gene roles.")
    if safety_block is not None:
        out.append("Drug–drug interactions are checked from FDA labels (openFDA) and the FDA CYP / transporter table: "
                   "pharmacokinetic interactions only, not additive toxicity or pharmacodynamic effects"
                   + ("; no current medications were given, so only combination regimens were checked."
                      if not safety_block["medications"] else "."))
    if info.get("format") == "vcf" and not info.get("assembly"):
        out.append("The VCF header does not name the genome build; GRCh38 was assumed.")
    return out
