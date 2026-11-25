"""MLflow evaluation of Workflow 4 (patient VCF + cancer type -> candidate drugs) on the mock VCFs.

    flask --app run mlflow-eval            deterministic stages only (QC, tiering, tracks): fast, no LLM cost
    flask --app run mlflow-eval --llm      the whole workflow, including the LLM reports and exploration agents

Each run appears in the MLflow UI under experiment "<prefix>-vcf":
  - Datasets         "<prefix>-vcf-cases": every mock VCF with its expected answer (CASES below)
  - Judges           the quality checks (scorers) below
  - Evaluation runs  one row per case and one column per check; each row links to the case's trace

The expected answers come from the published evidence (CIViC / FDA labels / NCCN for NSCLC), not from the
pipeline's own output, so a failing check is either a pipeline bug or a gap in the reference data.
"""

import logging
import os

from flask import current_app

from . import tracking

logger = logging.getLogger(__name__)

# Expected answers per mock VCF (app/mock_vcfs/manifest.json).
#   tiers                variant -> expected tier, or a list of acceptable tiers
#   qc_dropped           variants removed by the QC filters (FILTER / depth / ALT reads / VAF)
#   qc_passed            variants left after QC, germline and consequence filtering
#   established          drugs that must be on the established track and not down-ranked
#   down_ranked          drugs that must be down-ranked for resistance, or absent from the established track
#   forbidden            drugs that must not be on the established track (e.g. from a QC artefact)
#   no_targeted_therapy  whether the "no approved targeted therapy" outcome is expected
CASES = [
    {"example": "mock_luad_egfr_l858r_t790m", "cancer_type": "LUAD", "expected": {
        "tiers": {"EGFR p.L858R": 1, "EGFR p.T790M": 1, "STK11 p.Q37*": [1, 2], "TP53 p.R273H": 2,
                  "LRP1B p.L1334P": 3},
        "qc_dropped": 3, "qc_passed": 5,
        "established": ["Osimertinib"],
        "down_ranked": ["Gefitinib", "Erlotinib", "Afatinib"],          # T790M: resistance to 1st/2nd-gen TKIs
        "forbidden": ["Sotorasib", "Adagrasib", "Dabrafenib + Trametinib"],  # from the QC artefacts KRAS / BRAF
        "no_targeted_therapy": False}},
    {"example": "mock_luad_egfr_t790m_c797s", "cancer_type": "LUAD", "expected": {
        "tiers": {"EGFR p.L858R": 1, "EGFR p.T790M": 1, "EGFR p.C797S": 1, "TP53 p.R273H": 2},
        "qc_dropped": 0, "qc_passed": 4,
        "established": [],
        "down_ranked": ["Osimertinib", "Gefitinib", "Erlotinib"],        # C797S: osimertinib resistance
        "forbidden": [],
        "no_targeted_therapy": False}},
    {"example": "mock_luad_egfr_exon19del", "cancer_type": "LUAD", "expected": {
        "tiers": {"EGFR p.E746_A750del": 1, "TP53 p.R175H": 2, "CDKN2A p.R80*": 2},
        "qc_dropped": 0, "qc_passed": 3,
        "established": ["Osimertinib", "Gefitinib", "Erlotinib", "Afatinib"],
        "down_ranked": [],
        "forbidden": ["Sotorasib", "Dabrafenib + Trametinib"],
        "no_targeted_therapy": False}},
    {"example": "mock_luad_kras_g12c", "cancer_type": "LUAD", "expected": {
        "tiers": {"KRAS p.G12C": 1, "STK11 p.Q37*": [1, 2], "KEAP1 p.G333C": [2, 3]},
        "qc_dropped": 0, "qc_passed": 3,
        "established": ["Sotorasib", "Adagrasib"],                       # both FDA-approved for KRAS G12C NSCLC
        "down_ranked": [],
        "forbidden": ["Gefitinib", "Erlotinib", "Osimertinib"],
        "no_targeted_therapy": False}},
    {"example": "mock_luad_braf_v600e", "cancer_type": "LUAD", "expected": {
        "tiers": {"BRAF p.V600E": 1, "TP53 p.R248Q": 2},
        "qc_dropped": 0, "qc_passed": 2,
        "established": ["Dabrafenib + Trametinib"],
        "down_ranked": [],
        "forbidden": ["Osimertinib", "Sotorasib"],
        "no_targeted_therapy": False}},
    {"example": "mock_lusc_no_targeted", "cancer_type": "LUSC", "expected": {
        "tiers": {"TP53 p.R248Q": 2, "PIK3CA p.E545K": 2, "NFE2L2 p.R34G": 2, "CDKN2A p.R80*": 2},
        "qc_dropped": 0, "qc_passed": 4,
        "established": [], "down_ranked": [],
        "forbidden": ["Alpelisib + Fulvestrant"],                       # PIK3CA: approved in breast cancer only
        "no_targeted_therapy": True}},
    {"example": "mock_sclc_tp53_rb1", "cancer_type": "SCLC", "expected": {
        "tiers": {"TP53 p.R273H": 2, "RB1 p.R320*": [1, 2]},
        "qc_dropped": 0, "qc_passed": 2,
        "established": [], "down_ranked": [], "forbidden": [],
        "no_targeted_therapy": True}},
    {"example": "mock_luad_tier3_only", "cancer_type": "LUAD", "expected": {
        "tiers": {"KEAP1 p.R413L": 3, "LRP1B p.L1334P": 3},
        "qc_dropped": 0, "qc_passed": 2,
        "established": [], "down_ranked": [], "forbidden": [],
        "no_targeted_therapy": True}},
]

