"""Workflow 4, stage 6: exploration agents (LLM) that work on the exploratory track only.

  Literature agent              what the retrieved papers (literature.py: PubTator, gated and re-ranked, with the
                                abstract sentences that answer the query) say about Tier 3 variants and C–E pairs
  Biological interpretation     pathway and co-mutation context (e.g. STK11 + KRAS, TP53 + RB1)
  Hypothesis agent              testable variant–drug hypotheses for follow-up

Their outputs feed only the exploratory section (B) of the report. They see the tiers, scores and evidence levels
as fixed facts and cannot change them. Every statement must cite an evidence ID or be labelled "Hypothesis"; the
check below flags reports that break this.
"""

import re

from flask import current_app

from ..prompts import format_evidence_item

AGENT_ROLES = ("literature", "biology", "hypothesis")
AGENT_LABELS = {"literature": "Literature agent", "biology": "Biological interpretation agent",
                "hypothesis": "Hypothesis agent"}

RULES = """Rules:
- Write in clear Australian English.
- You work on the EXPLORATORY track only. The QC results, variant tiers, Tier 3 scores, evidence levels, drug scores
  and the established track are fixed by deterministic tools: treat them as facts and never change, re-rank or
  re-tier anything.
- Use only the evidence items provided. Every statement must cite one or more evidence IDs, e.g. [E3], or start with
  "Hypothesis:" when it goes beyond the evidence. Facts taken from the lists above (tiers, scores, pathways, drugs)
  cite the evidence ID shown next to them, e.g. a variant's [E2]. Cite only IDs from the evidence list.
- Only mention drugs that are on the exploratory track. Never recommend a treatment; this is research and teaching
  support with synthetic test data, not clinical advice.
- Be brief: at most 250 words, as Markdown bullet points under one or two short headings."""

TASKS = {
    "literature": """You are the Literature agent. For each Tier 3 variant and each level C–E variant–drug pair below, say
what the retrieved papers report and how strongly they support it (supports / only mentions it / not relevant).
Each paper comes with the abstract sentences that best match the question, labelled (S1), (S2)...: base what you
say on those sentences, not on the title, and cite the paper's ID with the sentence, e.g. [E12] (S3). If the
sentences do not address the variant or drug, say the paper only mentions it. Say plainly when no paper was found.""",
    "biology": """You are the Biological interpretation agent. Explain the pathway and co-mutation context of the tiered
variants: which oncogenic pathways they hit, and what the co-occurring alterations (e.g. STK11 + KRAS, TP53 + RB1)
may mean for biology and for the exploratory drugs. Use the pathway and co-mutation facts given.""",
    "hypothesis": """You are the Hypothesis agent. Propose up to four testable variant–drug hypotheses for follow-up,
built from the exploratory track (Tier 3 variants, level C–E pairs, gene-level links). Start each with "Hypothesis:",
cite the evidence it rests on, and name one experiment that could test it (e.g. a cell line or organoid study).""",
}


def _tier3_line(v):
    t = v["tier3"]
    return (f"- {v['notation']} [{v['evidence_id']}]: S {t['S']} = {t['weights']['F']}·F {t['F']} ({t['F_source']}) + "
            f"{t['weights']['R']}·R {t['R']} ({t['R_frequency']:.1%} in {t['R_source']}) + "
            f"{t['weights']['B']}·B {t['B']} (role {t['role'] or 'none'}, pathways {', '.join(t['pathways']) or 'none'})")


