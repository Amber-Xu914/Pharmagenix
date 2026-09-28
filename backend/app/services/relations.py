"""Relation-type categories the user can pick, and how raw source labels map onto them."""

RELATION_TYPES = {
    "inhibit": {
        "label": "Inhibition",
        "terms": {
            "inhibitor", "antagonist", "blocker", "channel blocker", "negative modulator",
            "inhibitory allosteric modulator", "inverse agonist", "suppressor",
            "antisense oligonucleotide", "cleavage", "degrader", "negative allosteric modulator",
            "allosteric antagonist", "rnai inhibitor", "antisense inhibitor", "disrupting agent",
        },
    },
    "induce": {
        "label": "Induction / activation",
        "terms": {
            "inducer", "agonist", "partial agonist", "activator", "positive modulator",
            "positive allosteric modulator", "potentiator", "stimulator", "opener", "releasing agent",
        },
    },
    "bind": {
        "label": "Binding",
        "terms": {"binder", "ligand", "antibody", "modulator", "allosteric modulator", "chaperone", "cofactor",
                  "binding agent", "cross-linking agent", "sequestering agent", "chelating agent", "stabiliser"},
    },
    "metabolize": {
        "label": "Metabolism",
        "terms": {"substrate", "product of", "multitarget"},
    },
    "snp": {
        "label": "SNP-specific response",
        "terms": set(),  # comes from CPIC guidelines and PharmGKB annotations, not interaction labels
    },
    # Most DGIdb interactions (~90%) carry no type label, so this is on by default.
    "other": {
        "label": "Unspecified",
        "terms": set(),
    },
}

DEFAULT_RELATION_TYPES = ["inhibit", "induce", "bind", "metabolize", "snp", "other"]


def categorize(interaction_types):
    """Map raw interaction labels (e.g. DGIdb's "inhibitor") to our categories."""
    categories = []
    for raw in interaction_types or []:
        term = (raw or "").strip().lower()
        match = next((key for key, spec in RELATION_TYPES.items() if term in spec["terms"]), "other")
        if match not in categories:
            categories.append(match)
    return categories or ["other"]


def matches(categories, selected):
    return not selected or bool(set(categories) & set(selected))


def labels(selected):
    return [RELATION_TYPES[key]["label"] for key in selected if key in RELATION_TYPES]
