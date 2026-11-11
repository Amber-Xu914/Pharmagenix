"""Prompt templates: defaults, lookup of the active version, and rendering with evidence."""

from jinja2.sandbox import SandboxedEnvironment

from ..extensions import db
from ..models import PromptTemplate
from .relations import labels

DEFAULT_TEMPLATE_NAME = "dgi_report"
DEFAULT_TEMPLATE_VERSION = 4  # v4: a gene (gene → drugs) or a drug (drug → genes)
VCF_TEMPLATE_NAME = "vcf_report"  # Workflow 4 (patient VCF); its own active version, edited like the others
VCF_TEMPLATE_VERSION = 3  # v3: two tracks (established / exploratory) ranked separately; agents separate
SEEDED_NOTES = "Default template"

# Applied at render time as well as in the default template so reports remain
# in Australian English when an older database template is active.
OUTPUT_LANGUAGE_INSTRUCTION = (
    "Write the entire response in clear Australian English, even when the user's input is in another language."
)

DEFAULT_SYSTEM_PROMPT = """You are a pharmacogenomics research assistant writing for biomedical researchers and clinicians.
Rules:
- Write the entire response in clear Australian English, even when the user's input is in another language.
- Use ONLY the evidence items provided. Do not add facts from memory.
- Every factual sentence must cite one or more evidence IDs in square brackets, e.g. [E3] or [E3, E7].
- Never invent evidence IDs, PMIDs, drugs, genes or variants.
- The evidence grades were assigned by fixed rules; report them, don't re-grade. Point out conflicting evidence.
- If evidence is sparse, low-level or conflicting, say so explicitly.
- This is research support, not clinical advice."""

DEFAULT_USER_TEMPLATE = """{{ entity_label }}: {{ entities | join(", ") }}
Relationship types of interest: {{ relation_labels | join(", ") }}
{% if followup_question %}
The user's follow-up question: {{ followup_question }}
Answer it directly in the Overview section.
{% endif %}
Graded gene–drug pairs (rule-based; strongest first):
{{ pairs_block }}

Evidence ({{ evidence_count }} items; cite by ID):
{{ evidence_block }}

{{ analysis_context }}

Keep it under 500 words. Prefer higher evidence grades, higher PharmGKB levels (1A > 1B > 2A > 2B > 3 > 4) and
CPIC levels (A > B > C > D)."""

# Used by the default template through {{ analysis_context }}, and appended to custom templates that don't
# include it.
REPORT_INSTRUCTIONS = """Write a report in Markdown with these sections:
{% if entity_type == "drug" %}
## Overview
What the drug is (from its profile: type, approval, mechanism) and the headline: which genes it acts on (targets,
metabolising enzymes, transporters), and how strongly supported.
## Genes that interact with {{ entities | join(", ") }}
Group by relationship type (target inhibition, metabolism, induction, binding…), strongest grade first.
## Pharmacogenomic (SNP-specific) findings
Which gene variants change this drug's response, toxicity or dose (CPIC / PharmGKB).
{% else %}
## Overview
What the gene's product does (from its profile) and the headline: which drugs act on it, and how strongly supported.
## Drugs that interact with {{ entities | join(", ") }}
Group by relationship type (inhibition, induction, binding, metabolism…), strongest grade first.
## Pharmacogenomic (SNP-specific) findings
{% endif %}
## Clinical significance
If drug–drug interactions with the user's current medications or drug warnings are listed, report them here with
their severity and evidence IDs; never add interactions that are not listed.
## Evidence strength and limitations"""

WORKFLOW_LABEL = "Gene or drug → drug–gene interaction list"

# ---------- Workflow 4: patient VCF + cancer type -> candidate drugs ----------

VCF_SYSTEM_PROMPT = """You are the Analysis agent of a precision oncology research tool, writing for researchers and
students.
Rules:
- Write the entire response in clear Australian English.
- Use ONLY the validated evidence items provided. Do not add facts from memory.
- Every factual sentence must cite one or more evidence IDs in square brackets, e.g. [E3] or [E3, E7].
- Never invent evidence IDs, PMIDs, genes, variants or evidence levels.
- No new drug claims: only discuss drugs on the two ranked tracks.
- QC, variant tiers, Tier 3 scores, evidence tracks, drug scores and order were set by deterministic tools. Explain
  them; never re-rank, re-tier or re-score, and never move a drug between tracks.
- Keep the two tracks strictly separate: established clinical evidence (CIViC A/B, OncoKB 1–2 in this subtype) and
  exploratory predictions (lower levels, other cancer types, gene-level links, Tier 3 variants).
- Explain resistance and co-occurring variants: which of the patient's variants causes the resistance and which drugs
  it down-ranks.
- If the established track has no drug without resistance, say plainly that no approved targeted therapy was found.
- This is research and teaching support using synthetic test data, not clinical advice."""

