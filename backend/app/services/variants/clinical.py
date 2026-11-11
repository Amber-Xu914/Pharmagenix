"""Workflow 4: the patient's clinical context (optional form fields), and the Clinical summary agent.

The clinical context (stage, treatment status, age, sex, smoking, tumour purity, ECOG, brain metastases, notes) is
stored with the query (evidence["input"]["clinical"]). It never changes QC, tiers, scores or ranks: the agents read it,
and the report shows it.

The Clinical summary agent (LLM) turns the fixed results into what a treating physician reads first: the page-1 brief
results, first-line regimen options and a monitoring plan. Regimens, named trials and monitoring come from general
clinical knowledge rather than the retrieved evidence, so every such statement must end with [GK]; everything taken
from the evidence cites its evidence ID. A drug the pipeline down-ranked for resistance may never be recommended.
clinical_check() enforces this; a report that breaks it is stored as "flagged".
"""

import re

from flask import current_app

from ..prompts import format_evidence_item

FIELDS = {  # key: (label, allowed values or None for free text / numbers)
    "stage": ("Stage", ("I", "II", "III", "IV")),
    "treatment": ("Treatment status", None),
    "age": ("Age", None),
    "sex": ("Sex", ("female", "male", "other")),
    "smoking": ("Smoking", ("never", "former", "current")),
    "purity": ("Tumour purity", None),
    "ecog": ("ECOG", ("0", "1", "2", "3", "4")),
    "brain_mets": ("Brain metastases", ("yes", "no", "unknown")),
    "notes": ("Other context", None),
}
# Shown as "Not provided" when missing (the template's list), with the form field behind each
NOT_PROVIDED = [("ecog", "ECOG"), ("brain_mets", "brain imaging"), ("notes", "comorbidities")]

HEADINGS = ["Recommended first-line options", "Not recommended", "Confirm before treating",
            "Co-mutations and exploratory", "At progression", "Treatment options", "Monitoring and next steps"]
RECOMMENDING = ("Preferred", "Preferred alternative", "Alternative")


def parse_clinical(raw):
    """A dict (JSON body) or a form (fields "clinical_<key>") -> the clean clinical context; unknown or invalid
    values are dropped."""
    raw = raw or {}
    get = (lambda k: raw.get(f"clinical_{k}") or raw.get(k)) if hasattr(raw, "get") else (lambda k: None)
    out = {}
    for key, (_, allowed) in FIELDS.items():
        value = get(key)
        if value is None or str(value).strip() == "":
            continue
        value = re.sub(r"\s+", " ", str(value)).strip()
        if allowed:
            match = next((a for a in allowed if a.lower() == value.lower()), None)
            if match:
                out[key] = match
        elif key == "age":
            if value.isdigit() and 0 < int(value) <= 120:
                out[key] = int(value)
        elif key == "purity":
            number = value.rstrip("%").strip()
            if re.fullmatch(r"\d{1,3}(\.\d+)?", number) and 0 < float(number) <= 100:
                out[key] = round(float(number))
        else:
            out[key] = value[:300 if key == "notes" else 120]
    return out


def summary(clinical):
    """'Stage IV, treatment-naive; 58 y female, never-smoker; tumour purity ~75%; ECOG 1' (or '')."""
    c = clinical or {}
    first = ", ".join(filter(None, [c.get("stage") and f"Stage {c['stage']}", c.get("treatment")]))
    person = " ".join(filter(None, [c.get("age") and f"{c['age']} y", c.get("sex")]))
    smoking = {"never": "never-smoker", "former": "former smoker", "current": "current smoker"}.get(c.get("smoking"))
    second = ", ".join(filter(None, [person, smoking]))
    rest = [c.get("purity") and f"tumour purity ~{c['purity']}%", c.get("ecog") and f"ECOG {c['ecog']}",
            c.get("brain_mets") and f"brain metastases: {c['brain_mets']}", c.get("notes")]
    return "; ".join(filter(None, [first, second, *rest]))


def not_provided(clinical, medications=()):
    c = clinical or {}
    out = [label for key, label in NOT_PROVIDED if not c.get(key)]
    if not medications:
        out.append("co-medications")
    return out


# ---------- the Clinical summary agent ----------

