"""One complete user workflow through the HTTP API, in the order a user clicks through it: create an account, upload
a synthetic VCF, reopen the result from its link, ask a follow-up, rate the answer (twice), download the Word report,
see both searches in the history, log out, and check that the result is then private. The external sources and the
LLMs are stubbed (see test_vcf.py), so the run is offline and repeatable."""

from io import BytesIO

from app.extensions import db
from app.models import Feedback, Query
from app.services.vcf_explorer import mock_path

from .conftest import log_in
from .test_vcf import FakeLLM, stub_live, vcf_reference  # noqa: F401 - fixtures

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_complete_vcf_workflow(app, client, vcf_reference, stub_live, monkeypatch):  # noqa: F811
    prompts = []
    monkeypatch.setattr("app.services.llm.report_generator.get_llm_client", lambda provider: FakeLLM(prompts))

    # 1. an ordinary user creates an account (which logs them in)
    user = log_in(client, "researcher@example.com")
    assert client.get("/api/auth/me").get_json()["user"]["email"] == "researcher@example.com"

    # 2. uploads a synthetic VCF, confirming it is test data
    _, path = mock_path("mock_luad_egfr_t790m_c797s")
    form = {"file": (BytesIO(path.read_bytes()), "patient.vcf"), "cancer_type": "LUAD", "test_data": "1"}
    resp = client.post("/api/vcf/explore", data=form, content_type="multipart/form-data")
    assert resp.status_code == 201, resp.get_json()
    result = resp.get_json()
    assert result["status"] == "complete" and result["user_id"] == user["id"]
    tiers = {v["notation"]: v["tier"] for v in result["evidence"]["variants"]["tiered"]}
    assert tiers["EGFR p.C797S"] == 1 and tiers["EGFR p.T790M"] == 1
    assert {r["role"] for r in result["reports"]} == {"primary", "literature", "biology", "hypothesis", "clinical"}

    # 3. reopens it from its link (?q=<id>), exactly as stored
    reopened = client.get(f"/api/queries/{result['id']}").get_json()
    assert reopened["evidence"] == result["evidence"]

    # 4. asks a follow-up question: the stored evidence is reused, the agents run again
    followup = client.post(f"/api/queries/{result['id']}/ask", json={"followup_question": "Why not osimertinib?"})
    assert followup.status_code == 201
    follow = followup.get_json()
    assert follow["parent_id"] == result["id"] and follow["evidence"] == result["evidence"]
    assert any("Why not osimertinib?" in user_prompt for _, user_prompt in prompts[-5:])

    # 5. rates the analysis, then changes the rating: one rating per person
    report_id = next(r["id"] for r in follow["reports"] if r["role"] == "primary")
    assert client.post(f"/api/reports/{report_id}/feedback", json={"rating": 3}).status_code == 201
    assert client.post(f"/api/reports/{report_id}/feedback", json={"rating": 5, "comment": "Clear"}).status_code == 200
    [rating] = Feedback.query.filter_by(report_id=report_id).all()
    assert (rating.rating, rating.comment, rating.user_id) == (5, "Clear", user["id"])

    # 6. downloads the Word report
    docx = client.get(f"/api/queries/{follow['id']}/report.docx")
    assert docx.status_code == 200 and docx.mimetype == DOCX and docx.data[:2] == b"PK"

    # 7. both searches are in the history, newest first
    history = client.get("/api/queries?mine=1").get_json()
    assert [q["id"] for q in history] == [follow["id"], result["id"]]

    # 8. logs out: the result is private again, and another account cannot open it
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get(f"/api/queries/{result['id']}").status_code == 401
    other = app.test_client()
    log_in(other, "someone-else@example.com")
    assert other.get(f"/api/queries/{result['id']}").status_code == 403
    assert db.session.scalar(db.select(db.func.count(Query.id))) == 2
