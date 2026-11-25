"""MLflow tracking (optional): one run + one trace per search, user ratings added afterwards. Uses a throwaway
SQLite tracking store; nothing leaves the machine."""

import pytest

from app import create_app, tracking
from app.config import TestConfig
from app.extensions import db

from .conftest import BrowserClient, log_in
from .test_vcf import FakeLLM, stub_live, vcf_reference  # noqa: F401 - fixtures


@pytest.fixture(autouse=True)
def _logged_in(request):
    """The tests run the synthetic examples, which need an administrator: log the test client in as one."""
    if "client" in request.fixturenames:
        log_in(request.getfixturevalue("client"), admin=True)


@pytest.fixture
def mlflow(tmp_path, monkeypatch):
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_ENABLE_ASYNC_TRACE_LOGGING", "false")  # write traces before the test reads them
    monkeypatch.setattr(tracking, "_state", {"enabled": False, "prefix": "", "artifacts": True, "experiments": {}})
    monkeypatch.chdir(tmp_path)  # artifacts land under tmp_path
    return mlflow


@pytest.fixture
def app(mlflow, tmp_path):
    """Overrides conftest's app for this module: the same app, with tracking to a throwaway SQLite store."""

    class TrackingConfig(TestConfig):
        MLFLOW_TRACKING_URI = f"sqlite:///{tmp_path}/mlflow.db"
        MLFLOW_EXPERIMENT_PREFIX = "test-pharmagenix"

    app = create_app(TrackingConfig)
    app.test_client_class = BrowserClient
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_search_is_tracked_as_run_and_trace(client, mlflow, vcf_reference, stub_live, monkeypatch):
    assert tracking.enabled()
    prompts = []
    monkeypatch.setattr("app.services.llm.report_generator.get_llm_client", lambda provider: FakeLLM(prompts))

    data = client.post("/api/vcf/explore", json={"example": "mock_luad_egfr_l858r_t790m"}).get_json()
    assert data["status"] == "complete"

    experiment = mlflow.get_experiment_by_name("test-pharmagenix-vcf")
    [run] = mlflow.search_runs([experiment.experiment_id], output_format="list")
    params, metrics, tags = run.data.params, run.data.metrics, run.data.tags
    assert (params["workflow"], params["cancer_type"], params["qc_min_depth"], params["template_version"]) == (
        "vcf", "LUAD", "20", "3")
    assert params["tier3_weights"] == '{"B": 0.3, "F": 0.4, "R": 0.3}'
    assert (metrics["variants_total"], metrics["variants_tier1"], metrics["drugs_established"]) == (11, 3, 2)
    assert metrics["report_primary_success"] == 1 and metrics["report_hypothesis_success"] == 1
    assert {"seconds_1_qc", "seconds_3_variant_tiering", "seconds_7_ranking", "seconds_total"} <= set(metrics)
    assert tags["query_id"] == str(data["id"]) and tags["source.civic"] == "ok"
    artifacts = {a.path for a in mlflow.MlflowClient().list_artifacts(run.info.run_id)}
    assert "evidence.json" in artifacts and any(p.startswith("report_literature_") for p in artifacts)

    trace = mlflow.get_trace(tags["trace_id"])
    names = [s.name for s in trace.data.spans]
    assert names[0] == "vcf_workflow"
    assert {"1_qc", "3_variant_tiering", "5_evidence_classification", "7_ranking", "source:vep", "source:dgidb",
            "llm_reports", "primary (openai)", "literature (openai)", "hypothesis (openai)"} <= set(names)
    agent = next(s for s in trace.data.spans if s.name == "hypothesis (openai)")
    assert agent.span_type == "AGENT"
    root_id = trace.data.spans[0].span_id
    llm_parent = next(s for s in trace.data.spans if s.name == "llm_reports").parent_id
    assert llm_parent == root_id  # the LLM calls (run in a thread pool) stay inside the search's trace

    # a user rating is added to the run and to the trace
    report_id = next(r["id"] for r in data["reports"] if r["role"] == "primary")
    assert client.post(f"/api/reports/{report_id}/feedback", json={"rating": 4, "comment": "Clear"}).status_code == 201
    run = mlflow.get_run(run.info.run_id)
    assert run.data.metrics["user_rating_primary"] == 4
    [assessment] = [a for a in mlflow.get_trace(tags["trace_id"]).info.assessments if a.name == "user_rating_primary"]
    assert assessment.value == 4 and assessment.rationale == "Clear"


def test_evaluation_scores_each_case(app, mlflow, vcf_reference, stub_live, monkeypatch):
    from app.evaluation import CASES, run_evaluation

    monkeypatch.setenv("MLFLOW_GENAI_EVAL_MAX_WORKERS", "1")
    result = run_evaluation(app, cases=["mock_luad_egfr_l858r_t790m"])
    checks = {"tiers_correct", "qc_filter_correct", "expected_drugs_established", "resistance_down_ranked",
              "established_not_contaminated", "no_therapy_flag_correct"}
    assert {f"{c}/mean" for c in checks} <= set(result.metrics)
    assert result.metrics["qc_filter_correct/mean"] == 1  # depends on the VCF only, not on the stubbed sources

    experiment = mlflow.get_experiment_by_name("test-pharmagenix-vcf")
    [trace] = mlflow.search_traces(locations=[experiment.experiment_id], run_id=result.run_id, return_type="list")
    assert {"1_qc", "3_variant_tiering", "7_ranking"} <= {s.name for s in trace.data.spans}
    assert checks <= {a.name for a in trace.info.assessments}
    # the case's search is a row of the evaluation run, not a run of its own
    assert len(mlflow.search_runs([experiment.experiment_id], output_format="list")) == 1
    from mlflow.genai.datasets import search_datasets
    [dataset] = search_datasets(experiment_ids=[experiment.experiment_id])
    assert len(dataset.to_df()) == len(CASES)


