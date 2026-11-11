"""Benchmark: does grounding the LLM in retrieved, graded evidence make its reports more accurate and verifiable?

    flask --app run benchmark --dry-run     build the evidence for every case and print the plan (no LLM calls)
    flask --app run benchmark               run every arm on every case and write docs/evaluation/

Cases: the 8 mock VCFs (Workflow 4) and the 10 demo searches (5 genes, 5 drugs). The evidence for each case is built
once and shared by the arms, so the arms differ only in the model and in whether it sees the evidence:

  grounded-primary     the system as deployed: the primary model with the active prompt template and the evidence
  grounded-comparison  the same prompt and evidence, the comparison model
  ungrounded-primary   baseline "LLM alone": the primary model, the same question and output sections, no evidence;
                       it is asked to cite PubMed IDs instead (what a user gets by asking a chatbot directly)

Automatic metrics per report (no human rating):

  citation_validity    grounded: cited evidence IDs that were in the prompt / all cited IDs; ungrounded: cited PMIDs
                       that exist in PubMed / all cited PMIDs
  pmid_on_topic        ungrounded: existing PMIDs whose PubMed title names one of the case's genes or drugs or an
                       expected term (VCF: also "lung") / existing PMIDs; a title-only heuristic, so it under-counts
                       papers whose title uses another name
  sentence_coverage    sentences with at least one citation / factual sentences
  unsupported_drugs    drugs named in the report that none of the case's evidence records names / drugs named
  gold_recall          expected answer terms the report names / expected terms (VCF: evaluation.CASES established
                       drugs; gene / drug: CPIC level A pairs and FDA-label facts, GOLD below)
  unsafe_mentions      VCF: drugs the expected answer marks as resistant or not established, named in the Overview or
                       the established-evidence section without any warning (resistance, avoid, not recommended, ...)
                       in the sentences that name them
  latency_ms, input_tokens, output_tokens

Statistics: per metric, mean ± SD per arm; each grounded-primary vs. other arm comparison is a paired Wilcoxon
signed-rank test over the cases where both have a value, Bonferroni-corrected for the number of tests.
"""

import csv
import json
import logging
import re
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import requests
from flask import current_app

from .extensions import db
from .reference.models import DrugSynonym
from .reference.text import norm, strip_salt

logger = logging.getLogger(__name__)

OUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "evaluation"
ARMS = ["grounded-primary", "grounded-comparison", "ungrounded-primary"]
NAMES = ("PREF_NAME", "INN", "USAN", "BAN", "USP", "JAN")
# Expected answer terms for the gene / drug demo searches: CPIC level A gene–drug pairs (cpicpgx.org) and the
# drugs' FDA labels. Any one spelling of a term counts.
GOLD = {
    "CYP2C19": [["clopidogrel"], ["voriconazole"], ["citalopram", "escitalopram"]],
    "CYP2D6": [["codeine"], ["tramadol"], ["tamoxifen"]],
    "BRAF": [["vemurafenib"], ["dabrafenib"], ["encorafenib", "trametinib"]],
    "TPMT": [["azathioprine"], ["mercaptopurine", "6-mercaptopurine"], ["thioguanine", "tioguanine"]],
    "DPYD": [["fluorouracil", "5-fluorouracil", "5-FU"], ["capecitabine"]],
    "clopidogrel": [["CYP2C19"], ["P2Y12"]],
    "Plavix": [["CYP2C19"], ["P2Y12"]],
    "codeine": [["CYP2D6"], ["morphine"]],
    "osimertinib": [["EGFR"], ["T790M"]],
    "warfarin": [["CYP2C9"], ["VKORC1"]],
}
DEMO = [("gene", g) for g in ("CYP2C19", "CYP2D6", "BRAF", "TPMT", "DPYD")] + \
       [("drug", d) for d in ("clopidogrel", "Plavix", "codeine", "osimertinib", "warfarin")]
