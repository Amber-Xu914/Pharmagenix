"""MLflow tracking (optional): one run + one trace per search, user ratings added afterwards. Uses a throwaway
SQLite tracking store; nothing leaves the machine."""

import pytest

from app import create_app, tracking
from app.config import TestConfig
from app.extensions import db

from .test_vcf import FakeLLM, stub_live, vcf_reference  # noqa: F401 - fixtures


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
