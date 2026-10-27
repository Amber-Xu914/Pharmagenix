"""A local cache of the live API responses (VEP, PubTator, DGIdb, PharmGKB, UniProt, OncoKB, ...), so a search
still works when a public API is down, or with no network at all for a demo.

HTTP_CACHE (config / .env):
  fallback  (default) always ask the live API and store each successful answer; when the API fails (HTTP 5xx or
            429, a timeout, no connection), answer from the last stored copy instead
  offline   answer from the store whenever it has the request, without going online; live only for new requests
            (fill the store first with `flask --app run warm-cache`)
  off       no cache

Requests are keyed by method, URL and body (never headers, so API tokens are not part of the key), and stored in a
SQLite file (HTTP_CACHE_PATH). Only the live clients use it; the reference-data importers always download afresh.
"""

import hashlib
import json
import sqlite3
import threading
import time

import requests
from urllib.parse import urlsplit

from flask import current_app, g, has_app_context
from requests.adapters import HTTPAdapter
from requests.structures import CaseInsensitiveDict

MODES = ("fallback", "offline", "off")
FAILURE_CODES = {429, 500, 502, 503, 504}
FROM_CACHE_HEADER = "X-Pharmagenix-Cache"  # set on answers served from the store: "fallback" or "offline"


class ResponseStore:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, method TEXT, url TEXT, "
                         "status INTEGER, headers TEXT, body BLOB, saved_at REAL)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def get(self, key):
        with self.lock, self._connect() as conn:
            return conn.execute("SELECT status, headers, body, url FROM responses WHERE key = ?", (key,)).fetchone()

    def put(self, key, method, response):
        headers = {k: v for k, v in response.headers.items()
                   if k.lower() not in ("content-encoding", "transfer-encoding", "content-length", "set-cookie")}
        with self.lock, self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (key, method, response.url, response.status_code, json.dumps(headers), response.content,
                          time.time()))

    def count(self):
        with self.lock, self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]


_stores = {}


def _store(path):
    if path not in _stores:
        _stores[path] = ResponseStore(path)
    return _stores[path]


def request_key(request):
    body = request.body or b""
    if isinstance(body, str):
        body = body.encode()
    return hashlib.sha256(b"\n".join([request.method.encode(), request.url.encode(), body])).hexdigest()


class CachingAdapter(HTTPAdapter):
    """An HTTPAdapter (keeps its retry settings) that stores successful answers and replays them per the mode."""

    def __init__(self, store, mode, **kwargs):
        super().__init__(**kwargs)
        self.store, self.mode = store, mode

    def send(self, request, **kwargs):
        key = request_key(request)
        if self.mode == "offline":
            stored = self.store.get(key)
            if stored:
                return self._replay(request, stored, "offline")
        try:
            response = super().send(request, **kwargs)
        except requests.RequestException:
            stored = self.store.get(key)
            if stored:
                return self._replay(request, stored, "fallback")
            raise
        if response.status_code == 200:
            self.store.put(key, request.method, response)
        elif response.status_code in FAILURE_CODES:
            stored = self.store.get(key)
            if stored:
                return self._replay(request, stored, "fallback")
        return response

    @staticmethod
    def _replay(request, stored, how):
        if has_app_context():  # so the search can say which sources answered from the store
            g.setdefault("http_cache_replays", set()).add(urlsplit(request.url).hostname)
        status, headers, body, url = stored
        response = requests.Response()
        response.status_code = status
        response.headers = CaseInsensitiveDict(json.loads(headers))
        response.headers[FROM_CACHE_HEADER] = how
        response._content = body
        response.url = url
        response.request = request
        response.reason = "OK (cached)"
        response.encoding = requests.utils.get_encoding_from_headers(response.headers) or "utf-8"
        return response


def cached_session(max_retries=0):
    """A requests.Session for a live API client: cached per HTTP_CACHE, plain outside an app or when off."""
    session = requests.Session()
    mode = current_app.config.get("HTTP_CACHE", "off") if has_app_context() else "off"
    if mode not in MODES:
        raise ValueError(f"HTTP_CACHE must be one of {', '.join(MODES)}, not {mode!r}")
    if mode != "off":
        adapter = CachingAdapter(_store(current_app.config["HTTP_CACHE_PATH"]), mode, max_retries=max_retries)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    elif max_retries:
        session.mount("https://", HTTPAdapter(max_retries=max_retries))
    return session


def replayed_hosts():
    """API hosts answered from the store during this request (for the evidence's cached_sources note)."""
    return sorted(g.get("http_cache_replays") or ()) if has_app_context() else []


def store_size():
    """Responses in the store, or None when the cache is off."""
    if current_app.config.get("HTTP_CACHE", "off") == "off":
        return None
    return _store(current_app.config["HTTP_CACHE_PATH"]).count()
