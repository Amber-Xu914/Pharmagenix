from flask import jsonify, request
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from werkzeug.exceptions import HTTPException

from .extensions import db


class APIError(Exception):
    def __init__(self, message, status=400, payload=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.payload = payload or {}  # extra JSON fields, e.g. suggestions for unresolved input


def register_error_handlers(app):
    @app.errorhandler(APIError)
    def handle_api_error(err):
        return jsonify(error=err.message, **err.payload), err.status

    @app.errorhandler(HTTPException)
    def handle_http_error(err):
        if request.path.startswith("/api/"):
            return jsonify(error=err.description), err.code
        return err

    @app.errorhandler(SQLAlchemyError)
    def handle_database_error(err):
        """MySQL down, a lost connection or a failed statement: roll back and answer with a clear message instead of
        a stack trace. 503 when the database can't be reached (worth retrying), 500 for other database errors."""
        app.logger.exception("Database error")
        try:
            db.session.rollback()
        except SQLAlchemyError:
            pass
        if isinstance(err, OperationalError):
            return jsonify(error="The database is unavailable at the moment. Please try again in a minute; "
                                 "if it persists, check that MySQL is running."), 503
        return jsonify(error="A database error occurred and nothing was saved. Please try again."), 500

    @app.errorhandler(Exception)
    def handle_unexpected(err):
        app.logger.exception("Unhandled error")
        return jsonify(error="Internal server error"), 500
