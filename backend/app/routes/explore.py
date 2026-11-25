import base64
import binascii
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from ..accounts import check_access, has_patient_data, require_login
from ..errors import APIError
from ..extensions import db
from ..models import Query, iso
from ..reference.lookup import entity_details, latest_releases
from ..services.explorer import run_exploration
from ..services.vcf_explorer import run_vcf_followup
from ..services.llm import provider_info
from ..services.report_docx import DOCX_MIME, MAX_FIGURE_BYTES, build_report
from ..services.genotypes import parse_genotypes
from ..services.safety import parse_medications
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
    """{"gene": "CYP2C19"} (gene -> drugs) or {"entity_type": "drug", "drug": "clopidogrel"} (drug -> genes).
    Optional "medications": the patient's current medications, checked for drug–drug interactions.
    Optional "genotypes": the patient's pharmacogene results ("CYP2C19 *2/*2; HLA-B*57:01 negative" or a list),
    turned into CPIC phenotypes and recommendations."""
    body = request.get_json(silent=True) or {}
    entity_type = body.get("entity_type") or "gene"
    medications, genotypes = parse_medications(body.get("medications")), parse_genotypes(body.get("genotypes"))
    if medications or genotypes:
        require_login()
    query = run_exploration(
        body.get(entity_type) if entity_type in ("gene", "drug") else None,
        body.get("relation_types"),
        compare=bool(body.get("compare")),
        followup_question=body.get("followup_question"),
        confirm_unresolved=bool(body.get("confirm_unresolved")),
        entity_type=entity_type,
        medications=medications,
        genotypes=genotypes,
    )
    return _query_response(query, 201)


@bp.get("/queries")
def list_queries():
    """The logged-in user's recent queries (the query history page); an admin may leave out ?mine=1 to list everyone's."""
    limit = min(request.args.get("limit", 20, type=int), 100)
    stmt = db.select(Query).order_by(Query.id.desc()).limit(limit)
    user = require_login("Log in to see your query history")
    if request.args.get("mine") or not user.is_admin:
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
    return _query_response(check_access(db.get_or_404(Query, query_id)))


@bp.post("/queries/<int:query_id>/ask")
def ask_again(query_id):
    """Re-ask / follow-up. Reuses the parent's evidence when the gene and relation types are unchanged."""
    parent = check_access(db.get_or_404(Query, query_id))
    if has_patient_data(parent):
        require_login()
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


@bp.route("/queries/<int:query_id>/report.docx", methods=["GET", "POST"])
def report_docx(query_id):
    """The query as a Word report (services/report_docx.py). POST {figure: PNG data URL of the network,
    figure_shown, figure_total} adds the network as Figure 1; GET gives the report without it."""
    query = check_access(db.get_or_404(Query, query_id))
    if query.status != "complete" or not query.evidence:
        raise APIError("This search has no results to report")
    figure, note = None, None
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        data_url = body.get("figure") or ""
        if data_url:
            encoded = data_url.split(",", 1)[-1]
            if len(encoded) > MAX_FIGURE_BYTES * 4 // 3 + 4:
                raise APIError("The figure is too large")
            try:
                figure = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise APIError("The figure must be a base64-encoded PNG") from None
        note = {"shown": body.get("figure_shown"), "total": body.get("figure_total")}
    try:
        content, filename = build_report(query.to_dict(), figure, note)
    except ValueError as exc:  # a figure that is not a PNG
        raise APIError(str(exc)) from None
    return send_file(BytesIO(content), mimetype=DOCX_MIME, as_attachment=True, download_name=filename)
