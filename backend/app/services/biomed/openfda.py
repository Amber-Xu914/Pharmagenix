"""openFDA drug labels (US prescribing information, public domain): the DRUG INTERACTIONS section of a drug's label.

Free; without a key openFDA allows 240 requests a minute and 1,000 a day per IP (OPENFDA_API_KEY raises this).
Answers go through the local response store (http_cache.py), so a repeated or offline demo still has them.
"""

import re

from .base import BaseAPIClient

DAILYMED = "https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid={}"


class OpenFDAClient(BaseAPIClient):
    name = "openFDA"

    def __init__(self, base_url, api_key="", timeout=20):
        super().__init__(base_url, timeout)
        self.api_key = api_key

    def label(self, drug_name):
        """The most recent label for a generic name that has a DRUG INTERACTIONS section, or None.
        {"drug", "title", "set_id", "effective_time", "url", "text"}."""
        name = re.sub(r'["\\]', "", drug_name or "").strip().lower()
        if not name:
            return None
        for field in ("openfda.generic_name", "openfda.substance_name"):
            params = {"search": f'{field}:"{name}"', "limit": 10}
            if self.api_key:
                params["api_key"] = self.api_key
            data = self._request("GET", "drug/label.json", params=params, allow_404=True)
            labels = [r for r in (data or {}).get("results") or [] if r.get("drug_interactions")]
            if labels:
                best = max(labels, key=lambda r: r.get("effective_time") or "")
                openfda = best.get("openfda") or {}
                set_id = (openfda.get("spl_set_id") or [best.get("set_id")])[0]
                brand = (openfda.get("brand_name") or [None])[0]
                return {
                    "drug": drug_name,
                    "title": f"{brand} ({name})" if brand and brand.lower() != name else name,
                    "set_id": set_id,
                    "effective_time": best.get("effective_time"),
                    "url": DAILYMED.format(set_id) if set_id else "https://open.fda.gov/apis/drug/label/",
                    "text": " ".join(best["drug_interactions"]),
                }
        return None