VCF_USER_TEMPLATE = """Cancer type: {{ cancer_type }}
Sample: {{ sample }}
{% if followup_question %}
The user's follow-up question: {{ followup_question }}
Answer it directly in the Overview section.
{% endif %}
QC and tiering: {{ filtering }}

Tiered variants (all continue downstream):
{{ variants_block }}

A. Established track (rule-based scores; do not re-rank):
{{ established_block }}
{% if no_targeted_therapy %}
{{ no_targeted_therapy }}
{% endif %}

B. Exploratory track, top drugs (rule-based scores; predictions):
{{ exploratory_block }}

Co-mutations without a targeted drug (context): {{ comutations_block }}
Co-mutation patterns: {{ patterns_block }}

Evidence ({{ evidence_count }} items; cite by ID):
{{ evidence_block }}

Known limitations:
{{ limitations_block }}

{{ analysis_context }}

Keep it under 650 words."""

VCF_REPORT_INSTRUCTIONS = """Write a report in Markdown with these sections:
## Overview
The tiered variants and the headline: the top established drug(s) for this cancer type and any resistance, or "no
approved targeted therapy" when the established track has none without resistance.
## A. Established clinical evidence
In the given order: variant -> drug, evidence level, score, and why. Mention down-ranked drugs and their resistance.
## B. Exploratory predictions
The top exploratory drugs and the Tier 3 variants, each clearly labelled as a prediction (not evidence of response).
## Resistance and co-occurring variants
## Co-mutations without a targeted drug
## Safety: drug interactions and warnings
Only if any are listed with the drugs above: each drug–drug interaction (with a current medication or inside a
combination regimen) and each ChEMBL warning, with its severity and evidence ID. Do not add interactions that are
not listed; if none are listed, write one sentence saying none were found by the checks.
## Limitations
End with: "Research and teaching use only; not clinical advice." """

_env = SandboxedEnvironment(autoescape=False, trim_blocks=True, lstrip_blocks=True)


def template_family(name):
    """Templates named vcf_report serve the VCF workflow; every other name serves the gene workflow."""
    return "vcf" if name == VCF_TEMPLATE_NAME else "gene"


def query_family(query):
    return "vcf" if query.entity_type == "vcf" else "gene"


def get_active_template(family="gene"):
    active = PromptTemplate.query.filter_by(is_active=True).order_by(PromptTemplate.id.desc()).all()
    template = next((t for t in active if template_family(t.name) == family), None)
    return template or seed_default_template(family)


def activate_template(template):
    """Activate a template; only the other templates of the same workflow are deactivated."""
    for other in PromptTemplate.query.filter_by(is_active=True).all():
        if template_family(other.name) == template_family(template.name):
            other.is_active = False
    template.is_active = True
    db.session.commit()


