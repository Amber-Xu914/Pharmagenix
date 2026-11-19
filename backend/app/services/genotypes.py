"""The patient's pharmacogene results -> CPIC phenotype -> CPIC prescribing recommendation (deterministic).

Input, one entry per gene: a diplotype ("CYP2C19 *2/*2"), a phenotype ("CYP2D6 poor metabolizer", "CYP2C9 IM") or,
for HLA genes, an allele status ("HLA-B*57:01 positive").

  1 Phenotype       the diplotype is looked up in CPIC's diplotype table (either allele order); a phenotype is
                    checked against the gene's CPIC results; HLA allele statuses are used as they are
  2 Recommendation  for every drug of the result with a CPIC guideline for one of the genes, the guideline's
                    recommendation whose lookup key matches the patient: all genes of a multi-gene key must match;
                    a gene the patient has no result for is matched as "No Result" (CPIC's row for an untested gene)
  3 Phenoconversion a current medication that strongly or moderately inhibits the gene (FDA table) may make the
                    functional phenotype poorer than the genotype predicts; noted, never used to change the phenotype

Each matched recommendation becomes an evidence item (kind "genotype") the LLM can cite.
"""

import re

from flask import current_app

from ..errors import APIError
from ..extensions import db
from ..reference import lookup
from ..reference.models import CpicDiplotype, CpicGeneResult, CpicPair, CpicRecommendation
from ..reference.text import norm, strip_salt

PHENOTYPE_ABBREVIATIONS = {
    "PM": "Poor Metabolizer", "IM": "Intermediate Metabolizer", "NM": "Normal Metabolizer",
    "RM": "Rapid Metabolizer", "UM": "Ultrarapid Metabolizer",
    "POOR": "Poor Metabolizer", "INTERMEDIATE": "Intermediate Metabolizer", "NORMAL": "Normal Metabolizer",
    "RAPID": "Rapid Metabolizer", "ULTRARAPID": "Ultrarapid Metabolizer",
    "PF": "Poor Function", "DF": "Decreased Function", "NF": "Normal Function", "IF": "Increased Function",
}
NO_RESULT = {"no result", "indeterminate"}
ENTRY = re.compile(r"^\s*(?P<gene>[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)?)\s*(?P<value>[*:\s].*)$")
CLASSIFICATION_ORDER = {"Strong": 0, "Moderate": 1, "Optional": 2}
# CPIC's population codes, written out for clinicians ("" = applies to everyone).
POPULATION_LABELS = {
    "general": "", "adults": "Adults", "pediatrics": "Children",
    "child >40kg_adult": "Children over 40 kg and adults", "<= 1g per day": "Dose of 1 g per day or less",
    "CVI ACS PCI": "Cardiovascular: acute coronary syndrome and/or PCI",
    "CVI non-ACS non-PCI": "Cardiovascular: no acute coronary syndrome or PCI",
    "NVI": "Neurovascular: stroke or TIA",
    "CBZ naive": "Not yet taking carbamazepine", "CBZ-no alternatives": "Carbamazepine with no alternative",
    "CBZ use >3mos": "Taking carbamazepine for over 3 months",
    "OXC naive": "Not yet taking oxcarbazepine", "OXC use >3 mos": "Taking oxcarbazepine for over 3 months",
    "PHT naive": "Not yet taking phenytoin", "PHT use >3mos": "Taking phenytoin for over 3 months",
}


def population_label(population):
    """CPIC's population code in plain words; unknown codes are shown as they are."""
    code = (population or "general").strip()
    return POPULATION_LABELS.get(code, code)


# ---------- input ----------

def parse_genotypes(raw):
    """A list or a ';' / newline / ','-separated string -> [{"gene", "value"}]. Raises APIError on an entry that
    is not "GENE result"."""
    if not raw:
        return []
    parts = raw if isinstance(raw, list) else re.split(r"[;\n,]+", str(raw))
    out, seen = [], set()
    for part in parts:
        text = re.sub(r"\s+", " ", str(part)).strip()[:120]
        if not text:
            continue
        m = ENTRY.match(text)
        if not m or not m["value"].strip():
            raise APIError(f"Could not read the genotype “{text}”. Write the gene, then the result, e.g. "
                           "“CYP2C19 *2/*2”, “CYP2D6 poor metabolizer” or “HLA-B*57:01 positive”.")
        gene = m["gene"].upper()
        if gene in seen:
            raise APIError(f"{gene} was entered twice; give one result per gene")
        seen.add(gene)
        out.append({"gene": gene, "value": m["value"].strip()})
    limit = current_app.config["MAX_GENOTYPES"]
    if len(out) > limit:
        raise APIError(f"At most {limit} genotypes per search")
    return out


