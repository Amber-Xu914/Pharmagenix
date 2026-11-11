# Evaluation

Two evaluations, both reproducible from the repository:

1. **Deterministic pipeline accuracy**: the QC, tiering and drug-track stages of Workflow 4 (no LLM) against expected
   answers taken from the published evidence (CIViC, FDA labels, NCCN guidance for NSCLC).
2. **LLM benchmark**: does grounding the LLM in retrieved, graded evidence make its reports more verifiable than the
   same model answering alone? Three arms on 18 cases, automatic metrics, paired statistics.

## 1. Deterministic pipeline accuracy

`flask --app run mlflow-eval` (backend/app/evaluation.py) runs the 8 synthetic mock VCFs through stages 1–7 and scores
six checks per case. Results (MLflow run `dcb610d739a44c07b8ec20a9df7d1572`, 11 November 2026):

| Check | What it tests | Pass rate (8 cases) |
| --- | --- | --- |
| `tiers_correct` | every variant gets its expected tier | 100% |
| `qc_filter_correct` | low-quality and germline variants are removed, the rest kept | 100% |
| `expected_drugs_established` | e.g. osimertinib for EGFR L858R + T790M, sotorasib / adagrasib for KRAS G12C | 100% |
| `resistance_down_ranked` | e.g. gefitinib / erlotinib with T790M, osimertinib with C797S | 100% |
| `established_not_contaminated` | no drug from a QC artefact or a Tier 3 variant on the established track | 100% |
| `no_therapy_flag_correct` | "no approved targeted therapy" shown exactly when expected (LUSC, SCLC, Tier 3 only) | 100% |

## 2. LLM benchmark: grounded vs. ungrounded

`flask --app run benchmark` (backend/app/benchmark.py). Full results: [benchmark.md](benchmark.md); per-case values:
`benchmark_cases.csv`; the 54 report texts: `benchmark_reports.json`.

**Design.** 18 cases: the 8 mock VCFs and the 10 demo searches (CYP2C19, CYP2D6, BRAF, TPMT, DPYD; clopidogrel, Plavix,
codeine, osimertinib, warfarin). The evidence for each case is built once, so the arms differ only in the model and in
whether it sees the evidence:

| Arm | Model | Prompt |
| --- | --- | --- |
| grounded-primary (the deployed system) | Claude Sonnet 4.6, temperature 0.3 | active template + graded evidence, cite evidence IDs |
| grounded-comparison | gpt-6-luna | the same prompt and evidence |
| ungrounded-primary (baseline: "ask the LLM") | Claude Sonnet 4.6 | the same question and sections, no evidence; cite PMIDs |

**Results** (mean ± SD; p from paired Wilcoxon signed-rank tests against grounded-primary, Bonferroni-corrected over
14 tests):

| Metric | Grounded Claude | Grounded GPT | Ungrounded Claude |
| --- | --- | --- | --- |
| Verifiable citations | 100% | 100% | 98% of PMIDs exist |
| ...of which on topic (PubMed title names the case's genes / drugs) | – | – | **42% ± 22%** |
| Sentences with a citation | 65% ± 15% | **93% ± 7%** (p < 0.001) | 47% ± 13% (p = 0.014) |
| Drugs named that no evidence record names | 3% ± 12% | 0% | **43% ± 37%** (p = 0.009) |
| Expected answer recall | 96% | 93% (n.s.) | 96% (n.s.) |
| Resistant / non-established drugs recommended without a warning | 0 | 0 | 0 |
| Latency | 30 s | **20 s** (p < 0.001) | 72 s (p < 0.001) |
| Output tokens | 1,480 | 1,990 (n.s.) | 3,900 (p < 0.001) |

**Findings.**

- *Grounding makes the report checkable without making it less complete.* The ungrounded model recalled the expected
  answers just as often (96%), but 43% of the drugs it named are in none of the case's evidence records, against 3%
  with grounding, and fewer of its sentences carry a citation (47% vs 65%).
- *An LLM's own references look valid but mostly are not.* The ungrounded arm cited 250 distinct PMIDs; 248 exist in
  PubMed (2 do not: 12495865, 19530844), so a check of "does the PMID exist" would pass it. But in only 42% of them does
  the PubMed title name the case's genes or drugs. Two examples, checked by hand against PubMed:
  - TPMT report: "rs1800460 … reduces TPMT protein stability via enhanced proteasomal degradation [PMID 17786338]".
    PMID 17786338 is *Prognostic value of differential expression of Laminin-5 gamma2 in oral squamous cell
    carcinomas*.
  - KRAS G12C report: "Amplification of wild-type KRAS or MET [PMID 34534465]". PMID 34534465 is *Proteogenomic
    characterization of pancreatic ductal adenocarcinoma*.

  In the grounded arms every citation points to a record the user can open, and the system checks each one
  automatically (100% valid in both models).
- *Model choice.* With the same evidence, gpt-6-luna cites more sentences (93% vs 65%) and is faster (20 s vs 30 s);
  recall and evidence use do not differ significantly. Claude remains the primary model for its temperature control
  (gpt-6-luna is a reasoning model and accepts none); the comparison report lets users see both.
- *Safety.* No arm presented a drug that the expected answer marks as resistant or not established as a recommendation
  without a warning, in any of the 8 VCF cases.

**Limitations.**

- **13 of the 18 ungrounded reports reached the 4,000-token output limit (`LLM_MAX_TOKENS`) and were cut off**; no
  grounded report came near it (longest 2,966 tokens). The ungrounded metrics are therefore measured on truncated
  text: the later sections (exploratory drugs, limitations) are partly missing, which lowers its drug and citation
  counts. Its expected-answer recall was still 96%. A re-run of that arm with a higher limit would remove this bias.
- 18 cases and one run per arm; the metrics are automatic and no clinician rated the reports.
- "On topic" is a title-only check: it under-counts relevant papers whose titles use other names, so 42% is a lower
  bound on relevance; it does not test whether a paper supports the specific claim.
- "Drugs not in the evidence" measures what the curated sources do not back, not what is false: the grounded codeine
  report names morphine, its active metabolite, from the model's own knowledge.
- The expected answers for the gene / drug cases are small lists of CPIC level A pairs and label facts
  (`benchmark.GOLD`).

## Reproducing

```bash
cd backend
flask --app run mlflow-eval                 # deterministic checks (needs MLFLOW_TRACKING_URI)
flask --app run benchmark --dry-run         # build the evidence and print the plan (no LLM calls)
flask --app run benchmark                   # 54 LLM calls; rewrites benchmark.md, benchmark_cases.csv, benchmark_reports.json
flask --app run benchmark --rescore         # recompute the metrics from the saved reports (no LLM calls)
```
