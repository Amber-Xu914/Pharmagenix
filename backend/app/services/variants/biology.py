"""Biological context for Workflow 4: core oncogenic pathways and known co-mutation patterns in lung cancer.

Used for the B ("biological evidence") component of the Tier 3 score and as facts for the Biological
Interpretation agent. Pathways: a curated subset of the ten TCGA PanCancer oncogenic signalling pathways
(Sanchez-Vega et al., Cell 2018), limited to well-established members.
"""

from ...extensions import db
from ...reference.models import GenePathway

PATHWAYS = {
    "RTK/RAS": {"EGFR", "ERBB2", "ERBB3", "ERBB4", "MET", "ALK", "ROS1", "RET", "NTRK1", "NTRK2", "NTRK3", "FGFR1",
                "FGFR2", "FGFR3", "FGFR4", "KIT", "PDGFRA", "IGF1R", "KRAS", "NRAS", "HRAS", "BRAF", "RAF1", "ARAF",
                "MAP2K1", "MAP2K2", "NF1", "PTPN11", "SOS1", "CBL", "RASA1"},
    "PI3K": {"PIK3CA", "PIK3CB", "PIK3R1", "PIK3R2", "PTEN", "AKT1", "AKT2", "AKT3", "MTOR", "TSC1", "TSC2",
             "RICTOR", "RHEB", "STK11"},
    "TP53": {"TP53", "MDM2", "MDM4", "ATM", "CHEK2", "RPS6KA3"},
    "Cell cycle": {"CDKN2A", "CDKN2B", "CDKN2C", "CDKN1A", "CDKN1B", "CDK4", "CDK6", "CCND1", "CCND2", "CCND3",
                   "CCNE1", "RB1", "E2F1", "E2F3"},
    "NRF2": {"KEAP1", "NFE2L2", "CUL3"},
    "NOTCH": {"NOTCH1", "NOTCH2", "NOTCH3", "NOTCH4", "FBXW7"},
    "WNT": {"APC", "CTNNB1", "AXIN1", "AXIN2", "RNF43", "ZNRF3", "TCF7L2"},
    "MYC": {"MYC", "MYCN", "MYCL", "MAX", "MGA"},
    "Hippo": {"NF2", "LATS1", "LATS2", "SAV1", "YAP1", "WWTR1"},
    "TGF-β": {"TGFBR1", "TGFBR2", "ACVR1B", "ACVR2A", "SMAD2", "SMAD3", "SMAD4"},
}

# Co-mutation patterns with a known biological meaning in lung cancer, given to the Biological Interpretation agent
# as facts to look for (it must still cite the patient's evidence items).
CO_MUTATION_PATTERNS = [
    ({"KRAS", "STK11"}, "KRAS + STK11 (KL subtype of lung adenocarcinoma)"),
    ({"KRAS", "KEAP1"}, "KRAS + KEAP1 (NRF2 pathway activation with RAS signalling)"),
    ({"KRAS", "TP53"}, "KRAS + TP53 (KP subtype of lung adenocarcinoma)"),
    ({"TP53", "RB1"}, "TP53 + RB1 (the hallmark loss pair of small cell lung cancer)"),
    ({"EGFR", "TP53"}, "EGFR + TP53"),
    ({"EGFR", "RB1"}, "EGFR + RB1 (associated with transformation to small cell histology)"),
    ({"STK11", "KEAP1"}, "STK11 + KEAP1"),
]


PATHWAY_SOURCE = "TCGA PanCancer oncogenic signalling pathways (Sanchez-Vega et al., Cell 2018), curated subset"


def pathways_of(gene):
    """The gene's pathways from the gene_pathways table (seeded from PATHWAYS); PATHWAYS itself if not seeded."""
    if db.session.query(GenePathway.id).first() is None:
        return sorted(name for name, genes in PATHWAYS.items() if gene in genes)
    return sorted(db.session.scalars(db.select(GenePathway.pathway).where(GenePathway.gene_symbol == gene)))


def seed_pathways():
    """(Re)load gene_pathways from PATHWAYS. Returns the number of rows."""
    db.session.execute(db.delete(GenePathway))
    rows = [GenePathway(pathway=name, gene_symbol=gene, source=PATHWAY_SOURCE)
            for name, genes in PATHWAYS.items() for gene in sorted(genes)]
    db.session.add_all(rows)
    db.session.commit()
    return len(rows)


def co_mutation_patterns(genes):
    genes = set(genes)
    return [label for members, label in CO_MUTATION_PATTERNS if members <= genes]