# ---------- 1 phenotype ----------

def label(entry):
    """'CYP2C19 *2/*2', 'HLA-B*57:01 positive'."""
    value = entry["value"]
    return f"{entry['gene']}{value}" if entry["gene"].startswith("HLA-") and value.startswith("*") \
        else f"{entry['gene']} {value}"


def _diplotype_variants(value):
    """'*17/*1' -> ['*17/*1', '*1/*17'], with the spaces around '/' removed."""
    text = re.sub(r"\s*/\s*", "/", value.strip())
    halves = text.split("/")
    return [text, "/".join(reversed(halves))] if len(halves) == 2 else [text]


def resolve(entry):
    """{"gene", "input", "diplotype", "phenotype", "activity_score", "key", "status", "note"}. key: the value
    CPIC recommendations are looked up by. status: "ok" | "unknown" | "no_data"."""
    gene, value = entry["gene"], entry["value"]
    out = {"gene": gene, "input": label(entry), "diplotype": None,
           "phenotype": None, "activity_score": None, "key": None, "status": "unknown", "note": None}

    if gene.startswith("HLA-"):  # "HLA-B*57:01 positive" / "*57:01 negative"
        m = re.match(r"^\*?(?P<allele>\d{2}:\d{2})\s+(?P<status>positive|negative)$", value.lstrip("*").strip(), re.I)
        if m:
            out.update(phenotype=f"{gene}*{m['allele']} {m['status'].lower()}", status="ok")
            out["key"] = out["phenotype"]
        else:
            out["note"] = f"Write the allele status, e.g. “{gene}*57:01 positive”."
        return out

    results = db.session.scalars(db.select(CpicGeneResult.result).where(CpicGeneResult.gene_symbol == gene)).all()
    if not results and not db.session.scalar(db.select(CpicDiplotype.id).where(CpicDiplotype.gene_symbol == gene)
                                             .limit(1)):
        out.update(status="no_data", note=f"CPIC has no phenotype table for {gene}.")
        return out

    # A phenotype, written out or abbreviated.
    plain = re.sub(r"metaboliser", "metabolizer", value, flags=re.I)
    wanted = PHENOTYPE_ABBREVIATIONS.get(re.sub(r"\s+METABOLIZER$", "", plain.upper()), plain)
    match = next((r for r in results if r.lower() == wanted.lower()), None)
    if match:
        out.update(phenotype=match, key=match, status="ok")
        return out

    # A diplotype.
    for candidate in _diplotype_variants(value):
        row = db.session.scalars(db.select(CpicDiplotype).where(
            CpicDiplotype.gene_symbol == gene, CpicDiplotype.diplotype == candidate)).first()
        if row:
            out.update(diplotype=row.diplotype, phenotype=row.phenotype, activity_score=row.total_activity_score,
                       key=row.phenotype, status="ok" if row.phenotype and row.phenotype.lower() not in NO_RESULT
                       else "unknown")
            if out["status"] != "ok":
                out["note"] = f"CPIC assigns {gene} {row.diplotype} no definite phenotype ({row.phenotype})."
            return out
    out["note"] = (f"“{value}” is not a {gene} diplotype or phenotype in CPIC's tables. Use star-allele notation "
                   f"(e.g. {gene} *1/*2) or a phenotype ({', '.join(sorted(results)[:4])}…).")
    return out


# ---------- 3 phenoconversion ----------

def _phenoconversion(gene, medications):
    """Notes for current medications that strongly or moderately inhibit gene (FDA table)."""
    notes = []
    for med in medications:
        for role in lookup.cyp_roles(med.get("chembl_id"), med.get("name")):
            if role["gene"] == gene and role["role"] == "inhibitor" and role["strength"] in ("strong", "moderate"):
                notes.append(f"{med['name']} is a {role['strength']} {role['pathway']} inhibitor: the functional "
                             "phenotype may be poorer than the genotype predicts (phenoconversion).")
                break
    return notes


# ---------- 2 recommendations ----------

def _matches(rec_lookup, keys):
    """rec_lookup: a recommendation's {gene: value}; keys: the patient's {gene: value}. All genes must match; a
    gene the patient has no result for matches only CPIC's 'No Result' row."""
    for gene, value in (rec_lookup or {}).items():
        mine = keys.get(gene)
        if mine is None:
            if not str(value).lower().startswith("no ") or "result" not in str(value).lower():
                return False
        elif str(value).lower() != mine.lower():
            return False
    return bool(rec_lookup) and any(gene in keys for gene in rec_lookup)


def phenotype_label(gene, key):
    """'CYP2C19 Poor Metabolizer'; an HLA key already names the gene ('HLA-B*57:01 positive')."""
    return key if key.upper().startswith(gene) else f"{gene} {key}"