def test_tracking_off_is_a_no_op(monkeypatch):
    monkeypatch.setattr(tracking, "_state", {"enabled": False, "prefix": "", "artifacts": True, "experiments": {}})
    with tracking.track_search("gene", "BRAF", {}) as handle, tracking.span("stage") as s:
        assert handle is None and s is None


def test_benchmark_scores_grounded_and_ungrounded_reports(app, vcf_reference, stub_live, monkeypatch, tmp_path):
    """The benchmark on one mock VCF with fake models: grounded reports cite evidence IDs, the ungrounded one PMIDs
    (one real, one made up); the metrics, the paired summary and the output files."""
    import json

    from app import benchmark

    answers = {
        "openai": ("## Overview\nOsimertinib is the established option for EGFR p.L858R with T790M in this sample [E1, E2].\n"
                   "Gefitinib is down-ranked because T790M confers resistance to it [E1].\nAn unknown claim cites a "
                   "missing record of evidence here [E999]."),
        "anthropic": ("## Overview\nOsimertinib is the established option for EGFR p.L858R with T790M in this sample [E1].\n"
                      "Gefitinib remains a strong first-line choice for these EGFR variants in practice [E2]."),
    }

    class Fake:
        enabled = True
        max_tokens, timeout = 4000, 90  # like LLMClient

        def __init__(self, provider):
            self.provider = provider

        def complete(self, system, user):
            from app.services.llm import LLMResult
            text = answers[self.provider] if "Evidence" in user else (
                "## Overview\nOsimertinib is used for EGFR T790M lung cancer as shown in a large trial "
                "[PMID 25923549].\nErlotinib is another option with good responses in these patients [PMID 99999999].")
            return LLMResult(text=text, model=f"fake-{self.provider}", input_tokens=10, output_tokens=20, latency_ms=5)

    monkeypatch.setattr("app.services.llm.get_llm_client", lambda provider: Fake(provider))
    monkeypatch.setattr(benchmark, "_pubmed_titles", lambda pmids: {"25923549": "Osimertinib in EGFR T790M lung cancer"})
    monkeypatch.setattr(benchmark, "_drug_dictionary", lambda: {"OSIMERTINIB": "C1", "GEFITINIB": "C2", "ERLOTINIB": "C3"})
    summary = benchmark.run(only=["mock_luad_egfr_l858r_t790m"], echo=lambda *a: None, out_dir=tmp_path)

    rows = {r["arm"]: r for r in __import__("csv").DictReader(open(tmp_path / "benchmark_cases.csv"))}
    assert set(rows) == set(benchmark.ARMS)
    # TestConfig: primary = openai, comparison = anthropic
    assert float(rows["grounded-primary"]["citation_validity"]) == 3 / 4      # E999 was not in the prompt
    assert float(rows["grounded-comparison"]["citation_validity"]) == 1.0
    assert float(rows["ungrounded-primary"]["citation_validity"]) == 0.5      # one PMID does not exist
    assert float(rows["ungrounded-primary"]["pmid_on_topic"]) == 1.0
    assert rows["grounded-primary"]["unsafe_mentions"] == "0"                 # "down-ranked ... resistance"
    assert rows["grounded-comparison"]["unsafe_mentions"] == "1"              # gefitinib named without a warning
    assert float(rows["grounded-primary"]["gold_recall"]) == 1.0              # osimertinib is the expected drug
    assert summary["arms"]["grounded-primary"]["sentence_coverage"]["n"] == 1
    assert (tmp_path / "benchmark.md").read_text().startswith("# Benchmark")
    assert {r["arm"] for r in json.loads((tmp_path / "benchmark_reports.json").read_text())} == set(benchmark.ARMS)

    # Re-running one arm with a higher output limit keeps the other arms' reports and re-scores them together
    before = {r["arm"]: r["text"] for r in json.loads((tmp_path / "benchmark_reports.json").read_text())}
    benchmark.run(arms=["ungrounded-primary"], only=["mock_luad_egfr_l858r_t790m"], echo=lambda *a: None,
                  out_dir=tmp_path, max_tokens=8000)
    after = {r["arm"]: r for r in json.loads((tmp_path / "benchmark_reports.json").read_text())}
    assert set(after) == set(benchmark.ARMS)
    assert after["grounded-primary"]["text"] == before["grounded-primary"]
    assert after["ungrounded-primary"]["max_tokens"] == 8000 and after["grounded-primary"]["max_tokens"] == 4000
    assert "| ungrounded-primary | 8,000 | 0 of 1 |" in (tmp_path / "benchmark.md").read_text()