def seed_default_template(family=None):
    """Add each workflow's current default template, and activate it unless an admin's own template is active
    for that workflow. Safe to run repeatedly (flask init-db does). Returns the active template of `family`
    (the gene workflow's when None). Older seeded defaults stay in the table for their logs."""
    defaults = {"gene": (DEFAULT_TEMPLATE_NAME, DEFAULT_TEMPLATE_VERSION, DEFAULT_SYSTEM_PROMPT, DEFAULT_USER_TEMPLATE),
                "vcf": (VCF_TEMPLATE_NAME, VCF_TEMPLATE_VERSION, VCF_SYSTEM_PROMPT, VCF_USER_TEMPLATE)}
    for fam, (name, version, system_prompt, user_template) in defaults.items():
        current = PromptTemplate.query.filter_by(name=name, version=version).first()
        if current is None:
            latest = db.session.scalar(db.select(db.func.max(PromptTemplate.version))
                                       .where(PromptTemplate.name == name)) or 0
            if latest >= version:  # the version number is taken by an admin edit: keep theirs
                continue
            current = PromptTemplate(name=name, version=version, system_prompt=system_prompt,
                                     user_template=user_template, notes=SEEDED_NOTES, is_active=False)
            db.session.add(current)
            db.session.flush()
        active = [t for t in PromptTemplate.query.filter_by(is_active=True).all() if template_family(t.name) == fam]
        if all(t.notes == SEEDED_NOTES and t.name == name for t in active):
            for t in active:
                t.is_active = False
            current.is_active = True
    db.session.commit()
    active = PromptTemplate.query.filter_by(is_active=True).order_by(PromptTemplate.id.desc()).all()
    return next((t for t in active if template_family(t.name) == (family or "gene")), None)


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
        line = (f"[{item['id']}] PubTator literature | PMID {item['pmid']} ({item.get('year') or 'n.d.'}, "
                f"{item.get('journal') or 'n/a'}): {item.get('title')}")
        if item.get("ranker"):  # Workflow 4: retrieved and re-ranked, with the sentences that answer the query
            sentences = " ".join(f"(S{p['n']}) {p['text']}" for p in item.get("passages") or [])
            line += (f" | relevance {item['score']} ({item['ranker']}) | abstract sentences: "
                     f"{sentences or 'no abstract available'}")
        return line
    if kind == "profile":
        return format_profile(item)
    if kind == "variant":
        return format_variant(item)
    if kind == "civic":
        return (f"[{item['id']}] CIViC EID{item['evidence_id']} | {item['molecular_profile']} | "
                f"{' + '.join(item['therapies']) if item.get('therapy_interaction') == 'Combination' else ', '.join(item['therapies'])} | "
                f"{item['significance']} ({item['direction']}) | level {item['level']} | disease: {item['disease']} "
                f"({item['disease_match_label']}) | match: {item['specificity']} | "
                f"statement: {(item.get('statement') or 'n/a')[:500]} | PMID: {item.get('pmid') or 'n/a'}")
    if kind == "fda_cyp":
        notes = f" | FDA note: {item['notes'][:300]}" if item.get("notes") else ""
        return (f"[{item['id']}] FDA drug-interaction table | {item['drug']} – {item['gene']} | {item['label']} "
                f"(clinical classification by effect on exposure){notes}")
    if kind == "ddi":
        label = f" | label ({item.get('label_title')}): \"{item['label_text']}\"" if item.get("label_text") else ""
        return (f"[{item['id']}] Drug–drug interaction check ({item['source']}) | {item['drug']} with {item['other']} "
                f"({item['context']}) | severity: {item['severity']} | {item['summary']}{label}")
    if kind == "oncokb":
        return (f"[{item['id']}] OncoKB | {item['gene']} {item['protein_change']} | {item['drug']} | "
                f"{item['significance']} | {item['level']} | cancer type: {item.get('cancer_type') or 'n/a'}")
    return f"[{item['id']}] {item.get('source')}: {item}"


def format_profile(item):
    if item.get("entity_type") == "drug":
        phase = f"{item['max_phase']:g}" if item.get("max_phase") is not None else "n/a"
        return (f"[{item['id']}] Drug profile ({item['source']}) | {item['entity']}: {item.get('full_name') or 'n/a'} | "
                f"type: {item.get('molecule_type') or 'n/a'} | max clinical phase: {phase} (4 = approved) | "
                f"first approval: {item.get('first_approval') or 'n/a'} | trade names: "
                f"{', '.join(item.get('trade_names') or []) or 'n/a'} | mechanisms of action: "
                f"{'; '.join(item.get('mechanisms') or []) or 'n/a'}")
    protein = item.get("protein") or {}
    domains = ", ".join(d["name"] for d in protein.get("domains") or [] if d.get("name")) or "n/a"
    return (f"[{item['id']}] Gene profile ({item['source']}) | {item['entity']}: {item.get('full_name') or 'n/a'} | "
            f"protein: {protein.get('protein_name') or 'n/a'} | function: {(protein.get('function') or 'n/a')[:700]} | "
            f"domains: {domains} | location: {', '.join(protein.get('locations') or []) or 'n/a'}")


def format_variant(item):
    exon = f" (exon {item['exon']})" if item.get("exon") else ""
    vaf = item["vaf"] if item.get("vaf") is not None else "n/a"
    t3 = item.get("tier3")
    score = f" | Tier 3 S {t3['S']} (F {t3['F']}, R {t3['R']}, B {t3['B']})" if t3 else ""
    freq = item.get("subtype_frequency")
    return (f"[{item['id']}] Patient variant ({item['source']}) | {item['notation']} | Tier {item.get('tier')}: "
            f"{item.get('tier_reason')}{score} | consequence: {item.get('consequence') or 'n/a'}{exon} | "
            f"VAF: {vaf} | ClinVar: {', '.join(item.get('clinvar') or []) or 'n/a'} | "
            f"mutated in subtype cohort: {f'{freq:.1%}' if freq is not None else 'n/a'} | "
            f"pathways: {', '.join(item.get('pathways') or []) or 'none'}")