AGENT_ROLES = ("literature", "biology", "hypothesis")


def records(cases=None):
    """The evaluation dataset: inputs (what the user submits) and expectations (the expected answer)."""
    return [{"inputs": {"example": c["example"], "cancer_type": c["cancer_type"]}, "expectations": c["expected"]}
            for c in CASES if not cases or c["example"] in cases]


def summarise(evidence, reports=()):
    """The workflow's structured answer, compared by the scorers with the expected answer."""
    variants = evidence.get("variants") or {}
    counts = variants.get("counts") or {}
    tracks = evidence.get("tracks") or {}
    return {
        "tiers": {v["notation"]: v["tier"] for v in variants.get("tiered") or []},
        "qc_dropped": (counts.get("dropped") or {}).get("qc", 0),
        "qc_passed": counts.get("qc_passed", 0),
        "established": [{"drug": c["drug"], "down_ranked": bool(c.get("strong_resistance")),
                         "variants": c.get("variants") or [], "score": c.get("score")}
                        for c in tracks.get("established") or []],
        "exploratory": [c["drug"] for c in tracks.get("exploratory") or []][:15],
        "no_targeted_therapy": bool((evidence.get("outcome") or {}).get("no_targeted_therapy")),
        "reports": [{"role": r.role, "status": r.status, "problems": r.error_message,
                     "citations": len(r.citations or []), "invalid_citations": r.invalid_citations or []}
                    for r in reports],
    }


def make_predict_fn(app, llm):
    def predict_fn(example, cancer_type):
        # MLflow calls this from worker threads, which have no Flask app context of their own.
        with app.app_context():
            from .services.vcf_explorer import load_example, run_vcf_exploration

            variants, info, _ = load_example(example)
            if llm:  # the full workflow, saved as a query like a search from the web UI
                query = run_vcf_exploration(variants, info, cancer_type)
                return summarise(query.evidence, query.reports)
            from .services.variants.cancer_types import resolve_cancer_type
            from .services.variants.pipeline import run_pipeline

            with tracking.span("vcf_workflow", inputs={"sample": example, "cancer_type": cancer_type}):
                return summarise(run_pipeline(variants, info, resolve_cancer_type(cancer_type)))
    return predict_fn


# ---------- quality checks (scorers) ----------
# One criterion each, pass / fail. They are self-contained (no module-level names) so MLflow can register them.