def _first_sentence(text, limit=220):
    text = re.sub(r"\s+", " ", text or "").strip()
    m = re.match(r"^(.+?[.;])(?:\s|$)", text)
    short = m.group(1) if m else text
    return short if len(short) <= limit else short[: limit - 1].rstrip() + "…"


def _needs(rec_lookup, keys):
    """The genes a recommendation also needs when it matches the patient on every gene they gave."""
    if not any(g in keys for g in rec_lookup or {}):
        return None
    if any(g in keys and str(v).lower() != keys[g].lower() for g, v in rec_lookup.items()):
        return None
    return [g for g in rec_lookup if g not in keys]


def recommendations(resolved, drugs, release=None):
    """resolved: resolve() results; drugs: [{"name", "chembl_id"}] of the result. -> (evidence items without IDs,
    [{"drug", "needs"}] for guidelines that also need a gene the patient has no result for)."""
    keys = {r["gene"]: r["key"] for r in resolved if r["status"] == "ok"}
    if not keys or not drugs:
        return [], []
    names = {norm(strip_salt(d["name"])) for d in drugs if d.get("name")} | {norm(d["name"]) for d in drugs
                                                                            if d.get("name")}
    ids = {d["chembl_id"] for d in drugs if d.get("chembl_id")}
    conditions = [CpicPair.drug_name_norm.in_(names)] + ([CpicPair.chembl_id.in_(ids)] if ids else [])
    pairs = db.session.scalars(db.select(CpicPair).where(CpicPair.gene_symbol.in_(keys), db.or_(*conditions))).all()

    by_patient = {r["gene"]: r for r in resolved}
    items, seen, incomplete = [], set(), {}
    for pair in sorted(pairs, key=lambda p: (p.cpic_level or "Z", p.drug_name)):
        if not pair.guideline_id:
            continue
        recs = db.session.scalars(db.select(CpicRecommendation).where(
            CpicRecommendation.guideline_id == pair.guideline_id,
            CpicRecommendation.drug_name_norm == pair.drug_name_norm)).all()
        matched = False
        for rec in recs:
            if not _matches(rec.lookup, keys):
                continue
            matched = True
            population = (rec.population or "").strip() or "general"
            dedupe = (pair.drug_name_norm, population, rec.recommendation)
            if dedupe in seen:  # CPIC repeats a recommendation per activity score / allele status
                continue
            seen.add(dedupe)
            genes = [g for g in rec.lookup if g in keys]
            items.append({
                "kind": "genotype", "source": "CPIC", "release": release, "categories": ["snp"],
                "gene": " + ".join(genes), "genes": genes, "drug": pair.drug_name.upper(),
                "genotype": "; ".join(by_patient[g]["input"] for g in genes),
                "phenotype": "; ".join(phenotype_label(g, keys[g]) for g in genes),
                "lookup": rec.lookup, "population": population, "population_label": population_label(population),
                "implications": rec.implications or {}, "recommendation": rec.recommendation,
                "short_recommendation": _first_sentence(rec.recommendation),
                "classification": rec.classification, "cpic_level": pair.cpic_level,
                "guideline_name": pair.guideline_name, "url": pair.guideline_url, "pmids": pair.pmids or [],
            })
        if not matched:
            needs = sorted({g for rec in recs for g in (_needs(rec.lookup, keys) or [])})
            if needs:
                incomplete.setdefault(pair.drug_name.upper(), set()).update(needs)
    items.sort(key=lambda i: (CLASSIFICATION_ORDER.get(i["classification"], 9), i["drug"], i["population"]))
    return items, [{"drug": d, "needs": sorted(n)} for d, n in sorted(incomplete.items())]


def check(genotypes, drugs, medications, items, status, release=None):
    """Resolves the genotypes, appends the matched recommendations to items (numbered after them) and returns
    evidence["genotypes"]. drugs / medications: [{"name", "chembl_id"}]."""
    if not genotypes:
        return None
    if release is None and status.get("cpic", {}).get("state") == "disabled":
        resolved = [{"gene": g["gene"], "input": label(g), "status": "no_data",
                     "note": "CPIC data not imported"} for g in genotypes]
        return {"entered": resolved, "recommendations": [], "incomplete": []}
    resolved = [resolve(g) for g in genotypes]
    for r in resolved:
        r["phenoconversion"] = _phenoconversion(r["gene"], medications) if r["status"] == "ok" else []
    recs, incomplete = recommendations(resolved, drugs, release)
    for rec in recs:
        rec["id"] = f"E{len(items) + 1}"
        items.append(rec)
    return {"entered": resolved, "recommendations": [r["id"] for r in recs], "incomplete": incomplete}
