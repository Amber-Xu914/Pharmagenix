"""Resolve what the user typed (P53, Plavix, clopidogrel bisulfate, ENSG...) to standard entities."""

import re

from ..extensions import db
from .models import Drug, DrugSynonym, Gene, GeneAlias, PbsItem
from .text import norm, strip_salt

GENE_ALIAS_LABELS = {"symbol": "HGNC symbol", "previous": "HGNC previous symbol", "alias": "HGNC alias"}
DRUG_SYNONYM_PRIORITY = {"PREF_NAME": 0, "INN": 1, "USAN": 2, "BAN": 3, "TRADE_NAME": 4}


def resolve_entities(entities, entity_type):
    return [_resolve(raw, entity_type) for raw in entities]


def _resolve(raw, entity_type):
    key = norm(raw)
    want_gene = entity_type in ("gene", "auto")
    want_drug = entity_type in ("drug", "auto")
    notes = []

    # Most specific matches first: identifiers and approved symbols, then drug names, then aliases.
    steps = []
    if want_gene:
        steps += [lambda: _gene_by_identifier(key), lambda: _gene_by_alias(key, "symbol", notes)]
    if want_drug:
        steps += [lambda: _drug_by_name(key, "ChEMBL synonym")]
    if want_gene:
        steps += [lambda: _gene_by_alias(key, "previous", notes), lambda: _gene_by_alias(key, "alias", notes)]
    if want_drug:
        steps += [lambda: _drug_by_name(strip_salt(key), "parent drug of salt form"), lambda: _drug_by_pbs_brand(key)]

    for step in steps:
        match = step()
        if match:
            return {"input": raw, **match, "note": "; ".join(notes) or None}
    return {"input": raw, "name": key, "type": None, "id": None, "matched_by": None,
            "note": "; ".join(notes) or "not found in local reference data"}


def _gene(gene, matched_by):
    return {"name": gene.symbol.upper(), "type": "gene", "id": gene.hgnc_id, "matched_by": matched_by}


def _gene_by_identifier(key):
    if key.startswith("HGNC:"):
        gene, label = db.session.get(Gene, key), "HGNC ID"
    elif re.fullmatch(r"ENSG\d{11}", key):
        gene, label = Gene.query.filter_by(ensembl_gene_id=key).first(), "Ensembl ID"
    elif key.isdigit():
        gene, label = Gene.query.filter_by(entrez_id=key).first(), "NCBI Gene ID"
    else:
        return None
    return _gene(gene, label) if gene else None


def _gene_by_alias(key, alias_type, notes):
    hgnc_ids = db.session.scalars(
        db.select(GeneAlias.hgnc_id).where(GeneAlias.alias_norm == key, GeneAlias.alias_type == alias_type).distinct()
    ).all()
    if len(hgnc_ids) > 1:
        symbols = db.session.scalars(db.select(Gene.symbol).where(Gene.hgnc_id.in_(hgnc_ids))).all()
        notes.append(f"ambiguous {GENE_ALIAS_LABELS[alias_type]}: {', '.join(sorted(symbols))}")
        return None
    if hgnc_ids:
        return _gene(db.session.get(Gene, hgnc_ids[0]), GENE_ALIAS_LABELS[alias_type])
    return None


def _drug_by_name(key, label):
    rows = db.session.execute(
        db.select(DrugSynonym.chembl_id, DrugSynonym.syn_type).where(DrugSynonym.synonym_norm == key)
    ).all()
    if not rows:
        return None
    chembl_id = min(rows, key=lambda r: DRUG_SYNONYM_PRIORITY.get(r[1], 9))[0]
    drug = db.session.get(Drug, chembl_id)
    syn_type = next(r[1] for r in rows if r[0] == chembl_id)
    matched_by = f"{label} ({syn_type.lower().replace('_', ' ')})" if label == "ChEMBL synonym" else label
    return {"name": norm(drug.pref_name), "type": "drug", "id": drug.chembl_id, "matched_by": matched_by}


def _drug_by_pbs_brand(key):
    item = PbsItem.query.filter_by(brand_name_norm=key).first()
    if not item:
        return None
    if item.chembl_id and (drug := db.session.get(Drug, item.chembl_id)):
        return {"name": norm(drug.pref_name), "type": "drug", "id": drug.chembl_id, "matched_by": "PBS brand"}
    return {"name": item.drug_name_norm, "type": "drug", "id": None, "matched_by": "PBS brand"}
