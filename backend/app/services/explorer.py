"""Orchestrates one exploration: validate input -> collect evidence -> generate reports."""

import re

from flask import current_app

from ..errors import APIError
from ..extensions import db
from ..models import Query
from .biomed import collect_evidence
from .llm.report_generator import generate_reports
from .relations import DEFAULT_RELATION_TYPES, RELATION_TYPES

ENTITY_TYPES = {"auto", "gene", "drug"}


def normalize_entities(raw, max_entities):
    if isinstance(raw, str):
        raw = re.split(r"[,;\n]+", raw)
    if not isinstance(raw, list):
        raise APIError("entities must be a list or a comma-separated string")
    out, seen = [], set()
    for value in raw:
        value = str(value).strip()
        if not value or value.upper() in seen:
            continue
        if len(value) > 100:
            raise APIError(f"Entity name too long: {value[:30]}...")
        seen.add(value.upper())
        out.append(value)
    if not out:
        raise APIError("Please enter at least one gene or drug name")
    if len(out) > max_entities:
        raise APIError(f"At most {max_entities} genes/drugs per query (got {len(out)})")
    return out


def normalize_relation_types(raw):
    if not raw:
        return list(DEFAULT_RELATION_TYPES)
    unknown = [r for r in raw if r not in RELATION_TYPES]
    if unknown:
        raise APIError(f"Unknown relation types: {', '.join(unknown)}")
    return list(dict.fromkeys(raw))


def run_exploration(entities, entity_type, relation_types, compare=False, followup_question=None, parent=None):
    cfg = current_app.config
    if entity_type not in ENTITY_TYPES:
        raise APIError(f"entity_type must be one of {sorted(ENTITY_TYPES)}")

    query = Query(
        parent_id=parent.id if parent else None,
        entities=normalize_entities(entities, cfg["MAX_ENTITIES"]),
        entity_type=entity_type,
        relation_types=normalize_relation_types(relation_types),
        followup_question=(followup_question or "").strip() or None,
        status="pending",
    )
    db.session.add(query)
    db.session.commit()  # persist early so even a crash is visible in the admin panel

    try:
        if parent is not None and parent.evidence and _same_scope(parent, query):
            query.evidence = parent.evidence  # follow-up on same data: skip the external API calls
        else:
            query.evidence = collect_evidence(query.entities, query.entity_type, query.relation_types)
        db.session.commit()

        targets = [(cfg["PRIMARY_PROVIDER"], "primary")]
        if compare:
            targets.append((cfg["COMPARISON_PROVIDER"], "comparison"))
        generate_reports(query, targets)
        query.status = "complete"
    except Exception as exc:
        db.session.rollback()
        query.status = "failed"
        query.error_message = str(exc)[:2000]
        db.session.commit()
        raise
    db.session.commit()
    return query


def _same_scope(a, b):
    return (
        [e.upper() for e in a.entities] == [e.upper() for e in b.entities]
        and a.entity_type == b.entity_type
        and sorted(a.relation_types) == sorted(b.relation_types)
    )