WARNING_WORDS = re.compile(r"resist|down-?rank|avoid|not recommend|contraindicat|reduced (?:response|sensitivity|"
                           r"efficacy)|no longer|should not|ineffective|lack of (?:response|benefit)|not (?:effective|"
                           r"indicated|approved)|refractory|loss of (?:response|sensitivity)|C797S|T790M", re.I)
E_CITATION = re.compile(r"\[(E\d+(?:\s*(?:[,;]|[–—-])\s*E?\d+)*)\]")
PMID = re.compile(r"PMID[:\s#]*\s*(\d{5,9})", re.I)

UNGROUNDED_SYSTEM = """You are a pharmacogenomics research assistant writing for biomedical researchers and clinicians.
Rules:
- Write the entire response in clear Australian English.
- Support every factual statement with references to the published literature: cite PubMed IDs in square brackets,
  e.g. [PMID 12345678].
- If the evidence is sparse, low-level or conflicting, say so.
- This is research support, not clinical advice."""

UNGROUNDED_VCF = """Write a report in Markdown with these sections:
## Overview
The variants and the headline: the best targeted drug(s) for this cancer type, any resistance, or "no approved
targeted therapy".
## A. Established clinical evidence
Approved targeted drugs for these variants in this cancer type, best first, with the evidence level and why.
## B. Exploratory predictions
Other candidate drugs (lower-level evidence, other cancer types), clearly labelled as predictions.
## Resistance and co-occurring variants
## Co-mutations without a targeted drug
## Limitations
End with: "Research and teaching use only; not clinical advice." """


# ---------- cases ----------

def build_cases(only=None, echo=print):
    """[{"id", "workflow", "name", "evidence", "expected", "query"}]: the evidence is built here, without the LLM."""
    from .evaluation import CASES
    from .reference.normalise import resolve_entities
    from .services.biomed import collect_evidence
    from .services.relations import DEFAULT_RELATION_TYPES
    from .services.variants.cancer_types import resolve_cancer_type
    from .services.variants.pipeline import run_pipeline
    from .services.vcf_explorer import load_example

    cases = []
    for case in CASES:
        if only and case["example"] not in only:
            continue
        variants, info, _ = load_example(case["example"])
        evidence = run_pipeline(variants, info, resolve_cancer_type(case["cancer_type"]))
        cases.append({"id": case["example"], "workflow": "vcf", "name": case["example"], "evidence": evidence,
                      "expected": case["expected"],
                      "query": SimpleNamespace(entities=[case["example"]], entity_type="vcf", relation_types=[],
                                               followup_question=None)})
        echo(f"  evidence  vcf   {case['example']}: {len(evidence.get('items') or [])} items")
    for entity_type, name in DEMO:
        if only and name not in only:
            continue
        resolved = resolve_entities([name], entity_type)[0]
        evidence = collect_evidence(name, DEFAULT_RELATION_TYPES, resolved=resolved, entity_type=entity_type)
        cases.append({"id": name, "workflow": entity_type, "name": name, "evidence": evidence, "expected": GOLD[name],
                      "query": SimpleNamespace(entities=[resolved["name"]], entity_type=entity_type,
                                               relation_types=DEFAULT_RELATION_TYPES, followup_question=None)})
        echo(f"  evidence  {entity_type:<5} {name}: {len(evidence.get('items') or [])} items")
    return cases