def format_pair(row, shown):
    """shown: the evidence IDs in the prompt. Only those are listed, so the LLM never cites an item it can't see."""
    conflicts = f" | CONFLICT: {'; '.join(row['conflicts'])}" if row.get("conflicts") else ""
    if row.get("warnings"):
        conflicts += f" | DRUG WARNING (ChEMBL): {'; '.join(row['warnings'])}"
    if row.get("interactions"):
        conflicts += " | INTERACTION WITH CURRENT MEDICATION: " + "; ".join(
            f"{a['other']} ({a['severity']}) [{a['evidence_id']}]" for a in row["interactions"] if a["evidence_id"] in shown)
    evidence_ids = [eid for eid in row["evidence_ids"] if eid in shown]
    return (f"- {row['gene']} – {row['drug']} | "
            f"grade: {row['grade_label']} ({row['grade_reason']}) | relationship: {', '.join(row['categories'])} | "
            f"evidence: {', '.join(evidence_ids[:8])}{conflicts}")


def _format_lookup(lookup):
    return ", ".join(f"{gene} {value}" for gene, value in (lookup or {}).items()) or "any genotype"


def select_prompt_evidence(items, limit):
    """Keep a balanced subset so interactions don't crowd out variants and papers."""
    by_kind = {}
    for item in items:
        by_kind.setdefault(item["kind"], []).append(item)
    quotas = {"profile": 0.03, "ddi": 0.1, "interaction": 0.3, "mechanism": 0.12, "guideline": 0.12,
              "fda_cyp": 0.08, "variant_annotation": 0.12, "publication": 0.08, "drug_info": 0.05}
    chosen = []
    for kind, share in quotas.items():
        chosen += by_kind.get(kind, [])[: max(1, int(limit * share))]
    leftovers = [i for i in items if i not in chosen]
    chosen += leftovers[: max(0, limit - len(chosen))]
    return sorted(chosen, key=lambda i: int(i["id"][1:]))


def select_vcf_evidence(evidence, limit):
    """The tiered variants, then the evidence behind the established track, then the top exploratory drugs."""
    items = {i["id"]: i for i in evidence.get("items", [])}
    chosen = [i["id"] for i in items.values() if i["kind"] == "variant"]
    chosen += [i["id"] for i in items.values() if i["kind"] == "ddi"][:15]
    tracks = evidence.get("tracks") or {}
    for c in tracks.get("established", []):
        chosen += c["evidence_ids"][:6]
    for c in tracks.get("exploratory", []):
        if c.get("top"):
            chosen += c["evidence_ids"][:3]
    chosen = [c for c in dict.fromkeys(chosen) if c in items][:limit]
    return sorted((items[c] for c in chosen), key=lambda i: int(i["id"][1:]))


def format_candidate(c, shown):
    ids = [eid for eid in c["evidence_ids"] if eid in shown]
    extras = []
    if c.get("co_occurrence"):
        extras.append(f"co-occurring variants: {'; '.join(c['co_occurrence'])}")
    if c.get("resistance_notes"):
        extras.append(f"resistance: {'; '.join(c['resistance_notes'][:3])}")
    if c.get("conflicts"):
        extras.append(f"conflicts: {'; '.join(c['conflicts'])}")
    if c.get("mechanism"):
        extras.append(f"mechanism (ChEMBL): {c['mechanism']}")
    safety = c.get("safety") or {}
    if safety.get("warnings"):
        extras.append(f"DRUG WARNING (ChEMBL): {'; '.join(safety['warnings'])}")
    for a in safety.get("interactions") or []:
        if a["evidence_id"] in shown:
            extras.append(f"INTERACTION ({a['context']}, {a['severity']}): {a['drug']} with {a['other']}, "
                          f"{a['mechanism']} [{a['evidence_id']}]")
    return (f"#{c['rank']} {c['drug']} | score {c['score']} | {c['track_reason']} | variants: "
            f"{', '.join(c['variants']) or 'n/a'} | approved drug: {'yes' if c.get('approved') else 'no / unknown'}"
            + "".join(f" | {e}" for e in extras) + f" | evidence: {', '.join(ids[:8]) or 'n/a'}")


