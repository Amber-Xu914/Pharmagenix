"""The local store of live API answers (app/http_cache.py), against a local server that fails on purpose."""

import http.server
import json
import threading

import pytest
import requests

from app.http_cache import FROM_CACHE_HEADER, cached_session, replayed_hosts


@pytest.fixture
def server():
    """Answers 200 the first time, then 503. stop() takes it down entirely."""
    calls = {"n": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self):
            calls["n"] += 1
            body = json.dumps({"call": calls["n"]}).encode()
            self.send_response(200 if calls["n"] == 1 else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _answer

        def log_message(self, *args):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{srv.server_port}/api", "calls": calls,
           "stop": lambda: (srv.shutdown(), srv.server_close())}
    srv.server_close()


@pytest.fixture
def cache(app, tmp_path):
    app.config.update(HTTP_CACHE="fallback", HTTP_CACHE_PATH=str(tmp_path / "cache.sqlite"))
    return app


def test_fallback_serves_the_stored_answer_when_the_api_fails(cache, server):
    session = cached_session()
    first = session.post(server["url"], json={"q": 1})
    assert first.json() == {"call": 1} and FROM_CACHE_HEADER not in first.headers

    second = session.post(server["url"], json={"q": 1})  # live answer is a 503
    assert second.status_code == 200 and second.json() == {"call": 1}
    assert second.headers[FROM_CACHE_HEADER] == "fallback" and server["calls"]["n"] == 2  # it did try live

    server["stop"]()
    third = session.post(server["url"], json={"q": 1}, timeout=2)  # no connection at all
    assert third.json() == {"call": 1}
    assert replayed_hosts() == ["127.0.0.1"]


def test_failures_without_a_stored_answer_still_fail(cache, server):
    session = cached_session()
    session.post(server["url"], json={"q": 1})
    assert session.post(server["url"], json={"q": 2}).status_code == 503  # a different body is a different request
    server["stop"]()
    with pytest.raises(requests.ConnectionError):
        session.post(server["url"], json={"q": 3}, timeout=2)


def test_offline_answers_from_the_store_without_going_online(cache, server):
    cached_session().get(server["url"])
    cache.config["HTTP_CACHE"] = "offline"
    response = cached_session().get(server["url"])
    assert response.json() == {"call": 1} and response.headers[FROM_CACHE_HEADER] == "offline"
    assert server["calls"]["n"] == 1


def test_headers_are_not_part_of_the_key(cache, server):
    cached_session().get(server["url"], headers={"Authorization": "Bearer one"})
    response = cached_session().get(server["url"], headers={"Authorization": "Bearer two"})
    assert response.json() == {"call": 1}  # same request, other token: the stored answer is reused on failure


def test_off_is_a_plain_session(app, server):
    session = cached_session()  # TestConfig: HTTP_CACHE = "off"
    session.get(server["url"])
    assert session.get(server["url"]).status_code == 503
