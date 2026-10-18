"""PharmGKB / ClinPGx clinical annotations: variant (SNP / haplotype)–drug response evidence."""

import time

from ...reference.text import strip_salt
from .base import BaseAPIClient

# Level of evidence ordering: 1A strongest, 4 weakest.
LEVEL_ORDER = {"1A": 0, "1B": 1, "2A": 2, "2B": 3, "3": 4, "4": 5}
WEB_URL = "https://www.clinpgx.org/clinicalAnnotation/{id}"


class PharmGKBClient(BaseAPIClient):
    name = "PharmGKB"
    min_interval = 0.5  # API asks for <= 2 requests/second

    def clinical_annotations(self, genes=(), drugs=(), limit=40, max_lookups=10, pairs=()):
        """pairs: (gene, drug) the user asked about; their annotations are ranked first so the limit keeps them."""
        # Gene symbols are upper case; chemical names only match in lower case.
        lookups = ([("location.genes.symbol", g.upper()) for g in genes]
                   + [("relatedChemicals.name", d.lower()) for d in drugs])
        seen, out = set(), []
        for i, (field, value) in enumerate(lookups[:max_lookups]):
            if i:
                time.sleep(self.min_interval)
            data = self._request("GET", "data/clinicalAnnotation",
                                 params={field: value, "view": "base"}, allow_404=True) or {}
            for ann in data.get("data") or []:
                if ann.get("id") in seen:
                    continue
                seen.add(ann.get("id"))
                out.append(self._normalize(ann))
        wanted = {(gene.upper(), strip_salt(drug)) for gene, drug in pairs}

        def about_pair(ann):
            return any((g.upper(), strip_salt(d)) in wanted for g in ann["genes"] for d in ann["drugs"])

        out.sort(key=lambda a: (not about_pair(a), LEVEL_ORDER.get(a["level"], 99)))
        return out[:limit]

    @staticmethod
    def _normalize(ann):
        location = ann.get("location") or {}
        phenotypes = ann.get("allelePhenotypes") or []
        return {
            "annotation_id": ann.get("id"),
            "accession_id": ann.get("accessionId"),
            "variant": location.get("displayName"),
            "genes": [g.get("symbol") for g in location.get("genes") or [] if g.get("symbol")],
            "drugs": [c.get("name") for c in ann.get("relatedChemicals") or [] if c.get("name")],
            "diseases": [d.get("name") for d in ann.get("relatedDiseases") or [] if d.get("name")],
            "level": (ann.get("levelOfEvidence") or {}).get("term"),
            "phenotype_categories": ann.get("types") or [],
            "score": ann.get("score"),
            # First allele phenotype sentence gives the LLM the actual direction of effect.
            "summary": phenotypes[0].get("phenotype") if phenotypes else None,
            "url": WEB_URL.format(id=ann.get("id")),
        }
