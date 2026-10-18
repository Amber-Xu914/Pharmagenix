from app.services.prompts import DEFAULT_TEMPLATE_VERSION


def test_admin_requires_token(client):
    assert client.get("/api/admin/prompt-logs").status_code == 401
    assert client.get("/api/admin/prompt-logs", headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_prompt_logs_and_failures(client, fake_sources, admin_headers):
    fake_sources["answers"]["openai"] = "No citations at all."
    client.post("/api/explore", json={"gene": "BRAF"})

    logs = client.get("/api/admin/prompt-logs", headers=admin_headers).get_json()
    assert logs["total"] == 1
    detail = client.get(f"/api/admin/prompt-logs/{logs['items'][0]['id']}", headers=admin_headers).get_json()
    assert "[E1]" in detail["user_prompt"] and detail["raw_response"] == "No citations at all."

    failures = client.get("/api/admin/failures", headers=admin_headers).get_json()
    assert failures["items"][0]["status"] == "flagged"

    fake_sources["answers"]["openai"] = "Fixed [E1]."
    retried = client.post(f"/api/admin/reports/{failures['items'][0]['id']}/retry", headers=admin_headers)
    assert retried.status_code == 201 and retried.get_json()["status"] == "success"


def test_template_versioning_and_preview(client, fake_sources, admin_headers):
    query_id = client.post("/api/explore", json={"gene": "BRAF"}).get_json()["id"]
    body = {"name": "dgi_report", "system_prompt": "Be brief.", "user_template": "Genes: {{ entities|join(',') }}"}

    created = client.post("/api/admin/templates", json=body | {"activate": True}, headers=admin_headers).get_json()
    assert created["version"] == DEFAULT_TEMPLATE_VERSION + 1 and created["is_active"]
    templates = client.get("/api/admin/templates", headers=admin_headers).get_json()
    # activation is per workflow: the new gene template replaces the old one, the VCF template stays active
    assert [(t["name"], t["is_active"]) for t in templates] == [
        ("dgi_report", True), ("dgi_report", False), ("vcf_report", True)]

    bad = client.post("/api/admin/templates", json=body | {"user_template": "{% if %}"}, headers=admin_headers)
    assert bad.status_code == 400

    preview = client.post("/api/admin/templates/preview", json=body | {"query_id": query_id},
                          headers=admin_headers).get_json()
    assert preview["user_prompt"] == "Genes: BRAF"
    assert "Australian English" in preview["system_prompt"]


def test_stats(client, fake_sources, admin_headers):
    client.post("/api/explore", json={"gene": "BRAF", "compare": True})
    stats = client.get("/api/admin/stats", headers=admin_headers).get_json()
    assert stats["queries"] == 1
    assert {m["provider"] for m in stats["models"]} == {"openai", "anthropic"}
