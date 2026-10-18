"""MLflow integration (optional): experiment tracking and tracing for both workflows.

Enabled when MLFLOW_TRACKING_URI is set (e.g. sqlite:///mlflow.db). Then every search becomes
  - an MLflow run (experiment "<prefix>-gene" or "<prefix>-vcf"): parameters (thresholds, weights, template version,
    models), metrics (counts per stage, stage timings, LLM latency / tokens, citation checks) and artifacts (the
    evidence bundle and the generated reports), tagged with the query ID; user ratings are added later
  - a trace of the same run: a span per workflow stage and per data source, and every OpenAI / Anthropic call
    (MLflow autolog), including the exploration agents, so prompts and responses can be inspected
Tracking must never break a search: when it is off every helper is a no-op, and MLflow errors are only logged.
"""

import contextlib
import contextvars
import json
import logging
import re
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_state = {"enabled": False, "prefix": "pharmagenix", "artifacts": True, "experiments": {}}
# Stage timings of the search being tracked, read when its run is logged.
_timings = contextvars.ContextVar("pharmagenix_stage_timings", default=None)


def init_tracking(app):
    uri = app.config.get("MLFLOW_TRACKING_URI")
    if not uri:
        return
    try:
        import mlflow

        mlflow.set_tracking_uri(uri)
        mlflow.openai.autolog()      # every OpenAI call becomes an LLM span
        mlflow.anthropic.autolog()   # and every Anthropic (comparison report) call
        _state.update(enabled=True, prefix=app.config["MLFLOW_EXPERIMENT_PREFIX"],
                      artifacts=app.config["MLFLOW_LOG_ARTIFACTS"])
        logger.info("MLflow tracking enabled: %s", uri)
    except Exception as exc:  # noqa: BLE001 - the app works without tracking
        logger.warning("MLflow tracking disabled: %s", exc)


def enabled():
    return _state["enabled"]


def _experiment_id(workflow):
    """One experiment per workflow; created on first use. Passing the ID to each run (instead of the global
    mlflow.set_experiment) keeps concurrent requests from different workflows apart."""
    import mlflow

    name = f"{_state['prefix']}-{workflow}"
    if name not in _state["experiments"]:
        experiment = mlflow.get_experiment_by_name(name)
        _state["experiments"][name] = experiment.experiment_id if experiment else mlflow.create_experiment(name)
    return _state["experiments"][name]


# ---------- spans ----------

@contextlib.contextmanager
def span(name, span_type="CHAIN", inputs=None):
    """A child span of the current trace, and a stage timing for the run. A no-op when tracking is off."""
    timings = _timings.get()
    start = time.perf_counter()
    if not enabled():
        try:
            yield None
        finally:
            if timings is not None:
                timings[name] = timings.get(name, 0) + (time.perf_counter() - start)
        return
    with contextlib.ExitStack() as stack:
        s = _enter_span(stack, name, span_type, inputs)
        try:
            yield s
        finally:
            if timings is not None:
                timings[name] = timings.get(name, 0) + (time.perf_counter() - start)


def _enter_span(stack, name, span_type, inputs):
    """Open a span, or return None if MLflow fails: tracking must never break a search."""
    try:
        import mlflow

        s = stack.enter_context(mlflow.start_span(name=name, span_type=span_type))
        if inputs is not None:
            s.set_inputs(inputs)
        return s
    except Exception as exc:  # noqa: BLE001
        logger.warning("MLflow span %s not recorded: %s", name, exc)
        return None


def set_outputs(s, outputs):
    if s is not None:
        try:
            s.set_outputs(outputs)
        except Exception as exc:  # noqa: BLE001
            logger.debug("span outputs not recorded: %s", exc)


def copy_context():
    """For the LLM thread pool: run each call in a copy of this context so its spans join the search's trace."""
    return contextvars.copy_context()


# ---------- one run (and trace) per search ----------