def render_vcf_prompt(template, query, evidence, max_items):
    items = select_vcf_evidence(evidence, max_items)
    shown = {i["id"] for i in items}
    tracks = evidence.get("tracks") or {}
    inp = evidence.get("input") or {}
    cancer = inp.get("cancer_type") or {}
    variants = evidence.get("variants") or {}
    counts = variants.get("counts") or {}
    context = {
        "entities": query.entities,
        "followup_question": query.followup_question,
        "cancer_type": f"{cancer.get('name')} (OncoTree {cancer.get('code')})",
        "sample": inp.get("example") or inp.get("filename") or query.entities[0],
        "filtering": (f"{counts.get('total', 0)} variants, {counts.get('qc_passed', 0)} passed QC; Tier 1: "
                      f"{counts.get('tier1', 0)}, Tier 2: {counts.get('tier2', 0)}, Tier 3: {counts.get('tier3', 0)}, "
                      f"low priority: {counts.get('low_priority', 0)}"),
        "variants_block": "\n".join(f"- Tier {v['tier']} {v['notation']} [{v['evidence_id']}]: {v['tier_reason']}"
                                     for v in variants.get("tiered") or []) or "(none)",
        "established_block": "\n".join(format_candidate(c, shown) for c in tracks.get("established", [])) or "(none)",
        "exploratory_block": "\n".join(format_candidate(c, shown) for c in tracks.get("exploratory", [])
                                        if c.get("top")) or "(none)",
        "comutations_block": ", ".join(v["notation"] for v in evidence.get("co_mutations") or []) or "none",
        "patterns_block": "; ".join(evidence.get("co_mutation_patterns") or []) or "none",
        "no_targeted_therapy": (evidence.get("outcome") or {}).get("no_targeted_therapy"),
        "evidence_count": len(items),
        "evidence_block": "\n".join(format_evidence_item(i) for i in items) or "(no evidence found)",
        "limitations_block": "\n".join(f"- {n}" for n in evidence.get("limitations") or []) or "(none)",
        "source_status": evidence.get("source_status", {}),
        "outcome": evidence.get("outcome") or {},
    }
    context["analysis_context"] = VCF_REPORT_INSTRUCTIONS
    system = _env.from_string(template.system_prompt).render(**context)
    user = _env.from_string(template.user_template).render(**context)
    if "analysis_context" not in template.user_template:
        user = (f"{user.rstrip()}\n\nThese instructions take precedence over any section list above.\n"
                f"{VCF_REPORT_INSTRUCTIONS}\n\nA. Established track:\n{context['established_block']}\n\n"
                f"B. Exploratory track:\n{context['exploratory_block']}")
    return system, user, [i["id"] for i in items]


def render_prompt(template, query, evidence, max_items):
    evidence = evidence or {}
    if (evidence.get("workflow") or {}).get("key") == "vcf":
        return render_vcf_prompt(template, query, evidence, max_items)
    items = select_prompt_evidence(evidence.get("items", []), max_items)
    shown = {i["id"] for i in items}
    pairs = [r for r in evidence.get("table") or [] if set(r["evidence_ids"]) & shown]
    context = {
        "entities": query.entities,
        "entity_type": query.entity_type,
        "entity_label": "Drug" if query.entity_type == "drug" else "Gene",
        "relation_types": query.relation_types,
        "relation_labels": labels(query.relation_types) or ["all"],
        "followup_question": query.followup_question,
        "evidence_count": len(items),
        "evidence_block": "\n".join(format_evidence_item(i) for i in items) or "(no evidence found)",
        "source_status": evidence.get("source_status", {}),
        "outcome": evidence.get("outcome") or {},
        "checked_sources": (evidence.get("outcome") or {}).get("checked_sources", []),
        "pairs_block": "\n".join(format_pair(r, shown) for r in pairs[:40]) or "(none)",
        # Still provided for templates written when there were several workflows.
        "workflow_number": 1,
        "workflow_label": WORKFLOW_LABEL,
    }
    context["analysis_context"] = _env.from_string(REPORT_INSTRUCTIONS).render(**context)

    system = _env.from_string(template.system_prompt).render(**context)
    if OUTPUT_LANGUAGE_INSTRUCTION not in system:
        system = f"{system.rstrip()}\n\n{OUTPUT_LANGUAGE_INSTRUCTION}"
    user = _env.from_string(template.user_template).render(**context)
    if "analysis_context" not in template.user_template and evidence.get("outcome"):
        # A template without the section list, used on workflow evidence: add the sections and the graded pairs.
        user = (f"{user.rstrip()}\n\nThese instructions take precedence over any section list above.\n"
                f"{context['analysis_context']}\n\nGraded gene–drug pairs (rule-based):\n{context['pairs_block']}")
    return system, user, [i["id"] for i in items]
