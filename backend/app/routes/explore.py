from flask import Blueprint, current_app, jsonify, request

from ..errors import APIError
from ..extensions import db
from ..models import Query
from ..services.explorer import run_exploration
from ..services.llm import provider_info
from ..services.panel import parse_gene_panel
from ..services.relations import DEFAULT_RELATION_TYPES, RELATION_TYPES

bp = Blueprint("explore", __name__)

MAX_PANEL_BYTES = 200_000


@bp.get("/meta")
def meta():
    """Everything the frontend needs to render the search form."""
    return jsonify(
        relation_types=[{"key": k, "label": v["label"]} for k, v in RELATION_TYPES.items()],
        default_relation_types=DEFAULT_RELATION_TYPES,
        providers=provider_info(),
        limits={"max_entities": current_app.config["MAX_ENTITIES"]},
    )


@bp.post("/panels/parse")
def parse_panel():
    upload = request.files.get("file")
    if upload is None:
        raise APIError("Upload a gene panel file in the 'file' field")
    raw = upload.read(MAX_PANEL_BYTES + 1)
    if len(raw) > MAX_PANEL_BYTES:
        raise APIError("Gene panel file is too large (max 200 KB)")
    text = raw.decode("utf-8", errors="replace")
    return jsonify(parse_gene_panel(text, current_app.config["MAX_ENTITIES"]))


@bp.post("/explore")
def explore():
    body = request.get_json(silent=True) or {}
    query = run_exploration(
        entities=body.get("entities"),
        entity_type=body.get("entity_type", "auto"),
        relation_types=body.get("relation_types"),
        compare=bool(body.get("compare")),
        followup_question=body.get("followup_question"),
    )
    return jsonify(query.to_dict()), 201


@bp.get("/queries")
def list_queries():
    limit = min(request.args.get("limit", 20, type=int), 100)
    queries = Query.query.order_by(Query.id.desc()).limit(limit).all()
    return jsonify([q.to_dict(include_evidence=False) for q in queries])


@bp.get("/queries/<int:query_id>")
def get_query(query_id):
    return jsonify(db.get_or_404(Query, query_id).to_dict())


@bp.post("/queries/<int:query_id>/ask")
def ask_again(query_id):
    """Re-ask / follow-up. Reuses the parent's evidence when entities and relation types are unchanged."""
    parent = db.get_or_404(Query, query_id)
    body = request.get_json(silent=True) or {}
    query = run_exploration(
        entities=body.get("entities") or parent.entities,
        entity_type=body.get("entity_type") or parent.entity_type,
        relation_types=body.get("relation_types") or parent.relation_types,
        compare=bool(body.get("compare")),
        followup_question=body.get("followup_question"),
        parent=parent,
    )
    return jsonify(query.to_dict()), 201
