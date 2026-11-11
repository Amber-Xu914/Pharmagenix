"""Orchestrates one exploration of a single gene (gene -> drugs) or drug (drug -> genes) through the workflow
stages (see services/workflow/__init__.py):

  1. resolve the name; stop and ask the user if it is unknown or ambiguous
  2–4. profile it, retrieve its drug–gene interactions and integrate the evidence (biomed/aggregator.py)
  5. ask the LLM to interpret the evidence, unless no interaction was found
"""

from flask import current_app

from .. import tracking
from ..accounts import current_user_id
from ..errors import APIError
from ..http_cache import replayed_hosts
from ..extensions import db
from ..models import Query
from ..reference.normalise import resolve_entities
from .biomed import collect_evidence
from .interactions import record_interactions
from .llm.report_generator import generate_reports
from .relations import DEFAULT_RELATION_TYPES, RELATION_TYPES
from .workflow.resolution import unresolved_problem


ENTITY_TYPES = ("gene", "drug")


def normalize_name(raw, entity_type="gene"):
    if entity_type not in ENTITY_TYPES:
        raise APIError(f"entity_type must be one of: {', '.join(ENTITY_TYPES)}")
    if not isinstance(raw, str):
        raise APIError(f"{entity_type} must be a string")
    value = raw.strip()
    if not value:
        raise APIError(f"Please enter a {entity_type} name")
    if len(value) > 100:
        raise APIError(f"{entity_type.capitalize()} name too long: {value[:30]}...")
    if any(sep in value for sep in ",;\n"):
        raise APIError(f"Please enter one {entity_type} at a time")
    return value


def normalize_relation_types(raw):
    if not raw:
        return list(DEFAULT_RELATION_TYPES)
    unknown = [r for r in raw if r not in RELATION_TYPES]
    if unknown:
        raise APIError(f"Unknown relation types: {', '.join(unknown)}")
    return list(dict.fromkeys(raw))


def run_exploration(name, relation_types, compare=False, followup_question=None, parent=None,
                    confirm_unresolved=False, entity_type="gene", medications=()):
    """name: a gene (entity_type "gene": gene -> drugs) or a drug ("drug": drug -> genes).
    confirm_unresolved: the user chose to search an unrecognised name as typed (it goes to the APIs as-is).
    With MLflow tracking on, the search is one run and one trace (see tracking.py)."""
    with tracking.track_search("gene", str(name)[:100], {entity_type: name, "relation_types": relation_types,
                                                         "followup": followup_question, "compare": compare}) as handle:
        return _run_exploration(name, entity_type, relation_types, compare, followup_question, parent,
                                confirm_unresolved, handle, medications)


def _run_exploration(name, entity_type, relation_types, compare, followup_question, parent, confirm_unresolved,
                     handle, medications=()):
    cfg = current_app.config
    name = normalize_name(name, entity_type)
    relation_types = normalize_relation_types(relation_types)

    # Stage 1: entity resolution. Reusing a parent's evidence needs no new resolution.
    reuse = parent is not None and parent.evidence and _same_scope(parent, name, entity_type, relation_types)
    resolved = None
    if not reuse:
        with tracking.span("resolution", inputs={entity_type: name}) as s:
            resolved = resolve_entities([name], entity_type)[0]
            tracking.set_outputs(s, resolved)
        problem = unresolved_problem(resolved, entity_type)
        if problem and not confirm_unresolved and parent is None:
            raise APIError(_problem_message(problem, entity_type), 422,
                           payload={"code": "needs_confirmation", "resolution": [problem]})

    query = Query(
        parent_id=parent.id if parent else None,
        user_id=current_user_id(),
        entities=[name],
        entity_type=entity_type,
        relation_types=relation_types,
        followup_question=(followup_question or "").strip() or None,
        status="pending",
    )
    db.session.add(query)
    db.session.commit()  # persist early so even a crash is visible in the admin panel

    try:
        if reuse:
            query.evidence = parent.evidence  # follow-up on same data: skip the external API calls
        else:
            evidence = collect_evidence(name, relation_types, resolved=resolved, entity_type=entity_type,
                                        medications=medications)
            query.evidence = {**evidence, "cached_sources": replayed_hosts()}
        record_interactions(query)
        db.session.commit()

        # Stage 5: the LLM only interprets evidence. With no known interaction the report lists the sources
        # checked (rendered by the frontend) and no LLM-generated claims are made.
        if needs_analysis(query.evidence):
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
        tracking.log_query(handle, query, _params(query, compare, reuse))
        raise
    db.session.commit()
    tracking.log_query(handle, query, _params(query, compare, reuse))
    return query


def _params(query, compare, reused):
    cfg = current_app.config
    return {"workflow": query.entity_type, query.entity_type: query.entities[0], "relation_types": query.relation_types,
            "compare": compare, "followup": bool(query.followup_question), "reused_evidence": bool(reused),
            "primary_model": cfg["OPENAI_MODEL"], "comparison_model": cfg["ANTHROPIC_MODEL"],
            "template_version": next((r.template.version for r in query.reports if r.template), None),
            "max_interactions": cfg["MAX_INTERACTIONS"], "max_prompt_evidence": cfg["MAX_PROMPT_EVIDENCE"]}


def needs_analysis(evidence):
    """False when stage 5 found no interaction at all (evidence from before the workflow always has analysis)."""
    return ((evidence or {}).get("outcome") or {}).get("status") != "none"


def _same_scope(parent, name, entity_type, relation_types):
    return (
        parent.entity_type == entity_type
        and [e.upper() for e in parent.entities] == [name.upper()]
        and sorted(parent.relation_types) == sorted(relation_types)
    )


def _problem_message(problem, entity_type="gene"):
    if problem["reason"] == "ambiguous":
        return f"“{problem['input']}” is an alias of more than one {entity_type}. Pick one or search as typed."
    return f"“{problem['input']}” was not found. Pick a suggestion or search as typed."
