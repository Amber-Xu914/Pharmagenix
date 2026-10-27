"""The logged-in user (signed session cookie set by routes/auth.py), for linking queries and feedback to accounts."""

from flask import has_request_context, session

from .extensions import db
from .models import User


def current_user():
    user_id = session.get("user_id") if has_request_context() else None
    return db.session.get(User, user_id) if user_id else None


def current_user_id():
    """The user's ID, or None when anonymous or outside a request (CLI, evaluation)."""
    user = current_user()
    return user.id if user else None