def build_scorers(llm):
    from mlflow.genai.scorers import scorer

    @scorer
    def tiers_correct(outputs, expectations):
        """Every expected variant is in its expected tier (Tier 1/2/3; missing = not tiered)."""
        from mlflow.entities import Feedback

        wrong = []
        for variant, tier in expectations["tiers"].items():
            allowed = tier if isinstance(tier, list) else [tier]
            got = outputs["tiers"].get(variant)
            if got not in allowed:
                wrong.append(f"{variant}: Tier {got or 'none'}, expected {' or '.join(map(str, allowed))}")
        return Feedback(value=not wrong, rationale="; ".join(wrong) or "all variants in the expected tier")

    @scorer
    def qc_filter_correct(outputs, expectations):
        """The QC artefacts are removed and the real somatic variants are kept."""
        from mlflow.entities import Feedback

        got = (outputs["qc_dropped"], outputs["qc_passed"])
        want = (expectations["qc_dropped"], expectations["qc_passed"])
        return Feedback(value=got == want, rationale=f"QC dropped {got[0]} (expected {want[0]}), "
                                                     f"kept {got[1]} (expected {want[1]})")

    @scorer
    def expected_drugs_established(outputs, expectations):
        """Outcome: the guideline drugs for this sample are on the established track and not down-ranked."""
        from mlflow.entities import Feedback

        shown = {c["drug"].lower(): c for c in outputs["established"]}
        missing = [d for d in expectations["established"] if d.lower() not in shown]
        down = [d for d in expectations["established"] if d.lower() in shown and shown[d.lower()]["down_ranked"]]
        problems = [f"missing: {', '.join(missing)}"] * bool(missing) + [f"down-ranked: {', '.join(down)}"] * bool(down)
        return Feedback(value=not problems, rationale="; ".join(problems) or "all expected drugs established")

    @scorer
    def resistance_down_ranked(outputs, expectations):
        """Drugs the sample is resistant to are down-ranked (or not on the established track)."""
        from mlflow.entities import Feedback

        shown = {c["drug"].lower(): c for c in outputs["established"]}
        wrong = [d for d in expectations["down_ranked"]
                 if d.lower() in shown and not shown[d.lower()]["down_ranked"]]
        return Feedback(value=not wrong, rationale=f"not down-ranked: {', '.join(wrong)}" if wrong
                        else "resistance handled")

    @scorer
    def established_not_contaminated(outputs, expectations):
        """The established track holds only drugs backed by a Tier 1–2 variant, and none of the forbidden drugs."""
        from mlflow.entities import Feedback

        tier12 = {v for v, t in outputs["tiers"].items() if t in (1, 2)}
        forbidden = {d.lower() for d in expectations["forbidden"]}
        problems = []
        for c in outputs["established"]:
            if c["drug"].lower() in forbidden:
                problems.append(f"{c['drug']} should not be established")
            elif c["variants"] and not set(c["variants"]) & tier12:
                problems.append(f"{c['drug']} rests on {', '.join(c['variants'])}, none in Tier 1–2")
        if expectations["no_targeted_therapy"] and any(not c["down_ranked"] for c in outputs["established"]):
            problems.append("drugs established although no approved targeted therapy is expected")
        return Feedback(value=not problems, rationale="; ".join(problems) or "clean")

    @scorer
    def no_therapy_flag_correct(outputs, expectations):
        """The "no approved targeted therapy" outcome is shown exactly when it should be."""
        from mlflow.entities import Feedback

        got, want = outputs["no_targeted_therapy"], expectations["no_targeted_therapy"]
        return Feedback(value=got == want, rationale=f"flag {got}, expected {want}")

    scorers = [tiers_correct, qc_filter_correct, expected_drugs_established, resistance_down_ranked,
               established_not_contaminated, no_therapy_flag_correct]
    if not llm:
        return scorers

    @scorer
    def report_citations_valid(outputs):
        """The analysis report answers, cites evidence, and cites only evidence IDs it was given."""
        from mlflow.entities import Feedback

        main = [r for r in outputs["reports"] if r["role"] in ("primary", "comparison")]
        if not main:
            return Feedback(value=not outputs["tiers"], rationale="no report (expected only when nothing is tiered)")
        problems = [f"{r['role']}: {r['status']}" + (f" ({r['problems']})" if r["problems"] else "")
                    for r in main if r["status"] != "success" or r["invalid_citations"] or not r["citations"]]
        return Feedback(value=not problems, rationale="; ".join(problems) or "all citations valid")

    @scorer
    def agent_statements_grounded(outputs):
        """Every exploration-agent statement cites evidence or is labelled as a hypothesis."""
        from mlflow.entities import Feedback

        agents = [r for r in outputs["reports"] if r["role"] in ("literature", "biology", "hypothesis")]
        if not agents:
            return Feedback(value=True, rationale="no exploration agents ran")
        problems = [f"{r['role']}: {r['problems'] or r['status']}" for r in agents if r["status"] != "success"]
        return Feedback(value=not problems, rationale="; ".join(problems) or "all statements grounded")

    return scorers + [report_citations_valid, agent_statements_grounded]


# ---------- the run ----------

def _dataset(experiment_id, name, data):
    """The cases as an MLflow evaluation dataset (shown under Datasets); falls back to the plain list."""
    try:
        from mlflow.genai.datasets import create_dataset, search_datasets

        found = search_datasets(experiment_ids=[experiment_id], filter_string=f"name = '{name}'")
        dataset = found[0] if found else create_dataset(name=name, experiment_id=[experiment_id])
        dataset.merge_records(data)
        return dataset
    except Exception as exc:  # noqa: BLE001 - the evaluation runs on the plain list too
        logger.warning("MLflow evaluation dataset not saved: %s", exc)
        return data


