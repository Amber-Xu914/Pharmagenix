def register(client, email="alice@example.com", password="correct-horse"):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def test_register_logs_in(client):
    resp = register(client)
    assert resp.status_code == 201
    assert resp.get_json()["user"]["email"] == "alice@example.com"
    assert client.get("/api/auth/me").get_json()["user"]["email"] == "alice@example.com"


def test_register_validation(client):
    for bad_email in ["alice", "alice@example", "a b@example.com", "@example.com", "alice@@example.com"]:
        assert register(client, email=bad_email).status_code == 400, bad_email
    assert register(client, email="x" * 250 + "@a.co").status_code == 400
    assert register(client, password="short").status_code == 400
    assert register(client).status_code == 201
    assert register(client, email="  Alice@Example.COM ").status_code == 409


def test_email_is_normalised(client):
    assert register(client, email=" Bob@Example.com").get_json()["user"]["email"] == "bob@example.com"


def test_password_is_hashed(app, client):
    from app.models import User

    register(client)
    user = User.query.filter_by(email="alice@example.com").one()
    assert "correct-horse" not in user.password_hash


def test_login_and_logout(client):
    register(client)
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").get_json()["user"] is None

    bad = client.post("/api/auth/login", json={"email": "alice@example.com", "password": "wrong-password"})
    assert bad.status_code == 401
    unknown = client.post("/api/auth/login", json={"email": "bob@example.com", "password": "correct-horse"})
    assert unknown.get_json()["error"] == bad.get_json()["error"]

    resp = client.post("/api/auth/login", json={"email": "ALICE@example.com", "password": "correct-horse"})
    assert resp.status_code == 200
    assert resp.get_json()["user"]["last_login_at"]
    assert client.get("/api/auth/me").get_json()["user"]["email"] == "alice@example.com"


def test_login_requires_email(client):
    register(client)
    assert client.post("/api/auth/login", json={}).status_code == 400
    # Usernames are not accepted: login is by email only.
    assert client.post("/api/auth/login", json={"email": "alice", "password": "correct-horse"}).status_code == 400
    assert client.post("/api/auth/login", json={"username": "alice@example.com",
                                                "password": "correct-horse"}).status_code == 400