SYSTEM = """You are the Clinical summary agent of a precision oncology research tool. You turn the fixed pipeline
results into the sections a treating physician reads first. The case is synthetic test data; this is research and
teaching support, not a treatment recommendation.

Rules:
- Write in clear Australian English.
- QC results, tiers, tracks, scores, ranks and the "not recommended" flags are fixed by deterministic tools. Never
  change them. A drug marked DOWN-RANKED or RESISTANCE ONLY below must never appear as a recommended option, and
  neither may a regimen that contains one (e.g. "Erlotinib + Ramucirumab" when erlotinib is down-ranked).
- Follow the established track's order of preference. A drug–drug interaction with a current medication does not
  move a drug down: keep the drug and say how to manage the interaction (for example, change the co-medication).
- Every fact taken from the results or evidence cites its evidence ID, e.g. [E3].
- You may add standard clinical knowledge that the evidence does not contain: guideline regimens, named trials,
  monitoring, confirmatory testing, what to test at progression. End every such sentence, bullet or table row with
  [GK]. Do not invent trial results, numbers, doses beyond the labelled standard dose, or PMIDs.
- Use the clinical context given. If missing information would change the choice (performance status, brain
  metastases, comorbidities), say so instead of assuming it.
- Write options for the treating physician to weigh; never state that a treatment must be given."""

TASK = """Write Markdown with exactly these second-level headings, in this order, and nothing before the first:

## Recommended first-line options
One or two sentences: the preferred option(s) and alternatives, with the score and evidence of the top drug.
## Not recommended
The down-ranked and resistance-only drugs and why (one or two sentences). Add drug classes that guidelines advise
against for this profile, marked [GK].
## Confirm before treating
Findings to confirm with an orthogonal test before they drive the choice (e.g. a low-VAF resistance variant, an
unusual co-mutation), and resistance variants that were not detected.
## Co-mutations and exploratory
One or two sentences on the co-mutations and the top exploratory (trial-only) hypotheses.
## At progression
One sentence: what to re-test at progression.
## Treatment options
A Markdown table with the columns | Regimen | Key evidence | Fit for this patient | Main cautions | Recommendation |,
one row per regimen including the ones not recommended. Recommendation is one of: Preferred, Preferred alternative,
Alternative, Not recommended, Not first line, Not indicated (trial only).
## Monitoring and next steps
Four bullets starting with **Before starting:**, **On treatment:**, **At progression:** and **Clinical trials:**.

At most 450 words outside the table."""

CITE = re.compile(r"\[\s*(E\d+|GK)")
NEGATION = re.compile(r"\bnot\b|avoid|rather than|instead of|resistan|down-?rank|ineffective|contraindicat", re.I)


def _drug_line(c, flag=""):
    safety = c.get("safety") or {}
    extra = []
    if c.get("resistance_notes"):
        extra.append("resistance: " + "; ".join(c["resistance_notes"][:3]))
    if safety.get("warnings"):
        extra.append("warning: " + "; ".join(safety["warnings"]))
    for a in safety.get("interactions") or []:
        extra.append(f"interaction ({a['context']}, {a['severity']}) with {a['other']} [{a['evidence_id']}]")
    return (f"- #{c['rank']} {c['drug']}{flag} | score {c['score']} | {c['track_reason']} | variants: "
            f"{', '.join(c.get('variants') or []) or 'n/a'} | evidence: {', '.join(c['evidence_ids'][:6])}"
            + "".join(f" | {e}" for e in extra))


def down_ranked(evidence):
    tracks = evidence.get("tracks") or {}
    names = [c["drug"] for rows in tracks.values() for c in rows if c.get("strong_resistance")]
    return names + [c["drug"] for c in evidence.get("resistance_only") or []]


def context(evidence):
    inp = evidence.get("input") or {}
    cancer = inp.get("cancer_type") or {}
    variants = evidence.get("variants") or {}
    tracks = evidence.get("tracks") or {}
    status = evidence.get("source_status") or {}
    safety = evidence.get("safety") or {}
    meds = [m["name"] for m in safety.get("medications") or []]
    disabled = [k for k, s in status.items() if s.get("state") == "disabled"]
    lines = [
        f"Cancer type: {cancer.get('name')} (OncoTree {cancer.get('code')}; lineage "
        f"{' > '.join(cancer.get('lineage') or [])})",
        f"Clinical context: {summary(inp.get('clinical')) or 'none given'}",
        f"Not provided: {', '.join(not_provided(inp.get('clinical'), meds)) or 'nothing'}",
        f"Current medications: {', '.join(meds) or 'none given'}",
        "",
        "Tiered variants (fixed):",
        *[f"- Tier {v['tier']} {v['notation']} [{v['evidence_id']}]: {v['tier_reason']}; VAF "
          f"{round((v.get('vaf') or 0) * 100)}%" for v in variants.get("tiered") or []],
        "Low-priority variants (kept, not actionable): "
        + (", ".join(f"{v.get('notation') or v.get('gene')} ({v.get('reason')})" for v in variants.get("low_priority") or [])
           or "none"),
        "",
        "Established track (fixed order; DOWN-RANKED = not recommended):",
        *([_drug_line(c, " DOWN-RANKED" if c.get("strong_resistance") else "")
           for c in tracks.get("established") or [] if c.get("top")] or ["(none)"]),
        "Resistance evidence only (not recommended): "
        + ("; ".join(f"{c['drug']} RESISTANCE ONLY ({'; '.join(c.get('resistance_notes') or [])})"
                     for c in evidence.get("resistance_only") or []) or "none"),
        "",
        "Exploratory track, top 5 (hypotheses only):",
        *([_drug_line(c) for c in (tracks.get("exploratory") or [])[:5]] or ["(none)"]),
        "",
        "Co-mutations without a targeted drug: "
        + (", ".join(v["notation"] for v in evidence.get("co_mutations") or []) or "none"),
        "Co-mutation patterns: " + ("; ".join(evidence.get("co_mutation_patterns") or []) or "none"),
        f"Sources not enabled: {', '.join(disabled) or 'none'}",
        "Not covered by this test: fusions (ALK, ROS1, RET, NTRK, NRG1), copy-number changes (MET, ERBB2 "
        "amplification), PD-L1 and TMB.",
    ]
    return "\n".join(lines)


