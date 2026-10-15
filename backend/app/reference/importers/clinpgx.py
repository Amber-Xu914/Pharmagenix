"""ClinPGx (formerly PharmGKB) bulk downloads (CC BY-NC-SA 4.0, attribution required).

Imported: gene–chemical relationships, drug label annotations (FDA, EMA, PMDA, HCSC, Swissmedic) and guideline
annotations (CPIC, DPWG, CPNDS, RNPGx, ...), which are joined with the CPIC and FDA tables.
Clinical annotations are not imported: they are queried live from the ClinPGx API on every search.
"""

import csv
import io
import json
import re
import zipfile
from datetime import date

from flask import current_app

from ..models import ClinpgxDrugLabel, ClinpgxGuideline, ClinpgxGuidelinePair, ClinpgxRelationship
from ..text import norm
from . import drug_synonym_index, gene_symbol_index, http_session, lookup_drug, replace_tables

FILES = ("relationships", "drugLabels", "guidelineAnnotations.json")
SITE = "https://www.clinpgx.org"


def run(echo):
    base = current_app.config["CLINPGX_DOWNLOAD_URL"].rstrip("/")
    session = http_session()
    archives, created = {}, {}
    for name in FILES:
        echo(f"[clinpgx] downloading {name}.zip")
        resp = session.get(f"{base}/{name}.zip", timeout=300)
        resp.raise_for_status()
        archives[name] = zipfile.ZipFile(io.BytesIO(resp.content))
        created[name] = _created_date(archives[name])

    genes, drugs = gene_symbol_index(), drug_synonym_index()
    relationships = _tsv(archives["relationships"], "relationships.tsv")
    labels = _tsv(archives["drugLabels"], "drugLabels.tsv")
    guideline_files = [json.loads(archives["guidelineAnnotations.json"].read(n))
                       for n in archives["guidelineAnnotations.json"].namelist() if n.endswith(".json")]

    guidelines, guideline_pairs = build_guidelines(guideline_files, genes, drugs)
    tables = [
        (ClinpgxRelationship, build_relationships(relationships, genes, drugs)),
        (ClinpgxDrugLabel, build_drug_labels(labels, genes, drugs)),
        (ClinpgxGuideline, guidelines),
        (ClinpgxGuidelinePair, guideline_pairs),
    ]
    echo(f"[clinpgx] writing {sum(len(rows) for _, rows in tables)} rows")
    replace_tables(tables)
    # The files are rebuilt on different schedules, so record each one's date.
    dates = sorted(set(created.values()))
    version = f"ClinPGx downloads created {dates[-1]}"
    stale = {n: d for n, d in created.items() if d != dates[-1]}
    if stale:
        version += " (" + ", ".join(f"{n} {d}" for n, d in stale.items()) + ")"
    return {"version": version[:100], "url": base, "notes": json.dumps(created),
            "counts": {model.__tablename__: len(rows) for model, rows in tables}}


def _created_date(archive):
    for name in archive.namelist():
        match = re.match(r"CREATED_(\d{4}-\d{2}-\d{2})\.txt$", name)
        if match:
            return match.group(1)
    return "unknown"


def _tsv(archive, name):
    with archive.open(name) as fh:
        return list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"), delimiter="\t"))


def _split(value, sep=";"):
    return [v.strip() for v in (value or "").split(sep) if v.strip()]


def _date(value):
    try:
        return date.fromisoformat(value.strip())
    except (AttributeError, ValueError):
        return None


def _gene(symbol, genes):
    return {"gene_symbol": norm(symbol)[:64] or None, "hgnc_id": genes.get(norm(symbol))}


def _drug(name, drugs):
    return {"drug_name": (name or "")[:255] or None, "drug_name_norm": norm(name)[:255] or None,
            "chembl_id": lookup_drug(drugs, name) if name else None}


