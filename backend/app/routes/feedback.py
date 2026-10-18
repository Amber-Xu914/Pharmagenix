from flask import Blueprint, jsonify, request

from .. import tracking
from ..errors import APIError
from ..extensions import db
from ..models import Feedback, Report

bp = Blueprint("feedback", __name__)


@bp.post("/reports/<int:report_id>/feedback")
def submit_feedback(report_id):
    report = db.get_or_404(Report, report_id)
    body = request.get_json(silent=True) or {}

    rating = body.get("rating")
    if rating is not None:
        if not isinstance(rating, int) or not 1 <= rating <= 5:
            raise APIError("rating must be an integer from 1 to 5")
    comment = (body.get("comment") or "").strip()[:5000] or None
    suggestion = (body.get("suggestion") or "").strip()[:5000] or None
    if rating is None and not comment and not suggestion:
        raise APIError("Provide a rating, comment or suggestion")

    feedback = Feedback(report=report, rating=rating, comment=comment, suggestion=suggestion)
    db.session.add(feedback)
    db.session.commit()
    tracking.log_feedback(report, rating, comment)  # MLflow: metric on the search's run, assessment on its trace
    return jsonify(feedback=feedback.to_dict(), report=report.to_dict()), 201