def _evidence(evidence, limit):
    items = {i["id"]: i for i in evidence.get("items") or []}
    tracks = evidence.get("tracks") or {}
    chosen = [i["id"] for i in items.values() if i["kind"] in ("variant", "ddi")]
    for c in tracks.get("established") or []:
        if c.get("top"):
            chosen += c["evidence_ids"][:6]
    for c in evidence.get("resistance_only") or []:
        chosen += c["evidence_ids"][:3]
    for c in (tracks.get("exploratory") or [])[:5]:
        chosen += c["evidence_ids"][:3]
    chosen = [c for c in dict.fromkeys(chosen) if c in items][:limit]
    return [items[c] for c in sorted(chosen, key=lambda c: int(c[1:]))]


def sections(text):
    """{heading: body} of the agent's Markdown (second-level headings)."""
    out, current = {}, None
    for line in (text or "").splitlines():
        m = re.match(r"^\s*##\s+(.+?)\s*$", line)
        if m:
            current = next((h for h in HEADINGS if h.lower() == m.group(1).strip().lower()), m.group(1).strip())
            out[current] = []
        elif current:
            out[current].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def table_rows(markdown):
    """Rows of the first Markdown table in a section, as lists of cell texts (header and separator left out)."""
    rows = [[c.strip() for c in line.strip().strip("|").split("|")]
            for line in (markdown or "").splitlines() if line.strip().startswith("|")]
    return [r for r in rows[1:] if not all(re.fullmatch(r":?-{2,}:?", c) for c in r if c)]


def check(down):
    """The check run on the agent's answer: every heading present, every statement cited ([E…] or [GK]), and no
    down-ranked drug among the recommended options."""
    down_lower = [d.lower() for d in down]

    def run(text):
        problems = []
        found = sections(text)
        missing = [h for h in HEADINGS if h not in found]
        if missing:
            problems.append(f"missing section(s): {', '.join(missing)}")
        bad = 0
        for line in (text or "").splitlines():
            plain = line.strip()
            if not plain or plain.startswith("#") or re.fullmatch(r"\|?[\s:|-]+\|?", plain) or len(plain) < 25:
                continue
            if plain.startswith("|") and "Regimen" in plain and "Recommendation" in plain:
                continue  # the table header
            if not CITE.search(plain):
                bad += 1
        if bad:
            problems.append(f"{bad} statement(s) cite neither evidence nor [GK]")
        # Recommending text: the sentences of the first section that don't speak against a drug, and the regimens of
        # table rows marked Preferred / Alternative
        recommending = [s.lower() for s in re.split(r"(?<=[.;])\s+", found.get("Recommended first-line options", ""))
                        if not NEGATION.search(s)]
        for row in table_rows(found.get("Treatment options")):
            if row and row[-1].strip("* ").startswith(RECOMMENDING):
                recommending.append(row[0].lower())
        named = [d for d in down_lower if any(re.search(rf"\b{re.escape(d)}\b", s) for s in recommending)]
        if named:
            problems.append(f"down-ranked drug(s) among the recommended options: {', '.join(named)}")
        return problems

    return run


def build_prompt(query):
    cfg = current_app.config
    evidence = query.evidence
    items = _evidence(evidence, cfg["MAX_PROMPT_EVIDENCE"])
    user = (f"{context(evidence)}\n\nEvidence ({len(items)} items; cite by ID):\n"
            + "\n".join(format_evidence_item(i) for i in items)
            + (f"\n\nThe user's follow-up question: {query.followup_question}" if query.followup_question else "")
            + f"\n\n{TASK}")
    return {"provider": cfg["PRIMARY_PROVIDER"], "role": "clinical", "system": SYSTEM, "user": user,
            "prompt_ids": [i["id"] for i in items], "check": check(down_ranked(evidence))}


def should_run(evidence):
    return bool(current_app.config["VCF_CLINICAL_AGENT"]
                and ((evidence or {}).get("outcome") or {}).get("status") == "found")
