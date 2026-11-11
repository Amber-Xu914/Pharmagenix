import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.services.llm import LLMResult

FAKE_EVIDENCE_ITEMS = [
    {"id": "E1", "kind": "interaction", "source": "DGIdb", "gene": "CYP2C19", "drug": "CLOPIDOGREL",
     "interaction_types": ["substrate"], "categories": ["metabolize"], "directionality": None, "score": 2.1,
     "sources": ["PharmGKB"], "pmids": [123], "drug_approved": True, "url": "https://dgidb.org/genes/CYP2C19"},
    {"id": "E2", "kind": "variant_annotation", "source": "PharmGKB", "categories": ["snp"],
     "annotation_id": 1, "accession_id": "PA1", "variant": "CYP2C19*2", "genes": ["CYP2C19"],
     "drugs": ["clopidogrel"], "diseases": [], "level": "1A", "phenotype_categories": ["Efficacy"],
     "score": 100, "summary": "Reduced response.", "url": "https://www.clinpgx.org/clinicalAnnotation/1"},
]


class FakeLLM:
    enabled = True

    def __init__(self, provider, text):
        self.provider, self.model, self.text = provider, f"fake-{provider}", text

    def complete(self, system, user):
        return LLMResult(text=self.text, model=self.model, input_tokens=10, output_tokens=20, latency_ms=5)


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def fake_labels(monkeypatch):
    """No test reaches openFDA: drug labels come from this dict (lower-case generic name -> DRUG INTERACTIONS text);
    a test adds the labels it needs."""
    labels = {}

    def label(self, drug_name):
        text = labels.get(drug_name.lower())
        return text and {"drug": drug_name, "title": drug_name.lower(), "set_id": "test", "effective_time": "20260101",
                         "url": "https://dailymed.example/test", "text": text}

    monkeypatch.setattr("app.services.biomed.openfda.OpenFDAClient.label", label)
    return labels


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_headers():
    return {"X-Admin-Token": TestConfig.ADMIN_TOKEN}


@pytest.fixture
def fake_sources(monkeypatch):
    """Replace external APIs and LLMs. Returns a dict so tests can change the LLM answers."""
    from app.services.biomed.aggregator import build_network
    from app.services.workflow.evidence import consolidate_pairs

    answers = {"openai": "CYP2C19 metabolises clopidogrel [E1]. *2 reduces response [E2].",
               "anthropic": "Summary citing [E1, E2]."}
    calls = {"collect": 0}

    def fake_collect(gene, relation_types, resolved=None, entity_type="gene", medications=()):
        calls["collect"] += 1
        items = [dict(i) for i in FAKE_EVIDENCE_ITEMS]
        return {"items": items, "table": consolidate_pairs(items), "network": build_network(items, [gene]),
                "source_status": {"dgidb": {"state": "ok", "count": 1}}, "counts": {}}

    monkeypatch.setattr("app.services.explorer.collect_evidence", fake_collect)
    monkeypatch.setattr("app.services.llm.report_generator.get_llm_client",
                        lambda provider: FakeLLM(provider, answers[provider]))
    return {"answers": answers, "calls": calls}
