import hmac
from functools import wraps

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import func

from ..errors import APIError
from ..extensions import db
from ..models import Feedback, PromptLog, PromptTemplate, Query, Report
from ..reference.lookup import latest_releases
from ..services.llm.report_generator import generate_reports
from ..services.prompts import activate_template as activate_in_workflow, render_prompt
from ..services.variants.agents import AGENT_ROLES, build_prompts as agent_prompts

bp = Blueprint("admin", __name__)


@bp.before_request
def require_admin():
    # Simple shared-token auth; replace with real accounts before deploying publicly.
    token = request.headers.get("X-Admin-Token", "")
    if not hmac.compare_digest(token, current_app.config["ADMIN_TOKEN"]):
        raise APIError("Invalid admin token", 401)


def _page():
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = min(max(request.args.get("per_page", 25, type=int), 1), 100)
    return page, per_page


def _paginate(stmt, serialize):
    page, per_page = _page()
    result = db.paginate(stmt, page=page, per_page=per_page, error_out=False)
    return jsonify(items=[serialize(x) for x in result.items], page=page, per_page=per_page, total=result.total)


# ---------- Prompt logs ----------

@bp.get("/prompt-logs")
def list_prompt_logs():
    stmt = db.select(PromptLog).order_by(PromptLog.id.desc())
    if status := request.args.get("status"):
        stmt = stmt.where(PromptLog.status == status)
    if provider := request.args.get("provider"):
        stmt = stmt.where(PromptLog.provider == provider)
    if query_id := request.args.get("query_id", type=int):
        stmt = stmt.where(PromptLog.query_id == query_id)
    return _paginate(stmt, lambda log: log.to_dict())


@bp.get("/prompt-logs/<int:log_id>")
def get_prompt_log(log_id):
    return jsonify(db.get_or_404(PromptLog, log_id).to_dict(full=True))


# ---------- Templates ----------

@bp.get("/templates")
def list_templates():
    templates = PromptTemplate.query.order_by(PromptTemplate.name, PromptTemplate.version.desc()).all()
    return jsonify([t.to_dict() for t in templates])


@bp.post("/templates")
def create_template():
    """Saving an edit always creates a new version; old versions stay attached to their logs."""
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    system_prompt = body.get("system_prompt") or ""
    user_template = body.get("user_template") or ""
    if not name or not system_prompt.strip() or not user_template.strip():
        raise APIError("name, system_prompt and user_template are required")
    _check_renders(system_prompt, user_template)

    latest = db.session.scalar(db.select(func.max(PromptTemplate.version)).where(PromptTemplate.name == name))
    template = PromptTemplate(name=name, version=(latest or 0) + 1, system_prompt=system_prompt,
                              user_template=user_template, notes=body.get("notes"), is_active=False)
    db.session.add(template)
    db.session.commit()
    if body.get("activate"):
        _activate(template)
    return jsonify(template.to_dict()), 201


@bp.post("/templates/<int:template_id>/activate")
def activate_template(template_id):
    template = db.get_or_404(PromptTemplate, template_id)
    _activate(template)
    return jsonify(template.to_dict())


@bp.post("/templates/preview")
def preview_template():
    """Render a (possibly unsaved) template against a stored query's evidence without calling an LLM."""
    body = request.get_json(silent=True) or {}
    query = db.get_or_404(Query, body.get("query_id") or 0)
    draft = PromptTemplate(system_prompt=body.get("system_prompt") or "",
                           user_template=body.get("user_template") or "")
    try:
        system, user, ids = render_prompt(draft, query, query.evidence,
                                          current_app.config["MAX_PROMPT_EVIDENCE"])
    except Exception as exc:  # noqa: BLE001 - template syntax errors are user input errors
        raise APIError(f"Template error: {exc}") from exc
    return jsonify(system_prompt=system, user_prompt=user, evidence_ids=ids)


def _activate(template):
    activate_in_workflow(template)


def _check_renders(system_prompt, user_template):
    from ..services.prompts import _env

    try:
        _env.from_string(system_prompt)
        _env.from_string(user_template)
    except Exception as exc:  # noqa: BLE001
        raise APIError(f"Template syntax error: {exc}") from exc


# ---------- Failed / flagged responses ----------

@bp.get("/failures")
def list_failures():
    statuses = request.args.getlist("status") or ["failed", "flagged"]
    stmt = db.select(Report).where(Report.status.in_(statuses)).order_by(Report.id.desc())

    def serialize(report):
        data = report.to_dict()
        data["query"] = {"id": report.query.id, "entities": report.query.entities,
                         "relation_types": report.query.relation_types}
        data["prompt_log_ids"] = [log.id for log in report.prompt_logs]
        return data

    return _paginate(stmt, serialize)


@bp.post("/reports/<int:report_id>/retry")
def retry_report(report_id):
    """Regenerate with the currently active template; the new report is added alongside the old one."""
    report = db.get_or_404(Report, report_id)
    if not report.query.evidence:
        raise APIError("The query has no stored evidence to regenerate from")
    if report.role in AGENT_ROLES:  # a Workflow 4 exploration agent: rebuilt from its own prompt
        [new_report] = generate_reports(report.query, [], extra_prompts=agent_prompts(report.query, [report.role]))
    else:
        [new_report] = generate_reports(report.query, [(report.provider, report.role)])
    return jsonify(new_report.to_dict()), 201


# ---------- Feedback & stats ----------

@bp.get("/feedback")
def list_feedback():
    stmt = db.select(Feedback).order_by(Feedback.id.desc())
    if max_rating := request.args.get("max_rating", type=int):
        stmt = stmt.where(Feedback.rating <= max_rating)

    def serialize(fb):
        return fb.to_dict() | {
            "provider": fb.report.provider,
            "model": fb.report.model,
            "query_id": fb.report.query_id,
            "entities": fb.report.query.entities,
        }

    return _paginate(stmt, serialize)


@bp.get("/stats")
def stats():
    rows = db.session.execute(
        db.select(
            Report.provider,
            Report.model,
            func.count(Report.id),
            func.sum(db.case((Report.status == "failed", 1), else_=0)),
            func.sum(db.case((Report.status == "flagged", 1), else_=0)),
            func.avg(Report.latency_ms),
        ).group_by(Report.provider, Report.model)
    ).all()
    ratings = dict(
        db.session.execute(
            db.select(Report.model, func.avg(Feedback.rating)).join(Feedback.report).group_by(Report.model)
        ).all()
    )
    return jsonify(
        reference_data={source: r.to_dict() for source, r in latest_releases().items()},
        queries=db.session.scalar(db.select(func.count(Query.id))),
        feedback=db.session.scalar(db.select(func.count(Feedback.id))),
        models=[
            {
                "provider": provider,
                "model": model,
                "reports": total,
                "failed": int(failed or 0),
                "flagged": int(flagged or 0),
                "avg_latency_ms": round(float(latency)) if latency is not None else None,
                "avg_rating": round(float(ratings[model]), 2) if ratings.get(model) is not None else None,
            }
            for provider, model, total, failed, flagged, latency in rows
        ],
    )
