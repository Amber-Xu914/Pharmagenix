"""Prompt templates: defaults, lookup of the active version, and rendering with evidence."""

from jinja2.sandbox import SandboxedEnvironment

from ..extensions import db
from ..models import PromptTemplate
from .relations import labels

DEFAULT_TEMPLATE_NAME = "dgi_report"

# Applied at render time as well as in the default template so reports remain
# in Australian English when an older database template is active.
OUTPUT_LANGUAGE_INSTRUCTION = (
    "Write the entire response in clear Australian English, even when the user's input is in another language."
)

DEFAULT_SYSTEM_PROMPT = """You are a pharmacogenomics research assistant writing for biomedical researchers.
Rules:
- Write the entire response in clear Australian English, even when the user's input is in another language.
- Use ONLY the evidence items provided. Do not add facts from memory.
- Every factual sentence must cite one or more evidence IDs in square brackets, e.g. [E3] or [E3, E7].
- Never invent evidence IDs, PMIDs, drugs, genes or variants.
- If evidence is sparse, low-level or conflicting, say so explicitly.
- This is research support, not clinical advice."""

DEFAULT_USER_TEMPLATE = """Query ({{ entity_type }}): {{ entities | join(", ") }}
Relationship types of interest: {{ relation_labels | join(", ") }}
{% if followup_question %}
The user's follow-up question: {{ followup_question }}
Answer it directly in the Overview section.
{% endif %}
Evidence ({{ evidence_count }} items; cite by ID):
{{ evidence_block }}

Write a report in Markdown with these sections:
## Overview
## Key drug–gene interactions
## Pharmacogenomic (SNP-specific) findings
## Evidence gaps and caveats

Keep it under 500 words. Prefer higher-scoring interactions and higher PharmGKB evidence levels (1A > 1B > 2A > 2B > 3 > 4).
For SNP-specific findings, lead with CPIC guideline evidence (level A > B > C > D) where available."""

_env = SandboxedEnvironment(autoescape=False, trim_blocks=True, lstrip_blocks=True)


def get_active_template():
    template = PromptTemplate.query.filter_by(is_active=True).order_by(PromptTemplate.id.desc()).first()
    return template or seed_default_template()


def seed_default_template():
    existing = PromptTemplate.query.filter_by(name=DEFAULT_TEMPLATE_NAME).first()
    if existing:
        return existing
    template = PromptTemplate(
        name=DEFAULT_TEMPLATE_NAME,
        version=1,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        user_template=DEFAULT_USER_TEMPLATE,
        notes="Default template",
        is_active=True,
    )
    db.session.add(template)
    db.session.commit()
    return template


def format_evidence_item(item):
    kind = item["kind"]
    if kind == "interaction":
        types = ", ".join(item["interaction_types"]) or "unspecified"
        pmids = ", ".join(map(str, item["pmids"][:5])) or "none"
        return (f"[{item['id']}] DGIdb interaction | {item['gene']} – {item['drug']} | type: {types} | "
                f"score: {item['score']} | sources: {', '.join(item['sources'])} | PMIDs: {pmids}")
    if kind == "mechanism":
        pmids = ", ".join(map(str, item["pmids"][:5])) or "none"
        phase = f"{item['max_phase']:g}" if item.get("max_phase") is not None else "n/a"
        return (f"[{item['id']}] ChEMBL mechanism of action | {item['drug']} – {item['gene']} | "
                f"action: {item['action_type'] or 'unspecified'} | mechanism: {item['mechanism']} | "
                f"target: {item['target_name']} | max clinical phase: {phase} | PMIDs: {pmids}")
    if kind == "guideline":
        recs = "; ".join(
            f"{_format_lookup(r['lookup'])} -> {r['recommendation']} ({r['classification']})"
            for r in item.get("recommendations", [])[:4]
        ) or "no genotype-specific recommendations"
        return (f"[{item['id']}] CPIC guideline | {item['gene']} – {item['drug']} | CPIC level {item['cpic_level']} | "
                f"guideline: {item.get('guideline_name') or 'none published'} | recommendations: {recs[:1200]}")
    if kind == "variant_annotation":
        return (f"[{item['id']}] PharmGKB clinical annotation | variant: {item['variant']} | "
                f"genes: {', '.join(item['genes'])} | drugs: {', '.join(item['drugs'])} | "
                f"level: {item['level']} | phenotype: {', '.join(item['phenotype_categories'])} | "
                f"diseases: {', '.join(item['diseases']) or 'n/a'} | finding: {item.get('summary') or 'n/a'}")
    if kind == "drug_info":
        return (f"[{item['id']}] DrugBank | {item['drug']} ({item.get('drugbank_id')}) | "
                f"mechanism: {(item.get('mechanism') or 'n/a')[:600]}")
    if kind == "publication":
        return (f"[{item['id']}] PubTator literature | PMID {item['pmid']} ({item.get('year') or 'n.d.'}, "
                f"{item.get('journal') or 'n/a'}): {item.get('title')}")
    return f"[{item['id']}] {item.get('source')}: {item}"


def _format_lookup(lookup):
    return ", ".join(f"{gene} {value}" for gene, value in (lookup or {}).items()) or "any genotype"


def select_prompt_evidence(items, limit):
    """Keep a balanced subset so interactions don't crowd out variants and papers."""
    by_kind = {}
    for item in items:
        by_kind.setdefault(item["kind"], []).append(item)
    quotas = {"interaction": 0.35, "mechanism": 0.15, "guideline": 0.15, "variant_annotation": 0.15,
              "publication": 0.12, "drug_info": 0.08}
    chosen = []
    for kind, share in quotas.items():
        chosen += by_kind.get(kind, [])[: max(1, int(limit * share))]
    leftovers = [i for i in items if i not in chosen]
    chosen += leftovers[: max(0, limit - len(chosen))]
    return sorted(chosen, key=lambda i: int(i["id"][1:]))


def render_prompt(template, query, evidence, max_items):
    items = select_prompt_evidence((evidence or {}).get("items", []), max_items)
    context = {
        "entities": query.entities,
        "entity_type": query.entity_type,
        "relation_types": query.relation_types,
        "relation_labels": labels(query.relation_types) or ["all"],
        "followup_question": query.followup_question,
        "evidence_count": len(items),
        "evidence_block": "\n".join(format_evidence_item(i) for i in items) or "(no evidence found)",
        "source_status": (evidence or {}).get("source_status", {}),
    }
    system = _env.from_string(template.system_prompt).render(**context)
    if OUTPUT_LANGUAGE_INSTRUCTION not in system:
        system = f"{system.rstrip()}\n\n{OUTPUT_LANGUAGE_INSTRUCTION}"
    user = _env.from_string(template.user_template).render(**context)
    return system, user, [i["id"] for i in items]
