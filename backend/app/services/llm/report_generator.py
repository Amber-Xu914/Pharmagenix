"""Turn a query's evidence into LLM-written reports, logging every call and checking citations."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from flask import current_app

from ...extensions import db
from ...models import PromptLog, Report
from ..prompts import get_active_template, render_prompt
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


def generate_reports(query, targets, template=None):
    """targets: list of (provider, role). LLM calls run in parallel; DB writes stay on this thread."""
    template = template or get_active_template()
    max_items = current_app.config["MAX_PROMPT_EVIDENCE"]
    jobs = [_prepare(query, template, provider, role, max_items) for provider, role in targets]

    runnable = [job for job in jobs if job.error is None]
    if runnable:
        with ThreadPoolExecutor(max_workers=len(runnable)) as pool:
            list(pool.map(_call, runnable))

    reports = [_finalize(query, template, job) for job in jobs]
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
    try:
        job.result = job.client.complete(job.system, job.user)
    except Exception as exc:  # noqa: BLE001 - recorded as a failed report
        logger.warning("LLM call to %s failed: %s", job.provider, exc)
        job.error = exc


def _finalize(query, template, job):
    model = job.client.model if job.client else "unknown"
    report = Report(query=query, template_id=template.id, provider=job.provider, model=model, role=job.role)
    log = PromptLog(report=report, query_id=query.id, template_id=template.id, provider=job.provider,
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
        if job.prompt_ids and not used:
            problems.append("report contains no citations")
        if result.truncated:
            problems.append("output truncated (token limit reached)")

        report.model = log.model = result.model or model
        report.content_md = log.raw_response = result.text
        report.citations = [eid for eid in used if eid not in invalid]
        report.invalid_citations = invalid
        report.status = "flagged" if problems else "success"
        report.error_message = "; ".join(problems) or None
        report.latency_ms = log.latency_ms = result.latency_ms
        report.input_tokens = log.input_tokens = result.input_tokens
        report.output_tokens = log.output_tokens = result.output_tokens

    log.status = report.status
    log.error_message = report.error_message
    db.session.add_all([report, log])
    return report