def _context(evidence):
    variants = evidence["variants"]["tiered"]
    exploratory = [c for c in evidence["tracks"]["exploratory"] if c.get("top")]
    tier3 = [v for v in variants if v["tier"] == 3]
    lines = [
        f"Cancer type: {evidence['input']['cancer_type']['name']} (OncoTree {evidence['input']['cancer_type']['code']})",
        "",
        "Tiered variants (fixed):",
        *[f"- Tier {v['tier']} {v['notation']} [{v['evidence_id']}]: {v['tier_reason']}; pathways: "
          f"{', '.join(v.get('pathways') or []) or 'none'}" for v in variants],
        "",
        "Tier 3 scores (fixed):", *([_tier3_line(v) for v in tier3] or ["(none)"]),
        "",
        "Exploratory track, top drugs (fixed order and scores):",
        *([f"- #{c['rank']} {c['drug']} | score {c['score']} | {c['track_reason']} | variants: "
           f"{', '.join(c['variants']) or 'n/a'} | evidence: {', '.join(c['evidence_ids'][:6])}"
           + (f" | literature: {', '.join(p['item_id'] for p in c.get('literature') or [])}" if c.get("literature") else "")
           for c in exploratory] or ["(none)"]),
        "",
        "Co-mutation patterns present: " + ("; ".join(evidence.get("co_mutation_patterns") or []) or "none"),
    ]
    return "\n".join(lines), exploratory


def _evidence(evidence, exploratory, role, limit):
    """The items an agent may cite: tiered variants, then what the exploratory drugs rest on, then papers. Every
    agent gets the same set, so any ID shown in its context is also citable."""
    items = {i["id"]: i for i in evidence["items"]}
    chosen = [i["id"] for i in evidence["items"] if i["kind"] == "variant"]
    for c in exploratory:
        chosen += c["evidence_ids"][:6]
    chosen += [i["id"] for i in evidence["items"] if i["kind"] == "publication"]
    chosen = [c for c in dict.fromkeys(chosen) if c in items][:limit]
    return [items[c] for c in sorted(chosen, key=lambda c: int(c[1:]))]


CITE_RE = re.compile(r"\[\s*E\d+")
# Saying that nothing was found (or listed), or that this is not a treatment recommendation, is not a claim that
# needs a citation.
ABSENCE_RE = re.compile(r"\bno\b[^.]{0,40}?\b(papers?|literature|records?|evidence|variants?|pairs?|hits?)\b"
                        r"|\bnot found\b|\bnone (was|were) found\b"
                        r"|\bnot (a )?(treatment|clinical) (recommendations?|advice)\b", re.IGNORECASE)


def statement_check(text):
    """Every statement (a bullet, or a paragraph line) must cite an evidence ID, be labelled as a hypothesis (which
    then covers the whole bullet, e.g. the experiment that would test it), or report that nothing was found."""
    bad = 0
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-*•0123456789. ").strip()
        if not line or line.startswith("#") or len(line) < 25:
            continue
        if not (CITE_RE.search(line) or "hypothes" in line.lower() or ABSENCE_RE.search(line)):
            bad += 1
    return [f"{bad} statement(s) neither cite evidence nor are labelled as a hypothesis"] if bad else []


def build_prompts(query, roles=AGENT_ROLES):
    cfg = current_app.config
    evidence = query.evidence
    context, exploratory = _context(evidence)
    prompts = []
    for role in roles:
        items = _evidence(evidence, exploratory, role, cfg["MAX_PROMPT_EVIDENCE"])
        user = (f"{context}\n\nEvidence ({len(items)} items; cite by ID):\n"
                + "\n".join(format_evidence_item(i) for i in items)
                + (f"\n\nThe user's follow-up question: {query.followup_question}" if query.followup_question else ""))
        prompts.append({"provider": cfg["PRIMARY_PROVIDER"], "role": role, "system": f"{TASKS[role]}\n\n{RULES}",
                        "user": user, "prompt_ids": [i["id"] for i in items], "check": statement_check})
    return prompts


def should_run(evidence):
    tracks = (evidence or {}).get("tracks") or {}
    return bool(current_app.config["VCF_AGENTS"] and (tracks.get("exploratory")
                or any(v["tier"] == 3 for v in (evidence.get("variants") or {}).get("tiered", []))))


