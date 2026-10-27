"""Turn a query's evidence into LLM-written reports, logging every call and checking citations."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from flask import current_app

from ... import tracking
from ...extensions import db
from ...models import PromptLog, Report
from ..prompts import get_active_template, query_family, render_prompt
from . import LLMError, get_llm_client

logger = logging.getLogger(__name__)

CITATION_RE = re.compile(r"\[\s*(E\d+(?:\s*[,;]\s*E\d+)*)\s*\]")


def extract_citations(text, valid_ids):
    """Return (cited IDs in order of first use, cited IDs that were not in the prompt)."""
    used = []
    for match in CITATION_RE.finditer(text or ""):
        for eid in re.split(r"\s*[,;]\s*", match.group(1)):
            if eid not in used:
                used.append(eid)
    return used, [eid for eid in used if eid not in valid_ids]


@dataclass
class _Job:
    provider: str
    role: str
    client: object = None
    system: str = None
    user: str = None
    prompt_ids: list = field(default_factory=list)
    result: object = None
    error: Exception = None
    check: object = None  # optional extra validation: text -> list of problems (the exploration agents use it)


def _custom_job(p):
    job = _Job(provider=p["provider"], role=p["role"], system=p["system"], user=p["user"],
               prompt_ids=p["prompt_ids"], check=p.get("check"))
    try:
        job.client = get_llm_client(p["provider"])
        if not job.client.enabled:
            raise LLMError(f"{p['provider']} API key is not configured")
    except Exception as exc:  # noqa: BLE001 - recorded as a failed report
        job.error = exc
    return job


def generate_reports(query, targets, template=None, extra_prompts=()):
    """targets: list of (provider, role) rendered with the active template. extra_prompts: prompts built elsewhere
    (the Workflow 4 exploration agents: {"provider", "role", "system", "user", "prompt_ids", "check"}), logged and
    citation-checked the same way. All LLM calls run in parallel; DB writes stay on this thread."""
    template = template or (get_active_template(query_family(query)) if targets else None)
    max_items = current_app.config["MAX_PROMPT_EVIDENCE"]
    jobs = [_prepare(query, template, provider, role, max_items) for provider, role in targets]
    extra = [_custom_job(p) for p in extra_prompts]

    runnable = [job for job in jobs + extra if job.error is None]
    if runnable:
        with tracking.span("llm_reports", span_type="CHAIN", inputs={"roles": [j.role for j in runnable]}):
            # each call runs in a copy of this context, so its spans join the search's trace
            with ThreadPoolExecutor(max_workers=len(runnable)) as pool:
                futures = [pool.submit(tracking.copy_context().run, _call, job) for job in runnable]
                for future in futures:
                    future.result()

    reports = [_finalize(query, template, job) for job in jobs] + [_finalize(query, None, job) for job in extra]
    db.session.commit()
    return reports


def _prepare(query, template, provider, role, max_items):
    job = _Job(provider=provider, role=role)
    try:
        job.client = get_llm_client(provider)
        job.system, job.user, job.prompt_ids = render_prompt(template, query, query.evidence, max_items)
        if not job.client.enabled:
            raise LLMError(f"{provider} API key is not configured")
    except Exception as exc:  # noqa: BLE001 - recorded as a failed report
        job.error = exc
    return job


def _call(job):
    span_type = "AGENT" if job.check else "LLM"  # the Workflow 4 exploration agents, or the analysis report
    with tracking.span(f"{job.role} ({job.provider})", span_type=span_type,
                       inputs={"role": job.role, "evidence_ids": job.prompt_ids}) as s:
        try:
            job.result = job.client.complete(job.system, job.user)
            tracking.set_outputs(s, {"text": job.result.text, "latency_ms": job.result.latency_ms})
        except Exception as exc:  # noqa: BLE001 - recorded as a failed report
            logger.warning("LLM call to %s failed: %s", job.provider, exc)
            job.error = exc
            tracking.set_outputs(s, {"error": str(exc)[:500]})


def _finalize(query, template, job):
    model = job.client.model if job.client else "unknown"
    template_id = template.id if template else None
    report = Report(query=query, template_id=template_id, provider=job.provider, model=model, role=job.role)
    log = PromptLog(report=report, query_id=query.id, template_id=template_id, provider=job.provider,
                    model=model, system_prompt=job.system, user_prompt=job.user)

    if job.error is not None:
        report.status = "failed"
        report.error_message = f"{type(job.error).__name__}: {job.error}"[:2000]
    else:
        result = job.result
        used, invalid = extract_citations(result.text, set(job.prompt_ids))
        problems = []
        if invalid:
            problems.append(f"cites unknown evidence IDs: {', '.join(invalid)}")
        if job.prompt_ids and not used and not job.check:  # agents: their check covers each statement
            problems.append("report contains no citations")
        if result.truncated:
            problems.append("output truncated (token limit reached)")
        if job.check:
            problems += job.check(result.text)

        report.model = log.model = result.model or model
        report.content_md = log.raw_response = result.text
        report.set_citations(used, invalid, {i["id"]: i for i in (query.evidence or {}).get("items") or []})
        report.status = "flagged" if problems else "success"
        report.error_message = "; ".join(problems) or None
        report.latency_ms = log.latency_ms = result.latency_ms
        report.input_tokens = log.input_tokens = result.input_tokens
        report.output_tokens = log.output_tokens = result.output_tokens

    log.status = report.status
    log.error_message = report.error_message
    db.session.add_all([report, log])
    return report