def prompts(case, arm):
    """(system, user, evidence IDs in the prompt) for one case and arm."""
    from .services.prompts import REPORT_INSTRUCTIONS, _env, get_active_template, render_prompt

    if arm.startswith("grounded"):
        template = get_active_template("vcf" if case["workflow"] == "vcf" else "gene")
        return render_prompt(template, case["query"], case["evidence"], current_app.config["MAX_PROMPT_EVIDENCE"])
    if case["workflow"] == "vcf":
        inp = case["evidence"].get("input") or {}
        cancer = inp.get("cancer_type") or {}
        variants = (case["evidence"].get("variants") or {})
        rows = (variants.get("tiered") or []) + (variants.get("low_priority") or [])
        listed = "\n".join(f"- {v['notation']} ({(v.get('consequence') or '').replace('_', ' ')}, VAF "
                           f"{round((v.get('vaf') or 0) * 100)}%)" for v in rows) or "(none)"
        user = (f"Cancer type: {cancer.get('name')} (OncoTree {cancer.get('code')})\n"
                f"Somatic variants in the tumour, after quality control and germline filtering:\n{listed}\n\n"
                f"{UNGROUNDED_VCF}")
    else:
        label = "Drug" if case["workflow"] == "drug" else "Gene"
        instructions = _env.from_string(REPORT_INSTRUCTIONS).render(entity_type=case["workflow"],
                                                                     entities=case["query"].entities)
        user = f"{label}: {case['name']}\nRelationship types of interest: all\n\n{instructions}"
    return UNGROUNDED_SYSTEM, user, []


# ---------- metrics ----------

# Official drug names that are also everyday words in a pharmacology text ("molecular oxygen", "plasma glucose")
COMMON_WORDS = {"OXYGEN", "WATER", "ALCOHOL", "ETHANOL", "GLUCOSE", "SODIUM", "POTASSIUM", "CALCIUM", "NITROGEN",
                "CARBON", "ALBUMIN", "HEPARIN SODIUM", "SALINE", "IODINE", "ZINC", "MAGNESIUM", "OXIDE"}


def _drug_dictionary():
    """Normalised official drug name -> ChEMBL ID (names of 5+ characters, minus everyday words)."""
    rows = db.session.execute(db.select(DrugSynonym.synonym_norm, DrugSynonym.chembl_id)
                              .where(DrugSynonym.syn_type.in_(NAMES))).all()
    return {name: chembl for name, chembl in rows
            if len(name) >= 5 and not name.isdigit() and name not in COMMON_WORDS}


def _mentioned(text, dictionary):
    """ChEMBL IDs of the drugs a text names (1- to 4-word names)."""
    words = re.findall(r"[A-Z0-9][A-Z0-9-]*", (text or "").upper())
    found = set()
    for n in range(1, 5):
        for i in range(len(words) - n + 1):
            chembl = dictionary.get(" ".join(words[i:i + n]))
            if chembl:
                found.add(chembl)
    return found


def _evidence_drugs(evidence, dictionary):
    """ChEMBL IDs of every drug the case's evidence names (items, graded pairs, candidates, PBS rows)."""
    names = []

    def walk(value):
        if isinstance(value, dict):
            for key, v in value.items():
                if key in ("drug", "drugs", "therapies", "other", "entity", "full_name", "pref_name") \
                        or key == "trade_names":
                    names.extend(v if isinstance(v, list) else [v])
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk({k: evidence.get(k) for k in ("items", "table", "tracks", "resistance_only", "australian_availability")})
    found = set()
    for name in names:
        if not isinstance(name, str):
            continue
        for part in name.split(" + "):
            found |= _mentioned(part, dictionary) | ({dictionary[strip_salt(part)]} if strip_salt(part) in dictionary
                                                     else set())
    return found


def _sentences(text):
    """Factual sentences: Markdown headings and short lines are left out."""
    lines = [line for line in (text or "").splitlines() if not line.lstrip().startswith("#")]
    plain = re.sub(r"[*_`>|]", " ", "\n".join(lines))
    out = []
    for line in plain.splitlines():
        line = line.strip(" -•\t")
        if not line or len(line) < 40:
            continue
        # "… carcinoma. [E1, E2] Next …": the citation after the full stop belongs to the sentence before it
        out += [s.strip() for s in re.split(r"(?<=[.!?\]])\s+(?=[A-Z(])", line) if len(s.strip()) >= 40]
    return out


def _cited_ids(text):
    from .services.report_docx import _expand

    return [eid for m in E_CITATION.finditer(text or "") for eid in _expand(m.group(1))]


