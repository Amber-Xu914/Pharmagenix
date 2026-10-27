from flask import Blueprint, jsonify, request

from ..accounts import current_user
from ..errors import APIError
from ..extensions import db
from ..models import Query, iso
from ..reference.lookup import entity_details, latest_releases
from ..services.explorer import run_exploration
from ..services.vcf_explorer import run_vcf_followup
from ..services.llm import provider_info
from ..services.relations import DEFAULT_RELATION_TYPES, RELATION_TYPES

bp = Blueprint("explore", __name__)


def _query_response(query, status=200):
    data = query.to_dict()
    # Aligned with evidence.normalisation; feeds the gene header on the results page.
    data["entity_details"] = entity_details((query.evidence or {}).get("normalisation"))
    return jsonify(data), status


@bp.get("/meta")
def meta():
    """Everything the frontend needs to render the search form."""
    return jsonify(
        relation_types=[{"key": k, "label": v["label"]} for k, v in RELATION_TYPES.items()],
        default_relation_types=DEFAULT_RELATION_TYPES,
        providers=provider_info(),
        # Shown on the home page as "reference data last updated"; empty until `import-data` has run.
        reference_data=[{"source": source, "version": r.version, "imported_at": iso(r.imported_at)}
                        for source, r in latest_releases().items()],
    )


@bp.post("/explore")
def explore():
    """{"gene": "CYP2C19"} (gene -> drugs) or {"entity_type": "drug", "drug": "clopidogrel"} (drug -> genes)."""
    body = request.get_json(silent=True) or {}
    entity_type = body.get("entity_type") or "gene"
    query = run_exploration(
        body.get(entity_type) if entity_type in ("gene", "drug") else None,
        body.get("relation_types"),
        compare=bool(body.get("compare")),
        followup_question=body.get("followup_question"),
        confirm_unresolved=bool(body.get("confirm_unresolved")),
        entity_type=entity_type,
    )
    return _query_response(query, 201)


@bp.get("/queries")
def list_queries():
    """Recent queries; ?mine=1 limits them to the logged-in user's own (the query history page)."""
    limit = min(request.args.get("limit", 20, type=int), 100)
    stmt = db.select(Query).order_by(Query.id.desc()).limit(limit)
    if request.args.get("mine"):
        user = current_user()
        if not user:
            raise APIError("Log in to see your query history", 401)
        stmt = stmt.where(Query.user_id == user.id)
    queries = db.session.scalars(stmt).all()
    return jsonify([_summary(q) for q in queries])


def _summary(query):
    data = query.to_dict(include_evidence=False)
    inp = (query.evidence or {}).get("input") or {}
    data["cancer_type"] = (inp.get("cancer_type") or {}).get("code")
    data["n_interactions"] = len(query.interactions)
    return data


@bp.get("/queries/<int:query_id>")
def get_query(query_id):
    return _query_response(db.get_or_404(Query, query_id))


@bp.post("/queries/<int:query_id>/ask")
def ask_again(query_id):
    """Re-ask / follow-up. Reuses the parent's evidence when the gene and relation types are unchanged."""
    parent = db.get_or_404(Query, query_id)
    body = request.get_json(silent=True) or {}
    if parent.entity_type == "vcf":
        query = run_vcf_followup(parent, body.get("followup_question"), bool(body.get("compare")))
        return _query_response(query, 201)
    query = run_exploration(
        parent.entities[0],
        body.get("relation_types") or parent.relation_types,
        compare=bool(body.get("compare")),
        followup_question=body.get("followup_question"),
        parent=parent,
        entity_type=parent.entity_type if parent.entity_type in ("gene", "drug") else "gene",
    )
    return _query_response(query, 201)
