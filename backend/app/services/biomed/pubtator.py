"""PubTator3 literature search (https://www.ncbi.nlm.nih.gov/research/pubtator3/api)."""

from .base import BaseAPIClient


class PubTatorClient(BaseAPIClient):
    name = "PubTator"

    def search(self, text, limit=5):
        data = self._request("GET", "search/", params={"text": text, "page": 1}) or {}
        results = []
        for hit in (data.get("results") or [])[:limit]:
            pmid = hit.get("pmid")
            if not pmid:
                continue
            results.append({
                "pmid": pmid,
                "title": hit.get("title"),
                "journal": hit.get("journal"),
                "year": (hit.get("date") or hit.get("meta_date_publication") or "")[:4] or None,
                "authors": (hit.get("authors") or [])[:3],
                "query": text,
            })
        return results

    def search_pairs(self, pairs, fallback_terms, limit_total=15, per_query=5):
        """Search literature for the top gene–drug pairs, then top up with the raw query terms."""
        queries = [f"{gene} AND {drug}" for gene, drug in pairs[:3]] + list(fallback_terms[:3])
        seen, out = set(), []
        for q in queries:
            for pub in self.search(q, limit=per_query):
                if pub["pmid"] not in seen:
                    seen.add(pub["pmid"])
                    out.append(pub)
                if len(out) >= limit_total:
                    return out
        return out