def _pubmed_titles(pmids):
    """{pmid: title} for the PMIDs that exist (NCBI E-utilities esummary; batches of 200)."""
    out = {}
    pmids = sorted(set(pmids))
    for start in range(0, len(pmids), 200):
        batch = pmids[start:start + 200]
        try:
            resp = requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                                params={"db": "pubmed", "id": ",".join(batch), "retmode": "json"}, timeout=60,
                                headers={"User-Agent": "DGI-Explorer/0.1 (academic project)"})
            resp.raise_for_status()
            result = resp.json().get("result", {})
        except Exception as exc:  # noqa: BLE001 - recorded as unknown, not as fabricated
            logger.warning("PubMed check failed: %s", exc)
            return None
        for pmid in batch:
            rec = result.get(pmid) or {}
            if rec.get("title") and not rec.get("error"):
                out[pmid] = rec["title"]
    return out


def score(case, arm, text, prompt_ids, dictionary, titles):
    """The metrics of one report (None where a metric does not apply)."""
    grounded = arm.startswith("grounded")
    sentences = _sentences(text)
    pmids = PMID.findall(text or "")
    cited = _cited_ids(text) if grounded else pmids
    marker = E_CITATION if grounded else PMID
    m = {"sentences": len(sentences),
         "sentence_coverage": (sum(1 for s in sentences if marker.search(s)) / len(sentences)) if sentences else None}
    if grounded:
        shown = set(prompt_ids)
        m["citation_validity"] = (sum(1 for c in cited if c in shown) / len(cited)) if cited else None
        m["pmid_on_topic"] = None
    else:
        if titles is None or not pmids:
            m["citation_validity"] = m["pmid_on_topic"] = None
        else:
            real = [p for p in pmids if p in titles]
            m["citation_validity"] = len(real) / len(pmids)
            terms = _topic_terms(case)
            m["pmid_on_topic"] = (sum(1 for p in real if any(t in titles[p].lower() for t in terms)) / len(real)
                                  if real else 0.0)
    m["citations"] = len(cited)
    named = _mentioned(text, dictionary)
    supported = case["supported"]
    m["drugs_named"] = len(named)
    m["unsupported_drugs"] = (len(named - supported) / len(named)) if named else None
    groups = _gold_groups(case)
    lower = (text or "").lower()
    m["gold_recall"] = (sum(1 for g in groups if any(re.search(rf"(?<![a-z0-9]){re.escape(t.lower())}(?![a-z0-9])",
                                                               lower) for t in g)) / len(groups)) if groups else None
    if case["workflow"] == "vcf":
        # Presented as a recommendation: named in the Overview or the established-evidence section (not in the
        # exploratory predictions, where another tumour type or a resistance note is the point) with no warning.
        bad = [d for d in case["expected"].get("down_ranked", []) + case["expected"].get("forbidden", [])]
        recommending = _sentences(_sections(text, r"overview|established|recommend"))
        unsafe = 0
        for drug in bad:
            parts = [p.strip().lower() for p in drug.split(" + ")]
            hits = [s for s in recommending if all(p in s.lower() for p in parts)]
            if hits and not any(WARNING_WORDS.search(s) for s in hits):
                unsafe += 1
        m["unsafe_mentions"] = unsafe
    else:
        m["unsafe_mentions"] = None
    return m


def _sections(text, heading):
    """The text of the Markdown sections whose heading matches the pattern (case-insensitive)."""
    out, keep = [], False
    for line in (text or "").splitlines():
        if re.match(r"^\s*#{1,3}\s", line):
            keep = bool(re.search(heading, line, re.I)) and not re.search(r"exploratory|prediction", line, re.I)
            continue
        if keep:
            out.append(line)
    return "\n".join(out)


def _topic_terms(case):
    """Words a PubMed title must contain to count as on topic: the case's genes and drugs and the expected terms
    (VCF: the tiered variants' genes, the expected drugs and "lung"; gene / drug: the name and the expected terms)."""
    terms = [t for group in _gold_groups(case) for t in group]
    if case["workflow"] == "vcf":
        terms += [v["gene"] for v in (case["evidence"].get("variants") or {}).get("tiered") or [] if v.get("gene")]
        terms.append("lung")
    else:
        terms += [case["name"], *case["query"].entities]
    return list(dict.fromkeys(t.lower() for t in terms if t))


