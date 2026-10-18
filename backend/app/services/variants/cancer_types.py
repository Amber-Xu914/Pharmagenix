"""Cancer type / subtype handling for the lung-only prototype.

The user's cancer type is mapped to an OncoTree code (LUAD, NSCLC, ...). That code decides
  - which IntOGen cohorts define the subtype's driver genes (the subtype and its parents: LUAD -> NSCLC -> LUNG)
  - how well a CIViC evidence item's disease (a Disease Ontology ID) matches the patient's cancer
"""

import re

from ...errors import APIError
from ...extensions import db
from ...reference.models import OncotreeType

# Disease Ontology IDs CIViC uses for each OncoTree lung type (prototype: lung only).
LUNG_DOIDS = {
    "LUNG": {"1324", "3905"},              # lung cancer, lung carcinoma
    "NSCLC": {"3908"},                      # lung non-small cell carcinoma
    "LUAD": {"3910", "6482", "0080303"},    # adenocarcinoma, acinar, mucinous
    "LUSC": {"3907"},
    "LUAS": {"4829"},                       # adenosquamous
    "LCLC": {"4450"},
    "SCLC": {"5409"},
    "LNET": set(),
}
ALL_LUNG_DOIDS = set().union(*LUNG_DOIDS.values())

# Used when OncoTree hasn't been imported: the common lung types and their parents.
FALLBACK = {
    "LUNG": ("Lung", None), "NSCLC": ("Non-Small Cell Lung Cancer", "LUNG"),
    "LUAD": ("Lung Adenocarcinoma", "NSCLC"), "LUSC": ("Lung Squamous Cell Carcinoma", "NSCLC"),
    "LCLC": ("Large Cell Lung Carcinoma", "NSCLC"), "LUAS": ("Lung Adenosquamous Carcinoma", "NSCLC"),
    "LNET": ("Lung Neuroendocrine Tumor", "LUNG"), "SCLC": ("Small Cell Lung Cancer", "LNET"),
}
ALIASES = {"LUNG CANCER": "LUNG", "NON-SMALL CELL LUNG CANCER": "NSCLC", "NON SMALL CELL LUNG CANCER": "NSCLC",
           "ADENOCARCINOMA": "LUAD", "SQUAMOUS CELL CARCINOMA": "LUSC", "SMALL CELL LUNG CANCER": "SCLC"}

# Disease match of an evidence item, best first.
MATCH_LABELS = {3: "same cancer type", 2: "related lung cancer", 1: "other tumour type"}


def lung_types():
    """[{code, name, parent, level}] for the cancer type picker."""
    rows = db.session.scalars(db.select(OncotreeType).where(OncotreeType.tissue == "Lung")
                              .order_by(OncotreeType.level, OncotreeType.name)).all()
    if rows:
        return [{"code": r.code, "name": r.name, "parent": r.parent, "level": r.level} for r in rows]
    return [{"code": code, "name": name, "parent": parent, "level": None} for code, (name, parent) in FALLBACK.items()]


def resolve_cancer_type(raw):
    """'luad' / 'Lung adenocarcinoma' / 'NSCLC' / 'Lung Adenocarcinoma (LUAD)' (the form's suggestions)
    -> {"input", "code", "name", "lineage": [code, parent, ...]}."""
    key = re.sub(r"\s+", " ", (raw or "").strip().upper())
    suggested = re.fullmatch(r".*\(([A-Z0-9]+)\)", key)  # "NAME (CODE)": the code in brackets decides
    if suggested:
        key = suggested.group(1)
    if not key:
        raise APIError("Please choose a cancer type / subtype")
    types = {t["code"]: t for t in lung_types()}
    by_name = {t["name"].upper(): code for code, t in types.items()}
    code = key if key in types else by_name.get(key) or ALIASES.get(key) or by_name.get(f"LUNG {key}")
    if code not in types:
        raise APIError(f"“{raw}” is not a lung cancer type. This prototype supports lung cancer only "
                       f"(e.g. {', '.join(c for c in ('LUAD', 'LUSC', 'NSCLC', 'SCLC') if c in types)}).")
    lineage, current = [], code
    while current and current in types and current not in lineage:
        lineage.append(current)
        current = types[current]["parent"]
    return {"input": raw, "code": code, "name": types[code]["name"], "lineage": lineage}


def disease_match(doid, cancer):
    """3: the patient's subtype or a parent type (LUAD evidence, or NSCLC evidence for LUAD); generic "lung
    cancer" evidence only counts as 3 when the patient's type is just "lung". 2: another lung cancer; 1: another
    tumour type."""
    if not doid:
        return 1
    for i, code in enumerate(cancer["lineage"]):
        if (code != "LUNG" or i == 0) and doid in LUNG_DOIDS.get(code, ()):
            return 3
    return 2 if doid in ALL_LUNG_DOIDS else 1
