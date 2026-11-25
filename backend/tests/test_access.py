"""Access control (accounts.py): searches with patient data (VCF, medications, genotypes) need an account and are
private to it and to admins; plain anonymous gene / drug searches stay open."""

import pytest

from app.extensions import db
from app.models import Query, Report, User

from .conftest import log_in


def make_query(user=None, entity_type="vcf", evidence=None):
    query = Query(entities=["sample"], entity_type=entity_type, relation_types=[], status="complete",
                  user_id=user and user["id"], evidence=evidence or {"items": []})
    query.reports.append(Report(provider="anthropic", model="m", content_md="text"))
    db.session.add(query)
    db.session.commit()
    return query.id, query.reports[0].id


@pytest.fixture
def alice(app):
    client = app.test_client()
    return client, log_in(client, "alice@example.com")


@pytest.fixture
def bob(app):
    client = app.test_client()
    return client, log_in(client, "bob@example.com")


def test_patient_searches_need_login(client):
    assert client.post("/api/vcf/explore", json={"example": "mock_luad_kras_g12c"}).status_code == 401
    assert client.post("/api/vcf/ism", json={"chrom": "7", "pos": 1}).status_code == 401
    for extra in ({"medications": "fluoxetine"}, {"genotypes": "CYP2C19 *2/*2"}):
        resp = client.post("/api/explore", json={"gene": "CYP2C19", **extra})
        assert resp.status_code == 401 and "Log in" in resp.get_json()["error"]


def test_owner_opens_own_result_and_others_get_403(alice, bob):
    (a_client, a_user), (b_client, _) = alice, bob
    qid, rid = make_query(a_user)
    assert a_client.get(f"/api/queries/{qid}").status_code == 200
    for resp in (b_client.get(f"/api/queries/{qid}"),
                 b_client.post(f"/api/queries/{qid}/ask", json={"followup_question": "why?"}),
                 b_client.get(f"/api/queries/{qid}/report.docx"),
                 b_client.post(f"/api/reports/{rid}/feedback", json={"rating": 5})):
        assert resp.status_code == 403 and resp.get_json()["error"] == "This result belongs to another account"


def test_logged_out_visitor_gets_401_for_private_results(client, alice):
    qid, _ = make_query(alice[1])
    anonymous_vcf, _ = make_query(None)  # made before accounts were required: admins only
    with_meds, _ = make_query(None, "gene", {"items": [], "safety": {"medications": [{"name": "fluoxetine"}]}})
    for query_id in (qid, anonymous_vcf, with_meds):
        resp = client.get(f"/api/queries/{query_id}")
        assert resp.status_code == 401 and "patient data" in resp.get_json()["error"]


def test_anonymous_gene_search_stays_open(client, alice):
    qid, rid = make_query(None, "gene")
    assert client.get(f"/api/queries/{qid}").status_code == 200
    assert alice[0].get(f"/api/queries/{qid}").status_code == 200
    assert client.post(f"/api/reports/{rid}/feedback", json={"rating": 4}).status_code == 201


def test_admin_opens_everything(alice, bob):
    (a_client, a_user), (b_client, _) = alice, bob
    qid, _ = make_query(a_user)
    User.query.filter_by(email="bob@example.com").one().is_admin = True
    db.session.commit()
    assert b_client.get(f"/api/queries/{qid}").status_code == 200
    assert [q["id"] for q in b_client.get("/api/queries").get_json()] == [qid]  # admin: everyone's
    assert b_client.get("/api/queries?mine=1").get_json() == []                  # ?mine=1: own only


def test_query_list_needs_login(client):
    assert client.get("/api/queries").status_code == 401


def test_synthetic_examples_are_for_admins_only(app, client, alice):
    a_client = alice[0]
    for c in (client, a_client):  # logged out, and an ordinary user
        assert c.get("/api/vcf/options").get_json()["examples"] == []
    assert client.post("/api/vcf/explore", json={"example": "mock_luad_kras_g12c"}).status_code == 401
    resp = a_client.post("/api/vcf/explore", json={"example": "mock_luad_kras_g12c"})
    assert resp.status_code == 403 and "administrators only" in resp.get_json()["error"]
    assert a_client.get("/api/vcf/examples/mock_luad_kras_g12c").status_code == 403

    admin = app.test_client()
    log_in(admin, "admin@example.com", admin=True)
    assert len(admin.get("/api/vcf/options").get_json()["examples"]) == 8
    assert admin.get("/api/vcf/examples/mock_luad_kras_g12c").status_code == 200