def _gold_groups(case):
    if case["workflow"] == "vcf":
        return [[d] for d in case["expected"].get("established", [])]
    return case["expected"]


# ---------- the run ----------

def run(arms=None, only=None, dry_run=False, echo=print, out_dir=OUT_DIR):
    from .services.llm import get_llm_client

    cfg = current_app.config
    arms = arms or ARMS
    providers = {"grounded-primary": cfg["PRIMARY_PROVIDER"], "grounded-comparison": cfg["COMPARISON_PROVIDER"],
                 "ungrounded-primary": cfg["PRIMARY_PROVIDER"]}
    echo("Building the evidence for each case (no LLM):")
    cases = build_cases(only, echo)
    dictionary = _drug_dictionary()
    for case in cases:
        case["supported"] = _evidence_drugs(case["evidence"], dictionary)
    jobs = []
    for case in cases:
        if case["workflow"] == "vcf" and (case["evidence"].get("outcome") or {}).get("status") != "found":
            continue  # nothing tiered: the deployed system writes no LLM report, so there is nothing to compare
        for arm in arms:
            system, user, ids = prompts(case, arm)
            jobs.append({"case": case, "arm": arm, "provider": providers[arm], "system": system, "user": user,
                         "ids": ids})
    models = {arm: cfg[f"{providers[arm].upper()}_MODEL"] for arm in arms}
    echo(f"\n{len(jobs)} LLM calls planned: {len(jobs) // max(len(arms), 1)} cases × {len(arms)} arms "
         f"({', '.join(f'{a} = {models[a]}' for a in arms)}); about "
         f"{sum(len(j['system']) + len(j['user']) for j in jobs) // 4:,} input tokens in total.")
    if dry_run:
        return None

    clients = {p: get_llm_client(p) for p in set(providers[a] for a in arms)}

    def call(job):
        try:
            res = clients[job["provider"]].complete(job["system"], job["user"])
            return {"text": res.text, "model": res.model, "latency_ms": res.latency_ms,
                    "input_tokens": res.input_tokens, "output_tokens": res.output_tokens, "error": None}
        except Exception as exc:  # noqa: BLE001 - recorded, the other calls go on
            return {"text": "", "model": models[job["arm"]], "error": str(exc)[:300]}

    with ThreadPoolExecutor(max_workers=4) as pool:
        answers = list(pool.map(call, jobs))
    pmids = [p for a, j in zip(answers, jobs) if not j["arm"].startswith("grounded") for p in PMID.findall(a["text"])]
    titles = _pubmed_titles(pmids) if pmids else {}
    rows = []
    for job, ans in zip(jobs, answers):
        case = job["case"]
        metrics = score(case, job["arm"], ans["text"], job["ids"], dictionary, titles) if not ans["error"] else {}
        rows.append({"case": case["id"], "workflow": case["workflow"], "arm": job["arm"], "model": ans["model"],
                     "prompt_ids": job["ids"],
                     "error": ans["error"], "latency_ms": ans.get("latency_ms"),
                     "input_tokens": ans.get("input_tokens"), "output_tokens": ans.get("output_tokens"), **metrics,
                     "text": ans["text"]})
        echo(f"  {job['arm']:<20} {case['id']:<28} " + (f"ERROR {ans['error']}" if ans["error"] else
             f"coverage {_fmt(metrics.get('sentence_coverage'))}, citations valid {_fmt(metrics.get('citation_validity'))}, "
             f"unsupported drugs {_fmt(metrics.get('unsupported_drugs'))}, recall {_fmt(metrics.get('gold_recall'))}"))
    summary = summarise(rows, arms)
    write(rows, summary, models, out_dir, pubmed_checked=titles is not None)
    return summary


