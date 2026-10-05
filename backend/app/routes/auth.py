import re

from flask import Blueprint, jsonify, request, session
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from ..errors import APIError
from ..extensions import db
from ..models import User, utcnow

bp = Blueprint("auth", __name__)

# Deliberately loose (one @, a dot in the domain, no spaces); full RFC 5322 validation isn't worth it here.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL = 254
MIN_PASSWORD, MAX_PASSWORD = 8, 128


def current_user():
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def _credentials():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not email or not password:
        raise APIError("Email and password are required")
    if len(email) > MAX_EMAIL or not EMAIL_RE.match(email):
        raise APIError("Enter a valid email address")
    return email, password


def _log_in(user):
    # Start a fresh session so a session cookie set before login can't be reused (session fixation).
    session.clear()
    session["user_id"] = user.id
    session.permanent = True
    user.last_login_at = utcnow()
    db.session.commit()


@bp.post("/register")
def register():
    email, password = _credentials()
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise APIError(f"Password must be {MIN_PASSWORD}–{MAX_PASSWORD} characters")
    if User.query.filter_by(email=email).first():
        raise APIError("An account with that email already exists", 409)

    user = User(email=email, password_hash=generate_password_hash(password))
    db.session.add(user)
    try:
        db.session.flush()
    except IntegrityError:  # registered by a concurrent request since the check above
        db.session.rollback()
        raise APIError("An account with that email already exists", 409)
    _log_in(user)
    return jsonify(user=user.to_dict()), 201


@bp.post("/login")
def login():
    email, password = _credentials()
    user = User.query.filter_by(email=email).first()
    # Same message for unknown email and wrong password, so accounts can't be probed.
    if not user or not check_password_hash(user.password_hash, password):
        raise APIError("Incorrect email or password", 401)
    _log_in(user)
    return jsonify(user=user.to_dict())


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@bp.get("/me")
def me():
    user = current_user()
    return jsonify(user=user.to_dict() if user else None)
