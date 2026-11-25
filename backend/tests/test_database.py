"""Normalised storage: query owners and history, citations and query_interactions as rows, pathways, upgrade-db."""

from app.extensions import db
from app.models import Citation, Feedback, Query, QueryInteraction, User


def register(client, email="alice@example.com", password="correct-horse"):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def test_queries_and_feedback_belong_to_the_user(client, fake_sources):
    register(client)
    data = client.post("/api/explore", json={"gene": "CYP2C19", "relation_types": ["snp"]}).get_json()
    alice = User.query.filter_by(email="alice@example.com").one()
    assert db.session.get(Query, data["id"]).user_id == alice.id
    report_id = data["reports"][0]["id"]
    assert client.post(f"/api/reports/{report_id}/feedback", json={"rating": 4}).status_code == 201
    assert Feedback.query.one().user_id == alice.id


def test_history_lists_only_own_queries(client, fake_sources):
    assert client.get("/api/queries?mine=1").status_code == 401
    client.post("/api/explore", json={"gene": "CYP2C19", "relation_types": ["snp"]})  # anonymous
    register(client)
    mine = client.post("/api/explore", json={"gene": "CYP2C19", "relation_types": ["snp"]}).get_json()
    history = client.get("/api/queries?mine=1").get_json()
    assert [q["id"] for q in history] == [mine["id"]]
    assert history[0]["n_interactions"] == 2  # DGIdb + PharmGKB rows for CYP2C19–clopidogrel
    assert [q["id"] for q in client.get("/api/queries").get_json()] == [mine["id"]]  # not an admin: own only
    User.query.filter_by(email="alice@example.com").one().is_admin = True
    db.session.commit()
    assert len(client.get("/api/queries").get_json()) == 2  # an admin's unfiltered list shows everyone's


def test_citations_are_rows(client, fake_sources):
    data = client.post("/api/explore", json={"gene": "CYP2C19", "relation_types": ["snp"]}).get_json()
    rows = Citation.query.filter_by(report_id=data["reports"][0]["id"]).order_by(Citation.position).all()
    assert [(c.evidence_id, c.source, c.kind, c.pmid, c.valid) for c in rows] == [
        ("E1", "DGIdb", "interaction", 123, True), ("E2", "PharmGKB", "variant_annotation", None, True)]
    assert data["reports"][0]["citations"] == ["E1", "E2"]  # the API shape is unchanged


def test_query_interactions_mirror_the_evidence(client, fake_sources):
    data = client.post("/api/explore", json={"gene": "CYP2C19", "relation_types": ["snp"]}).get_json()
    rows = {(r.source, r.gene_symbol, r.drug_name, r.relation, r.level)
            for r in db.session.scalars(db.select(QueryInteraction).where(QueryInteraction.query_id == data["id"]))}
    assert rows == {("DGIdb", "CYP2C19", "CLOPIDOGREL", "substrate", None),
                    ("PharmGKB", "CYP2C19", "CLOPIDOGREL", "Efficacy", "1A")}


def test_admin_account_needs_no_token(app, client):
    assert client.get("/api/admin/stats").status_code == 401
    register(client)
    assert client.get("/api/admin/stats").status_code == 401  # an ordinary account is not enough
    User.query.filter_by(email="alice@example.com").one().is_admin = True
    db.session.commit()
    assert client.get("/api/admin/stats").status_code == 200


def test_empty_admin_token_is_rejected(app, client):
    app.config["ADMIN_TOKEN"] = ""
    assert client.get("/api/admin/stats", headers={"X-Admin-Token": ""}).status_code == 401


def test_pathways_come_from_the_table_once_seeded(app):
    from app.reference.models import GenePathway
    from app.services.variants.biology import pathways_of, seed_pathways

    assert pathways_of("KEAP1") == ["NRF2"]  # built-in list before seeding
    assert seed_pathways() == GenePathway.query.count() > 90
    db.session.add(GenePathway(pathway="Test", gene_symbol="KEAP1", source="test"))
    db.session.commit()
    assert pathways_of("KEAP1") == ["NRF2", "Test"]


def test_upgrade_db_is_idempotent_on_a_current_schema(app):
    from app.migrations import upgrade

    messages = []
    upgrade(echo=messages.append)
    upgrade(echo=messages.append)
    assert not any(m.startswith("added ") or m.startswith("moved ") for m in messages)
    assert messages[-1].startswith("Database up to date")


def test_seed_demo_accounts_can_log_in(app, client):
    result = app.test_cli_runner().invoke(args=["seed-demo"])
    assert result.exit_code == 0, result.output
    app.test_cli_runner().invoke(args=["seed-demo"])  # re-running resets, never duplicates
    assert User.query.count() == 2
    resp = client.post("/api/auth/login", json={"email": "admin@pharmagenix.test", "password": "Admin1234!"})
    assert resp.status_code == 200 and resp.get_json()["user"]["is_admin"] is True
    assert client.get("/api/admin/stats").status_code == 200
    resp = client.post("/api/auth/login", json={"email": "demo@pharmagenix.test", "password": "Demo1234!"})
    assert resp.get_json()["user"]["is_admin"] is False


def test_upgrade_db_fixes_old_civic_variant_links(app):
    """Patient-variant items saved with CIViC's old search URL (now the home page) get a working link."""
    from app.migrations import upgrade
    from app.models import Report
    from app.services.variants.pipeline import variant_url

    old = "https://civicdb.org/search/molecular-profiles?name="
    items = [{"id": "E1", "kind": "variant", "gene": "PIK3CA", "variant_id": "V1", "url": old + "PIK3CA"},
             {"id": "E2", "kind": "variant", "gene": "LRP1B", "variant_id": "V2", "url": old + "LRP1B"},
             {"id": "E3", "kind": "civic", "variant_ids": ["V1"], "url": "https://civicdb.org/links/evidence_items/1"}]
    query = Query(entities=["sample"], entity_type="vcf", relation_types=[], evidence={"items": items})
    report = Report(provider="anthropic", model="m", content_md="[E1] [E2]")
    query.reports.append(report)
    db.session.add(query)
    db.session.flush()
    report.set_citations(["E1", "E2"], [], {i["id"]: i for i in items})
    db.session.commit()

    messages = []
    upgrade(echo=messages.append)
    db.session.expire_all()
    saved = {i["id"]: i["url"] for i in db.session.get(Query, query.id).evidence["items"]}
    assert saved["E1"] == variant_url("PIK3CA", True) == "https://civicdb.org/links/entrez_name/PIK3CA"
    assert saved["E2"] == variant_url("LRP1B", False) and "ensembl.org" in saved["E2"]
    assert {c.evidence_id: c.url for c in db.session.get(Report, report.id).citation_rows} == \
        {"E1": saved["E1"], "E2": saved["E2"]}
    assert "patient-variant links: 2 updated (old CIViC search URL)" in messages
    upgrade(echo=messages.append)
    assert messages.count("patient-variant links: 0 updated (old CIViC search URL)") == 1