def _pairs(gene_symbols, drug_names):
    """Every (gene, drug) combination of a record; a missing side is kept as None."""
    return [(g, d) for g in (gene_symbols or [None]) for d in (drug_names or [None])]


def build_relationships(rows, genes, drugs):
    """Gene–chemical rows only. The file lists each pair in both directions; the Gene → Chemical one is kept."""
    out = []
    for r in rows:
        if r.get("Entity1_type") != "Gene" or r.get("Entity2_type") != "Chemical":
            continue
        out.append({
            "gene_pa_id": r["Entity1_id"],
            **_gene(r["Entity1_name"], genes),
            "chemical_pa_id": r["Entity2_id"],
            **_drug(r["Entity2_name"], drugs),
            "evidence": _split(r.get("Evidence"), ","),
            "association": r.get("Association") or None,
            "pk": r.get("PK") == "PK",
            "pd": r.get("PD") == "PD",
            "pmids": [int(p) for p in _split(r.get("PMIDs")) if p.isdigit()],
        })
    return out


def build_drug_labels(rows, genes, drugs):
    out = []
    for r in rows:
        record = {
            "label_id": r["PharmGKB ID"],
            "name": (r.get("Name") or "")[:500],
            "source": r.get("Source"),
            "biomarker_flag": r.get("Biomarker Flag") or None,
            "testing_level": r.get("Testing Level") or None,
            "has_prescribing_info": bool(r.get("Has Prescribing Info")),
            "has_dosing_info": bool(r.get("Has Dosing Info")),
            "has_alternate_drug": bool(r.get("Has Alternate Drug")),
            "has_other_prescribing_guidance": bool(r.get("Has Other Prescribing Guidance")),
            "cancer_genome": bool(r.get("Cancer Genome")),
            "variants": r.get("Variants/Haplotypes") or None,
            "latest_history_date": _date(r.get("Latest History Date (YYYY-MM-DD)")),
            "url": f"{SITE}/labelAnnotation/{r['PharmGKB ID']}",
        }
        for gene, drug in _pairs(_split(r.get("Genes")), _split(r.get("Chemicals"))):
            out.append({**record, **_gene(gene, genes), **_drug(drug, drugs)})
    return out


def _plain_text(markup):
    """The guideline text is HTML; keep paragraphs, drop tags."""
    if not markup:
        return None
    text = re.sub(r"</(p|h\d|li|tr)>", "\n", markup)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def build_guidelines(files, genes, drugs):
    guidelines, pairs = [], []
    for f in files:
        g = f["guideline"]
        pmids = []
        for lit in g.get("literature") or []:
            pmids += [int(x["resourceId"]) for x in lit.get("crossReferences") or []
                      if x.get("resource") == "PubMed" and str(x.get("resourceId")).isdigit()]
        guidelines.append({
            "guideline_id": g["id"],
            "name": g["name"][:500],
            "source": g.get("source"),
            "recommendation": g.get("recommendation"),
            "dosing_information": g.get("dosingInformation"),
            "alternate_drug_available": g.get("alternateDrugAvailable"),
            "other_prescribing_guidance": g.get("otherPrescribingGuidance"),
            "has_testing_info": g.get("hasTestingInfo"),
            "pediatric": g.get("pediatric"),
            "cancer_genome": g.get("cancerGenome"),
            "summary": _plain_text((g.get("summaryMarkdown") or {}).get("html")),
            "text": _plain_text((g.get("textMarkdown") or {}).get("html")),
            "pmids": pmids,
            "url": f"{SITE}/guideline/{g['id']}",
        })
        gene_symbols = [x["symbol"] for x in g.get("relatedGenes") or [] if x.get("symbol")]
        drug_names = [x["name"] for x in g.get("relatedChemicals") or [] if x.get("name")]
        for gene, drug in _pairs(gene_symbols, drug_names):
            pairs.append({"guideline_id": g["id"], **_gene(gene, genes), **_drug(drug, drugs)})
    return guidelines, pairs