def _register(scorers, experiment_id):
    """Save the checks under Judges (re-registering adds a version). MLflow allows this for code-based checks only
    when MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS=true, because a registered check is code that the store runs later:
    enable it only for a tracking store you trust (e.g. the local mlflow.db). The checks are scored either way."""
    if os.getenv("MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS", "").lower() != "true":
        return
    for s in scorers:
        try:
            s.register(name=s.name, experiment_id=experiment_id)
        except Exception as exc:  # noqa: BLE001
            logger.info("Scorer %s not registered: %s", s.name, exc)


def run_evaluation(app, llm=False, cases=None):
    """Evaluate the workflow on the mock VCFs; returns MLflow's EvaluationResult."""
    import mlflow

    if not tracking.enabled():
        raise RuntimeError("MLflow tracking is off: set MLFLOW_TRACKING_URI (e.g. sqlite:///mlflow.db)")
    cfg = current_app.config
    experiment_id = tracking._experiment_id("vcf")
    mlflow.set_experiment(experiment_id=experiment_id)
    data = records(cases)
    if not data:
        raise ValueError(f"No such case: {', '.join(cases)}")
    scorers = build_scorers(llm)
    _register(scorers, experiment_id)
    # The saved dataset always holds every case; a run on selected cases uses just those records.
    dataset = _dataset(experiment_id, f"{cfg['MLFLOW_EXPERIMENT_PREFIX']}-vcf-cases", records())
    if cases or isinstance(dataset, list):
        dataset = data

    from .services.prompts import get_active_template

    template = get_active_template("vcf") if llm else None
    # MLflow would otherwise run the first case an extra time to check that it is traced (another LLM call).
    os.environ.setdefault("MLFLOW_GENAI_EVAL_SKIP_TRACE_VALIDATION", "true")
    # One case at a time: loading the MedCPT models (PyTorch) in MLflow's worker threads crashes on macOS.
    os.environ.setdefault("MLFLOW_GENAI_EVAL_MAX_WORKERS", "1")
    tracking._state["evaluating"] = True  # each case is a row of this run, not a run of its own
    try:
        with mlflow.start_run(run_name=f"eval {'full (LLM)' if llm else 'deterministic'} · {len(data)} cases"):
            mlflow.set_tags({"evaluation": "workflow4", "mode": "llm" if llm else "deterministic"})
            mlflow.log_params({"cases": len(data), "llm": llm, "qc_min_depth": cfg["VCF_QC_MIN_DEPTH"],
                               "qc_min_vaf": cfg["VCF_QC_MIN_VAF"], "tier3_weights": str(cfg["VCF_TIER3_WEIGHTS"]),
                               "tier3_min_score": cfg["VCF_TIER3_MIN_SCORE"],
                               "template_version": template.version if template else "n/a"})
            return mlflow.genai.evaluate(data=dataset, predict_fn=make_predict_fn(app, llm), scorers=scorers)
    finally:
        tracking._state["evaluating"] = False


# ---------- classification metrics: precision / recall / F1 and confusion matrices (no MLflow needed) ----------
#
#   flask --app run eval-metrics      runs stages 1–7 on the mock VCFs and writes docs/evaluation/pipeline_metrics.json,
#                                     shown in the admin console (Evaluation tab)
#
# Two tasks, both scored against CASES (the expected answers above):
#   1. Drug recommendation (binary). Every labelled drug of a case is a positive (expected on the established track,
#      not down-ranked) or a negative (resistance: down_ranked; or forbidden). "Predicted positive" = established and
#      not down-ranked. In a "no approved targeted therapy" case every recommended drug is a false positive. Other
#      unlabelled recommendations (e.g. amivantamab regimens for EGFR L858R) are listed but not scored: the
#      expected answers name the guideline drugs, not every acceptable one.
#   2. Variant tiering (multi-class): expected vs. assigned tier of every labelled variant. Where a case accepts
#      two tiers (e.g. STK11 Q37* Tier 1 or 2), an accepted answer counts as correct.

METRICS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            "docs", "evaluation", "pipeline_metrics.json")
TIER_LABELS = ["Tier 1", "Tier 2", "Tier 3", "Not tiered"]


def drug_rows(outputs, expected):
    """(scored rows, unscored extra recommendations) for one case."""
    recommended = {c["drug"].lower(): c["drug"] for c in outputs["established"] if not c["down_ranked"]}
    labels = {d.lower(): (d, True) for d in expected["established"]}
    labels.update({d.lower(): (d, False) for d in expected["down_ranked"] + expected["forbidden"]})
    rows = [{"drug": name, "expected": truth, "predicted": key in recommended} for key, (name, truth) in labels.items()]
    extras = [name for key, name in recommended.items() if key not in labels]
    if expected["no_targeted_therapy"]:
        rows += [{"drug": name, "expected": False, "predicted": True} for name in extras]
        extras = []
    return rows, sorted(extras)


