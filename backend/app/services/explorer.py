"""Orchestrates one exploration of a single gene through the workflow stages (see services/workflow/__init__.py):

  1. resolve the gene; stop and ask the user if the name is unknown or ambiguous
  2–4. profile the gene, retrieve its drug interactions and integrate the evidence (biomed/aggregator.py)
  5. ask the LLM to interpret the evidence, unless no interaction was found
"""

from flask import current_app

from .. import tracking
from ..errors import APIError
from ..extensions import db
from ..models import Query
from ..reference.normalise import resolve_entities
from .biomed import collect_evidence
from .llm.report_generator import generate_reports
from .relations import DEFAULT_RELATION_TYPES, RELATION_TYPES
from .workflow.resolution import unresolved_problem


def normalize_gene(raw):
    if not isinstance(raw, str):
        raise APIError("gene must be a string")
    value = raw.strip()
    if not value:
        raise APIError("Please enter a gene name")
    if len(value) > 100:
        raise APIError(f"Gene name too long: {value[:30]}...")
    if any(sep in value for sep in ",;\n"):
        raise APIError("Please enter one gene at a time")
    return value


def normalize_relation_types(raw):
    if not raw:
        return list(DEFAULT_RELATION_TYPES)
    unknown = [r for r in raw if r not in RELATION_TYPES]
    if unknown:
        raise APIError(f"Unknown relation types: {', '.join(unknown)}")
    return list(dict.fromkeys(raw))


def run_exploration(gene, relation_types, compare=False, followup_question=None, parent=None,
                    confirm_unresolved=False):
    """confirm_unresolved: the user chose to search an unrecognised name as typed (it goes to the APIs as-is).
    With MLflow tracking on, the search is one run and one trace (see tracking.py)."""
    with tracking.track_search("gene", str(gene)[:100], {"gene": gene, "relation_types": relation_types,
                                                         "followup": followup_question, "compare": compare}) as handle:
        return _run_exploration(gene, relation_types, compare, followup_question, parent, confirm_unresolved, handle)


def _run_exploration(gene, relation_types, compare, followup_question, parent, confirm_unresolved, handle):
    cfg = current_app.config
    gene = normalize_gene(gene)
    relation_types = normalize_relation_types(relation_types)

    # Stage 1: entity resolution. Reusing a parent's evidence needs no new resolution.
    reuse = parent is not None and parent.evidence and _same_scope(parent, gene, relation_types)
    resolved = None
    if not reuse:
        with tracking.span("resolution", inputs={"gene": gene}) as s:
            resolved = resolve_entities([gene], "gene")[0]
            tracking.set_outputs(s, resolved)
        problem = unresolved_problem(resolved)
        if problem and not confirm_unresolved and parent is None:
            raise APIError(_problem_message(problem), 422,
                           payload={"code": "needs_confirmation", "resolution": [problem]})

    query = Query(
        parent_id=parent.id if parent else None,
        entities=[gene],
        entity_type="gene",
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
            query.evidence = collect_evidence(gene, relation_types, resolved=resolved)
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
    return {"workflow": "gene", "gene": query.entities[0], "relation_types": query.relation_types,
            "compare": compare, "followup": bool(query.followup_question), "reused_evidence": bool(reused),
            "primary_model": cfg["OPENAI_MODEL"], "comparison_model": cfg["ANTHROPIC_MODEL"],
            "template_version": next((r.template.version for r in query.reports if r.template), None),
            "max_interactions": cfg["MAX_INTERACTIONS"], "max_prompt_evidence": cfg["MAX_PROMPT_EVIDENCE"]}


def needs_analysis(evidence):
    """False when stage 5 found no interaction at all (evidence from before the workflow always has analysis)."""
    return ((evidence or {}).get("outcome") or {}).get("status") != "none"


def _same_scope(parent, gene, relation_types):
    return (
        [e.upper() for e in parent.entities] == [gene.upper()]
        and sorted(parent.relation_types) == sorted(relation_types)
    )


def _problem_message(problem):
    if problem["reason"] == "ambiguous":
        return f"“{problem['input']}” is an alias of more than one gene. Pick one or search as typed."
    return f"“{problem['input']}” was not found. Pick a suggestion or search as typed."
