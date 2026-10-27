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

    def abstracts(self, pmids, batch=100):
        """{pmid: {title, abstract, gene_ids, chemicals}} from the BioC export, with PubTator's entity annotations:
        NCBI Gene IDs of the genes and the (lower-cased) chemical names it tagged. Papers without an abstract in
        PubMed come back with an empty one."""
        out = {}
        pmids = [str(p) for p in dict.fromkeys(pmids)]
        for start in range(0, len(pmids), batch):
            data = self._request("GET", "publications/export/biocjson",
                                 params={"pmids": ",".join(pmids[start:start + batch])}) or {}
            for doc in data.get("PubTator3") or []:
                texts, gene_ids, chemicals = {"title": "", "abstract": ""}, set(), set()
                for passage in doc.get("passages") or []:
                    kind = (passage.get("infons") or {}).get("type")
                    if kind in texts:
                        texts[kind] = (texts[kind] + " " + (passage.get("text") or "")).strip()
                    for ann in passage.get("annotations") or []:
                        infons = ann.get("infons") or {}
                        if infons.get("type") == "Gene":
                            gene_ids.update(str(infons.get("identifier") or "").split(";"))
                        elif infons.get("type") == "Chemical" and ann.get("text"):
                            chemicals.add(ann["text"].lower())
                pmid = int(doc.get("pmid") or doc.get("id"))
                out[pmid] = {**texts, "gene_ids": gene_ids - {""}, "chemicals": chemicals}
        return out

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
