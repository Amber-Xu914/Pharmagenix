import requests
from urllib3.util.retry import Retry

from ...http_cache import cached_session


class SourceError(Exception):
    """A biomedical source failed; the aggregator records it and carries on with the others."""


class BaseAPIClient:
    name = "base"

    def __init__(self, base_url, timeout=20, headers=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        retry = Retry(total=2, backoff_factor=0.8, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET", "POST"))
        self.session = cached_session(max_retries=retry)  # see http_cache.py: stored answers when the API fails
        self.session.headers.update({"User-Agent": "DGI-Explorer/0.1 (academic project)"})
        if headers:
            self.session.headers.update(headers)

    def _request(self, method, path, allow_404=False, **kwargs):
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        try:
            resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise SourceError(f"{self.name}: {exc}") from exc
        if allow_404 and resp.status_code == 404:
            return None
        if not resp.ok:
            raise SourceError(f"{self.name}: HTTP {resp.status_code} {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise SourceError(f"{self.name}: invalid JSON response") from exc
