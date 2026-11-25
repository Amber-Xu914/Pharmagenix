"""The logged-in user (signed session cookie set by routes/auth.py), for linking queries and feedback to accounts."""

from flask import has_request_context, session

from .errors import APIError
from .extensions import db
from .models import User


def current_user():
    user_id = session.get("user_id") if has_request_context() else None
    return db.session.get(User, user_id) if user_id else None


def current_user_id():
    """The user's ID, or None when anonymous or outside a request (CLI, evaluation)."""
    user = current_user()
    return user.id if user else None


# ---------- access control: who may run and open which searches ----------
#
# A search that holds patient data (a VCF, current medications or genotypes) needs an account and is private to it
# (and to admins). A plain gene / drug search holds no patient data: it can be run without logging in, and an
# anonymous one can be opened by anyone with its link.

def has_patient_data(query):
    evidence = query.evidence or {}
    return (query.entity_type == "vcf" or bool(evidence.get("genotypes"))
            or bool((evidence.get("safety") or {}).get("medications")))


def can_view(query, user):
    if user and user.is_admin:
        return True
    if query.user_id is not None:
        return bool(user) and user.id == query.user_id
    return not has_patient_data(query)


def require_login(message="Log in to use patient data (VCF, current medications or genotypes)"):
    """The logged-in user, or 401."""
    user = current_user()
    if not user:
        raise APIError(message, 401)
    return user


def check_access(query):
    """401 when a logged-out visitor opens a private search, 403 when it belongs to another account."""
    user = current_user()
    if not can_view(query, user):
        if not user:
            raise APIError("Log in to open this result: it contains patient data", 401)
        raise APIError("This result belongs to another account", 403)
    return query
