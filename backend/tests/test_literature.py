"""Workflow 4 literature retrieval: entity gate, re-ranking threshold and passage selection (no network, no MedCPT)."""

from app.services.variants import literature
from app.services.variants.literature import Query, retrieve, split_sentences


class FakePubTator:
    def __init__(self, hits, docs):
        self.hits, self.docs, self.searched = hits, docs, []

    def search(self, text, limit=5):
        self.searched.append((text, limit))
        return self.hits[:limit]

    def abstracts(self, pmids):
        return {p: self.docs[p] for p in pmids if p in self.docs}


HITS = [{"pmid": 1, "title": "Neratinib in EGFR-mutant lung adenocarcinoma"},
        {"pmid": 2, "title": "TP53 in germ cell tumours"},  # off-topic: no EGFR, no neratinib
        {"pmid": 3, "title": "Neratinib in HER2-positive breast cancer"},  # drug, but no EGFR anywhere
        {"pmid": 4, "title": "Pan-HER inhibition"}]  # EGFR and the drug only in the abstract / annotations
DOCS = {
    1: {"abstract": "Background on kinase inhibitors in general terms. EGFR L858R cells were sensitive to neratinib "
                    "in vitro. Further work is needed in larger cohorts.", "gene_ids": {"1956"}, "chemicals": {"neratinib"}},
    2: {"abstract": "Germ cell tumours are often cured with cisplatin.", "gene_ids": {"7157"}, "chemicals": {"cisplatin"}},
    3: {"abstract": "HER2-positive breast cancer responds to neratinib.", "gene_ids": {"2064"}, "chemicals": {"neratinib"}},
    4: {"abstract": "We tested pan-HER inhibitors in lung cancer models.", "gene_ids": {"1956"}, "chemicals": {"neratinib"}},
}


def egfr_neratinib():
    return Query("drug", "NERATINIB", "EGFR AND Neratinib", "EGFR", "1956", "L858R", "Neratinib",
                 "Lung Adenocarcinoma")


def test_split_sentences_keeps_abbreviations_and_variants():
    text = "Smith et al. reported p.T790M in 12 patients. Response was seen (e.g. in EGFR exon 19 deletions). That was all for this cohort."
    assert split_sentences(text) == ["Smith et al. reported p.T790M in 12 patients.",
                                     "Response was seen (e.g. in EGFR exon 19 deletions).", "That was all for this cohort."]


def test_gate_needs_gene_and_drug():
    q = egfr_neratinib()
    assert [h["pmid"] for h in HITS if q.gate(h["title"], DOCS[h["pmid"]])] == [1, 4]  # 4 via annotations


def test_keyword_retrieval_ranks_and_picks_sentences(app):
    client = FakePubTator(HITS, DOCS)
    with app.app_context():
        kept = retrieve([egfr_neratinib()], client, status := {})
    papers = kept[("drug", "NERATINIB")]
    assert [p["pmid"] for p in papers] == [1, 4]  # off-topic papers gated out; 1 names the variant too
    assert papers[0]["ranker"] == "keyword" and papers[0]["score"] == 1.0
    assert papers[0]["passages"] == [{"n": 2, "text": "EGFR L858R cells were sensitive to neratinib in vitro."}]
    assert client.searched == [("EGFR AND Neratinib", 10)]  # VCF_LITERATURE_CANDIDATES
    assert "medcpt" not in status  # the keyword ranker was configured, not a fallback


class FakeMedCPT:
    name = "MedCPT"

    def score_articles(self, query, articles):
        return [5.0 if "lung" in f"{t} {a}".lower() else -10.0 for t, a in articles]

    def score_sentences(self, query, title, sentences):
        return [float("neratinib" in s.lower()) for s in sentences]


def test_medcpt_threshold_drops_low_scores(app, monkeypatch):
    monkeypatch.setattr(literature, "get_ranker", lambda device=None: FakeMedCPT())
    app.config["VCF_LITERATURE_RANKER"] = "auto"
    hits = HITS + [{"pmid": 5, "title": "EGFR and neratinib in colorectal cancer"}]
    docs = {**DOCS, 5: {"abstract": "Colorectal models.", "gene_ids": {"1956"}, "chemicals": {"neratinib"}}}
    with app.app_context():
        kept = retrieve([egfr_neratinib()], FakePubTator(hits, docs), status := {})
    assert [p["pmid"] for p in kept[("drug", "NERATINIB")]] == [1, 4]  # 5 passes the gate but scores -10
    assert status["medcpt"] == {"state": "ok", "count": 3}


def test_falls_back_to_keywords_without_medcpt(app, monkeypatch):
    def unavailable(device=None):
        raise literature.MedCPTUnavailable("not installed")

    monkeypatch.setattr(literature, "get_ranker", unavailable)
    app.config["VCF_LITERATURE_RANKER"] = "auto"
    with app.app_context():
        kept = retrieve([egfr_neratinib()], FakePubTator(HITS, DOCS), status := {})
    assert kept[("drug", "NERATINIB")][0]["ranker"] == "keyword"
    assert status["medcpt"]["state"] == "disabled"