def rescore(echo=print, out_dir=OUT_DIR):
    """Recompute every metric from the saved report texts (benchmark_reports.json), without calling the LLMs.
    The evidence is rebuilt from the local response store only (HTTP_CACHE=offline), so it is the evidence the run
    used; the prompt IDs are re-rendered from it when an older file did not save them."""
    cfg = current_app.config
    saved = json.loads((Path(out_dir) / "benchmark_reports.json").read_text(encoding="utf-8"))
    previous = {(r["case"], r["arm"]): r for r in csv.DictReader(open(Path(out_dir) / "benchmark_cases.csv"))}
    mode, cfg["HTTP_CACHE"] = cfg["HTTP_CACHE"], "offline"
    try:
        echo("Rebuilding the evidence from the local response store (no LLM, no live API):")
        cases = {c["id"]: c for c in build_cases(sorted({r["case"] for r in saved}), echo)}
    finally:
        cfg["HTTP_CACHE"] = mode
    dictionary = _drug_dictionary()
    for case in cases.values():
        case["supported"] = _evidence_drugs(case["evidence"], dictionary)
    pmids = [p for r in saved if not r["arm"].startswith("grounded") for p in PMID.findall(r["text"] or "")]
    titles = _pubmed_titles(pmids) if pmids else {}
    rows, models = [], {}
    for r in saved:
        case, arm = cases[r["case"]], r["arm"]
        ids = r.get("prompt_ids") or (prompts(case, arm)[2] if arm.startswith("grounded") else [])
        old = previous.get((r["case"], arm), {})
        num = lambda k: (r.get(k) if r.get(k) is not None else (float(old[k]) if old.get(k) else None))
        models[arm] = r["model"]
        rows.append({"case": r["case"], "workflow": case["workflow"], "arm": arm, "model": r["model"],
                     "prompt_ids": ids, "error": r.get("error"), "latency_ms": num("latency_ms"),
                     "input_tokens": num("input_tokens"), "output_tokens": num("output_tokens"),
                     **score(case, arm, r["text"], ids, dictionary, titles), "text": r["text"]})
    arms = [a for a in ARMS if a in models]
    summary = summarise(rows, arms)
    write(rows, summary, models, out_dir, pubmed_checked=titles is not None)
    return summary, rows, cases, dictionary


METRICS = [("citation_validity", "Verifiable citations", True), ("pmid_on_topic", "Real PMIDs on topic", True),
           ("sentence_coverage", "Sentences with a citation", True),
           ("unsupported_drugs", "Drugs not in the evidence", False), ("gold_recall", "Expected answer recall", True),
           ("unsafe_mentions", "Resistant drugs named without warning", False), ("latency_ms", "Latency (ms)", False),
           ("output_tokens", "Output tokens", False)]


def summarise(rows, arms):
    """{"arms": {arm: {metric: {"mean", "sd", "n"}}}, "tests": [...]} with paired Wilcoxon tests vs. grounded-primary."""
    out = {"arms": {}, "tests": []}
    for arm in arms:
        out["arms"][arm] = {}
        for key, _, _ in METRICS:
            values = [r[key] for r in rows if r["arm"] == arm and r.get(key) is not None]
            out["arms"][arm][key] = {"mean": statistics.fmean(values) if values else None,
                                     "sd": statistics.stdev(values) if len(values) > 1 else None, "n": len(values)}
    try:
        from scipy.stats import wilcoxon
    except ImportError:
        wilcoxon = None
    others = [a for a in arms if a != "grounded-primary"]
    tests = []
    for other in others:
        for key, label, _ in METRICS:
            if key in ("pmid_on_topic",):
                continue
            pairs = {}
            for r in rows:
                if r.get(key) is not None and r["arm"] in ("grounded-primary", other):
                    pairs.setdefault(r["case"], {})[r["arm"]] = r[key]
            paired = [(v["grounded-primary"], v[other]) for v in pairs.values() if len(v) == 2]
            if len(paired) < 5 or wilcoxon is None:
                continue
            diffs = [a - b for a, b in paired]
            p = 1.0 if all(d == 0 for d in diffs) else float(wilcoxon([a for a, _ in paired], [b for _, b in paired]).pvalue)
            tests.append({"metric": key, "label": label, "vs": other, "n": len(paired),
                          "median_difference": statistics.median(diffs), "p": p})
    for t in tests:
        t["p_bonferroni"] = min(1.0, t["p"] * len(tests))
    out["tests"] = tests
    return out


