"""MedCPT (NCBI; Jin et al., Bioinformatics 2023): biomedical retrieval models trained on PubMed search logs.

  Cross-Encoder     scores (query, article) pairs; used to re-rank the PubTator hits
  Query Encoder     embeds the query } inner product = relevance; used to pick the abstract sentences
  Article Encoder   embeds sentences } that best answer the query

Runs locally and free (https://huggingface.co/ncbi). Optional: needs `pip install -r requirements-rag.txt`
(PyTorch, transformers). The models (~440 MB each) download on first use; loading them takes a few seconds, so they
are kept in memory for the life of the process.
"""

import threading

MODELS = {"query": "ncbi/MedCPT-Query-Encoder", "article": "ncbi/MedCPT-Article-Encoder",
          "cross": "ncbi/MedCPT-Cross-Encoder"}

_lock = threading.Lock()
_ranker = None


class MedCPTUnavailable(Exception):
    """PyTorch / transformers are not installed, or the models could not be loaded."""


def get_ranker(device=None):
    """The shared MedCPT ranker, loaded on first use. Raises MedCPTUnavailable."""
    global _ranker
    with _lock:
        if _ranker is None:
            _ranker = MedCPTRanker(device)
        return _ranker


class MedCPTRanker:
    name = "MedCPT"

    def __init__(self, device=None):
        try:
            import torch
            from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:
            raise MedCPTUnavailable("PyTorch / transformers not installed (pip install -r requirements-rag.txt)") from exc
        self.torch = torch
        self.device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        try:
            self.tokenizers = {k: AutoTokenizer.from_pretrained(m) for k, m in MODELS.items()}
            self.models = {
                "query": AutoModel.from_pretrained(MODELS["query"]),
                "article": AutoModel.from_pretrained(MODELS["article"]),
                "cross": AutoModelForSequenceClassification.from_pretrained(MODELS["cross"]),
            }
        except Exception as exc:  # noqa: BLE001 - no network on first use, a corrupt cache, ...
            raise MedCPTUnavailable(f"MedCPT models could not be loaded: {exc}") from exc
        for model in self.models.values():
            model.to(self.device).eval()

    def _run(self, kind, texts, max_length, batch=16):
        out = []
        with self.torch.no_grad():
            for start in range(0, len(texts), batch):
                encoded = self.tokenizers[kind](texts[start:start + batch], truncation=True, padding=True,
                                                return_tensors="pt", max_length=max_length).to(self.device)
                result = self.models[kind](**encoded)
                if kind == "cross":
                    out.append(result.logits.squeeze(dim=1).float().cpu())
                else:
                    out.append(result.last_hidden_state[:, 0, :].float().cpu())  # the [CLS] embedding
        return self.torch.cat(out) if out else self.torch.empty(0)

    def score_articles(self, query, articles):
        """Cross-encoder relevance (a logit; higher is more relevant) of each (title, abstract) to the query."""
        if not articles:
            return []
        pairs = [[query, f"{title} {abstract}".strip()] for title, abstract in articles]
        return [round(float(s), 3) for s in self._run("cross", pairs, max_length=512)]

    def score_sentences(self, query, title, sentences):
        """Bi-encoder relevance of each abstract sentence (with its paper's title as context) to the query."""
        if not sentences:
            return []
        q = self._run("query", [query], max_length=64)
        s = self._run("article", [[title, sentence] for sentence in sentences], max_length=256)
        return [round(float(x), 3) for x in (s @ q[0])]
