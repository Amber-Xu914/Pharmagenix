"""Error handling: database failures give a clear JSON message (no stack trace) and leave nothing half-saved."""

from sqlalchemy.exc import IntegrityError, OperationalError

from app.extensions import db
from app.models import Feedback, Query, Report


def mysql_down(*args, **kwargs):
    raise OperationalError("SELECT ...", {}, Exception("(2003) Can't connect to MySQL server on 'localhost'"))


def test_database_unavailable_gives_503(client, monkeypatch):
    monkeypatch.setattr(db.session, "get", mysql_down)
    resp = client.get("/api/queries/1")
    assert resp.status_code == 503
    error = resp.get_json()["error"]
    assert "database is unavailable" in error and "MySQL" in error and "2003" not in error  # no internals leaked


def test_failed_write_is_rolled_back(client, monkeypatch):
    query = Query(entities=["CYP2C19"], entity_type="gene", relation_types=[], status="complete", evidence={})
    query.reports.append(Report(provider="anthropic", model="m", content_md="text"))
    db.session.add(query)
    db.session.commit()
    report_id = query.reports[0].id

    def failing_commit():
        raise IntegrityError("INSERT INTO feedback ...", {}, Exception("constraint failed"))

    monkeypatch.setattr(db.session, "commit", failing_commit)
    resp = client.post(f"/api/reports/{report_id}/feedback", json={"rating": 5})
    assert resp.status_code == 500 and resp.get_json()["error"].startswith("A database error occurred")
    monkeypatch.undo()
    assert Feedback.query.count() == 0  # the half-done insert was rolled back


def test_unexpected_error_hides_the_stack_trace(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("app.routes.explore.run_exploration", boom)
    resp = client.post("/api/explore", json={"gene": "CYP2C19"})
    assert resp.status_code == 500 and resp.get_json() == {"error": "Internal server error"}


def test_unknown_api_route_is_json_404(client):
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404 and "error" in resp.get_json()
