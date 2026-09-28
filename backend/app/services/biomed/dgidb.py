"""DGIdb v5 GraphQL client (https://dgidb.org/api/graphql)."""

from .base import BaseAPIClient, SourceError

_INTERACTION_FIELDS = """
    interactionScore
    interactionTypes { type directionality }
    publications { pmid }
    sources { sourceDbName }
"""

GENE_QUERY = f"""
query ($names: [String!]!) {{
  genes(names: $names) {{
    nodes {{
      name
      conceptId
      interactions {{ {_INTERACTION_FIELDS} drug {{ name conceptId approved }} }}
    }}
  }}
}}"""

DRUG_QUERY = f"""
query ($names: [String!]!) {{
  drugs(names: $names) {{
    nodes {{
      name
      conceptId
      approved
      interactions {{ {_INTERACTION_FIELDS} gene {{ name conceptId }} }}
    }}
  }}
}}"""


class DGIdbClient(BaseAPIClient):
    name = "DGIdb"

    def fetch_interactions(self, genes=(), drugs=()):
        rows = []
        if genes:
            rows += self._run(GENE_QUERY, "genes", [g.upper() for g in genes], anchor="gene")
        if drugs:
            rows += self._run(DRUG_QUERY, "drugs", [d.upper() for d in drugs], anchor="drug")

        merged = {}
        for row in rows:
            key = (row["gene"], row["drug"])
            if key not in merged or (row["score"] or 0) > (merged[key]["score"] or 0):
                merged[key] = row
        return list(merged.values())

    def _run(self, query, root, names, anchor):
        payload = self._request("POST", "", json={"query": query, "variables": {"names": names}})
        if payload.get("errors"):
            raise SourceError(f"DGIdb: {payload['errors'][0].get('message')}")
        nodes = ((payload.get("data") or {}).get(root) or {}).get("nodes") or []
        rows = []
        for node in nodes:
            for it in node.get("interactions") or []:
                partner = it.get("drug") if anchor == "gene" else it.get("gene")
                if not partner:
                    continue
                gene = node["name"] if anchor == "gene" else partner["name"]
                drug = partner["name"] if anchor == "gene" else node["name"]
                rows.append({
                    "gene": gene,
                    "drug": drug,
                    "drug_approved": partner.get("approved") if anchor == "gene" else node.get("approved"),
                    "interaction_types": [t["type"] for t in it.get("interactionTypes") or [] if t.get("type")],
                    "directionality": next(
                        (t["directionality"] for t in it.get("interactionTypes") or [] if t.get("directionality")),
                        None,
                    ),
                    "score": it.get("interactionScore"),
                    "sources": sorted({s["sourceDbName"] for s in it.get("sources") or []}),
                    "pmids": [p["pmid"] for p in it.get("publications") or [] if p.get("pmid")],
                })
        return rows