def tier_rows(outputs, expected):
    rows = []
    for variant, tier in expected["tiers"].items():
        allowed = tier if isinstance(tier, list) else [tier]
        got = outputs["tiers"].get(variant)
        truth = got if got in allowed else allowed[0]
        rows.append({"variant": variant, "expected": f"Tier {truth}",
                     "predicted": f"Tier {got}" if got else "Not tiered", "accepted": [f"Tier {t}" for t in allowed]})
    return rows


def _ratio(num, den):
    return round(num / den, 4) if den else None


def _f1(precision, recall):
    return round(2 * precision * recall / (precision + recall), 4) if precision and recall else 0.0


def binary_metrics(rows):
    tp = sum(r["expected"] and r["predicted"] for r in rows)
    fp = sum(not r["expected"] and r["predicted"] for r in rows)
    fn = sum(r["expected"] and not r["predicted"] for r in rows)
    tn = sum(not r["expected"] and not r["predicted"] for r in rows)
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    return {"n": len(rows), "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
            "precision": precision, "recall": recall, "f1": _f1(precision, recall),
            "specificity": _ratio(tn, tn + fp), "accuracy": _ratio(tp + tn, len(rows))}


def multiclass_metrics(rows, labels=TIER_LABELS):
    """Confusion matrix (rows = expected, columns = predicted), per-class precision / recall / F1, macro F1."""
    matrix = [[sum(r["expected"] == e and r["predicted"] == p for r in rows) for p in labels] for e in labels]
    per_class = {}
    for i, label in enumerate(labels):
        tp = matrix[i][i]
        predicted, actual = sum(row[i] for row in matrix), sum(matrix[i])
        if not actual and not predicted:
            continue  # the class does not occur (e.g. nothing is expected to stay untiered)
        precision, recall = _ratio(tp, predicted), _ratio(tp, actual)
        per_class[label] = {"support": actual, "precision": precision, "recall": recall,
                            "f1": _f1(precision or 0, recall or 0)}
    scored = [v for v in per_class.values() if v["support"]]
    return {"n": len(rows), "labels": labels, "matrix": matrix, "per_class": per_class,
            "accuracy": _ratio(sum(matrix[i][i] for i in range(len(labels))), len(rows)),
            "macro_f1": round(sum(v["f1"] for v in scored) / len(scored), 4) if scored else None}


def score_outputs(outputs_by_case):
    """{example: summarise(...) output} -> the metrics document (pure: no pipeline, no I/O)."""
    cases, all_drugs, all_tiers = [], [], []
    for case in CASES:
        outputs = outputs_by_case.get(case["example"])
        if outputs is None:
            continue
        drugs, extras = drug_rows(outputs, case["expected"])
        tiers = tier_rows(outputs, case["expected"])
        all_drugs += drugs
        all_tiers += tiers
        cases.append({"example": case["example"], "cancer_type": case["cancer_type"], "drugs": drugs,
                      "unscored_recommendations": extras, "tiers": tiers,
                      "drug_metrics": binary_metrics(drugs)["confusion"],
                      "tiers_correct": sum(r["predicted"] == r["expected"] for r in tiers)})
    return {"cases": cases, "drug_recommendation": binary_metrics(all_drugs),
            "variant_tiering": multiclass_metrics(all_tiers)}


def run_metrics(cases=None):
    """Run stages 1–7 (deterministic, no LLM) on the mock VCFs and score them. Needs an app context."""
    from datetime import datetime, timezone

    from .services.variants.cancer_types import resolve_cancer_type
    from .services.variants.pipeline import run_pipeline
    from .services.vcf_explorer import load_example

    outputs = {}
    for case in CASES:
        if cases and case["example"] not in cases:
            continue
        variants, info, _ = load_example(case["example"])
        outputs[case["example"]] = summarise(run_pipeline(variants, info, resolve_cancer_type(case["cancer_type"])))
    result = score_outputs(outputs)
    cfg = current_app.config
    result["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result["settings"] = {"qc_min_depth": cfg["VCF_QC_MIN_DEPTH"], "qc_min_vaf": cfg["VCF_QC_MIN_VAF"],
                          "tier3_min_score": cfg["VCF_TIER3_MIN_SCORE"]}
    return result
