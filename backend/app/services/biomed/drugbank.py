"""DrugBank client. DrugBank's API is licensed: without DRUGBANK_API_KEY this source is skipped.

TODO: endpoint paths and response fields differ between DrugBank API products (Clinical API vs
Discovery API). Check them against the docs for your plan (https://docs.drugbank.com) and adjust
`lookup_drug` accordingly.
"""

from .base import BaseAPIClient


class DrugBankClient(BaseAPIClient):
    name = "DrugBank"

    def __init__(self, base_url, api_key, timeout=20):
        super().__init__(base_url, timeout, headers={"Authorization": api_key} if api_key else None)
        self.enabled = bool(api_key)

    def lookup_drugs(self, drug_names, limit=10):
        out = []
        for name in drug_names[:limit]:
            info = self.lookup_drug(name)
            if info:
                out.append(info)
        return out

    def lookup_drug(self, name):
        data = self._request("GET", "drugs", params={"q": name}, allow_404=True)
        hits = data if isinstance(data, list) else (data or {}).get("drugs") or []
        if not hits:
            return None
        drug = hits[0]
        drugbank_id = drug.get("drugbank_id") or drug.get("id")
        return {
            "drug": name.upper(),
            "drugbank_id": drugbank_id,
            "name": drug.get("name"),
            "mechanism": drug.get("mechanism_of_action") or drug.get("description"),
            "url": f"https://go.drugbank.com/drugs/{drugbank_id}" if drugbank_id else None,
        }
