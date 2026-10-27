"""OncoKB variant annotation (optional): therapeutic levels for a protein change in a tumour type.

Needs a licensed API token (free for academic use); without ONCOKB_API_TOKEN the source is skipped and the
workflow uses CIViC alone. Levels: 1 / 2 standard care, 3A / 3B clinical evidence, 4 biological evidence,
R1 / R2 resistance.
"""

from ...http_cache import cached_session

ESTABLISHED = {"LEVEL_1", "LEVEL_2"}
RESISTANCE = {"LEVEL_R1", "LEVEL_R2"}


class OncoKBClient:
    def __init__(self, base_url, token, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.enabled = bool(token)
        self.timeout = timeout
        self.session = cached_session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def annotate(self, gene, protein_change, tumour_type):
        resp = self.session.get(f"{self.base_url}/annotate/mutations/byProteinChange", timeout=self.timeout,
                                params={"hugoSymbol": gene, "alteration": protein_change, "tumorType": tumour_type})
        resp.raise_for_status()
        return from_oncokb(resp.json(), gene, protein_change)


def from_oncokb(data, gene, protein_change):
    """One record per drug (combinations joined with ' + ')."""
    out = []
    for t in data.get("treatments") or []:
        drugs = [d.get("drugName") for d in t.get("drugs") or [] if d.get("drugName")]
        if not drugs:
            continue
        level = t.get("level") or ""
        cancer = (t.get("levelAssociatedCancerType") or {})
        out.append({
            "gene": gene, "protein_change": protein_change, "drug": " + ".join(drugs), "level": level,
            "significance": "Resistance" if level in RESISTANCE else "Sensitivity/Response",
            "cancer_type": cancer.get("code") or cancer.get("name") or (cancer.get("mainType") or {}).get("name"),
            # A level belongs to one OncoTree code, or (code empty) to a whole main type such as NSCLC
            "cancer_code": cancer.get("code") or None,
            "cancer_main_type": (cancer.get("mainType") or {}).get("name") or None,
            "pmids": [int(p) for p in t.get("pmids") or [] if str(p).isdigit()][:10],
            "oncogenic": data.get("oncogenic"), "mutation_effect": (data.get("mutationEffect") or {}).get("knownEffect"),
        })
    return out
