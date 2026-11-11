"""Workflow 4 orchestration: a somatic VCF (test data: synthetic mock VCFs) + cancer type -> candidate drug report.

The deterministic tools (QC, tiering, evidence classification, ranking) run in services/variants/pipeline.py. Then,
in one parallel batch, the Analysis agent explains both tracks (the active vcf_report template) and the exploration
agents (variants/agents.py) work on the exploratory track. Nothing is sent to an LLM when no variant was tiered.
"""

import gzip
import json
from pathlib import Path

from flask import current_app

from .. import tracking
from ..accounts import current_user_id
from ..errors import APIError
from ..http_cache import replayed_hosts
from ..extensions import db
from ..models import Query
from .interactions import record_interactions
from .llm.report_generator import generate_reports
from .variants.cancer_types import resolve_cancer_type
from .variants.parse import parse_upload
from .variants.agents import build_prompts as agent_prompts, should_run as agents_should_run
from .variants.clinical import build_prompt as clinical_prompt, should_run as clinical_should_run
from .variants.pipeline import run_pipeline


def read_upload(raw, filename):
    max_bytes = current_app.config["MAX_VCF_BYTES"]
    if len(raw) > max_bytes:
        raise APIError(f"The file is too large (max {max_bytes // 1_000_000} MB)")
    if raw[:2] == b"\x1f\x8b":  # .vcf.gz
        try:
            raw = gzip.decompress(raw)
        except OSError as exc:
            raise APIError(f"Could not decompress {filename}: {exc}") from exc
        if len(raw) > max_bytes * 5:
            raise APIError("The decompressed file is too large")
    return parse_upload(raw.decode("utf-8", errors="replace"), filename)


MOCK_DIR = Path(__file__).resolve().parent.parent / "mock_vcfs"  # made by scripts/make_mock_vcfs.py


def mock_examples():
    """The synthetic mock VCFs offered as test data: [{id, file, cancer_type, label, variants}]."""
    manifest = MOCK_DIR / "manifest.json"
    return json.loads(manifest.read_text()) if manifest.exists() else []


def mock_path(example_id):
    example = next((e for e in mock_examples() if e["id"] == example_id), None)
    if example is None:
        raise APIError(f"Unknown example: {example_id}")
    return example, MOCK_DIR / example["file"]


def load_example(example_id):
    example, path = mock_path(example_id)
    variants, info = parse_upload(path.read_text(), example["file"])
    info.update(filename=example["file"], example=example["id"])
    return variants, info, example


def run_vcf_exploration(variants, info, cancer_type, compare=False, medications=(), clinical=None):
    """With MLflow tracking on, the search is one run and one trace (see tracking.py)."""
    cancer = resolve_cancer_type(cancer_type)
    label = info.get("example") or info.get("filename") or info.get("tumour_sample") or "uploaded sample"
    with tracking.track_search("vcf", label, {"sample": label, "cancer_type": cancer["code"],
                                              "variants": len(variants), "format": info.get("format")}) as handle:
        query = Query(user_id=current_user_id(), entities=[label], entity_type="vcf", relation_types=[],
                      status="pending")
        db.session.add(query)
        db.session.commit()  # persist early so even a crash is visible in the admin panel
        try:
            query.evidence = {**run_pipeline(variants, info, cancer, medications, clinical),
                              "cached_sources": replayed_hosts()}
            record_interactions(query)
            db.session.commit()
            if query.evidence["outcome"]["status"] == "found":  # nothing tiered: no LLM, no drug claims
                _report(query, compare)
            query.status = "complete"
        except Exception as exc:
            db.session.rollback()
            query.status = "failed"
            query.error_message = str(exc)[:2000]
            db.session.commit()
            tracking.log_query(handle, query, _params(query, compare))
            raise
        db.session.commit()
        tracking.log_query(handle, query, _params(query, compare))
        return query


def run_vcf_followup(parent, followup_question=None, compare=False):
    """Re-ask / follow-up on a VCF query: the stored evidence is reused (the file is not kept)."""
    if not parent.evidence:
        raise APIError("This query has no stored evidence to reuse; upload the file again")
    with tracking.track_search("vcf", f"{parent.entities[0]} (follow-up)",
                               {"parent_query_id": parent.id, "followup": followup_question}) as handle:
        query = Query(parent_id=parent.id, user_id=current_user_id(), entities=parent.entities, entity_type="vcf",
                      relation_types=[],
                      followup_question=(followup_question or "").strip() or None, status="pending",
                      evidence=parent.evidence)
        record_interactions(query)
        db.session.add(query)
        db.session.commit()
        if query.evidence["outcome"]["status"] == "found":
            _report(query, compare)
        query.status = "complete"
        db.session.commit()
        tracking.log_query(handle, query, _params(query, compare))
        return query


def _params(query, compare):
    """The run's parameters: what was searched and every threshold / weight that shaped the result."""
    cfg = current_app.config
    evidence = query.evidence or {}
    inp = evidence.get("input") or {}
    parameters = evidence.get("parameters") or {}
    return {
        "workflow": "vcf", "sample": query.entities[0], "cancer_type": (inp.get("cancer_type") or {}).get("code"),
        "input_format": inp.get("format"), "annotation": inp.get("annotation") or "Ensembl VEP",
        "assembly": inp.get("assembly"), "compare": compare, "followup": bool(query.followup_question),
        **{f"qc_{k}": v for k, v in (parameters.get("qc") or {}).items()},
        **{f"tier3_{k}": v for k, v in (parameters.get("tier3") or {}).items()},
        "agents": cfg["VCF_AGENTS"], "literature_queries": cfg["VCF_LITERATURE_QUERIES"],
        "top_per_track": cfg["VCF_TOP_PER_TRACK"], "oncokb": bool(cfg["ONCOKB_API_TOKEN"]),
        "primary_model": cfg["OPENAI_MODEL"],
        "template_version": next((r.template.version for r in query.reports if r.template), None),
    }


def _report(query, compare):
    cfg = current_app.config
    targets = [(cfg["PRIMARY_PROVIDER"], "primary")]
    if compare:
        targets.append((cfg["COMPARISON_PROVIDER"], "comparison"))
    extra = agent_prompts(query) if agents_should_run(query.evidence) else []
    if clinical_should_run(query.evidence):
        extra.append(clinical_prompt(query))
    generate_reports(query, targets, extra_prompts=extra)
