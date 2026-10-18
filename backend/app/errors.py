from flask import jsonify, request
from werkzeug.exceptions import HTTPException


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

    @app.errorhandler(Exception)
    def handle_unexpected(err):
        app.logger.exception("Unhandled error")
        return jsonify(error="Internal server error"), 500
