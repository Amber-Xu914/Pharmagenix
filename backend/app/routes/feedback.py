from flask import Blueprint, jsonify, request, session

from .. import tracking
from ..accounts import check_access, current_user_id
from ..errors import APIError
from ..extensions import db
from ..models import Feedback, Report

bp = Blueprint("feedback", __name__)


@bp.post("/reports/<int:report_id>/feedback")
def submit_feedback(report_id):
    report = db.get_or_404(Report, report_id)
    check_access(report.query)  # rate only results you may open
    body = request.get_json(silent=True) or {}

    rating = body.get("rating")
    if rating is not None:
        if not isinstance(rating, int) or not 1 <= rating <= 5:
            raise APIError("rating must be an integer from 1 to 5")
    comment = (body.get("comment") or "").strip()[:5000] or None
    suggestion = (body.get("suggestion") or "").strip()[:5000] or None
    if rating is None and not comment and not suggestion:
        raise APIError("Provide a rating, comment or suggestion")

    # One rating per person per report: submitting again (a double click, a resent form) updates it instead of
    # adding a second row that would skew the average. A logged-in user is matched by account; an anonymous visitor
    # by the feedback IDs remembered in their session.
    user_id = current_user_id()
    feedback = _own_feedback(report, user_id)
    created = feedback is None
    if created:
        feedback = Feedback(report=report, user_id=user_id)
        db.session.add(feedback)
    feedback.rating, feedback.comment, feedback.suggestion = rating, comment, suggestion
    db.session.commit()
    if user_id is None:
        session["feedback"] = {**session.get("feedback", {}), str(report.id): feedback.id}
    tracking.log_feedback(report, rating, comment)  # MLflow: metric on the search's run, assessment on its trace
    return jsonify(feedback=feedback.to_dict(), report=report.to_dict(), updated=not created), 201 if created else 200


def _own_feedback(report, user_id):
    if user_id is not None:
        return Feedback.query.filter_by(report_id=report.id, user_id=user_id).order_by(Feedback.id.desc()).first()
    feedback_id = session.get("feedback", {}).get(str(report.id))
    feedback = db.session.get(Feedback, feedback_id) if feedback_id else None
    return feedback if feedback and feedback.report_id == report.id and feedback.user_id is None else None