@contextlib.contextmanager
def track_search(workflow, name, inputs):
    """Wrap one search: opens the run and the root span; the caller logs the finished query with log_query()."""
    token = _timings.set({})
    if not enabled():
        try:
            yield None
        finally:
            _timings.reset(token)
        return
    import mlflow

    run = None
    try:
        # During an evaluation (evaluation.py) each search is one row of the evaluation run: a trace, no run.
        if not _state.get("evaluating"):
            run = mlflow.start_run(experiment_id=_experiment_id(workflow), run_name=name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("MLflow run not started: %s", exc)
    try:
        with contextlib.ExitStack() as stack:
            root = _enter_span(stack, f"{workflow}_workflow", "CHAIN", inputs)
            yield {"run": run, "root": root, "start": time.perf_counter()} if root is not None else None
    finally:
        _timings.reset(token)
        if run is not None:
            try:
                mlflow.end_run()
            except Exception as exc:  # noqa: BLE001
                logger.warning("MLflow run not closed: %s", exc)


def _metric_name(name):
    """MLflow names allow letters, digits and _ - . / : and spaces only."""
    return re.sub(r"[^A-Za-z0-9_\-./: ]+", "_", name).strip("_")


def log_query(handle, query, params=None):
    """Log the finished query (or follow-up) to its run: parameters, metrics, tags, artifacts. Each part is
    logged on its own, so one failing part doesn't lose the others."""
    if handle is None or handle.get("run") is None:
        return
    import mlflow

    evidence = query.evidence or {}
    root = handle["root"]
    set_outputs(root, {"query_id": query.id, "status": query.status,
                       "outcome": (evidence.get("outcome") or {}).get("status"),
                       "reports": {r.role: r.status for r in query.reports}})

    def metrics():
        timings = _timings.get() or {}
        out = {_metric_name(f"seconds_{k}"): round(v, 3) for k, v in timings.items()}
        out["seconds_total"] = round(time.perf_counter() - handle["start"], 3)
        out.update(_evidence_metrics(evidence))
        for report in query.reports:
            prefix = f"report_{report.role}"
            out[f"{prefix}_success"] = 1 if report.status == "success" else 0
            for field in ("latency_ms", "input_tokens", "output_tokens"):
                if getattr(report, field) is not None:
                    out[f"{prefix}_{field}"] = getattr(report, field)
            out[f"{prefix}_invalid_citations"] = len(report.invalid_citations or [])
            out[f"{prefix}_citations"] = len(report.citations or [])
        mlflow.log_metrics(out)

    def tags():
        out = {"query_id": str(query.id), "status": query.status, "trace_id": root.trace_id,
                "outcome": (evidence.get("outcome") or {}).get("status") or "",
                "entities": ", ".join(query.entities)[:250]}
        if query.parent_id:
            out["parent_query_id"] = str(query.parent_id)
        for source, state in (evidence.get("source_status") or {}).items():
            out[f"source.{source}"] = state.get("state", "")
        mlflow.set_tags(out)

    parts = [("tags", tags), ("params", lambda: mlflow.log_params({k: _param(v) for k, v in (params or {}).items()})),
             ("metrics", metrics)]
    if _state["artifacts"]:
        parts.append(("artifacts", lambda: _log_artifacts(query, evidence)))
    for name, log in parts:
        try:
            log()
        except Exception as exc:  # noqa: BLE001
            logger.warning("MLflow %s not logged for query %s: %s", name, query.id, exc)


def _param(value):
    text = json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
    return text[:6000]


def _evidence_metrics(evidence):
    out = {}
    if (evidence.get("workflow") or {}).get("key") == "vcf":
        counts = (evidence.get("variants") or {}).get("counts") or {}
        for key in ("total", "qc_passed", "tier1", "tier2", "tier3", "low_priority"):
            out[f"variants_{key}"] = counts.get(key, 0)
        for stage, n in (counts.get("dropped") or {}).items():
            out[f"variants_dropped_{stage}"] = n
        tracks = evidence.get("tracks") or {}
        established = tracks.get("established") or []
        out["drugs_established"] = sum(1 for c in established if not c.get("strong_resistance"))
        out["drugs_established_down_ranked"] = sum(1 for c in established if c.get("strong_resistance"))
        out["drugs_exploratory"] = len(tracks.get("exploratory") or [])
        out["drugs_resistance_only"] = len(evidence.get("resistance_only") or [])
        out["no_targeted_therapy"] = 1 if (evidence.get("outcome") or {}).get("no_targeted_therapy") else 0
        if established:
            out["top_established_score"] = established[0]["score"]
    else:
        for key, value in (evidence.get("counts") or {}).items():
            out[f"count_{key}"] = value
        table = evidence.get("table") or []
        out["pairs_strong"] = sum(1 for r in table if r.get("grade") == "strong")
        out["interaction_found"] = 0 if (evidence.get("outcome") or {}).get("status") == "none" else 1
    out["evidence_items"] = len(evidence.get("items") or [])
    return out


def _log_artifacts(query, evidence):
    import mlflow

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "evidence.json").write_text(json.dumps(evidence, default=str, indent=1))
        for report in query.reports:
            if report.content_md:
                (folder / f"report_{report.role}_{report.id}.md").write_text(report.content_md)
        mlflow.log_artifacts(tmp)


# ---------- feedback ----------

def log_feedback(report, rating, comment=None):
    """A user's rating of a report: added to the search's run (metric) and to its trace (assessment)."""
    if not enabled() or rating is None:
        return
    try:
        import mlflow
        from mlflow.entities import AssessmentSource

        experiments = [e for e in (mlflow.get_experiment_by_name(f"{_state['prefix']}-{w}") for w in ("gene", "vcf")) if e]
        runs = mlflow.search_runs(experiment_ids=[e.experiment_id for e in experiments], output_format="list",
                                  filter_string=f"tags.query_id = '{report.query_id}'", max_results=1)
        if not runs:
            return
        run = runs[0]
        client = mlflow.MlflowClient()
        client.log_metric(run.info.run_id, f"user_rating_{report.role}", rating)
        trace_id = run.data.tags.get("trace_id")
        if trace_id:
            mlflow.log_feedback(trace_id=trace_id, name=f"user_rating_{report.role}", value=rating,
                                rationale=comment, source=AssessmentSource(source_type="HUMAN", source_id="web_ui"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("MLflow feedback not logged for report %s: %s", report.id, exc)
