"""UniProt REST client (https://rest.uniprot.org): protein function, domains and location for gene profiles."""

import re

from .base import BaseAPIClient

FIELDS = "accession,protein_name,cc_function,ft_domain,cc_subcellular_location"
# "(PubMed:12345, PubMed:67890)" citations inside UniProt free text; the entry itself is linked instead.
PUBMED_REFS = re.compile(r"\s*\((?:PubMed:\d+(?:,\s*)?)+\)")


class UniProtClient(BaseAPIClient):
    name = "UniProt"

    def protein(self, accession=None, gene_symbol=None):
        """By accession when HGNC gave one, otherwise the reviewed human entry for the gene symbol."""
        if accession:
            entry = self._request("GET", f"uniprotkb/{accession}.json", params={"fields": FIELDS}, allow_404=True)
        else:
            data = self._request("GET", "uniprotkb/search", params={
                "query": f"gene_exact:{gene_symbol} AND organism_id:9606 AND reviewed:true",
                "fields": FIELDS, "size": 1,
            }) or {}
            entry = (data.get("results") or [None])[0]
        return self._normalize(entry) if entry else None

    @staticmethod
    def _normalize(entry):
        accession = entry.get("primaryAccession")
        comments = entry.get("comments") or []
        function = next((t.get("value") for c in comments if c.get("commentType") == "FUNCTION"
                         for t in c.get("texts") or []), None)
        locations = [loc["location"]["value"] for c in comments if c.get("commentType") == "SUBCELLULAR LOCATION"
                     for loc in c.get("subcellularLocations") or [] if loc.get("location")]
        recommended = ((entry.get("proteinDescription") or {}).get("recommendedName") or {}).get("fullName") or {}
        return {
            "accession": accession,
            "protein_name": recommended.get("value"),
            "function": PUBMED_REFS.sub("", function).strip() if function else None,
            "domains": [{"name": f.get("description"), "start": f["location"]["start"]["value"],
                         "end": f["location"]["end"]["value"]}
                        for f in entry.get("features") or [] if f.get("type") == "Domain"][:12],
            "locations": list(dict.fromkeys(locations))[:5],
            "url": f"https://www.uniprot.org/uniprotkb/{accession}/entry",
        }
