"""CSRF protection (app/csrf.py) and safe repeated submissions (feedback, prompt templates)."""

from flask.testing import FlaskClient

from app.config import TestConfig
from app.extensions import db
from app.models import Feedback, PromptTemplate, Query, Report, User

from .conftest import log_in

ACCOUNT = {"email": "csrf@example.com", "password": "correct-horse"}


def plain_client(app):
    """A client that does NOT add the CSRF token by itself: what a forged cross-site request looks like."""
    return FlaskClient(app, app.response_class, use_cookies=True)


def make_report(user_id=None, entity_type="gene"):
    query = Query(entities=["CYP2C19"], entity_type=entity_type, relation_types=[], status="complete",
                  user_id=user_id, evidence={"items": []})
    query.reports.append(Report(provider="anthropic", model="m", content_md="text"))
    db.session.add(query)
    db.session.commit()
    return query.reports[0].id


def test_state_changing_requests_need_the_csrf_token(app):
    client = plain_client(app)
    for headers in ({}, {"X-CSRF-Token": "guessed"}):  # missing, or not the session's token
        resp = client.post("/api/auth/register", json=ACCOUNT, headers=headers)
        assert resp.status_code == 403 and resp.get_json()["code"] == "csrf"
    assert db.session.scalar(db.select(db.func.count(User.id))) == 0  # nothing was done

    token = client.get("/api/auth/csrf").get_json()["csrf_token"]
    resp = client.post("/api/auth/register", json=ACCOUNT, headers={"X-CSRF-Token": token})
    assert resp.status_code == 201
    # logging in starts a new session (session fixation), so the token changes and the old one stops working
    new_token = resp.headers["X-CSRF-Token"]
    assert new_token != token
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": token}).status_code == 403
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": new_token}).status_code == 200


def test_forged_form_post_with_the_session_cookie_is_rejected(app):
    """A page on another site can make the browser send a form POST with the user's cookie, but not the token."""
    victim = plain_client(app)
    token = victim.get("/api/auth/csrf").get_json()["csrf_token"]
    assert victim.post("/api/auth/register", json=ACCOUNT, headers={"X-CSRF-Token": token}).status_code == 201
    report_id = make_report()
    forged = victim.post(f"/api/reports/{report_id}/feedback", data={"rating": "1"},
                         content_type="application/x-www-form-urlencoded")
    assert forged.status_code == 403
    assert db.session.scalar(db.select(db.func.count(Feedback.id))) == 0


def test_every_api_response_carries_the_token_and_reads_need_none(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200 and resp.headers["X-CSRF-Token"]


def test_admin_token_header_needs_no_csrf_token(app):
    client = plain_client(app)
    body = {"name": "gene_report", "system_prompt": "S", "user_template": "U {{ entities }}"}
    assert client.post("/api/admin/templates", json=body,
                       headers={"X-Admin-Token": TestConfig.ADMIN_TOKEN}).status_code == 201
    wrong = client.post("/api/admin/templates", json=body, headers={"X-Admin-Token": "wrong"})
    assert wrong.status_code == 403 and wrong.get_json()["code"] == "csrf"


def test_resubmitted_feedback_updates_instead_of_duplicating(app, client):
    report_id = make_report()
    # an anonymous visitor (a plain gene search may be rated without an account)
    assert client.post(f"/api/reports/{report_id}/feedback", json={"rating": 2}).status_code == 201
    again = client.post(f"/api/reports/{report_id}/feedback", json={"rating": 4, "comment": "Better"})
    assert again.status_code == 200 and again.get_json()["updated"] is True
    assert again.get_json()["report"]["feedback_count"] == 1 and again.get_json()["report"]["avg_rating"] == 4

    other = app.test_client()  # a different visitor adds their own rating
    assert other.post(f"/api/reports/{report_id}/feedback", json={"rating": 5}).status_code == 201

    log_in(client)  # a logged-in user: matched by account
    assert client.post(f"/api/reports/{report_id}/feedback", json={"rating": 3}).status_code == 201
    assert client.post(f"/api/reports/{report_id}/feedback", json={"rating": 3}).status_code == 200
    ratings = sorted(f.rating for f in Feedback.query.filter_by(report_id=report_id))
    assert ratings == [3, 4, 5]


def test_saving_the_same_template_twice_creates_one_version(client, admin_headers):
    body = {"name": "gene_report", "system_prompt": "S", "user_template": "U {{ entities }}"}
    first = client.post("/api/admin/templates", json=body, headers=admin_headers)
    second = client.post("/api/admin/templates", json=body, headers=admin_headers)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.get_json()["id"] == second.get_json()["id"]
    changed = client.post("/api/admin/templates", json={**body, "system_prompt": "S2"}, headers=admin_headers)
    assert changed.status_code == 201 and changed.get_json()["version"] == first.get_json()["version"] + 1
    assert PromptTemplate.query.filter_by(name="gene_report").count() == 2