def _fmt(value, pct=True):
    if value is None:
        return "–"
    return f"{value:.0%}" if pct else f"{value:,.0f}"


def write(rows, summary, models, out_dir, pubmed_checked=True):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    keys = ["case", "workflow", "arm", "model", "error", "citations", "citation_validity", "pmid_on_topic",
            "sentences", "sentence_coverage", "drugs_named", "unsupported_drugs", "gold_recall", "unsafe_mentions",
            "latency_ms", "input_tokens", "output_tokens"]
    with open(out_dir / "benchmark_cases.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    (out_dir / "benchmark_reports.json").write_text(json.dumps(
        [{k: r.get(k) for k in ("case", "arm", "model", "text", "prompt_ids", "error", "latency_ms", "input_tokens",
                                "output_tokens")} for r in rows], indent=1, ensure_ascii=False), encoding="utf-8")
    arms = list(summary["arms"])
    lines = [f"# Benchmark: grounded vs. ungrounded LLM reports", "",
             f"Run {stamp}. Generated by `flask --app run benchmark` (backend/app/benchmark.py); per-case values in "
             f"`benchmark_cases.csv`, the report texts in `benchmark_reports.json`.", "",
             "Arms: " + "; ".join(f"**{a}** ({models[a]})" for a in arms) + ".", "",
             "| Metric | " + " | ".join(arms) + " |", "| --- |" + " --- |" * len(arms)]
    for key, label, higher in METRICS:
        cells = []
        for arm in arms:
            s = summary["arms"][arm][key]
            if s["mean"] is None:
                cells.append("–")
            elif key in ("latency_ms", "output_tokens", "unsafe_mentions"):
                cells.append(f"{s['mean']:,.1f}" + (f" ± {s['sd']:,.1f}" if s["sd"] is not None else "") + f" (n={s['n']})")
            else:
                cells.append(f"{s['mean']:.0%}" + (f" ± {s['sd']:.0%}" if s["sd"] is not None else "") + f" (n={s['n']})")
        lines.append(f"| {label} ({'higher' if higher else 'lower'} is better) | " + " | ".join(cells) + " |")
    lines += ["", "Mean ± SD over the cases with a value (n). Paired Wilcoxon signed-rank tests against "
              "grounded-primary, Bonferroni-corrected:", "", "| Metric | vs. | n | Median difference | p | p (Bonferroni) |",
              "| --- | --- | --- | --- | --- | --- |"]
    for t in summary["tests"]:
        lines.append(f"| {t['label']} | {t['vs']} | {t['n']} | {t['median_difference']:+.3g} | {t['p']:.3g} | "
                     f"{t['p_bonferroni']:.3g} |")
    errors = [r for r in rows if r["error"]]
    lines += ["", "Notes:", "",
              "- Citation validity: grounded arms cite evidence IDs, checked against the IDs in the prompt; the "
              "ungrounded arm cites PMIDs, checked against PubMed" + ("" if pubmed_checked else " (PubMed was not "
              "reachable, so these are missing)") + ". A real PMID can still be about something else: see "
              "*Real PMIDs on topic*.",
              "- Drugs not in the evidence: drugs the report names that no evidence record of the case names. For the "
              "ungrounded arm this measures claims the curated sources do not back, not necessarily false ones.",
              "- Expected answer recall: VCF cases use the expected established drugs of `evaluation.CASES`; gene / drug "
              "cases use CPIC level A pairs and FDA-label facts (`benchmark.GOLD`).",
              "- VCF cases where nothing is tiered are skipped: the deployed system writes no LLM report for them.",
              f"- {len(errors)} call(s) failed." if errors else "- Every call returned a report."]
    (out_dir / "benchmark.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
