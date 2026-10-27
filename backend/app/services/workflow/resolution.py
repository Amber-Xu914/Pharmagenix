"""Stage 1, Entity Resolution: decide whether the gene or drug resolved, and suggest corrections when not.

Resolution itself (input -> HGNC gene / ChEMBL drug) lives in reference/normalise.py. This module is the "Entity
resolved?" decision: an unknown or ambiguous name stops the query before any API or LLM call, and the user is shown
the closest gene symbols or drug names to pick from (or can search the name as typed).
"""

import difflib

from ...extensions import db
from ...reference.lookup import latest_releases
from ...reference.models import Drug, DrugSynonym, Gene, GeneAlias
from ...reference.text import norm

MAX_SUGGESTIONS = 5
CANDIDATE_POOL = 5000  # names sharing a prefix with the input that are compared for similarity


def unresolved_problem(entry, entity_type="gene"):
    """The problem the user must confirm or correct, with suggestions; None when the name resolved.

    Only possible when HGNC (genes) or ChEMBL (drugs) is imported; without it nothing can be called "not found", so
    the name is passed to the live APIs unchanged.
    """
    if entry["type"] is not None or ("chembl" if entity_type == "drug" else "hgnc") not in latest_releases():
        return None
    candidates = entry.get("candidates") or []
    if candidates:
        suggestions = [_gene_suggestion(symbol) for symbol in candidates[:MAX_SUGGESTIONS]]
        reason = "ambiguous"
    else:
        suggestions = suggest_drug(entry["input"]) if entity_type == "drug" else suggest(entry["input"])
        reason = "not_found"
    return {"input": entry["input"], "reason": reason, "note": entry.get("note"),
            "suggestions": [s for s in suggestions if s]}


def suggest(raw, limit=MAX_SUGGESTIONS):
    """Closest gene symbols to a misspelt input, best match first."""
    key = norm(raw)
    if not key:
        return []
    scored = _closest(key, GeneAlias.alias_norm, GeneAlias.hgnc_id, prefix=2, make=_gene_suggestion_by_id)
    scored.sort(key=lambda pair: -pair[0])
    out, seen = [], set()
    for _, suggestion in scored:
        if suggestion and suggestion["name"] not in seen:
            seen.add(suggestion["name"])
            out.append(suggestion)
    return out[:limit]


def suggest_drug(raw, limit=MAX_SUGGESTIONS):
    """Closest drug names (ChEMBL names and trade names) to a misspelt input, best match first."""
    key = norm(raw)
    if not key:
        return []
    scored = sorted(_closest(key, DrugSynonym.synonym_norm, DrugSynonym.chembl_id, prefix=2,
                             make=_drug_suggestion_by_id), key=lambda pair: -pair[0])
    out, seen = [], set()
    for _, suggestion in scored:
        if suggestion and suggestion["name"] not in seen:
            seen.add(suggestion["name"])
            out.append(suggestion)
    return out[:limit]


def _drug_suggestion_by_id(chembl_id):
    drug = db.session.get(Drug, chembl_id)
    if not drug or not drug.pref_name:
        return None
    phase = "approved" if (drug.max_phase or 0) >= 4 else f"max phase {drug.max_phase:g}" if drug.max_phase else None
    return {"name": drug.pref_name.upper(), "type": "drug",
            "description": ", ".join(p for p in (drug.molecule_type, phase) if p) or None}


def _closest(key, name_col, id_col, prefix, make):
    """Compare the input with names sharing its first characters; returns [(similarity, suggestion)]."""
    start = key[: min(prefix, max(len(key) - 1, 1))]
    rows = db.session.execute(
        db.select(name_col, id_col).where(name_col.like(f"{start}%")).limit(CANDIDATE_POOL)
    ).all()
    ids_by_name = {}
    for name, entity_id in rows:
        ids_by_name.setdefault(name, entity_id)
    matches = difflib.get_close_matches(key, list(ids_by_name), n=MAX_SUGGESTIONS * 2, cutoff=0.6)
    return [(difflib.SequenceMatcher(None, key, m).ratio(), make(ids_by_name[m])) for m in matches]


def _gene_suggestion_by_id(hgnc_id):
    gene = db.session.get(Gene, hgnc_id)
    return {"name": gene.symbol.upper(), "type": "gene", "description": gene.name} if gene else None


def _gene_suggestion(symbol):
    gene = Gene.query.filter_by(symbol=symbol).first()
    return {"name": symbol.upper(), "type": "gene", "description": gene.name if gene else None}
