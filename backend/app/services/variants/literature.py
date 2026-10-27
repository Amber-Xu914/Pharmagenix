"""Workflow 4, stage 6 input: retrieval-augmented literature for the Literature agent.

  1 search        PubTator3 keyword search, VCF_LITERATURE_CANDIDATES hits per query
  2 fetch         titles and abstracts with PubTator's entity annotations (one BioC export request per 100 papers)
  3 entity gate   the paper must mention the gene (NCBI Gene ID annotation or symbol) and, for a drug query, the drug
  4 re-rank       MedCPT Cross-Encoder; papers scoring below VCF_LITERATURE_MIN_SCORE are dropped as off-topic
                  (without MedCPT: a keyword score over gene, variant, drug and lung-cancer mentions)
  5 passages      the abstract sentences that best answer the query (MedCPT Query / Article Encoders, or keywords),
                  which the Literature agent reads and cites instead of the bare title

The kept papers become citable publication items and count as literature support in the exploratory ranking.
Nothing here changes QC, tiers or evidence levels.
"""

import logging
import re

from flask import current_app

from ..biomed.medcpt import MedCPTUnavailable, get_ranker

logger = logging.getLogger(__name__)

# A sentence ends at . ! ? followed by a space and a capital, digit or bracket; "et al." and "e.g." don't end one.
SENTENCE_END = re.compile(r"(?<!\bet al\.)(?<!\be\.g\.)(?<!\bi\.e\.)(?<=[.!?])\s+(?=[A-Z0-9(\[])")
LUNG_RE = re.compile(r"\blung\b|\bNSCLC|\bSCLC|\bLUAD|\bLUSC|adenocarcinoma of the lung", re.IGNORECASE)


def split_sentences(text):
    return [s.strip() for s in SENTENCE_END.split(text or "") if len(s.strip()) > 20]


def _mentions(term, text):
    return bool(term) and re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, re.IGNORECASE)


class Query:
    """One literature question: a Tier 3 variant, or a level C–E variant–drug pair."""

    def __init__(self, kind, key, search, gene, entrez_id=None, variant=None, drug=None, cancer="lung cancer"):
        self.kind, self.key, self.search = kind, key, search
        self.id = (kind, key)  # variant IDs and drug keys are separate namespaces
        self.gene, self.entrez_id, self.variant, self.drug = gene, entrez_id, variant, drug
        self.text = " ".join(p for p in (gene, variant, drug, cancer) if p)  # what the ranker scores against

    def _has_gene(self, text, doc):
        return bool((self.entrez_id and self.entrez_id in doc.get("gene_ids", ())) or _mentions(self.gene, text))

    def _has_drug(self, text, doc):
        return bool(self.drug and (_mentions(self.drug, text) or self.drug.lower() in doc.get("chemicals", ())))

    def gate(self, title, doc):
        """Entity check: the gene, and for a drug query the drug, must be in the paper (not just near the query).
        PubTator's annotations count too, so a paper that calls EGFR "HER1" still passes."""
        text = f"{title} {doc.get('abstract', '')}"
        return self._has_gene(text, doc) and (not self.drug or self._has_drug(text, doc))

    def keyword_score(self, text, doc=None):
        """0–1 without MedCPT: gene 0.4, the exact variant 0.2, the drug 0.2, lung cancer 0.2. With the paper's
        annotations (doc), tagged genes and chemicals count as mentions."""
        doc = doc or {}
        return round(0.4 * self._has_gene(text, doc) + 0.2 * bool(_mentions(self.variant, text))
                     + 0.2 * self._has_drug(text, doc) + 0.2 * bool(LUNG_RE.search(text)), 2)


class KeywordRanker:
    name = "keyword"
    min_score = 0.4  # at least the gene

    def __init__(self, query):
        self.query = query

    def score_articles(self, query_text, articles, docs):
        return [self.query.keyword_score(f"{t} {a}", d) for (t, a), d in zip(articles, docs)]

    def score_sentences(self, query_text, title, sentences):
        return [self.query.keyword_score(s) for s in sentences]


def _ranker(cfg, status):
    """MedCPT when it is installed (or required); otherwise None, which means keyword scoring."""
    choice = cfg["VCF_LITERATURE_RANKER"]
    if choice == "keyword":
        return None
    try:
        return get_ranker(cfg.get("MEDCPT_DEVICE") or None)
    except MedCPTUnavailable as exc:
        if choice == "medcpt":
            raise
        logger.info("MedCPT unavailable, using keyword ranking: %s", exc)
        status["medcpt"] = {"state": "disabled", "count": 0, "error": str(exc)[:300]}
        return None


def retrieve(queries, client, status):
    """{(kind, key): [kept paper]} per query, best first. A kept paper: {pmid, title, journal, year, authors,
    score, ranker, passages: [{n, text}]}."""
    cfg = current_app.config
    hits = {q.id: client.search(q.search, limit=cfg["VCF_LITERATURE_CANDIDATES"]) for q in queries}
    pmids = [h["pmid"] for found in hits.values() for h in found]
    try:
        docs = client.abstracts(pmids) if pmids else {}
    except Exception as exc:  # noqa: BLE001 - titles alone still allow the gate and re-ranking
        logger.warning("PubTator abstracts failed: %s", exc)
        status["pubtator_abstracts"] = {"state": "error", "count": 0, "error": str(exc)[:300]}
        docs = {}
    medcpt = _ranker(cfg, status)
    kept, scored = {}, 0
    for q in queries:
        ranker = medcpt or KeywordRanker(q)
        min_score = cfg["VCF_LITERATURE_MIN_SCORE"] if medcpt else KeywordRanker.min_score
        candidates = [(h, docs.get(int(h["pmid"]), {})) for h in hits[q.id]]
        candidates = [(h, d) for h, d in candidates if q.gate(h.get("title") or "", d)]
        articles = [(h.get("title") or "", d.get("abstract", "")) for h, d in candidates]
        scores = (medcpt.score_articles(q.text, articles) if medcpt
                  else ranker.score_articles(q.text, articles, [d for _, d in candidates]))
        scored += len(candidates)
        papers = []
        for score, (hit, doc) in sorted(zip(scores, candidates), key=lambda x: -x[0]):
            if score < min_score or len(papers) >= cfg["VCF_LITERATURE_PER_QUERY"]:
                break
            sentences = split_sentences(doc.get("abstract"))
            best = sorted(zip(ranker.score_sentences(q.text, hit.get("title") or "", sentences),
                              range(1, len(sentences) + 1), sentences), key=lambda x: -x[0])
            if not medcpt:  # a sentence that names none of the query terms says nothing about it
                best = [b for b in best if b[0] > 0]
            passages = sorted(({"n": n, "text": s} for _, n, s in best[:cfg["VCF_LITERATURE_SENTENCES"]]),
                              key=lambda p: p["n"])
            papers.append({**hit, "title": hit.get("title") or doc.get("title"), "score": score,
                           "ranker": ranker.name, "passages": passages, "abstract_available": bool(sentences)})
        kept[q.id] = papers
    if medcpt:
        status["medcpt"] = {"state": "ok", "count": scored}
    return kept
