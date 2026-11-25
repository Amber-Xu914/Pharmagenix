"""CSRF protection for the JSON API: a synchronizer token per session.

Logins use Flask's session cookie, which the browser attaches to every request to this site, including one that a
page on another site makes it send (cross-site request forgery). So every state-changing request (POST, PUT, PATCH,
DELETE under /api/) must also carry the session's token in the X-CSRF-Token header. Another site cannot read the
token, so it cannot forge the request. The cookie's SameSite=Lax setting (config.py) is a second layer.

- The token is random (secrets.token_urlsafe), kept in the signed session and compared in constant time.
- Every /api/ response carries the current token in the X-CSRF-Token header; frontend/js/api.js keeps the latest one.
  Logging in or out starts a new session (accounts: session fixation), so the token changes there too.
- A request authenticated by the admin token header (X-Admin-Token, e.g. from a script) needs no CSRF token: a browser
  never adds that header by itself. The header must be valid; the admin routes check it again.
"""

import hmac
import secrets

from flask import current_app, request, session

from .errors import APIError

HEADER = "X-CSRF-Token"
SESSION_KEY = "csrf_token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def csrf_token():
    """The session's token, created on first use."""
    token = session.get(SESSION_KEY)
    if not token:
        token = session[SESSION_KEY] = secrets.token_urlsafe(32)
    return token


def _admin_token_valid():
    sent = request.headers.get("X-Admin-Token", "")
    expected = current_app.config.get("ADMIN_TOKEN") or ""
    return bool(sent and expected) and hmac.compare_digest(sent, expected)


def init_csrf(app):
    @app.before_request
    def check_csrf_token():
        if not app.config.get("CSRF_ENABLED", True):
            return
        if request.method not in UNSAFE_METHODS or not request.path.startswith("/api/"):
            return
        if _admin_token_valid():
            return
        sent = request.headers.get(HEADER, "")
        expected = session.get(SESSION_KEY, "")
        if not (sent and expected and hmac.compare_digest(sent, expected)):
            raise APIError("This request could not be verified (missing or expired security token). Reload the page "
                           "and try again.", 403, {"code": "csrf"})

    @app.after_request
    def send_csrf_token(response):
        if request.path.startswith("/api/"):
            response.headers[HEADER] = csrf_token()
            response.vary.add("Cookie")
        return response
